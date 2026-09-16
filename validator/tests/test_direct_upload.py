"""ACL credential isolation, direct signed S3 uploads and destination-specific recovery."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from pydantic import SecretStr, ValidationError

from tests.test_reporting import GENESIS, NOW, VALIDATORS, completed_ledger, service_at, signed_report
from validator.reporting.check import verify_window
from validator.reporting.credentials import StorageCredentials
from validator.reporting.journal import ReportJournal
from validator.reporting.protocol import SignedScoreReport, UploadReceipt
from validator.reporting.publisher import ReportingSettings, ReportSender
from validator.reporting.service import ScoreService
from validator.reporting.storage import HippiusStore
from validator.reporting.upload import HippiusUploader


def credentials(index: int = 0) -> StorageCredentials:
    return StorageCredentials(
        access_key_id=SecretStr(f"test-key-{index}"), secret_access_key=SecretStr(f"test-secret-{index}")
    )


def test_three_profiles_upload_as_three_hotkeys_and_reader_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-environment-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated-environment-secret")
    reports: list[SignedScoreReport] = []
    for index, key in enumerate(VALIDATORS):
        store = HippiusStore.connect("scores", credentials(index))
        presigned = store.client.generate_presigned_url("put_object", Params={"Bucket": "scores", "Key": "test"})
        assert parse_qs(urlsplit(presigned).query)["X-Amz-Credential"][0].startswith(f"test-key-{index}/")
        ledger = completed_ledger(tmp_path / f"validator-{index}.sqlite3")
        journal = ReportJournal(ledger.path, GENESIS, 2, key, "hippius:https://s3.hippius.com/scores")
        journal.initialize()
        report = journal.prepare_next(NOW)
        assert report is not None
        reports.append(report)
        with Stubber(store.client) as stub:
            stub.add_response(
                "put_object",
                {},
                {
                    "Bucket": "scores",
                    "Key": report.object_key,
                    "Body": report.model_dump_json().encode(),
                    "ContentType": "application/json",
                },
            )
            ReportSender(journal, HippiusUploader(store, key.ss58_address)).tick(NOW)
            assert journal.due(NOW) is None
            stub.assert_no_pending_responses()  # WRITE-only uploads make no GET or LIST request.
        with closing(sqlite3.connect(ledger.path)) as db:
            assert db.execute("SELECT verification FROM report_deliveries").fetchone() == ("upload_accepted",)
        store.client.close()

    reader = HippiusStore.connect("scores", credentials())
    with Stubber(reader.client) as stub:
        for report in sorted(reports, key=lambda item: item.report.validator_hotkey):
            data = report.model_dump_json().encode()
            stub.add_response(
                "get_object",
                {"Body": StreamingBody(BytesIO(data), len(data))},
                {
                    "Bucket": "scores",
                    "Key": report.object_key,
                },
            )
        page = ScoreService(reader, GENESIS, 2, frozenset(key.ss58_address for key in VALIDATORS)).read_page(100)
        assert {item.report.validator_hotkey for item in page.reports} == {key.ss58_address for key in VALIDATORS}
        stub.assert_no_pending_responses()
    reader.client.close()


def test_local_receipt_does_not_satisfy_hippius_and_credentials_can_rotate(tmp_path: Path) -> None:
    ledger = completed_ledger(tmp_path / "validator.sqlite3")
    local = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
    local.initialize()
    report = local.prepare_next(NOW)
    assert report is not None
    local.acknowledge(report, UploadReceipt(report_id=report.report_id, object_key=report.object_key, stored_at=NOW))
    assert local.due(NOW) is None
    remote = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0], "hippius:https://s3.hippius.com/scores")
    remote.initialize()
    assert remote.due(NOW) == report
    remote.acknowledge(
        report,
        UploadReceipt(
            report_id=report.report_id,
            object_key=report.object_key,
            stored_at=NOW,
            verification="upload_accepted",
        ),
    )
    remote.initialize()
    assert remote.due(NOW) is None
    first = ReportingSettings(bucket="scores", chain_genesis=GENESIS, wallet_name="operator", hotkey_name="default")
    second = first.model_copy()
    assert first.destination == second.destination


def test_wrong_signer_is_rejected_before_s3_and_readback_detects_replacement(tmp_path: Path) -> None:
    store = HippiusStore.connect("scores", credentials())
    report = signed_report()
    uploader = HippiusUploader(store, VALIDATORS[0].ss58_address, verify_readback=True)
    with Stubber(store.client) as stub:
        with pytest.raises(ValueError, match="hotkey"):
            uploader.upload(signed_report(VALIDATORS[1]))
        data = report.model_dump_json().encode()
        params = {"Bucket": "scores", "Key": report.object_key}
        stub.add_client_error(
            "get_object", service_error_code="NoSuchKey", http_status_code=404, expected_params=params
        )
        stub.add_response("put_object", {}, {**params, "Body": data, "ContentType": "application/json"})
        other = signed_report(round_id=2).model_dump_json().encode()
        stub.add_response("get_object", {"Body": StreamingBody(BytesIO(other), len(other))}, params)
        with pytest.raises(OSError, match="differs"):
            uploader.upload(report)
        stub.assert_no_pending_responses()
    uploader.close()


def test_acl_denial_remains_pending_without_local_fallback(tmp_path: Path) -> None:
    ledger = completed_ledger(tmp_path / "validator.sqlite3")
    journal = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0], "hippius:https://s3.hippius.com/scores")
    journal.initialize()
    report = journal.prepare_next(NOW)
    assert report is not None
    store = HippiusStore.connect("scores", credentials())
    with Stubber(store.client) as stub:
        stub.add_client_error(
            "put_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={
                "Bucket": "scores",
                "Key": report.object_key,
                "Body": report.model_dump_json().encode(),
                "ContentType": "application/json",
            },
        )
        with pytest.raises(ClientError):
            ReportSender(journal, HippiusUploader(store, VALIDATORS[0].ss58_address)).tick(NOW)
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT uploaded_at,attempts,last_error FROM report_deliveries").fetchone() == (
            None,
            1,
            "AccessDenied",
        )
    store.client.close()


def test_existing_newer_hippius_epoch_snapshot_retires_stale_retry_without_put(tmp_path: Path) -> None:
    ledger = completed_ledger(tmp_path / "validator.sqlite3")
    journal = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
    journal.initialize()
    old = journal.prepare_next(NOW)
    assert old is not None
    fresh = SignedScoreReport.sign(old.report.model_copy(update={"round_id": 2}), VALIDATORS[0])
    data = fresh.model_dump_json().encode()
    store = HippiusStore.connect("scores", credentials())
    with Stubber(store.client) as stub:
        stub.add_response(
            "get_object", {"Body": StreamingBody(BytesIO(data), len(data))}, {"Bucket": "scores", "Key": old.object_key}
        )
        ReportSender(journal, HippiusUploader(store, VALIDATORS[0].ss58_address, verify_readback=True)).tick(NOW)
        stub.assert_no_pending_responses()
    assert journal.due(NOW) is None
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT superseded_by,uploaded_at FROM report_deliveries").fetchone() == (
            fresh.report_id,
            None,
        )
    store.client.close()


def test_readback_requires_three_authors_in_the_same_window_and_rejects_forgery(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    for key in VALIDATORS[:2]:
        service.upload(signed_report(key).model_dump_json().encode())
    with pytest.raises(ValueError, match="every configured validator"):
        verify_window(service, 100, "unit-test-bucket")
    third = signed_report(VALIDATORS[2])
    other_source = SignedScoreReport.sign(
        third.report.model_copy(update={"source_digest": "c" * 64, "completed_block": 119}),
        VALIDATORS[2],
    )
    service.upload(other_source.model_dump_json().encode())
    with pytest.raises(ValueError, match="every configured validator"):
        verify_window(service, 100, "unit-test-bucket")
    service.upload(third.model_dump_json().encode())
    result = verify_window(service, 100, "unit-test-bucket")
    assert len(result.validator_hotkeys) == 3 and len(result.report_ids) == 3
    forged = third.model_copy(update={"signature": "0" * 128})
    service.store.write(third.object_key, forged.model_dump_json().encode())
    with pytest.raises(ValueError, match="every configured validator"):
        verify_window(service, 100, "unit-test-bucket")


def test_credentials_are_explicit_redacted_and_ignore_ambient_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_PROFILE", "nonexistent")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated")
    for i in range(3):
        monkeypatch.setenv("HIPPIUS_ACCESS_KEY_ID", f"test-key-{i}")
        monkeypatch.setenv("HIPPIUS_SECRET_ACCESS_KEY", f"test-secret-{i}")
        selected = StorageCredentials.model_validate({})
        assert f"test-secret-{i}" not in repr(selected)
        store = HippiusStore.connect("test-scores", selected)
        url = store.client.generate_presigned_url("put_object", Params={"Bucket": "test-scores", "Key": "test"})
        assert parse_qs(urlsplit(url).query)["X-Amz-Credential"][0].startswith(f"test-key-{i}/")
        store.client.close()
    monkeypatch.delenv("HIPPIUS_SECRET_ACCESS_KEY")
    with pytest.raises(ValidationError) as error:
        StorageCredentials.model_validate({})
    assert "test-key-2" not in str(error.value)
