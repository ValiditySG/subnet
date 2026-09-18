"""Logging consumer actors for the validator pipeline's error sources."""

from __future__ import annotations

import logging
from typing import override

from nexus.v1 import (
    Actor,
    ActorBuilder,
    ConsumerActor,
    Context,
    ContextStore,
    NexusException,
    Node,
    NodeSinks,
    NodeSources,
    PipeToBus,
    Sink,
    SinkName,
    get_logger,
)

logger: logging.Logger = get_logger(__name__)


class ErrorLoggerNode(Node, ActorBuilder):
    """Sink-only node that logs framework/internal errors emitted by upstream actors.

    sink sink: NexusException from any upstream actor's error source
    """

    sink: Sink[NexusException]

    def __init__(self, _id: str) -> None:
        super().__init__(_id)
        self.sink = Sink(f"{self.id}-sink", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(sinks={SinkName("sink"): self.sink})

    @override
    def sources(self) -> NodeSources:
        return NodeSources(sources={})

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return ErrorLoggerActor(spec=self.sink, pipe_to_bus=pipe_to_bus, context_store=context_store)


class ErrorLoggerActor(ConsumerActor[NexusException]):
    """Logs framework-level errors received from any upstream actor's error source."""

    @override
    def _consume(self, ctx: Context, payload: NexusException) -> None:
        logger.exception("Pipeline error from ctx=%s: %r", ctx.id, payload, exc_info=payload)
