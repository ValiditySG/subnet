"""Durable, equal-case RN rounds and auditable local weight proposals."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from pydantic import AwareDatetime, TypeAdapter

from validator.credentials.fixtures import ExpectedCase, FixtureCatalog, rejection_reason
from validator.credentials.ledger import CredentialLedger
from validator.credentials.protocol import CredentialRequest, CredentialResponse, WireModel

POLICY = "rn-equal-cases-v1"
ROSTER = TypeAdapter(dict[str, int])
SCORES = TypeAdapter(dict[str, float])


class Assignment(WireModel):
    """A committed request and its destination."""

    hotkey: str
    request: CredentialRequest


class WeightProposal(WireModel):
    """A reproducible completed round, never a transport acknowledgement."""

    round_id: int
    roster: dict[str, int]
    scores: dict[str, float]
    weights: dict[str, float]
    source_digest: str
    completed_at: AwareDatetime


def score_response(
    request: CredentialRequest,
    response: CredentialResponse | None,
    expected: ExpectedCase,
    catalog: FixtureCatalog,
    completed: datetime,
    outcome: str,
) -> float:
    """Reward supported truth, penalize unsafe assertions, and count failed work."""
    if response is None or outcome in ("expired", "transport_error") or completed > request.deadline_at:
        return 0.0
    version = catalog.label_version.replace("labels", "board")
    reason = rejection_reason(request, response, expected, catalog.snapshot_sha256, completed, version)
    check = response.checks[0]
    if reason is None:
        return 1.0
    if reason in ("task_mismatch", "provider_mismatch"):
        return -5.0
    if check.finding == "active" and (expected.finding != "active" or expected.identity_resolution != "matched"):
        return -5.0
    if check.evidence is not None and check.evidence.snapshot_sha256 != catalog.snapshot_sha256:
        # Explicitly old source versions earn nothing; invented current evidence is a severe failure.
        older = check.evidence.source_version == "fixture-board-v1" and version == "fixture-board-v2"
        return 0.0 if older else -5.0
    if reason in ("evidence_records_mismatch", "evidence_version_mismatch"):
        return -5.0
    if check.availability == "unavailable":
        return 0.0
    return -1.0


class EvaluationLedger(CredentialLedger):
    """Additive tables keep legacy conformance records outside reward windows."""

    def initialize_evaluation(self) -> None:
        """Initialize the ledger; existing v1 assignments remain intact."""
        self.initialize()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rounds (
                    id INTEGER PRIMARY KEY,
                    policy TEXT NOT NULL,
                    netuid INTEGER NOT NULL,
                    catalog_json TEXT NOT NULL,
                    roster_json TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    closed_at TEXT,
                    scores_json TEXT
                );
                CREATE TABLE IF NOT EXISTS round_slots (
                    round_id INTEGER NOT NULL REFERENCES rounds(id),
                    hotkey TEXT NOT NULL,
                    case_index INTEGER NOT NULL,
                    task_id TEXT REFERENCES assignments(task_id),
                    PRIMARY KEY (round_id, hotkey, case_index)
                );
                CREATE TABLE IF NOT EXISTS weight_batches (
                    epoch INTEGER PRIMARY KEY,
                    context_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL REFERENCES rounds(id),
                    proposal_json TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    queued_at TEXT,
                    confirmed_block INTEGER,
                    chain_weights_json TEXT
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(weight_batches)")}
            if "prepared_block" not in columns:
                db.execute("ALTER TABLE weight_batches ADD COLUMN prepared_block INTEGER NOT NULL DEFAULT 0")
            round_columns = {row[1] for row in db.execute("PRAGMA table_info(rounds)")}
            for column in ("completed_block", "epoch_start", "epoch_end"):
                if column not in round_columns:
                    db.execute(f"ALTER TABLE rounds ADD COLUMN {column} INTEGER")

    def next_assignment(
        self,
        catalog: FixtureCatalog,
        roster: dict[str, int],
        netuid: int,
        now: datetime,
        timeout: timedelta,
        max_in_flight: int,
        *,
        completed_block: int | None = None,
        epoch_start: int | None = None,
        epoch_end: int | None = None,
    ) -> Assignment | None:
        """Reserve one slot atomically; retry interrupted work with a fresh task ID."""
        if not roster:
            return None
        self.expire(now)
        catalog_json = catalog.model_dump_json()
        roster_json = json.dumps(roster, sort_keys=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE rounds SET status='void', closed_at=? WHERE status='open' "
                "AND (catalog_json<>? OR roster_json<>? OR netuid<>? OR policy<>?)",
                (now.isoformat(), catalog_json, roster_json, netuid, POLICY),
            )
            row = db.execute("SELECT id FROM rounds WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                cursor = db.execute(
                    "INSERT INTO rounds(policy,netuid,catalog_json,roster_json,opened_at) VALUES (?,?,?,?,?)",
                    (POLICY, netuid, catalog_json, roster_json, now.isoformat()),
                )
                round_id = cast(int, cursor.lastrowid)
                db.executemany(
                    "INSERT INTO round_slots(round_id,hotkey,case_index) VALUES (?,?,?)",
                    [(round_id, hotkey, i) for i in range(len(catalog.cases)) for hotkey in sorted(roster)],
                )
            else:
                round_id = int(row[0])
            slots = db.execute(
                "SELECT s.hotkey,s.case_index,a.task_id,a.outcome,a.request_json,a.response_json,a.completed_at "
                "FROM round_slots s LEFT JOIN assignments a ON a.task_id=s.task_id "
                "WHERE s.round_id=? ORDER BY s.case_index,s.hotkey",
                (round_id,),
            ).fetchall()
            if all(row[2] is not None and row[3] not in (None, "interrupted") for row in slots):
                scores = dict.fromkeys(roster, 0.0)
                for hotkey, case_index, _, outcome, request_json, response_json, completed_at in slots:
                    score = score_response(
                        CredentialRequest.model_validate_json(request_json),
                        CredentialResponse.model_validate_json(response_json) if response_json else None,
                        catalog.cases[int(case_index)],
                        catalog,
                        datetime.fromisoformat(completed_at),
                        outcome,
                    )
                    scores[hotkey] += score / len(catalog.cases)
                db.execute(
                    "UPDATE rounds SET status='complete',closed_at=?,scores_json=?,"
                    "completed_block=?,epoch_start=?,epoch_end=? WHERE id=?",
                    (
                        now.isoformat(),
                        json.dumps(scores, sort_keys=True),
                        completed_block,
                        epoch_start,
                        epoch_end,
                        round_id,
                    ),
                )
                return None
            pending = db.execute("SELECT count(*) FROM assignments WHERE outcome IS NULL").fetchone()[0]
            if pending >= max_in_flight:
                return None
            for hotkey, case_index, task_id, outcome, *_ in slots:
                if task_id is not None and outcome != "interrupted":
                    continue
                expected = catalog.cases[int(case_index)]
                request = CredentialRequest(
                    task_id=uuid4(),
                    provider_query=expected.provider_query,
                    checks=("fixture_license",),
                    issued_at=now,
                    deadline_at=now + timeout,
                )
                db.execute(
                    "INSERT INTO assignments(task_id,netuid,miner_hotkey,request_json,expected_json,"
                    "label_version,snapshot_sha256,deadline_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(request.task_id),
                        netuid,
                        hotkey,
                        request.model_dump_json(),
                        expected.model_dump_json(),
                        catalog.label_version,
                        catalog.snapshot_sha256,
                        request.deadline_at.isoformat(),
                    ),
                )
                db.execute(
                    "UPDATE round_slots SET task_id=? WHERE round_id=? AND hotkey=? AND case_index=?",
                    (str(request.task_id), round_id, hotkey, case_index),
                )
                return Assignment(hotkey=hotkey, request=request)
        return None

    def truth(self, request: CredentialRequest) -> tuple[ExpectedCase, str, str]:
        """Read the assignment's pinned truth, even after a source transition.

        Raises:
            ValueError: If the assignment is unknown.
        """
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT expected_json,snapshot_sha256,label_version FROM assignments WHERE task_id=?",
                (str(request.task_id),),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown assignment")
        return ExpectedCase.model_validate_json(row[0]), str(row[1]), str(row[2]).replace("labels", "board")

    def proposal(
        self,
        catalog: FixtureCatalog,
        roster: dict[str, int],
        netuid: int,
        now: datetime,
        max_age: timedelta,
    ) -> WeightProposal | None:
        """Use only the latest complete, current, fully covered round; never stale fallback."""
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT id,roster_json,scores_json,catalog_json,closed_at,netuid,policy FROM rounds "
                "WHERE status='complete' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        rid, roster_json, scores_json, catalog_json, closed_at, saved_netuid, policy = row
        if (
            catalog_json != catalog.model_dump_json()
            or ROSTER.validate_json(roster_json) != roster
            or saved_netuid != netuid
            or policy != POLICY
            or now - datetime.fromisoformat(closed_at) > max_age
        ):
            return None
        scores = SCORES.validate_json(scores_json)
        positive = {hotkey: max(0.0, score) for hotkey, score in scores.items() if score > 0}
        total = sum(positive.values())
        if total <= 0:
            return None
        return WeightProposal(
            round_id=rid,
            roster=roster,
            scores=scores,
            source_digest=catalog.snapshot_sha256,
            completed_at=datetime.fromisoformat(closed_at),
            weights={hotkey: score / total for hotkey, score in positive.items()},
        )

    def prepare_batch(
        self,
        epoch: int,
        context_id: str,
        proposal: WeightProposal,
        now: datetime,
        prepared_block: int = 0,
    ) -> None:
        """Pin an epoch proposal before handing it to the transport; retries keep the same weights."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO weight_batches(epoch,context_id,round_id,proposal_json,prepared_at,prepared_block) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(epoch) DO UPDATE SET context_id=excluded.context_id",
                (epoch, context_id, proposal.round_id, proposal.model_dump_json(), now.isoformat(), prepared_block),
            )

    def batch(self, epoch: int) -> WeightProposal:
        """Load exactly the proposal pinned for an epoch.

        Raises:
            ValueError: If no proposal exists for this epoch.
        """
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT proposal_json FROM weight_batches WHERE epoch=?", (epoch,)).fetchone()
        if row is None:
            raise ValueError("No prepared weight batch")
        return WeightProposal.model_validate_json(row[0])

    def mark_queued(self, context_id: str, now: datetime) -> None:
        """A sidecar acknowledgement records queueing only; chain readback is separate."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE weight_batches SET queued_at=? WHERE context_id=?", (now.isoformat(), context_id))
