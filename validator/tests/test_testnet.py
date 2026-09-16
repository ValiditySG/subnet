"""Security boundaries of the independently operated synthetic testnet validator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from threading import Thread
from typing import override
from unittest.mock import MagicMock

import httpx
import pytest
from bittensor_wallet import Keypair
from nexus.v1 import Neuron
from pydantic import ValidationError

from tests.test_credentials import make_request, make_response
from validator.chain import ChainSettings, registered_neurons
from validator.config import Settings
from validator.credentials.pipeline import public_http_neurons
from validator.credentials.transport import (
    MAX_EXCHANGE_BYTES,
    RESPONSE_DOMAIN,
    BoundResponse,
    SignedRequest,
    SignedResponse,
    TaskBinding,
    canonical,
    exchange,
    target_url,
)
from validator.operator import check_env_file, exclusive_state

VALIDATOR = Keypair.create_from_uri("//Alice")
MINER = Keypair.create_from_uri("//Bob")
GENESIS = "0x8f9cf856bf558a14440e75569c9e58594757048d7b3a84b5d25f6bd978263105"


def assignment() -> SignedRequest:
    now = datetime.now(UTC)
    request = make_request().model_copy(update={"issued_at": now, "deadline_at": now + timedelta(seconds=30)})
    task = TaskBinding(
        chain_genesis=GENESIS,
        netuid=123,
        validator_hotkey=VALIDATOR.ss58_address,
        miner_hotkey=MINER.ss58_address,
        request=request,
    )
    return SignedRequest.sign(task, VALIDATOR)


def answer(request: SignedRequest, key: Keypair = MINER) -> SignedResponse:
    result = BoundResponse(request_hash=request.request_hash, response=make_response(request.task.request))
    return SignedResponse(result=result, signature=key.sign(RESPONSE_DOMAIN + canonical(result)).hex())


def neuron(address: str, protocol: int = 4) -> Neuron:
    return Neuron.model_validate(
        {
            "uid": 1,
            "coldkey": MINER.ss58_address,
            "hotkey": MINER.ss58_address,
            "active": True,
            "axon_info": {"ip": address, "port": 8080, "protocol": protocol},
            "stake": 1,
            "rank": 0,
            "emission": 0,
            "incentive": 0,
            "consensus": 0,
            "trust": 0,
            "validator_trust": 0,
            "dividends": 0,
            "last_update": 0,
            "validator_permit": False,
            "pruning_score": 0,
            "stakes": {"alpha": 0, "tao": 0, "total": 0},
        }
    )


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "127.0.0.2", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "224.0.0.1"]
)
def test_discovery_and_transport_reject_internal_addresses(address: str) -> None:
    candidate = neuron(address)
    assert not public_http_neurons([candidate])
    with pytest.raises(ValueError, match="public HTTP"):
        target_url(candidate)


def test_public_endpoint_requires_http_without_redirects_or_proxy() -> None:
    candidate = neuron("8.8.8.8")
    assert public_http_neurons([candidate]) == [candidate]
    assert target_url(candidate) == "http://8.8.8.8:8080/v1/evaluate"
    with pytest.raises(ValueError):
        target_url(neuron("8.8.8.8", 0))


def test_requests_and_results_are_bound_to_both_hotkeys_chain_and_task() -> None:
    request = assignment()
    request.verify()
    assert answer(request).verify(request).task_id == request.task.request.task_id
    with pytest.raises(ValueError, match="signature"):
        answer(request, VALIDATOR).verify(request)
    for change in ({"netuid": 124}, {"chain_genesis": "0x" + "a" * 64}, {"miner_hotkey": VALIDATOR.ss58_address}):
        forged = request.model_copy(update={"task": request.task.model_copy(update=change)})
        with pytest.raises(ValueError, match="signature"):
            forged.verify()
        with pytest.raises(ValueError, match="binding"):
            answer(request).verify(forged)
    with pytest.raises(ValueError, match="binding"):
        answer(request).verify(assignment())


@pytest.mark.parametrize("kind", ["redirect", "oversize", "forgery", "compressed"])
def test_untrusted_responses_fail_closed(kind: str) -> None:
    request = assignment()
    calls: list[httpx.Request] = []

    def respond(incoming: httpx.Request) -> httpx.Response:
        calls.append(incoming)
        if kind == "redirect":
            return httpx.Response(307, headers={"location": "http://169.254.169.254/"})
        body = (
            b"x" * (MAX_EXCHANGE_BYTES + 1)
            if kind == "oversize"
            else answer(request, VALIDATOR).model_dump_json().encode()
        )
        return httpx.Response(
            200, stream=httpx.ByteStream(body), headers={"content-encoding": "gzip"} if kind == "compressed" else {}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises((ValueError, httpx.HTTPError)):
            exchange(client, "http://8.8.8.8/v1/evaluate", request)
    assert len(calls) == 1


def test_deadline_is_checked_before_network_access() -> None:
    signed = assignment()
    task = signed.task.model_copy(update={"request": make_request()})
    signed = SignedRequest.sign(task, VALIDATOR)
    with httpx.Client() as client, pytest.raises(httpx.TimeoutException):
        exchange(client, "http://8.8.8.8/v1/evaluate", signed)


def test_operator_config_defaults_to_testnet_568_and_rejects_other_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VALIDATOR_NETUID", raising=False)
    monkeypatch.delenv("VALIDATOR_NETWORK", raising=False)
    config = {
        "wallet_name": "operator",
        "hotkey_name": "default",
        "netuid": 123,
        "tempo": 360,
        "chain_genesis": GENESIS,
    }
    assert Settings.model_validate(config).mode == "synthetic"
    for change in (
        {"network": "finney"},
        {"network": "local"},
        {"chain_genesis": "0x" + "a" * 64},
        {"mode": "ping"},
        {"netuid": 0},
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate(config | change)
    del config["netuid"]
    defaults = Settings.model_validate(config)
    assert (defaults.network, defaults.netuid) == ("test", 568)


def test_chain_gate_rejects_wrong_subnet_absent_registration_and_revoked_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never use stale scores to reward through an invalid validator identity."""
    for name in ("SERVICE_ADDRESS", "OPEN_ACCESS_TOKEN", "IDENTITY_NAME", "IDENTITY_TOKEN"):
        monkeypatch.setenv(f"VALIDATOR_PYLON_{name}", "unit-test")
    client = MagicMock()
    client.__enter__.return_value = client

    def client_for_test(_: ChainSettings) -> MagicMock:
        return client

    monkeypatch.setattr(ChainSettings, "client", client_for_test)
    response = client.identity.get_recent_neurons.return_value
    client.identity.netuid = 124
    with pytest.raises(ValueError, match="another subnet"):
        registered_neurons(123, MINER.ss58_address)
    client.identity.netuid = 123
    response.neurons = {}
    with pytest.raises(ValueError, match="permit"):
        registered_neurons(123, MINER.ss58_address)
    candidate = neuron("8.8.8.8")
    response.neurons = {MINER.ss58_address: candidate}
    with pytest.raises(ValueError, match="permit"):
        registered_neurons(123, MINER.ss58_address)
    permitted = candidate.model_copy(update={"validator_permit": True})
    response.neurons = {MINER.ss58_address: permitted}
    assert registered_neurons(123, MINER.ss58_address) == [permitted]


