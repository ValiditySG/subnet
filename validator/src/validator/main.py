"""Validity testnet validator: synthetic RN evaluations, signed scores and chain weights."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path

import click
import sentry_sdk
from dotenv import load_dotenv
from nexus.v1 import (
    BlockBeatNode,
    BlockCount,
    NetUid,
    NexusValidator,
    SetWeightsBeatNode,
    Tempo,
    WeightSetterNode,
)
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.litestar import LitestarIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from validator.config import Settings
from validator.credentials.evaluation import EvaluationLedger
from validator.credentials.pipeline import EvaluationLoop, ObserveCredential
from validator.credentials.transport import CredentialHTTPS
from validator.logging_config import LoggingSettings, configure_logging
from validator.operator import check_env_file, exclusive_state
from validator.otel import OtelSettings, setup_otel
from validator.reporting.credentials import StorageCredentials
from validator.reporting.publisher import PublishReports, ReportingSettings
from validator.response_logger import ErrorLoggerNode


class Validator(NexusValidator):
    """Compose synthetic evaluation, authenticated transport and durable publication."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._connect_credentials(settings)

    def _connect_credentials(self, settings: Settings) -> None:
        ledger = EvaluationLedger(settings.ledger_path)
        evaluation = EvaluationLoop(
            ledger,
            settings.fixture_dir,
            settings.netuid,
            settings.total_processing_timeout,
            1,
            settings.max_score_age,
            settings.tempo,
            settings,
        )
        weight_beat = SetWeightsBeatNode(
            "credential-weight-clock",
            netuid=NetUid(settings.netuid),
            epoch_start_offset=BlockCount(10),
            attempts_cooldown=BlockCount(20),
            tempo=Tempo(settings.tempo),
        )
        setter = WeightSetterNode("credential-weight-setter", weighing_func=evaluation.weigh)
        communicator = CredentialHTTPS(settings)
        observe = ObserveCredential(ledger)
        errors = ErrorLoggerNode("credential-errors")
        self.connect(self.subnet_clock.source, evaluation.tick)
        weight_clock = BlockBeatNode("weight-chain-clock")
        self.connect(weight_clock.source, weight_beat.block_beat)
        self.connect(weight_beat.source, evaluation.weight_tick)
        self.connect(evaluation.weights, setter.sink)
        self.connect(setter.ok, evaluation.queued)
        self.connect(setter.error, errors.sink)
        self.connect(evaluation.tasks, communicator.input)
        self.connect(communicator.processed, observe.sink)
        self.connect(evaluation.error, errors.sink)
        self.connect(communicator.error, errors.sink)
        reporting = ReportingSettings.model_validate({})
        publisher = PublishReports(settings.ledger_path, settings.netuid, reporting)
        report_clock = BlockBeatNode("score-report-clock", polling_interval=timedelta(seconds=5))
        self.connect(report_clock.source, publisher.sink)


def _setup_sentry() -> None:
    # Optional Sentry integration. Enable by setting the SENTRY_DSN env var.
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            LitestarIntegration(),
            LoggingIntegration(event_level=logging.ERROR),
            HttpxIntegration(),
            ThreadingIntegration(propagate_scope=True),
        ],
    )


@click.command()
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
def main(env_file: Path | None) -> None:
    """CLI entry point: load env from --env-file (if given) and run the validator."""
    os.umask(0o077)
    selected = env_file or Path(".env")
    if selected.exists():
        check_env_file(selected)
        load_dotenv(selected)
    logging_settings = LoggingSettings()
    configure_logging(logging_settings)
    setup_otel(OtelSettings())
    _setup_sentry()
    settings = Settings.model_validate({})
    ReportingSettings.model_validate({})
    StorageCredentials.model_validate({})
    with exclusive_state(settings.ledger_path):
        Validator.run(settings_class=Settings)


if __name__ == "__main__":
    main()
