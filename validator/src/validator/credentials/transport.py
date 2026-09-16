"""Signed RN exchanges over mutually authenticated HTTPS, inside the actor runtime."""

from __future__ import annotations

import hashlib
import json
import ssl
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal, override

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
from pydantic import Field

from validator.config import Settings
from validator.credentials.protocol import CredentialRequest, CredentialResponse, Digest, WireModel
from validator.reporting.protocol import ChainId, HotkeyAddress

MAX_EXCHANGE_BYTES = 64 * 1024
REQUEST_DOMAIN = b"validity.rn-request.v1\n"
RESPONSE_DOMAIN = b"validity.rn-response.v1\n"
meter = metrics.get_meter("validity.transport")
events = meter.create_counter("validity.transport.events")
duration = meter.create_histogram("validity.transport.duration", unit="s")


def canonical(model: WireModel) -> bytes:
    """Canonical JSON shared by independent miner implementations."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class TaskBinding(WireModel):
    """Bind the assignment to a chain and both participant hotkeys."""

    version: Literal["validity.rn-exchange.v1"] = "validity.rn-exchange.v1"
    chain_genesis: ChainId
    netuid: int = Field(ge=1, le=65535)
    validator_hotkey: HotkeyAddress
    miner_hotkey: HotkeyAddress
    request: CredentialRequest


class SignedRequest(WireModel):
    """The validator signature authenticates the complete assignment."""

    task: TaskBinding
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(canonical(self.task)).hexdigest()

    def verify(self) -> None:
        """Verify the validator signature.

        Raises:
            ValueError: If the assignment has been altered or impersonated.
        """
        if not Keypair(ss58_address=self.task.validator_hotkey).verify(
            REQUEST_DOMAIN + canonical(self.task), bytes.fromhex(self.signature)
        ):
            raise ValueError("Invalid assignment signature")

    @classmethod
    def sign(cls, task: TaskBinding, key: Keypair) -> SignedRequest:
        """Sign only assignments belonging to this hotkey.

        Raises:
            ValueError: If the signing identity differs.
        """
        if key.ss58_address != task.validator_hotkey or key.crypto_type != 1:
            raise ValueError("Assignment signing identity differs")
        return cls(task=task, signature=key.sign(REQUEST_DOMAIN + canonical(task)).hex())


class BoundResponse(WireModel):
    """The request hash prevents reuse across tasks, validators, subnets and chains."""

    request_hash: Digest
    response: CredentialResponse


class SignedResponse(WireModel):
    """The miner signs its result, including the complete assignment's digest."""

    result: BoundResponse
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    def verify(self, request: SignedRequest) -> CredentialResponse:
        """Authenticate the expected miner before accepting its response.

        Raises:
            ValueError: If binding or miner signature verification fails.
        """
        response = self.result.response
        if (
            self.result.request_hash != request.request_hash
            or response.task_id != request.task.request.task_id
            or response.provider_ref != request.task.request.provider_query.provider_ref
        ):
            raise ValueError("Response assignment binding differs")
        if not Keypair(ss58_address=request.task.miner_hotkey).verify(
            RESPONSE_DOMAIN + canonical(self.result), bytes.fromhex(self.signature)
        ):
            raise ValueError("Invalid miner signature")
        return response


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
        raise ValueError("Miner must register a public HTTPS endpoint")
    return f"https://{format_host_for_url(axon.ip)}:{axon.port}/v1/evaluate"


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


class CredentialHTTPS(ExecutorCommunicator[CredentialRequest, CredentialResponse], ActorBuilder):
    """Use the public communicator extension point; no public callback listener is needed."""

    def __init__(self, settings: Settings) -> None:
        super().__init__("credential-https", CredentialRequest, CredentialResponse)
        self.settings = settings

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return CredentialHTTPSActor(self, pipe_to_bus, context_store)


class CredentialHTTPSActor(CommunicatorActor[CredentialRequest, CredentialResponse]):
    """Own network clients and key material for the lifetime of one runtime actor."""

    def __init__(self, node: CredentialHTTPS, pipe: PipeToBus, store: ContextStore) -> None:
        super().__init__(spec=node, pipe_to_bus=pipe, context_store=store)
        self.settings = node.settings
        self.client: httpx.Client | None = None
        self.key: Keypair | None = None

    @override
    def on_start(self) -> None:
        s = self.settings
        tls = ssl.create_default_context(cafile=str(s.tls_ca_file))
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(s.tls_cert_file, s.tls_key_file)
        self.key = Wallet(path=str(s.wallet_path), name=s.wallet_name, hotkey=s.hotkey_name).get_hotkey()
        self.client = httpx.Client(verify=tls, trust_env=False, follow_redirects=False)

    @override
    def on_stop(self) -> None:
        if self.client is not None:
            self.client.close()

    @override
    def handle_input(self, ctx: Context, event: ReceiveEvent[Routed[CredentialRequest]]) -> MessagesToSend:
        started = perf_counter()
        try:
            if self.client is None or self.key is None:
                raise RuntimeError("HTTPS actor has not started")
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
            return self._executor_error_event(ctx.id, RemoteRequestFailedException("Miner HTTPS request failed"))
        except ValueError:
            events.add(1, {"result": "invalid_response"})
            return self._executor_error_event(ctx.id, ResponseInvalidException("Invalid signed miner exchange"))
        finally:
            duration.record(perf_counter() - started)
