"""Upload reports in an independent actor so storage failures cannot block weights."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import override

from bittensor_wallet import Wallet
from nexus.v1 import (
    Actor,
    ActorBuilder,
    BlockBeat,
    ConsumerActor,
    Context,
    ContextStore,
    Node,
    NodeSinks,
    NodeSources,
    PipeToBus,
    Sink,
    SinkName,
    get_logger,
)
from pydantic import Field

from validator.config import WalletSettings
from validator.credentials.evaluation import EvaluationLedger
from validator.reporting.credentials import StorageCredentials
from validator.reporting.journal import ReportJournal
from validator.reporting.protocol import OBJECT_LAYOUT, ChainId
from validator.reporting.service import duration, events
from validator.reporting.snapshot import SupersededReport
from validator.reporting.storage import HippiusStore
from validator.reporting.upload import HippiusUploader, ReportUploader

logger = get_logger(__name__)


class ReportingSettings(WalletSettings):
    """Mandatory Hippius publishing with an explicit bucket and .env credential pair."""

    bucket: str = Field(min_length=3, validation_alias="HIPPIUS_BUCKET")
    verify_readback: bool = Field(default=True, validation_alias="HIPPIUS_VERIFY_READBACK")
    chain_genesis: ChainId
    timeout_seconds: float = Field(default=5, gt=0, le=30, validation_alias="HIPPIUS_TIMEOUT_SECONDS")

    @property
    def destination(self) -> str:
        """Rotating credentials preserves receipts; changing bucket or layout does not."""
        return f"hippius:https://s3.hippius.com/{self.bucket}#{OBJECT_LAYOUT}"

    def uploader(self, hotkey: str) -> ReportUploader:
        """Construct this operator's authenticated transport inside its actor."""
        credentials = StorageCredentials.model_validate({})
        return HippiusUploader(
            HippiusStore.connect(self.bucket, credentials, self.timeout_seconds),
            hotkey,
            verify_readback=self.verify_readback,
        )


class ReportSender:
    """One bounded attempt per block beat; SQLite owns all retry state."""

    def __init__(self, journal: ReportJournal, uploader: ReportUploader) -> None:
        self.journal = journal
        self.uploader = uploader

    def tick(self, now: datetime) -> None:
        """Prepare one report and retry one due upload, preserving the signed payload.

        Raises:
            Exception: If upload or receipt validation fails, after persisting retry state.
        """
        self.journal.prepare_next(now)
        signed = self.journal.due(now)
        if signed is None:
            return
        try:
            receipt = self.uploader.upload(signed)
            self.journal.acknowledge(signed, receipt)
            events.add(1, {"stage": "acknowledged"})
            logger.info("Score report upload acknowledged: report_id=%s", signed.report_id)
        except SupersededReport as exc:
            self.journal.supersede(signed.report_id, exc.report_id)
            events.add(1, {"stage": "superseded"})
            logger.info("Score report superseded by a newer stored epoch snapshot: report_id=%s", signed.report_id)
        except Exception as exc:
            self.journal.failed(signed.report_id, now, type(exc).__name__)
            raise


class PublishReports(Node, ActorBuilder):
    """Consume a dedicated clock's contexts, independently of evaluation and weight paths."""

    def __init__(self, path: Path, netuid: int, settings: ReportingSettings) -> None:
        super().__init__("score-report-publisher")
        self.path = path
        self.netuid = netuid
        self.settings = settings
        self.sink = Sink[BlockBeat](f"{self.id}-sink", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(sinks={SinkName("sink"): self.sink})

    @override
    def sources(self) -> NodeSources:
        return NodeSources(sources={})

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return PublishReportsActor(self, pipe_to_bus, context_store)


class PublishReportsActor(ConsumerActor[BlockBeat]):
    """Load the hotkey in the actor; log failures and retry through the durable outbox."""

    def __init__(self, node: PublishReports, pipe: PipeToBus, store: ContextStore) -> None:
        super().__init__(node.sink, pipe, store)
        self.node = node
        self.sender: ReportSender | None = None

    @override
    def on_start(self) -> None:
        settings = self.node.settings
        key = Wallet(
            name=settings.wallet_name, hotkey=settings.hotkey_name, path=str(settings.wallet_path)
        ).get_hotkey()
        EvaluationLedger(self.node.path).initialize_evaluation()
        journal = ReportJournal(self.node.path, settings.chain_genesis, self.node.netuid, key, settings.destination)
        journal.initialize()
        self.sender = ReportSender(journal, settings.uploader(key.ss58_address))
        logger.info("Score publisher ready: validator=%s backend=hippius", key.ss58_address)

    @override
    def on_stop(self) -> None:
        if self.sender:
            self.sender.uploader.close()

    @override
    def _consume(self, ctx: Context, payload: BlockBeat) -> None:
        started = perf_counter()
        try:
            if self.sender is None:
                raise RuntimeError("Score publisher has not started")
            self.sender.tick(datetime.now(UTC))
        except Exception as exc:
            events.add(1, {"stage": "publish_failed"})
            logger.warning("Score publishing failed; will retry: error_type=%s", type(exc).__name__)
        finally:
            duration.record(perf_counter() - started, {"operation": "publish"})
