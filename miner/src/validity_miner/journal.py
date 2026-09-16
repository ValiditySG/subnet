"""Transactional replay protection, per-validator quotas and byte-identical responses."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from validity_protocol.exchange import SignedRequest, SignedResponse


class ReplayConflict(ValueError):
    """The same task ID was used for a different signed assignment."""


class RateLimited(ValueError):
    """The authenticated validator has exhausted its current request allowance."""


class RequestJournal:
    """Private local response state, bound to one chain/subnet/miner identity."""

    def __init__(self, path: Path, chain: str, netuid: int, hotkey: str, per_minute: int = 120) -> None:
        self.path = path
        self.identity = chain, netuid, hotkey
        self.per_minute = per_minute

    def initialize(self) -> None:
        """Initialize durable state before serving requests.

        Raises:
            ValueError: If an operator tries to reuse another miner's state.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS identity (
                    id INTEGER PRIMARY KEY CHECK(id=1), chain TEXT, netuid INTEGER, hotkey TEXT
                );
                CREATE TABLE IF NOT EXISTS responses (
                    validator TEXT, task_id TEXT, request_hash TEXT NOT NULL, response BLOB NOT NULL,
                    created_at TEXT NOT NULL, deadline TEXT NOT NULL,
                    PRIMARY KEY(validator, task_id)
                );
                CREATE INDEX IF NOT EXISTS response_quota ON responses(validator, created_at);
            """)
            db.execute("INSERT OR IGNORE INTO identity VALUES (1,?,?,?)", self.identity)
            if db.execute("SELECT chain,netuid,hotkey FROM identity").fetchone() != self.identity:
                raise ValueError("Miner journal belongs to a different identity")

    def respond(self, signed: SignedRequest, now: datetime, create: Callable[[], SignedResponse]) -> bytes:
        """Commit one response before returning; retries preserve its exact signature bytes.

        Raises:
            ReplayConflict: If a task ID was changed after the first response.
            RateLimited: If the validator exceeds its request budget.
        """
        task = signed.task
        lookup = task.validator_hotkey, str(task.request.task_id)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_hash,response FROM responses WHERE validator=? AND task_id=?", lookup
            ).fetchone()
            if row:
                if row[0] != signed.request_hash:
                    raise ReplayConflict("Task ID already used for a different assignment")
                return bytes(row[1])
            db.execute("DELETE FROM responses WHERE deadline<?", ((now - timedelta(days=1)).isoformat(),))
            count = db.execute(
                "SELECT count(*) FROM responses WHERE validator=? AND created_at>=?",
                (task.validator_hotkey, (now - timedelta(minutes=1)).isoformat()),
            ).fetchone()[0]
            if count >= self.per_minute:
                raise RateLimited("Validator request limit reached")
            data = create().model_dump_json().encode()
            db.execute(
                "INSERT INTO responses VALUES (?,?,?,?,?,?)",
                (*lookup, signed.request_hash, data, now.isoformat(), task.request.deadline_at.isoformat()),
            )
            return data
