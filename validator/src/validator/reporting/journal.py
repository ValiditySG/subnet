"""Immutable signed reports and a durable, independently retried upload outbox."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from bittensor.sp_core import Keypair

from validator.credentials.audit import replay
from validator.credentials.evaluation import POLICY, ROSTER, score_response
from validator.credentials.fixtures import FixtureCatalog
from validator.credentials.protocol import CredentialRequest, CredentialResponse
from validator.reporting.protocol import OBJECT_LAYOUT, MinerScore, ScoreReport, SignedScoreReport, UploadReceipt


class ReportJournal:
    """Keep byte-identical retries across restarts, bound to one validator and chain."""

    def __init__(
        self,
        path: Path,
        chain_genesis: str,
        netuid: int,
        key: Keypair,
        destination: str = f"unconfigured:#{OBJECT_LAYOUT}",
    ) -> None:
        self.path = path
        self.chain_genesis = chain_genesis
        self.netuid = netuid
        self.key = key
        self.destination = destination

    def initialize(self) -> None:
        """Create additive reporting tables and reject reuse under another identity.

        Raises:
            ValueError: If the ledger belongs to a different chain, subnet or hotkey.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS report_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    instance_id TEXT NOT NULL, chain_genesis TEXT NOT NULL,
                    netuid INTEGER NOT NULL, hotkey TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_outbox (
                    round_id INTEGER PRIMARY KEY REFERENCES rounds(id),
                    report_id TEXT NOT NULL UNIQUE, envelope_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at TEXT NOT NULL, uploaded_at TEXT, last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS report_deliveries (
                    report_id TEXT NOT NULL REFERENCES report_outbox(report_id),
                    destination TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at TEXT NOT NULL,
                    uploaded_at TEXT, last_error TEXT, verification TEXT, superseded_by TEXT,
                    PRIMARY KEY(report_id,destination)
                );
            """)
            if "superseded_by" not in {row[1] for row in db.execute("PRAGMA table_info(report_deliveries)")}:
                db.execute("ALTER TABLE report_deliveries ADD COLUMN superseded_by TEXT")
            db.execute(
                "INSERT OR IGNORE INTO report_identity VALUES (1,?,?,?,?)",
                (str(uuid4()), self.chain_genesis, self.netuid, self.key.ss58_address),
            )
            row = db.execute("SELECT chain_genesis,netuid,hotkey FROM report_identity").fetchone()
            if row != (self.chain_genesis, self.netuid, self.key.ss58_address):
                raise ValueError("Reporting ledger identity differs; use a separate ledger for each validator/chain")
            # Retain original envelopes; an acknowledgement for a test gateway never satisfies Hippius delivery.
            db.execute(
                "INSERT OR IGNORE INTO report_deliveries(report_id,destination,retry_at) "
                "SELECT report_id,?,retry_at FROM report_outbox",
                (self.destination,),
            )
            self._supersede_history(db)

    def _supersede_history(self, db: sqlite3.Connection) -> None:
        db.execute(
            "WITH snapshots AS (SELECT o.report_id, first_value(o.report_id) OVER "
            "(PARTITION BY r.epoch_start ORDER BY o.round_id DESC) AS latest "
            "FROM report_outbox o JOIN rounds r ON r.id=o.round_id) "
            "UPDATE report_deliveries SET superseded_by=(SELECT latest FROM snapshots "
            "WHERE snapshots.report_id=report_deliveries.report_id AND latest<>snapshots.report_id) "
            "WHERE destination=? AND superseded_by IS NULL",
            (self.destination,),
        )

    def prepare_next(self, now: datetime) -> SignedScoreReport | None:
        """Audit and sign one newly completed round before any network operation.

        Legacy rounds without a recorded completion block are never backfilled.

        Raises:
            ValueError: If the round belongs to another subnet or policy.
        """
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT r.id,r.catalog_json,r.roster_json,r.opened_at,r.closed_at,"
                "r.completed_block,r.epoch_start,r.epoch_end,r.netuid,r.policy "
                "FROM rounds r LEFT JOIN report_outbox o ON r.id=o.round_id "
                "WHERE r.status='complete' AND r.completed_block IS NOT NULL AND o.round_id IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM rounds newer WHERE newer.status='complete' "
                "AND newer.epoch_start=r.epoch_start AND newer.id>r.id) "
                "ORDER BY r.id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            instance_id = UUID(db.execute("SELECT instance_id FROM report_identity").fetchone()[0])
            slots = db.execute(
                "SELECT s.hotkey,s.case_index,a.request_json,a.response_json,a.completed_at,a.outcome "
                "FROM round_slots s JOIN assignments a ON a.task_id=s.task_id "
                "WHERE s.round_id=? ORDER BY s.case_index,s.hotkey",
                (row[0],),
            ).fetchall()
        rid, catalog_json, roster_json, opened, closed, block, first, last, netuid, policy = row
        if netuid != self.netuid or policy != POLICY:
            raise ValueError("Completed round has a different subnet or scoring policy")
        replay(self.path, int(rid))
        catalog = FixtureCatalog.model_validate_json(catalog_json)
        roster = ROSTER.validate_json(roster_json)
        totals = dict.fromkeys(roster, 0)
        correct = dict.fromkeys(roster, 0)
        for hotkey, index, request_json, response_json, completed, outcome in slots:
            score = score_response(
                CredentialRequest.model_validate_json(request_json),
                CredentialResponse.model_validate_json(response_json) if response_json else None,
                catalog.cases[int(index)],
                catalog,
                datetime.fromisoformat(completed),
                str(outcome),
            )
            totals[str(hotkey)] += int(score)
            correct[str(hotkey)] += int(score == 1)
        report = ScoreReport(
            chain_genesis=self.chain_genesis,
            netuid=self.netuid,
            validator_hotkey=self.key.ss58_address,
            instance_id=instance_id,
            round_id=int(rid),
            epoch_start=int(first),
            epoch_end=int(last),
            completed_block=int(block),
            source_version=catalog.label_version.replace("labels", "board"),
            source_digest=catalog.snapshot_sha256,
            started_at=datetime.fromisoformat(opened),
            completed_at=datetime.fromisoformat(closed),
            miners=tuple(
                MinerScore(
                    miner_hotkey=hotkey,
                    miner_uid=roster[hotkey],
                    score_sum=totals[hotkey],
                    assigned_tasks=len(catalog.cases),
                    correct_tasks=correct[hotkey],
                )
                for hotkey in sorted(roster)
            ),
        )
        signed = SignedScoreReport.sign(report, self.key)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO report_outbox(round_id,report_id,envelope_json,retry_at) VALUES (?,?,?,?)",
                (rid, signed.report_id, signed.model_dump_json(), now.isoformat()),
            )
            db.execute(
                "INSERT OR IGNORE INTO report_deliveries(report_id,destination,retry_at) VALUES (?,?,?)",
                (signed.report_id, self.destination, now.isoformat()),
            )
            self._supersede_history(db)
        return signed

    def due(self, now: datetime) -> SignedScoreReport | None:
        """Prefer fresh scores over history while honoring each report's persisted retry delay."""
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT o.envelope_json FROM report_outbox o JOIN report_deliveries d ON d.report_id=o.report_id "
                "WHERE d.destination=? AND d.uploaded_at IS NULL AND d.superseded_by IS NULL AND d.retry_at<=? "
                "ORDER BY o.round_id DESC LIMIT 1",
                (self.destination, now.isoformat()),
            ).fetchone()
        return SignedScoreReport.model_validate_json(row[0]) if row else None

    def supersede(self, report_id: str, newer_report_id: str) -> None:
        """Retain the original envelope while retiring a retry for an older epoch snapshot."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE report_deliveries SET superseded_by=?,last_error=NULL WHERE report_id=? AND destination=?",
                (newer_report_id, report_id, self.destination),
            )

    def failed(self, report_id: str, now: datetime, error_type: str) -> None:
        """Back off exponentially up to five minutes; persist only the exception type."""
        with closing(sqlite3.connect(self.path)) as db, db:
            attempts = int(
                db.execute(
                    "SELECT attempts FROM report_deliveries WHERE report_id=? AND destination=?",
                    (report_id, self.destination),
                ).fetchone()[0]
            )
            retry = now + timedelta(seconds=min(300, 2 ** min(attempts + 1, 9)))
            db.execute(
                "UPDATE report_deliveries SET attempts=attempts+1,retry_at=?,last_error=? "
                "WHERE report_id=? AND destination=?",
                (retry.isoformat(), error_type, report_id, self.destination),
            )

    def acknowledge(self, signed: SignedScoreReport, receipt: UploadReceipt) -> None:
        """Mark uploaded only when the service acknowledges the expected content and path.

        Raises:
            ValueError: If the receipt describes another report or object.
        """
        if (receipt.report_id, receipt.object_key) != (signed.report_id, signed.object_key):
            raise ValueError("Upload receipt does not match the pending report")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE report_deliveries SET uploaded_at=?,last_error=NULL,verification=? "
                "WHERE report_id=? AND destination=?",
                (receipt.stored_at.isoformat(), receipt.verification, signed.report_id, self.destination),
            )
