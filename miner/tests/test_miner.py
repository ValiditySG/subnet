"""Independent resolution, durable replay protection and authenticated HTTP interoperability."""

import asyncio
import json
import socket
import time
from collections.abc import Awaitable, Callable, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
import pytest
import uvicorn
from bittensor.sp_core import Keypair
from pydantic import SecretStr
from validity_protocol.credentials import CredentialRequest, ProviderQuery
from validity_protocol.exchange import TESTNET_GENESIS, SignedRequest, SignedResponse, TaskBinding

from validity_miner.config import Settings
from validity_miner.http import MinerApp
from validity_miner.journal import RateLimited, ReplayConflict
from validity_miner.main import server_config
from validity_miner.service import AdmissionDenied, ExpiredRequest, MinerService, chain_admission

MINER = Keypair.create_from_uri("//Bob")
VALIDATOR = Keypair.create_from_uri("//Alice")
type Message = MutableMapping[str, Any]
type ASGIApplication = Callable[
    [Message, Callable[[], Awaitable[Message]], Callable[[Message], Awaitable[None]]], Awaitable[None]
]


def settings_at(path: Path, version: Literal["v1", "v2"] = "v1") -> Settings:
    return Settings(
        netuid=123,
        chain_genesis=TESTNET_GENESIS,
        wallet_name="operator",
        hotkey_name="default",
        allowed_validators=frozenset([VALIDATOR.ss58_address]),
        pylon_address="http://private-sidecar:8000",
        pylon_open_access_token=SecretStr("test-token"),
        bind_host="127.0.0.1",
        journal_path=path / "requests.sqlite3",
        source_version=version,
    )


def admitted(_: str) -> None:
    """Chain access is replaced only in automated tests."""


def request_for(index: int = 1, now: datetime | None = None) -> SignedRequest:
    now = now or datetime.now(UTC)
    request = CredentialRequest(
        task_id=uuid4(),
        provider_query=ProviderQuery(
            provider_ref=f"fictional-{1000 + index}", profession="RN", license_number=f"TEST-{1000 + index}"
        ),
        checks=("fixture_license",),
        issued_at=now,
        deadline_at=now + timedelta(seconds=30),
    )
    return SignedRequest.sign(
        TaskBinding(
            chain_genesis=TESTNET_GENESIS,
            netuid=123,
            validator_hotkey=VALIDATOR.ss58_address,
            miner_hotkey=MINER.ss58_address,
            request=request,
        ),
        VALIDATOR,
    )


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_resolver_matches_independent_validator_labels(tmp_path: Path, version: Literal["v1", "v2"]) -> None:
    service = MinerService(settings_at(tmp_path, version), MINER, admitted)
    source = Path(__file__).parents[2] / "validator/src/validator/synthetic" / f"expected-{version}.json"
    expected = json.loads(source.read_bytes())
    for case in expected["cases"]:
        request = request_for()
        payload = request.task.request.model_copy(
            update={"provider_query": ProviderQuery.model_validate(case["provider_query"])}
        )
        signed = SignedRequest.sign(request.task.model_copy(update={"request": payload}), VALIDATOR)
        response = SignedResponse.model_validate_json(
            service.evaluate(signed.model_dump_json().encode(), datetime.now(UTC))
        ).verify(signed)
        assert response.identity_resolution == case["identity_resolution"]
        assert response.checks[0].availability == case["availability"]
        assert response.checks[0].finding == case["finding"]
        evidence = response.checks[0].evidence
        if evidence:
            assert evidence.snapshot_sha256 == expected["snapshot_sha256"]
            assert list(evidence.record_ids) == case["record_ids"]


def test_retry_concurrency_and_restart_preserve_exact_response(tmp_path: Path) -> None:
    settings = settings_at(tmp_path)
    service = MinerService(settings, MINER, admitted)
    signed = request_for()
    body = signed.model_dump_json().encode()
    now = datetime.now(UTC)

    def evaluate(_: int) -> bytes:
        return service.evaluate(body, now)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(evaluate, range(8)))
    assert all(response == results[0] for response in results)
    reopened = MinerService(settings, MINER, admitted)
    assert reopened.evaluate(body, now) == results[0]
    other = signed.task.request.model_copy(update={"provider_query": request_for(2).task.request.provider_query})
    conflicting = SignedRequest.sign(signed.task.model_copy(update={"request": other}), VALIDATOR)
    with pytest.raises(ReplayConflict):
        reopened.evaluate(conflicting.model_dump_json().encode(), now)
    with pytest.raises(ValueError, match="different identity"):
        MinerService(settings, VALIDATOR, admitted)


def test_forged_and_wrong_context_requests_never_reach_admission(tmp_path: Path) -> None:
    checked: list[str] = []
    service = MinerService(settings_at(tmp_path), MINER, checked.append)
    request = request_for()
    forged = request.model_copy(update={"signature": "0" * 128})
    with pytest.raises(ValueError):
        service.evaluate(forged.model_dump_json().encode(), datetime.now(UTC))
    for change in ({"netuid": 124}, {"chain_genesis": "0x" + "a" * 64}, {"miner_hotkey": VALIDATOR.ss58_address}):
        signed = SignedRequest.sign(request.task.model_copy(update=change), VALIDATOR)
        with pytest.raises(AdmissionDenied):
            service.evaluate(signed.model_dump_json().encode(), datetime.now(UTC))
    assert checked == []