def test_env_permissions_and_state_lock(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("HIPPIUS_SECRET_ACCESS_KEY=never-print-this\n")
    env.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        check_env_file(env)
    env.chmod(0o600)
    check_env_file(env)
    ledger = tmp_path / "ledger.sqlite3"
    with exclusive_state(ledger), pytest.raises(ValueError, match="Another validator"):
        with exclusive_state(ledger):
            pytest.fail("second process acquired the same journal")
    with exclusive_state(ledger):
        pass


@pytest.mark.parametrize("tamper_response", [False, True])
def test_real_http_verifies_signed_results_without_certificates(tamper_response: bool) -> None:
    """Exercise actual HTTP sockets and enforce signatures across the exchange."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            signed = SignedRequest.model_validate_json(self.rfile.read(int(self.headers["content-length"])))
            signed.verify()
            response = answer(signed)
            if tamper_response:
                response = response.model_copy(update={"signature": "0" * 128})
            body = response.model_dump_json().encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer((str(ip_address("127.0.0.1")), 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1/evaluate"
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                if tamper_response:
                    with pytest.raises(ValueError, match="signature"):
                        exchange(client, url, assignment())
                else:
                    request = assignment()
                    assert exchange(client, url, request).task_id == request.task.request.task_id
        finally:
            server.shutdown()
            thread.join(timeout=5)
