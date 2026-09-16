"""Contract and durable lifecycle checks for the first local credential slice."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from validator.credentials.fixtures import FixtureCatalog, rejection_reason
from validator.credentials.ledger import CredentialLedger
from validator.credentials.protocol import CheckResult, CredentialRequest, CredentialResponse, Evidence

FIXTURES = Path(__file__).parents[2] / "localnet/fixtures"
CATALOG = FixtureCatalog.model_validate_json((FIXTURES / "expected-v1.json").read_bytes())
NOW = datetime(2026, 9, 15, tzinfo=UTC)


def make_request(case_index: int = 0) -> CredentialRequest:
    return CredentialRequest(
        task_id=uuid4(),
        provider_query=CATALOG.cases[case_index].provider_query,
        checks=("fixture_license",),
        issued_at=NOW,
        deadline_at=NOW + timedelta(seconds=10),
    )


def make_response(request: CredentialRequest, case_index: int = 0) -> CredentialResponse:
    expected = CATALOG.cases[case_index]
    return CredentialResponse(
        task_id=request.task_id,
        provider_ref=request.provider_query.provider_ref,
        identity_resolution=expected.identity_resolution,
        checks=(
            CheckResult(
                availability=expected.availability,
                finding=expected.finding,
                evidence=Evidence(snapshot_sha256=CATALOG.snapshot_sha256, record_ids=expected.record_ids)
                if expected.availability == "available"
                else None,
            ),
        ),
    )


@pytest.mark.parametrize("case_index", range(5))
def test_five_case_contract_round_trips(case_index: int) -> None:
    request = make_request(case_index)
    response = make_response(request, case_index)
    parsed = CredentialResponse.model_validate_json(response.model_dump_json())
    assert rejection_reason(request, parsed, CATALOG.cases[case_index], CATALOG.snapshot_sha256, NOW) is None
    wire = json.loads(request.model_dump_json())
    assert set(wire) == {"protocol_version", "task_id", "provider_query", "checks", "issued_at", "deadline_at"}


def test_reviewed_labels_pin_source_bytes() -> None:
    assert sha256((FIXTURES / "board-v1.json").read_bytes()).hexdigest() == CATALOG.snapshot_sha256


def test_mvp_contract_requires_rn_profession() -> None:
    query = make_request().provider_query.model_dump()
    query["profession"] = "NP"
    data = make_request().model_dump()
    data["provider_query"] = query
    with pytest.raises(ValidationError):
        CredentialRequest.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"protocol_version": "validity.v99"},
        {"checks": []},
        {"checks": [dict[str, object](), dict[str, object]()]},
        {"extra_field": "not supported"},
        {"identity_resolution": "ambiguous"},
    ],
)
def test_bad_response_contracts_are_rejected(change: dict[str, object]) -> None:
    data = make_response(make_request()).model_dump(mode="json")
    data.update(change)
    with pytest.raises(ValidationError):
        CredentialResponse.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"checks": []},
        {"checks": ["fixture_license", "fixture_license"]},
        {"checks": ["real_medical_board"]},
        {"issued_at": "2026-09-15T00:00:00"},
        {"deadline_at": "2026-09-14T00:00:00Z"},
        {"ssn": "not-an-allowed-field"},
    ],
)
def test_request_contract_limits(change: dict[str, object]) -> None:
    data = make_request().model_dump(mode="json")
    data.update(change)
    with pytest.raises(ValidationError):
        CredentialRequest.model_validate(data)


def test_outage_cannot_be_an_active_license_or_no_match() -> None:
    with pytest.raises(ValidationError):
        CheckResult(availability="unavailable", finding="active", evidence=None)
    response = make_response(make_request(3), 3).model_dump(mode="json")
    response["identity_resolution"] = "not_found"
    with pytest.raises(ValidationError):
        CredentialResponse.model_validate(response)


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("task_id", str(uuid4()), "task_mismatch"),
        ("provider_ref", "fictional-9999", "provider_mismatch"),
    ],
)
def test_response_is_bound_to_assignment(field: str, value: str, reason: str) -> None:
    request = make_request()
    data = make_response(request).model_dump(mode="json")
    data[field] = value
    response = CredentialResponse.model_validate(data)
    assert rejection_reason(request, response, CATALOG.cases[0], CATALOG.snapshot_sha256, NOW) == reason


def test_deadline_is_checked_even_if_transport_accepts_callback() -> None:
    request = make_request()
    assert (
        rejection_reason(
            request,
            make_response(request),
            CATALOG.cases[0],
            CATALOG.snapshot_sha256,
            request.deadline_at + timedelta(microseconds=1),
        )
        == "deadline_exceeded"
    )


def test_wrong_finding_and_fabricated_evidence_are_rejected() -> None:
    request = make_request(1)
    response = make_response(request, 1)
    check = response.checks[0]
    wrong_finding = response.model_copy(update={"checks": (check.model_copy(update={"finding": "active"}),)})
    assert (
        rejection_reason(
            request,
            wrong_finding,
            CATALOG.cases[1],
            CATALOG.snapshot_sha256,
            NOW,
        )
        == "finding_mismatch"
    )
    wrong_evidence = Evidence(snapshot_sha256="0" * 64, record_ids=("fixture-record-2",))
    forged = response.model_copy(update={"checks": (check.model_copy(update={"evidence": wrong_evidence}),)})
    assert (
        rejection_reason(request, forged, CATALOG.cases[1], CATALOG.snapshot_sha256, NOW) == "evidence_digest_mismatch"
    )


def assign(ledger: CredentialLedger, request: CredentialRequest) -> None:
    ledger.assign(request, CATALOG.cases[0], CATALOG.snapshot_sha256, CATALOG.label_version, 2, "local-test-hotkey")


def test_assignment_persists_before_response_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    ledger = CredentialLedger(path)
    ledger.initialize()
    request = make_request()
    assign(ledger, request)
    with closing(sqlite3.connect(path)) as db:
        row = db.execute("SELECT request_json, expected_json, outcome FROM assignments").fetchone()
    assert row is not None
    assert CredentialRequest.model_validate_json(row[0]) == request
    assert row[2] is None
    reopened = CredentialLedger(path)
    reopened.initialize()
    assert reopened.finish(request.task_id, "verified", None, make_response(request), NOW)
    assert reopened.recover(NOW) == 0


def test_restart_closes_inflight_and_duplicate_cannot_replace_it(tmp_path: Path) -> None:
    ledger = CredentialLedger(tmp_path / "ledger.sqlite3")
    ledger.initialize()
    request = make_request()
    assign(ledger, request)
    reopened = CredentialLedger(ledger.path)
    assert reopened.recover(NOW) == 1
    assert reopened.recover(NOW) == 0
    assert not reopened.finish(request.task_id, "verified", None, make_response(request), NOW)


def test_expired_and_completed_work_stay_terminal_under_concurrency(tmp_path: Path) -> None:
    ledger = CredentialLedger(tmp_path / "ledger.sqlite3")
    ledger.initialize()
    request = make_request()
    assign(ledger, request)

    def finish_once(_: int) -> bool:
        return ledger.finish(request.task_id, "verified", None, make_response(request), NOW)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(finish_once, range(8))) == 1
    assert ledger.expire(NOW + timedelta(seconds=20)) == 0
    second = make_request()
    assign(ledger, second)
    assert ledger.expire(NOW + timedelta(seconds=20)) == 1
    assert not ledger.finish(second.task_id, "verified", None, make_response(second), NOW)


def test_unsupported_database_schema_is_not_modified(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        CredentialLedger(path).initialize()
