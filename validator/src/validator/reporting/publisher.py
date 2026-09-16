"""Upload reports in an independent actor so storage failures cannot block weights."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal, Self, override
from urllib.parse import urlsplit

import httpx
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
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from validator.reporting.journal import ReportJournal
from validator.reporting.protocol import OBJECT_LAYOUT, ChainId
from validator.reporting.service import duration, events
from validator.reporting.snapshot import SupersededReport
from validator.reporting.storage import HippiusStore
from validator.reporting.upload import GatewayUploader, HippiusUploader, ReportUploader

logger = get_logger(__name__)


class ReportingSettings(BaseSettings):
    """Opt-in publishing with a private hotkey and a dedicated Hippius ACL credential profile."""

    model_config = SettingsConfigDict(env_prefix="VALIDATOR_REPORTS_", extra="ignore")
    enabled: bool = False
    backend: Literal["hippius", "gateway"] = "hippius"
    bucket: str | None = None
    credentials_file: Path | None = None
    profile: str | None = None
    verify_readback: bool = False
    gateway_url: str = "http://127.0.0.1:8090"
    chain_genesis: ChainId | None = None
    wallet_path: Path = Path("../localnet/wallets")
    wallet_name: str = "validator"
    hotkey_name: str = "default"
    timeout_seconds: float = Field(default=5, gt=0, le=30)

    @model_validator(mode="after")
    def check_enabled(self) -> Self:
        if self.enabled and self.chain_genesis is None:
            raise ValueError("Publishing requires the local chain genesis hash")
        if (
            self.enabled
            and self.backend == "hippius"
            and (not self.bucket or self.credentials_file is None or not self.profile)
        ):
            raise ValueError("Direct Hippius publishing requires bucket, credentials_file and profile")
        url = urlsplit(self.gateway_url)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("Use HTTPS for a remote score service")
        if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError("Gateway URL must be an origin without credentials, path or query")
        return self

    @property
    def destination(self) -> str:
        """Credential rotation preserves delivery status; changing storage targets does not."""
        if self.backend == "hippius":
            return f"hippius:https://s3.hippius.com/{self.bucket}#{OBJECT_LAYOUT}"
        return f"gateway:{self.gateway_url.rstrip('/')}#{OBJECT_LAYOUT}"

    def uploader(self, hotkey: str) -> ReportUploader:
        """Construct this validator's transport inside its publishing actor.

        Raises:
            ValueError: If direct upload credentials are incomplete.
        """
        if self.backend == "gateway":
            return GatewayUploader(httpx.Client(timeout=self.timeout_seconds), self.gateway_url)
        if not self.bucket or self.credentials_file is None or not self.profile:
            raise ValueError("Direct Hippius publishing requires a dedicated credential profile")
        return HippiusUploader(
            HippiusStore.connect(self.bucket, self.profile, self.credentials_file, self.timeout_seconds),
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
        if settings.chain_genesis is None:
            raise ValueError("Reporting chain genesis is required")
        key = Wallet(
            name=settings.wallet_name, hotkey=settings.hotkey_name, path=str(settings.wallet_path)
        ).get_hotkey()
        journal = ReportJournal(self.node.path, settings.chain_genesis, self.node.netuid, key, settings.destination)
        journal.initialize()
        self.sender = ReportSender(journal, settings.uploader(key.ss58_address))
        logger.info("Score publisher ready: validator=%s backend=%s", key.ss58_address, settings.backend)

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
