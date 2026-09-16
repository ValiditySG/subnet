"""Validity validator: local RN evaluation, durable scores and chain weights."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Literal

import click
import sentry_sdk
from dotenv import load_dotenv
from nexus.v1 import (
    AsyncHttpNeuronCommunicator,
    BlockCount,
    NetUid,
    NexusValidator,
    Port,
    RoundRobinNeuronRouter,
    SetWeightsBeatNode,
    Tempo,
    WeightSetterNode,
)
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.litestar import LitestarIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from validator.credentials.evaluation import EvaluationLedger
from validator.credentials.pipeline import EvaluationLoop, ObserveCredential, local_http_neurons
from validator.credentials.protocol import CredentialRequest, CredentialResponse
from validator.logging_config import LoggingSettings, configure_logging
from validator.otel import OtelSettings, setup_otel
from validator.payload import PingInput, PingPayloadCreator, PongOutput
from validator.response_logger import ErrorLoggerNode, ResponseLoggerNode


class Settings(BaseSettings):
    """Runtime configuration; credential conformance is explicitly local-only."""

    model_config = SettingsConfigDict(env_prefix="VALIDATOR_", extra="ignore")

    netuid: int = Field(validation_alias=AliasChoices("VALIDATOR_NETUID", "NETUID"))
    mode: Literal["ping", "local_credentials"] = "ping"
    fixture_dir: Path = Path("../localnet/fixtures")
    ledger_path: Path = Path("../localnet/state/credentials.sqlite3")
    callback_host: str = "127.0.0.1"
    callback_port: int = 8001
    send_timeout: timedelta = timedelta(seconds=2)
    total_processing_timeout: timedelta = timedelta(seconds=10)
    max_in_flight: int = Field(default=4, ge=1, le=32)
    tempo: int = Field(default=360, ge=20, validation_alias=AliasChoices("VALIDATOR_TEMPO", "SUBNET_TEMPO"))
    max_score_age: timedelta = timedelta(minutes=10)


class Validator(NexusValidator):
    """Compose evaluation, HTTP transport, and weight submission actors."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

        if settings.mode == "local_credentials":
            self._connect_credentials(settings)
        else:
            self._connect_ping(settings)

    def _connect_ping(self, settings: Settings) -> None:

        payload_creator = PingPayloadCreator("ping-payload-creator")
        router = RoundRobinNeuronRouter[PingInput](
            "miner-router",
            netuid=settings.netuid,
            neuron_filter=local_http_neurons,
        )
        communicator = AsyncHttpNeuronCommunicator[PingInput, PongOutput](
            "miner-communicator",
            target_path="/task",
            send_timeout=settings.send_timeout,
            total_processing_timeout=settings.total_processing_timeout,
            max_in_flight=settings.max_in_flight,
            callback_bind_ip=IPv4Address("127.0.0.1"),
            callback_port=Port(settings.callback_port),
            callback_path="/callback",
            callback_base_url=f"http://{settings.callback_host}:{settings.callback_port}",
            input_model=PingInput,
            output_model=PongOutput,
        )
        response_logger = ResponseLoggerNode("response-logger")
        error_logger = ErrorLoggerNode("error-logger")

        self.connect(self.subnet_clock.source, payload_creator.input)
        self.connect(payload_creator.created_payload, router.input)
        self.connect(router.routed, communicator.input)
        self.connect(communicator.processed, response_logger.sink)
        self.connect(payload_creator.error, error_logger.sink)
        self.connect(router.error, error_logger.sink)
        self.connect(communicator.error, error_logger.sink)

    def _connect_credentials(self, settings: Settings) -> None:
        ledger = EvaluationLedger(settings.ledger_path)
        evaluation = EvaluationLoop(
            ledger,
            settings.fixture_dir,
            settings.netuid,
            settings.total_processing_timeout,
            settings.max_in_flight,
            settings.max_score_age,
        )
        weight_beat = SetWeightsBeatNode(
            "credential-weight-clock",
            netuid=NetUid(settings.netuid),
            epoch_start_offset=BlockCount(10),
            attempts_cooldown=BlockCount(20),
            tempo=Tempo(settings.tempo),
        )
        setter = WeightSetterNode("credential-weight-setter", weighing_func=evaluation.weigh)
        communicator = AsyncHttpNeuronCommunicator[CredentialRequest, CredentialResponse](
            "credential-http",
            target_path="/task",
            send_timeout=settings.send_timeout,
            total_processing_timeout=settings.total_processing_timeout,
            max_in_flight=settings.max_in_flight,
            callback_bind_ip=IPv4Address("127.0.0.1"),
            callback_port=Port(settings.callback_port),
            callback_path="/callback",
            callback_base_url=f"http://{settings.callback_host}:{settings.callback_port}",
            input_model=CredentialRequest,
            output_model=CredentialResponse,
        )
        observe = ObserveCredential(ledger)
        errors = ErrorLoggerNode("credential-errors")
        self.connect(self.subnet_clock.source, evaluation.tick)
        self.connect(self.subnet_clock.source, weight_beat.block_beat)
        self.connect(weight_beat.source, evaluation.weight_tick)
        self.connect(evaluation.weights, setter.sink)
        self.connect(setter.ok, evaluation.queued)
        self.connect(setter.error, errors.sink)
        self.connect(evaluation.tasks, communicator.input)
        self.connect(communicator.processed, observe.sink)
        self.connect(evaluation.error, errors.sink)
        self.connect(communicator.error, errors.sink)


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
    load_dotenv(env_file)
    logging_settings = LoggingSettings()
    configure_logging(logging_settings)
    setup_otel(OtelSettings())
    _setup_sentry()
    Validator.run(settings_class=Settings)


if __name__ == "__main__":
    main()
