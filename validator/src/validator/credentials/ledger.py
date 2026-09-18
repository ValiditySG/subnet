"""Transactional local conformance ledger. This does not calculate miner rewards."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from validator.credentials.fixtures import ExpectedCase
from validator.credentials.protocol import CredentialRequest, CredentialResponse

type Outcome = Literal["verified", "rejected", "transport_error", "expired", "interrupted"]


class CredentialLedger:
    """One database per validator; short transactions use independent connections."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        """Create the v1 schema.

        Raises:
            ValueError: If the database has an unsupported schema version.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported credential ledger version: {version}")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS assignments (
                    task_id TEXT PRIMARY KEY,
                    netuid INTEGER NOT NULL,
                    miner_hotkey TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    expected_json TEXT NOT NULL,
                    label_version TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    deadline_at TEXT NOT NULL,
                    outcome TEXT,
                    reason TEXT,
                    response_json TEXT,
                    completed_at TEXT
                )
            """)
            db.execute("PRAGMA user_version=1")

    def recover(self, now: datetime) -> int:
        """Close prior in-flight work explicitly; no automatic resend or reward."""
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute(
                "UPDATE assignments SET outcome='interrupted', reason='validator_restart', completed_at=? "
                "WHERE outcome IS NULL",
                (now.isoformat(),),
            )
            return cursor.rowcount

    def assign(
        self,
        request: CredentialRequest,
        expected: ExpectedCase,
        snapshot_sha256: str,
        label_version: str,
        netuid: int,
        miner_hotkey: str,
    ) -> None:
        """Commit the selected request and its truth before sending any HTTP work."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO assignments (task_id, netuid, miner_hotkey, request_json, expected_json, "
                "label_version, snapshot_sha256, deadline_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(request.task_id),
                    netuid,
                    miner_hotkey,
                    request.model_dump_json(),
                    expected.model_dump_json(),
                    label_version,
                    snapshot_sha256,
                    request.deadline_at.isoformat(),
                ),
            )

    def expire(self, now: datetime) -> int:
        """Reconcile overdue assignments, including failures elsewhere in the graph."""
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute(
                "UPDATE assignments SET outcome='expired', reason='deadline_exceeded', completed_at=? "
                "WHERE outcome IS NULL AND deadline_at < ?",
                (now.isoformat(), now.isoformat()),
            )
            return cursor.rowcount

    def finish(
        self,
        task_id: UUID,
        outcome: Outcome,
        reason: str | None,
        response: CredentialResponse | None,
        now: datetime,
    ) -> bool:
        """Accept one terminal outcome only, even after duplicates or a restart."""
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute(
                "UPDATE assignments SET outcome=?, reason=?, response_json=?, completed_at=? "
                "WHERE task_id=? AND outcome IS NULL",
                (outcome, reason, response.model_dump_json() if response else None, now.isoformat(), str(task_id)),
            )
            return cursor.rowcount == 1
