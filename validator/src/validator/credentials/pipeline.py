"""Validity RN evaluation nodes using the public actor and HTTP interfaces."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, override

from nexus.v1 import (
    Actor,
    ActorBuilder,
    AxonProtocol,
    BlockBeat,
    ConsumerActor,
    Context,
    ContextStore,
    EventHandler,
    ExecutorFailureException,
    Hotkey,
    MessagesToSend,
    NetUid,
    Neuron,
    NexusException,
    Node,
    NodeSinks,
    NodeSources,
    PipeToBus,
    ProcessedInput,
    ReceiveEvent,
    RemoteExecutionException,
    RemoteRequestFailedException,
    RemoteRequestRejectedException,
    RemoteResponseTimeoutException,
    ResponseInvalidException,
    ResponseValidationException,
    Routed,
    SendEvent,
    SetWeightsBeat,
    Sink,
    SinkName,
    Source,
    SourceName,
    Tempo,
    Weight,
    WeightsCalculationBundle,
    WeightSettingSuccess,
    get_epoch_containing_block,
    get_logger,
)
from opentelemetry import metrics
from validity_protocol.identity import load_signing_key

from validator.chain import registered_neurons
from validator.config import Settings
from validator.credentials.evaluation import EvaluationLedger
from validator.credentials.fixtures import FixtureCatalog, rejection_reason
from validator.credentials.ledger import Outcome
from validator.credentials.protocol import CredentialRequest, CredentialResponse
from validator.reporting.journal import ReportJournal
from validator.reporting.publisher import ReportingSettings

logger = get_logger(__name__)
meter = metrics.get_meter("validity.credentials")
events = meter.create_counter("validity.credential.events", description="RN evaluation lifecycle events")
duration = meter.create_histogram("validity.credential.operation.duration", unit="s")


def public_http_neurons(neurons: Sequence[Neuron]) -> Sequence[Neuron]:
    """Select public HTTP-protocol axons; hotkey signatures are enforced by transport."""
    return [
        neuron
        for neuron in neurons
        if neuron.axon_info.protocol == AxonProtocol.HTTP
        and neuron.axon_info.ip.is_global
        and not neuron.axon_info.ip.is_multicast
        and neuron.axon_info.port > 0
    ]


def failure_outcome(error: NexusException) -> tuple[Outcome, str]:
    """Separate remote failures from validator faults before assigning a denominator."""
    cause = error.executor_error if isinstance(error, ExecutorFailureException) else error
    remote = isinstance(
        cause,
        (
            RemoteExecutionException,
            RemoteRequestFailedException,
            RemoteRequestRejectedException,
            RemoteResponseTimeoutException,
            ResponseInvalidException,
            ResponseValidationException,
        ),
    )
    return ("transport_error" if remote else "interrupted"), type(cause).__name__


class EvaluationFailure(NexusException):
    """An evaluation operation failed and will retry on the next chain beat."""


class EvaluationLoop(Node, ActorBuilder):
    """Persist balanced rounds and gate the reusable weight setter on completed scores."""

    def __init__(
        self,
        ledger: EvaluationLedger,
        fixture_dir: Path,
        netuid: int,
        timeout: timedelta,
        max_in_flight: int,
        max_score_age: timedelta,
        tempo: int = 360,
        settings: Settings | None = None,
    ) -> None:
        super().__init__("credential-evaluation")
        self.ledger = ledger
        self.fixture_dir = fixture_dir
        self.netuid = netuid
        self.timeout = timeout
        self.max_in_flight = max_in_flight
        self.max_score_age = max_score_age
        self.tempo = tempo
        self.settings = settings
        self.hotkey: str | None = None
        self.tick = Sink[BlockBeat](f"{self.id}-tick", owner_node=self)
        self.weight_tick = Sink[SetWeightsBeat](f"{self.id}-weight-tick", owner_node=self)
        self.queued = Sink[WeightSettingSuccess](f"{self.id}-queued", owner_node=self)
        self.tasks = Source[Routed[CredentialRequest]](f"{self.id}-tasks", owner_node=self)
        self.weights = Source[SetWeightsBeat](f"{self.id}-weights", owner_node=self)
        self.error = Source[NexusException](f"{self.id}-error", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(
            sinks={
                SinkName("tick"): self.tick,
                SinkName("weight_tick"): self.weight_tick,
                SinkName("queued"): self.queued,
            }
        )

    @override
    def sources(self) -> NodeSources:
        return NodeSources(
            sources={
                SourceName("tasks"): self.tasks,
                SourceName("weights"): self.weights,
                SourceName("error"): self.error,
            }
        )

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return EvaluationActor(self, pipe_to_bus, context_store)

    def neurons(self) -> dict[str, Neuron]:
        """Recheck current hotkey/UID bindings through the chain sidecar.

        Raises:
            ValueError: If the validator identity has not initialized.
        """
        if self.hotkey is None:
            raise ValueError("Validator identity is not initialized")
        neurons = registered_neurons(self.netuid, self.hotkey)
        return {
            str(neuron.hotkey): neuron for neuron in public_http_neurons(neurons) if str(neuron.hotkey) != self.hotkey
        }

    def weigh(self, bundle: WeightsCalculationBundle) -> dict[Hotkey, Weight]:
        """Load the persisted proposal again inside the weight setter actor.

        Raises:
            ValueError: If the source, roster, or freshness no longer matches.
        """
        started = perf_counter()
        try:
            proposal = self.ledger.batch(int(bundle.epoch.first_block))
            roster = {hotkey: int(neuron.uid) for hotkey, neuron in self.neurons().items()}
            catalog = FixtureCatalog.load(self.fixture_dir)
            if (
                roster != proposal.roster
                or catalog.snapshot_sha256 != proposal.source_digest
                or datetime.now(UTC) - proposal.completed_at > self.max_score_age
            ):
                raise ValueError("Weight batch is stale or its source/registration context changed")
            events.add(1, {"stage": "weight_attempt"})
            return {Hotkey(hotkey): Weight(weight) for hotkey, weight in proposal.weights.items()}
        finally:
            duration.record(perf_counter() - started, {"operation": "weigh"})


class EvaluationActor(Actor):
    """Serialize allocation and acknowledgements, keeping durable state in SQLite."""

    def __init__(self, node: EvaluationLoop, pipe: PipeToBus, store: ContextStore) -> None:
        super().__init__(name=node.id, pipe_to_bus=pipe, context_store=store)
        self.node = node

    @override
    def on_start(self) -> None:
        self.node.ledger.initialize_evaluation()
        settings = self.node.settings
        if settings is None:
            raise ValueError("Operator settings are required")
        key = load_signing_key(settings.wallet_path, settings.wallet_name, settings.hotkey_name)
        reporting = ReportingSettings.model_validate({})
        ReportJournal(
            self.node.ledger.path, settings.chain_genesis, self.node.netuid, key, reporting.destination
        ).initialize()
        self.node.hotkey = key.ss58_address
        recovered = self.node.ledger.recover(datetime.now(UTC))
        events.add(recovered, {"stage": "interrupted"})
        logger.info("RN evaluation ready: recovered=%s", recovered)

    @override
    def handlers(self) -> dict[Sink[Any], EventHandler]:
        return {self.node.tick: self._handle, self.node.weight_tick: self._handle, self.node.queued: self._handle}

    def _handle(self, ctx: Context, event: ReceiveEvent[Any]) -> MessagesToSend:
        started = perf_counter()
        now = datetime.now(UTC)
        try:
            if event.target == self.node.queued:
                self.node.ledger.mark_queued(str(ctx.id), now)
                events.add(1, {"stage": "weights_queued"})
                logger.info("Weight batch queued; awaiting independent chain readback")
                return ()
            neurons = self.node.neurons()
            roster = {hotkey: int(neuron.uid) for hotkey, neuron in neurons.items()}
            catalog = FixtureCatalog.load(self.node.fixture_dir)
            self.node.ledger.path.with_suffix(".heartbeat").touch(mode=0o600)
            if event.target == self.node.tick:
                block_beat: BlockBeat = event.payload
                epoch = get_epoch_containing_block(
                    block_beat.block_number, NetUid(self.node.netuid), Tempo(self.node.tempo)
                )
                task = self.node.ledger.next_assignment(
                    catalog,
                    roster,
                    self.node.netuid,
                    now,
                    self.node.timeout,
                    self.node.max_in_flight,
                    completed_block=int(block_beat.block_number),
                    epoch_start=int(epoch.first_block),
                    epoch_end=int(epoch.last_block),
                )
                if task is None:
                    return ()
                events.add(1, {"stage": "assigned"})
                return (
                    SendEvent(
                        ctx_id=event.ctx_id,
                        source=self.node.tasks,
                        payload=Routed(input=task.request, target=neurons[task.hotkey]),
                    ),
                )
            beat: SetWeightsBeat = event.payload
            proposal = self.node.ledger.proposal(catalog, roster, self.node.netuid, now, self.node.max_score_age)
            if proposal is None:
                events.add(1, {"stage": "weights_paused"})
                logger.info("Weights paused: no current complete positive round; chain weights may remain")
                return ()
            self.node.ledger.prepare_batch(
                int(beat.epoch.first_block), str(ctx.id), proposal, now, int(beat.block_number)
            )
            return (SendEvent(ctx_id=event.ctx_id, source=self.node.weights, payload=beat),)
        except Exception as exc:
            events.add(1, {"stage": "evaluation_error"})
            logger.exception("RN evaluation operation failed")
            return (SendEvent(ctx_id=event.ctx_id, source=self.node.error, payload=EvaluationFailure(str(exc))),)
        finally:
            duration.record(perf_counter() - started, {"operation": "evaluation"})


class ObserveCredential(Node, ActorBuilder):
    """Persist the first terminal decision using truth pinned before dispatch."""

    sink: Sink[ProcessedInput[Routed[CredentialRequest], CredentialResponse]]

    def __init__(self, ledger: EvaluationLedger) -> None:
        super().__init__("credential-observe")
        self.sink = Sink(f"{self.id}-sink", owner_node=self)
        self.ledger = ledger

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(sinks={SinkName("sink"): self.sink})

    @override
    def sources(self) -> NodeSources:
        return NodeSources(sources={})

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return ObserveCredentialActor(self, pipe_to_bus, context_store)


class ObserveCredentialActor(ConsumerActor[ProcessedInput[Routed[CredentialRequest], CredentialResponse]]):
    """Verify responses and commit once; transport deadlines count as zero in the round."""

    def __init__(self, node: ObserveCredential, pipe: PipeToBus, store: ContextStore) -> None:
        super().__init__(node.sink, pipe, store)
        self.node = node

    @override
    def _consume(self, ctx: Context, payload: ProcessedInput[Routed[CredentialRequest], CredentialResponse]) -> None:
        started = perf_counter()
        now = datetime.now(UTC)
        request = payload.input.input
        result = payload.output
        outcome: Outcome
        if isinstance(result, NexusException):
            outcome, reason = failure_outcome(result)
            response = None
        else:
            expected, digest, version = self.node.ledger.truth(request)
            reason = rejection_reason(request, result, expected, digest, now, version)
            outcome, response = ("rejected" if reason else "verified"), result
        saved = self.node.ledger.finish(request.task_id, outcome, reason, response, now)
        events.add(1, {"stage": outcome if saved else "duplicate_or_closed"})
        duration.record(perf_counter() - started, {"operation": "observe"})
        logger.info(
            "Credential result: task=%s outcome=%s reason=%s saved=%s hotkey=%s",
            request.task_id,
            outcome,
            reason,
            saved,
            payload.input.target.hotkey,
        )
