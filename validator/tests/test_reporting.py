"""Signed transport, multi-validator isolation, S3 readback and recovery regressions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
from bittensor_wallet import Keypair
from botocore.response import StreamingBody
from botocore.stub import Stubber
from pydantic import ValidationError

from tests.storage_helpers import GatewayUploader, LocalReportStore, ServiceUploader
from tests.test_evaluation import CATALOG, response_for
from validator.credentials.evaluation import EvaluationLedger
from validator.reporting.credentials import StorageCredentials
from validator.reporting.journal import ReportJournal
from validator.reporting.protocol import MinerScore, ScoreReport, SignedScoreReport, UploadReceipt
from validator.reporting.publisher import ReportSender
from validator.reporting.reader import ReaderSettings, ScoreHTTPServer
from validator.reporting.service import ReportPage, ScoreService
from validator.reporting.snapshot import SupersededReport
from validator.reporting.storage import HippiusStore

NOW = datetime(2026, 9, 16, tzinfo=UTC)
GENESIS = "0x" + "a" * 64
VALIDATORS = tuple(Keypair.create_from_uri(f"//{name}") for name in ("Alice", "Bob", "Charlie"))
MINERS = tuple(Keypair.create_from_uri(f"//{name}").ss58_address for name in ("Dave", "Eve"))
ROSTER = {hotkey: uid for uid, hotkey in enumerate(MINERS, 2)}


def signed_report(key: Keypair = VALIDATORS[0], round_id: int = 1) -> SignedScoreReport:
    report = ScoreReport(
        chain_genesis=GENESIS,
        netuid=2,
        validator_hotkey=key.ss58_address,
        instance_id=UUID(int=1),
        round_id=round_id,
        epoch_start=100,
        epoch_end=460,
        completed_block=120,
        source_version="fixture-board-v1",
        source_digest="b" * 64,
        started_at=NOW,
        completed_at=NOW,
        miners=tuple(
            MinerScore(
                miner_hotkey=hotkey,
                miner_uid=ROSTER[hotkey],
                score_sum=-1,
                assigned_tasks=5,
                correct_tasks=4,
            )
            for hotkey in sorted(MINERS)
        ),
    )
    return SignedScoreReport.sign(report, key)


def completed_ledger(path: Path, zero_hotkey: str = MINERS[1]) -> EvaluationLedger:
    ledger = EvaluationLedger(path)
    ledger.initialize_evaluation()
    for _ in range(len(CATALOG.cases) * len(ROSTER)):
        assignment = ledger.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=10), 4)
        assert assignment is not None
        response = response_for(assignment.request) if assignment.hotkey != zero_hotkey else None
        ledger.finish(assignment.request.task_id, "verified" if response else "transport_error", None, response, NOW)
    assert (
        ledger.next_assignment(
            CATALOG,
            ROSTER,
            2,
            NOW,
            timedelta(seconds=10),
            4,
            completed_block=120,
            epoch_start=100,
            epoch_end=460,
        )
        is None
    )
    return ledger


def service_at(path: Path) -> ScoreService:
    return ScoreService(LocalReportStore(path), GENESIS, 2, frozenset(key.ss58_address for key in VALIDATORS))


def test_latest_scores_publish_before_history_and_backoff_allows_other_reports(tmp_path: Path) -> None:
    ledger = completed_ledger(tmp_path / "validator.sqlite3")
    journal = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
    journal.initialize()
    old = journal.prepare_next(NOW)
    assert old is not None
    completed_ledger(ledger.path)
    completed_ledger(ledger.path)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE rounds SET completed_block=480,epoch_start=461,epoch_end=821 WHERE id>1")
    later = NOW + timedelta(seconds=1)
    fresh = journal.prepare_next(later)
    assert fresh is not None and fresh.report.round_id == old.report.round_id + 2
    assert journal.due(later) == fresh
    journal.failed(fresh.report_id, later, "TimeoutError")
    assert journal.due(later) == old
    receipt = UploadReceipt(report_id=old.report_id, object_key=old.object_key, stored_at=later)
    journal.acknowledge(old, receipt)
    assert journal.due(later) is None
    assert journal.due(later + timedelta(seconds=2)) == fresh


def test_same_epoch_coalesces_and_old_retries_stay_superseded_after_restart(tmp_path: Path) -> None:
    ledger = completed_ledger(tmp_path / "validator.sqlite3")
    journal = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
    journal.initialize()
    old = journal.prepare_next(NOW)
    assert old is not None
    completed_ledger(ledger.path)
    fresh = journal.prepare_next(NOW)
    assert fresh is not None and fresh.object_key == old.object_key
    journal.failed(fresh.report_id, NOW, "TimeoutError")
    assert journal.due(NOW) is None
    reopened = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
    reopened.initialize()
    assert reopened.due(NOW + timedelta(seconds=2)) == fresh
    reopened.acknowledge(fresh, UploadReceipt(report_id=fresh.report_id, object_key=fresh.object_key, stored_at=NOW))
    assert reopened.due(NOW + timedelta(days=1)) is None
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute(
            "SELECT superseded_by,uploaded_at FROM report_deliveries WHERE report_id=?", (old.report_id,)
        ).fetchone() == (fresh.report_id, None)
        assert db.execute("SELECT count(*) FROM report_outbox").fetchone() == (2,)


def test_epoch_file_updates_in_place_and_older_uploads_cannot_replace_it(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    old, fresh = signed_report(), signed_report(round_id=2)
    service.upload(old.model_dump_json().encode())
    service.upload(fresh.model_dump_json().encode())
    with pytest.raises(SupersededReport):
        service.upload(old.model_dump_json().encode())
    assert list(tmp_path.rglob("*.json")) == [tmp_path / fresh.object_key]
    assert service.read_page(100).reports == (fresh,)
    assert service.read_page(101).reports == ()
    with pytest.raises(ValueError, match="token"):
        service.read_page(100, "../untrusted")


def test_report_signature_canonicalization_and_tampering() -> None:
    signed = signed_report()
    signed.verify()
    assert signed.object_key == f"{VALIDATORS[0].ss58_address}/100.json"
    reordered = json.dumps(json.loads(signed.model_dump_json()), indent=2)
    SignedScoreReport.model_validate_json(reordered).verify()
    assert signed.report.miners[0].score == -0.2
    changed = signed.report.model_copy(update={"netuid": 3})
    with pytest.raises(ValueError, match="digest"):
        signed.model_copy(update={"report": changed}).verify()
    with pytest.raises(ValueError, match="signature"):
        signed.model_copy(update={"report": changed, "report_id": changed.report_id}).verify()
    with pytest.raises(ValueError, match="Signer"):
        SignedScoreReport.sign(signed.report, VALIDATORS[1])


def test_checked_in_interoperability_vector() -> None:
    path = Path(__file__).parents[2] / "protocol/score-reports-v1/test-vector.json"
    vector = json.loads(path.read_text())
    signed = SignedScoreReport.model_validate(vector["envelope"])
    signed.verify()
    assert signed.report.canonical_bytes().decode() == vector["canonical_report_utf8"]


def test_reject_ambiguous_miners_and_invalid_score_bounds() -> None:
    signed = signed_report()
    data = signed.report.model_dump()
    data["miners"] = [signed.report.miners[0], signed.report.miners[0]]
    with pytest.raises(ValidationError, match="unique"):
        ScoreReport.model_validate(data)
    with pytest.raises(ValidationError, match="bounds"):
        MinerScore(miner_hotkey=MINERS[0], miner_uid=2, score_sum=-26, assigned_tasks=5, correct_tasks=0)


def test_service_authorizes_before_touching_storage_and_retries_idempotently(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    signed = signed_report()
    first = service.upload(signed.model_dump_json().encode())
    second = service.upload(SignedScoreReport.sign(signed.report, VALIDATORS[0]).model_dump_json().encode())
    assert first.report_id == second.report_id
    assert len(list(tmp_path.rglob("*.json"))) == 1
    with pytest.raises(PermissionError):
        service.upload(signed_report(Keypair.create_from_uri("//Ferdie")).model_dump_json().encode())
    foreign = SignedScoreReport.sign(signed.report.model_copy(update={"chain_genesis": "0x" + "c" * 64}), VALIDATORS[0])
    with pytest.raises(PermissionError):
        service.upload(foreign.model_dump_json().encode())
    forged = signed.model_copy(update={"signature": "0" * 128})
    with pytest.raises(ValueError):
        service.upload(forged.model_dump_json().encode())
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_three_validators_remain_separate_through_real_http_and_pagination(tmp_path: Path) -> None:
    service = service_at(tmp_path / "objects")
    with ScoreHTTPServer(("127.0.0.1", 0), service) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
                for index, key in enumerate(VALIDATORS):
                    ledger = completed_ledger(tmp_path / f"validator-{index}.sqlite3", MINERS[index % 2])
                    journal = ReportJournal(ledger.path, GENESIS, 2, key)
                    journal.initialize()
                    sender = ReportSender(journal, ServiceUploader(service))
                    sender.tick(NOW)
                    assert journal.due(NOW) is None
                collected: list[SignedScoreReport] = []
                token: str | None = None
                while True:
                    params = {"epoch_start": "100", "limit": "1"}
                    if token:
                        params["token"] = token
                    response = client.get("/v1/reports", params=params)
                    assert response.status_code == 200
                    page = ReportPage.model_validate_json(response.content)
                    collected.extend(page.reports)
                    token = page.next_token
                    if token is None:
                        break
                assert {item.report.validator_hotkey for item in collected} == {key.ss58_address for key in VALIDATORS}
                assert len({item.report.window_id for item in collected}) == 1
                for item in collected:
                    item.verify()
                    assert sorted(miner.score for miner in item.report.miners) == [0, 1]
                    assert all(miner.assigned_tasks == 5 for miner in item.report.miners)
                assert client.post("/v1/reports", content=b"{}").status_code == 405
                assert client.get("/v1/reports?epoch_start=-1").status_code == 400
                assert client.post("/v1/reports", content=signed_report().model_dump_json()).status_code == 405
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_storage_outage_lost_ack_and_restart_keep_exact_bytes_and_chain_proposal(tmp_path: Path) -> None:
    ledger = completed_ledger(tmp_path / "ledger.sqlite3")
    journal = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
    journal.initialize()
    service = service_at(tmp_path / "objects")
    attempts: list[bytes] = []

    def transport(request: httpx.Request) -> httpx.Response:
        attempts.append(request.content)
        if len(attempts) == 1:
            return httpx.Response(503)
        receipt = service.upload(request.content)
        if len(attempts) == 2:
            raise httpx.ReadTimeout("simulated lost acknowledgement")
        return httpx.Response(200, content=receipt.model_dump_json())

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        sender = ReportSender(journal, GatewayUploader(client, "http://127.0.0.1"))
        with pytest.raises(httpx.HTTPStatusError):
            sender.tick(NOW)
        assert journal.due(NOW + timedelta(seconds=1)) is None
        proposal = ledger.proposal(CATALOG, ROSTER, 2, NOW, timedelta(minutes=10))
        assert proposal is not None
        ledger.prepare_batch(100, "weights-continue-during-outage", proposal, NOW)
        assert ledger.batch(100).weights == {MINERS[0]: 1.0}
        with pytest.raises(httpx.ReadTimeout):
            sender.tick(NOW + timedelta(seconds=2))
        reopened = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
        reopened.initialize()
        ReportSender(reopened, GatewayUploader(client, "http://127.0.0.1")).tick(NOW + timedelta(seconds=6))
        assert reopened.due(NOW + timedelta(days=1)) is None
    assert len(attempts) == 3 and attempts[0] == attempts[1] == attempts[2]
    assert len(list((tmp_path / "objects").rglob("*.json"))) == 1
    with pytest.raises(ValueError, match="identity differs"):
        ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[1]).initialize()


def test_wrong_receipt_does_not_mark_uploaded_and_legacy_rounds_are_not_invented(tmp_path: Path) -> None:
    ledger = completed_ledger(tmp_path / "ledger.sqlite3")
    journal = ReportJournal(ledger.path, GENESIS, 2, VALIDATORS[0])
    journal.initialize()
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE rounds SET completed_block=NULL")
    assert journal.prepare_next(NOW) is None
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE rounds SET completed_block=120")
    signed = journal.prepare_next(NOW)
    assert signed is not None
    with pytest.raises(ValueError, match="receipt"):
        journal.acknowledge(signed, UploadReceipt(report_id=signed.report_id, object_key="wrong", stored_at=NOW))
    assert journal.due(NOW) == signed


def test_collector_omits_corrupted_or_misplaced_reports(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    signed = signed_report()
    service.upload(signed.model_dump_json().encode())
    second = signed_report(VALIDATORS[1], round_id=2)
    service.store.write(second.object_key, signed.model_dump_json().encode())
    third = signed_report(VALIDATORS[2], round_id=3)
    service.store.write(third.object_key, b"broken")
    page = service.read_page(100)
    assert page.reports == (signed,)


def test_hippius_s3_adapter_put_readback_and_paginated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIPPIUS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("HIPPIUS_SECRET_ACCESS_KEY", "test")
    store = HippiusStore.connect("test-scores", StorageCredentials.model_validate({}))
    client = store.client
    assert client.meta.region_name == "decentralized"
    assert client.meta.endpoint_url == "https://s3.hippius.com"
    presigned = urlsplit(
        client.generate_presigned_url(
            "get_object",
            Params={"Bucket": "test-scores", "Key": "scores/test.json"},
            ExpiresIn=60,
        )
    )
    assert presigned.netloc == "s3.hippius.com" and presigned.path == "/test-scores/scores/test.json"
    assert parse_qs(presigned.query)["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert "/decentralized/s3/aws4_request" in parse_qs(presigned.query)["X-Amz-Credential"][0]
    signed = signed_report()
    data = signed.model_dump_json().encode()
    params = {"Bucket": "test-scores", "Key": signed.object_key}
    with Stubber(client) as stub:
        stub.add_client_error(
            "get_object", service_error_code="NoSuchKey", http_status_code=404, expected_params=params
        )
        stub.add_response("put_object", {}, {**params, "Body": data, "ContentType": "application/json"})
        stub.add_response("get_object", {"Body": StreamingBody(BytesIO(data), len(data))}, params)
        service = ScoreService(store, GENESIS, 2, frozenset([VALIDATORS[0].ss58_address]))
        receipt = service.upload(data)
        assert receipt.report_id == signed.report_id
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": signed.object_key}], "NextContinuationToken": "next"},
            {"Bucket": "test-scores", "Prefix": "scores/", "MaxKeys": 1, "ContinuationToken": "previous"},
        )
        assert store.list_page("scores/", "previous", 1).next_token == "next"
        stub.assert_no_pending_responses()


def test_reader_requires_valid_validator_hotkeys() -> None:
    with pytest.raises(ValidationError, match="hotkey"):
        ReaderSettings(chain_genesis=GENESIS, netuid=2, validators=frozenset(["invalid"]), bucket="test-scores")