def test_expiry_future_requests_revoked_permits_and_quota(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    settings = settings_at(tmp_path).model_copy(update={"requests_per_minute": 1})
    service = MinerService(settings, MINER, admitted)
    for date in (now - timedelta(minutes=1), now + timedelta(minutes=1)):
        with pytest.raises(ExpiredRequest):
            service.evaluate(request_for(now=date).model_dump_json().encode(), now)
    signed = request_for(now=now)
    body = signed.model_dump_json().encode()
    response = service.evaluate(body, now)
    assert service.evaluate(body, now) == response
    with pytest.raises(RateLimited):
        service.evaluate(request_for(2, now).model_dump_json().encode(), now)

    def revoked(_: str) -> None:
        raise AdmissionDenied("permit revoked")

    service.admission = revoked
    with pytest.raises(AdmissionDenied):
        service.evaluate(body, now)


def test_http_boundary_and_signed_response(tmp_path: Path) -> None:
    app = MinerApp(MinerService(settings_at(tmp_path), MINER, admitted))

    async def exercise() -> None:
        # Uvicorn models ASGI events with TypedDicts; httpx uses mutable mappings.
        transport = httpx.ASGITransport(app=cast(ASGIApplication, app))
        async with httpx.AsyncClient(transport=transport, base_url="http://miner") as client:
            signed = request_for()
            response = await client.post("/v1/evaluate", content=signed.model_dump_json())
            assert response.status_code == 200
            SignedResponse.model_validate_json(response.content).verify(signed)
            assert (await client.post("/v1/evaluate", content=b"x" * 65537)).status_code == 413
            assert (
                await client.post("/v1/evaluate", content=b"{}", headers={"content-encoding": "gzip"})
            ).status_code == 400
            assert (await client.post("/v1/evaluate", content=b"{}")).status_code == 400
            assert (await client.get("/v1/evaluate")).status_code == 405
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/unknown")).status_code == 404

    asyncio.run(exercise())


@pytest.mark.parametrize("missing", ["none", "miner", "validator", "permit", "outage"])
def test_chain_admission_uses_read_only_sidecar_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    settings = settings_at(tmp_path)
    neurons: dict[str, dict[str, object]] = {}
    for uid, (role, key) in enumerate((("miner", MINER), ("validator", VALIDATOR))):
        if missing == role:
            continue
        neurons[key.ss58_address] = {
            "uid": uid,
            "coldkey": key.ss58_address,
            "hotkey": key.ss58_address,
            "active": True,
            "axon_info": {"ip": "8.8.8.8", "port": 8080, "protocol": 4},
            "stake": 1,
            "rank": 0,
            "emission": 0,
            "incentive": 0,
            "consensus": 0,
            "trust": 0,
            "validator_trust": 0,
            "dividends": 0,
            "last_update": 0,
            "validator_permit": role == "validator" and missing != "permit",
            "pruning_score": 0,
            "stakes": {"alpha": 0, "tao": 0, "total": 0},
        }

    def send(_: httpx.Client, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "123" in request.url.path and "latest" in request.url.path
        assert request.headers["Authorization"] == "Bearer test-token"
        if missing == "outage":
            return httpx.Response(503, json={"detail": "unavailable"}, request=request)
        return httpx.Response(
            200,
            json={
                "block": {"number": 100, "hash": "0x" + "a" * 64},
                "neurons": neurons,
            },
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    if missing == "none":
        chain_admission(settings, MINER.ss58_address, VALIDATOR.ss58_address)
    elif missing == "outage":
        with pytest.raises(Exception, match="Invalid response"):
            chain_admission(settings, MINER.ss58_address, VALIDATOR.ss58_address)
    else:
        with pytest.raises(AdmissionDenied):
            chain_admission(settings, MINER.ss58_address, VALIDATOR.ss58_address)


def test_real_http_authenticates_requests_without_certificates(tmp_path: Path) -> None:
    settings = settings_at(tmp_path)
    admitted_hotkeys: list[str] = []
    app = MinerApp(MinerService(settings, MINER, admitted_hotkeys.append))
    config = server_config(app, settings)
    assert not config.is_ssl and not config.proxy_headers
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(32)
        server = uvicorn.Server(config)
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            end = time.monotonic() + 5
            while not server.started and time.monotonic() < end:
                time.sleep(0.01)
            assert server.started
            url = f"http://127.0.0.1:{listener.getsockname()[1]}/v1/evaluate"
            with httpx.Client(trust_env=False) as client:
                assert client.get(url.replace("/v1/evaluate", "/health")).status_code == 200
                assert client.post(url, content=b"{}").status_code == 400
                forged = request_for().model_copy(update={"signature": "0" * 128})
                assert client.post(url, content=forged.model_dump_json()).status_code == 400
                wrong = SignedRequest.sign(request_for().task.model_copy(update={"netuid": 124}), VALIDATOR)
                assert client.post(url, content=wrong.model_dump_json()).status_code == 403
                assert admitted_hotkeys == []
                signed = request_for()
                result = client.post(url, content=signed.model_dump_json())
                assert result.status_code == 200
                assert SignedResponse.model_validate_json(result.content).verify(signed).checks[0].finding == "active"
                assert admitted_hotkeys == [VALIDATOR.ss58_address]
        finally:
            server.should_exit = True
            thread.join(timeout=5)
