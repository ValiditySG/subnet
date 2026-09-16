"""Hotkey-authenticated synthetic RN exchanges over HTTP, inside the actor runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import override

import httpx
from bittensor_wallet import Keypair, Wallet
from nexus.v1 import (
    Actor,
    ActorBuilder,
    AxonProtocol,
    CommunicatorActor,
    Context,
    ContextStore,
    ExecutorCommunicator,
    MessagesToSend,
    Neuron,
    PipeToBus,
    ReceiveEvent,
    RemoteRequestFailedException,
    RemoteResponseTimeoutException,
    ResponseInvalidException,
    Routed,
    format_host_for_url,
)
from opentelemetry import metrics
from validity_protocol.exchange import MAX_EXCHANGE_BYTES as MAX_EXCHANGE_BYTES
from validity_protocol.exchange import REQUEST_DOMAIN as REQUEST_DOMAIN
from validity_protocol.exchange import RESPONSE_DOMAIN as RESPONSE_DOMAIN
from validity_protocol.exchange import BoundResponse as BoundResponse
from validity_protocol.exchange import SignedRequest as SignedRequest
from validity_protocol.exchange import SignedResponse as SignedResponse
from validity_protocol.exchange import TaskBinding as TaskBinding
from validity_protocol.exchange import canonical as canonical

from validator.config import Settings
from validator.credentials.protocol import CredentialRequest, CredentialResponse

meter = metrics.get_meter("validity.transport")
events = meter.create_counter("validity.transport.events")
duration = meter.create_histogram("validity.transport.duration", unit="s")


def target_url(neuron: Neuron) -> str:
    """Only contact globally routable literal addresses from registered HTTP axons.

    Raises:
        ValueError: If the endpoint could address local infrastructure or another protocol.
    """
    axon = neuron.axon_info
    if (
        axon.protocol != AxonProtocol.HTTP
        or not axon.ip.is_global
        or axon.ip.is_multicast
        or not 1 <= axon.port <= 65535
    ):
        raise ValueError("Miner must register a public HTTP endpoint")
    return f"http://{format_host_for_url(axon.ip)}:{axon.port}/v1/evaluate"


def exchange(client: httpx.Client, url: str, signed: SignedRequest) -> CredentialResponse:
    """Bound response size and deadline; never follow redirects to an unregistered endpoint.

    Raises:
        ValueError: If the response is too large, invalid or unauthenticated.
        httpx.TimeoutException: If the task's absolute deadline has expired.
    """
    remaining = (signed.task.request.deadline_at - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise httpx.TimeoutException("Assignment expired before dispatch")
    with client.stream(
        "POST",
        url,
        content=signed.model_dump_json(),
        headers={"Content-Type": "application/json", "Accept-Encoding": "identity"},
        timeout=remaining,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("Compressed responses are not supported")
        data = bytearray()
        for chunk in response.iter_raw():
            data.extend(chunk)
            if len(data) > MAX_EXCHANGE_BYTES:
                raise ValueError("Miner response exceeds the protocol limit")
            if datetime.now(UTC) > signed.task.request.deadline_at:
                raise httpx.TimeoutException("Assignment expired during response")
    return SignedResponse.model_validate_json(bytes(data)).verify(signed)


class CredentialHTTP(ExecutorCommunicator[CredentialRequest, CredentialResponse], ActorBuilder):
    """Use the public communicator extension point; no public callback listener is needed."""

    def __init__(self, settings: Settings) -> None:
        # Preserve the persisted actor identity across the transport change.
        super().__init__("credential-https", CredentialRequest, CredentialResponse)
        self.settings = settings

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return CredentialHTTPActor(self, pipe_to_bus, context_store)


class CredentialHTTPActor(CommunicatorActor[CredentialRequest, CredentialResponse]):
    """Own network clients and key material for the lifetime of one runtime actor."""

    def __init__(self, node: CredentialHTTP, pipe: PipeToBus, store: ContextStore) -> None:
        super().__init__(spec=node, pipe_to_bus=pipe, context_store=store)
        self.settings = node.settings
        self.client: httpx.Client | None = None
        self.key: Keypair | None = None

    @override
    def on_start(self) -> None:
        s = self.settings
        self.key = Wallet(path=str(s.wallet_path), name=s.wallet_name, hotkey=s.hotkey_name).get_hotkey()
        self.client = httpx.Client(trust_env=False, follow_redirects=False)

    @override
    def on_stop(self) -> None:
        if self.client is not None:
            self.client.close()

    @override
    def handle_input(self, ctx: Context, event: ReceiveEvent[Routed[CredentialRequest]]) -> MessagesToSend:
        started = perf_counter()
        try:
            if self.client is None or self.key is None:
                raise RuntimeError("HTTP actor has not started")
            task = TaskBinding(
                chain_genesis=self.settings.chain_genesis,
                netuid=self.settings.netuid,
                validator_hotkey=self.key.ss58_address,
                miner_hotkey=str(event.payload.target.hotkey),
                request=event.payload.input,
            )
            signed = SignedRequest.sign(task, self.key)
            output = exchange(self.client, target_url(event.payload.target), signed)
            events.add(1, {"result": "authenticated"})
            return self._processed_event(ctx.id, output)
        except httpx.TimeoutException:
            events.add(1, {"result": "timeout"})
            return self._executor_error_event(ctx.id, RemoteResponseTimeoutException("Miner deadline exceeded"))
        except httpx.HTTPError:
            events.add(1, {"result": "transport_error"})
            return self._executor_error_event(ctx.id, RemoteRequestFailedException("Miner HTTP request failed"))
        except ValueError:
            events.add(1, {"result": "invalid_response"})
            return self._executor_error_event(ctx.id, ResponseInvalidException("Invalid signed miner exchange"))
        finally:
            duration.record(perf_counter() - started)
