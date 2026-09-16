"""Reward, allocation, source-transition and restart regressions for the RN loop."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from nexus.v1 import ExecutorFailureException, NexusException, RemoteResponseTimeoutException

from validator.credentials.audit import replay
from validator.credentials.evaluation import EvaluationLedger, score_response
from validator.credentials.fixtures import FixtureCatalog
from validator.credentials.pipeline import failure_outcome
from validator.credentials.protocol import CheckResult, CredentialRequest, CredentialResponse, Evidence

FIXTURES = Path(__file__).parents[1] / "src/validator/synthetic"
CATALOG = FixtureCatalog.model_validate_json((FIXTURES / "expected-v1.json").read_bytes())
NEXT = FixtureCatalog.model_validate_json((FIXTURES / "expected-v2.json").read_bytes())
NOW = datetime(2026, 9, 15, tzinfo=UTC)
ROSTER = {"honest": 2, "timeout": 3}


def response_for(request: CredentialRequest, catalog: FixtureCatalog = CATALOG) -> CredentialResponse:
    expected = next(case for case in catalog.cases if case.provider_query == request.provider_query)
    return CredentialResponse(
        task_id=request.task_id,
        provider_ref=request.provider_query.provider_ref,
        identity_resolution=expected.identity_resolution,
        checks=(
            CheckResult(
                availability=expected.availability,
                finding=expected.finding,
                evidence=Evidence(
                    source_version="fixture-board-v1" if catalog == CATALOG else "fixture-board-v2",
                    snapshot_sha256=catalog.snapshot_sha256,
                    record_ids=expected.record_ids,
                )
                if expected.availability == "available"
                else None,
            ),
        ),
    )


def ledger_at(tmp_path: Path) -> EvaluationLedger:
    ledger = EvaluationLedger(tmp_path / "ledger.sqlite3")
    ledger.initialize_evaluation()
    return ledger


def complete_round(
    ledger: EvaluationLedger, catalog: FixtureCatalog = CATALOG, roster: dict[str, int] = ROSTER, now: datetime = NOW
) -> None:
    for _ in range(len(catalog.cases) * len(roster)):
        assignment = ledger.next_assignment(catalog, roster, 2, now, timedelta(seconds=10), 4)
        assert assignment is not None
        response = response_for(assignment.request, catalog) if assignment.hotkey == "honest" else None
        ledger.finish(assignment.request.task_id, "verified" if response else "transport_error", None, response, now)
    assert ledger.next_assignment(catalog, roster, 2, now, timedelta(seconds=10), 4) is None


def test_equal_coverage_counts_all_failed_assignments(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    complete_round(ledger)
    proposal = ledger.proposal(CATALOG, ROSTER, 2, NOW, timedelta(minutes=10))
    assert proposal is not None
    assert proposal.scores == {"honest": 1.0, "timeout": 0.0}
    assert proposal.weights == {"honest": 1.0}
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT count(*) FROM round_slots").fetchone()[0] == 10


def test_restart_retries_only_interrupted_slot_and_rejects_late_result(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    first = ledger.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=10), 4)
    assert first is not None
    assert ledger.recover(NOW) == 1
    reopened = EvaluationLedger(ledger.path)
    reopened.initialize_evaluation()
    retry = reopened.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=10), 4)
    assert retry is not None
    assert retry.hotkey == first.hotkey
    assert retry.request.provider_query == first.request.provider_query
    assert retry.request.task_id != first.request.task_id
    assert not reopened.finish(first.request.task_id, "verified", None, response_for(first.request), NOW)
    assert reopened.finish(retry.request.task_id, "verified", None, response_for(retry.request), NOW)
    assert not reopened.finish(retry.request.task_id, "verified", None, response_for(retry.request), NOW)


def test_expiry_keeps_denominator_and_caps_inflight(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    first = ledger.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=1), 1)
    assert first is not None
    assert ledger.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=1), 1) is None
    second = ledger.next_assignment(CATALOG, ROSTER, 2, NOW + timedelta(seconds=2), timedelta(seconds=1), 1)
    assert second is not None and second.hotkey != first.hotkey
    assert not ledger.finish(first.request.task_id, "verified", None, response_for(first.request), NOW)


def test_source_transition_voids_open_round_and_preserves_pinned_truth(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    first = ledger.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=10), 4)
    assert first is not None
    second = ledger.next_assignment(NEXT, ROSTER, 2, NOW, timedelta(seconds=10), 4)
    assert second is not None
    assert ledger.truth(first.request)[0].finding == "active"
    assert ledger.truth(second.request)[0].finding == "suspended"
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT status FROM rounds ORDER BY id").fetchall() == [("void",), ("open",)]


def test_no_stale_source_roster_uid_or_time_fallback(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    complete_round(ledger)
    age = timedelta(minutes=10)
    assert ledger.proposal(NEXT, ROSTER, 2, NOW, age) is None
    assert ledger.proposal(CATALOG, {"replacement": 2, "timeout": 3}, 2, NOW, age) is None
    assert ledger.proposal(CATALOG, {"honest": 4, "timeout": 3}, 2, NOW, age) is None
    assert ledger.proposal(CATALOG, ROSTER, 3, NOW, age) is None
    assert ledger.proposal(CATALOG, ROSTER, 2, NOW + age + timedelta(seconds=1), age) is None


def test_no_positive_scores_do_not_reuse_prior_positive_round(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    complete_round(ledger)
    for _ in range(10):
        assignment = ledger.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=10), 4)
        assert assignment is not None
        ledger.finish(assignment.request.task_id, "transport_error", "timeout", None, NOW)
    assert ledger.next_assignment(CATALOG, ROSTER, 2, NOW, timedelta(seconds=10), 4) is None
    assert ledger.proposal(CATALOG, ROSTER, 2, NOW, timedelta(minutes=10)) is None


def test_batch_retry_and_restart_preserve_exact_proposal(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    complete_round(ledger)
    proposal = ledger.proposal(CATALOG, ROSTER, 2, NOW, timedelta(minutes=10))
    assert proposal is not None
    ledger.prepare_batch(360, "first-context", proposal, NOW)
    changed = proposal.model_copy(update={"round_id": 999, "weights": {"timeout": 1.0}})
    ledger.prepare_batch(360, "retry-context", changed, NOW)
    assert EvaluationLedger(ledger.path).batch(360) == proposal
    ledger.mark_queued("retry-context", NOW)
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT queued_at,confirmed_block FROM weight_batches").fetchone() == (NOW.isoformat(), None)


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("catalog", [CATALOG, NEXT])
def test_all_supported_outcomes_have_equal_reward(index: int, catalog: FixtureCatalog) -> None:
    request = CredentialRequest(
        task_id=uuid4(),
        provider_query=catalog.cases[index].provider_query,
        checks=("fixture_license",),
        issued_at=NOW,
        deadline_at=NOW + timedelta(seconds=10),
    )
    assert score_response(request, response_for(request, catalog), catalog.cases[index], catalog, NOW, "verified") == 1
    assert score_response(request, None, catalog.cases[index], catalog, NOW, "expired") == 0


def test_unsafe_clean_and_fabricated_current_evidence_are_severe_failures() -> None:
    request = CredentialRequest(
        task_id=uuid4(),
        provider_query=CATALOG.cases[1].provider_query,
        checks=("fixture_license",),
        issued_at=NOW,
        deadline_at=NOW + timedelta(seconds=10),
    )
    correct = response_for(request)
    unsafe = correct.model_copy(update={"checks": (correct.checks[0].model_copy(update={"finding": "active"}),)})
    assert score_response(request, unsafe, CATALOG.cases[1], CATALOG, NOW, "rejected") == -5
    evidence = correct.checks[0].evidence
    assert evidence is not None
    fabricated = correct.model_copy(
        update={
            "checks": (
                correct.checks[0].model_copy(
                    update={"evidence": evidence.model_copy(update={"snapshot_sha256": "0" * 64})}
                ),
            )
        }
    )
    assert score_response(request, fabricated, CATALOG.cases[1], CATALOG, NOW, "rejected") == -5


def test_stale_source_scores_zero_but_stale_unsafe_clean_still_penalized() -> None:
    for index, score in [(0, -5), (1, 0), (2, 0), (3, 0), (4, 0)]:
        request = CredentialRequest(
            task_id=uuid4(),
            provider_query=NEXT.cases[index].provider_query,
            checks=("fixture_license",),
            issued_at=NOW,
            deadline_at=NOW + timedelta(seconds=10),
        )
        assert score_response(request, response_for(request), NEXT.cases[index], NEXT, NOW, "rejected") == score


def test_remote_failure_is_scored_but_validator_failure_is_retried() -> None:
    remote = ExecutorFailureException(RemoteResponseTimeoutException("timeout"))
    internal = ExecutorFailureException(NexusException("validator failure"))
    assert failure_outcome(remote) == ("transport_error", "RemoteResponseTimeoutException")
    assert failure_outcome(internal) == ("interrupted", "NexusException")


def test_audit_replays_observations_and_detects_changed_summary(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    complete_round(ledger)
    assert replay(ledger.path, 1) == {"honest": 1.0, "timeout": 0.0}
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE rounds SET scores_json=? WHERE id=1", ('{"honest":0.5,"timeout":0.5}',))
    with pytest.raises(ValueError, match="persisted summary"):
        replay(ledger.path, 1)
