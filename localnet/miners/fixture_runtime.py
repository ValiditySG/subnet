# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "bittensor",
#     "bittensor-wallet",
#     "litestar[standard]",
#     "httpx",
#     "click",
# ]
# ///
"""
Shared Validity miner fixture runtime. Fictional RN records only.

Models miners accepting work from a validator via HTTP POST,
then POSTing the result back to the validator via a callback URL.

Usage: uv run --frozen --project miner python localnet/miners/miner-honest.py [-n NUM_INSTANCES]
"""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import random
import socket
import sys
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import bittensor as bt
import httpx
import uvicorn
from bittensor.utils.balance import Balance
from bittensor_wallet import Keypair, Wallet
from litestar import Litestar, Response, get, post
from litestar.background_tasks import BackgroundTask
from pydantic import BaseModel

miner_profile = "honest"
PORT_RANGE = (10000, 65000)

# Must match the validator's AsyncHttpNeuronCommunicator target_path
TARGET_PATH = "/task"

WALLETS_DIR = Path(__file__).parent.parent / "wallets"
SUBTENSOR_NETWORK = "ws://127.0.0.1:9944"
NETUID = 2
FUND_AMOUNT_TAO = 1000.0


# ---------------------------------------------------------------------------
# Local asynchronous callback envelopes
# ---------------------------------------------------------------------------


class RequestEnvelope(BaseModel):
    request_id: str
    callback_url: str
    input: dict[str, Any]


class ResponseEnvelope(BaseModel):
    request_id: str
    output: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Independent fixture board resolver
# ---------------------------------------------------------------------------


class Query(BaseModel):
    """Public fictional query; contains no expected result."""

    provider_ref: str
    profession: Literal["RN"]
    license_number: str
    jurisdiction: Literal["ZZ-TEST"]


class Task(BaseModel):
    """Only the fields used by this intentionally small fixture resolver."""

    protocol_version: Literal["validity.localnet.v1"]
    task_id: str
    provider_query: Query
    checks: tuple[Literal["fixture_license"], ...]


class BoardRecord(BaseModel):
    record_id: str
    license_number: str
    status: Literal["active", "suspended"]


class Board(BaseModel):
    source_version: Literal["fixture-board-v1", "fixture-board-v2"]
    profession: Literal["RN"]
    unavailable_licenses: list[str]
    records: list[BoardRecord]


def run_task(input_data: dict[str, Any]) -> dict[str, Any]:
    """Resolve fixture records without reading validator labels or scorer code.

    Raises:
        ValueError: If the active source version is unsupported.
    """
    task = Task.model_validate(input_data)
    directory = Path(__file__).parent.parent / "fixtures"
    pointer = directory / "active-version"
    version = pointer.read_text().strip() if pointer.exists() else "v1"
    if miner_profile == "stale":
        version = "v1"
    if version not in ("v1", "v2"):
        raise ValueError("Unknown fixture source version")
    raw = (directory / f"board-{version}.json").read_bytes()
    board = Board.model_validate_json(raw)
    license_number = task.provider_query.license_number
    available = license_number not in board.unavailable_licenses
    records = [record for record in board.records if record.license_number == license_number] if available else []
    if not available:
        identity = "unresolved"
    elif not records:
        identity = "not_found"
    else:
        identity = "matched" if len(records) == 1 else "ambiguous"
    evidence = (
        {
            "source": "fixture-board",
            "source_version": board.source_version,
            "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
            "record_ids": [record.record_id for record in records],
        }
        if available
        else None
    )
    return {
        "protocol_version": task.protocol_version,
        "task_id": task.task_id,
        "provider_ref": task.provider_query.provider_ref,
        "identity_resolution": identity,
        "checks": [
            {
                "check": "fixture_license",
                "availability": "available" if available else "unavailable",
                "finding": ("active" if miner_profile == "unsafe" else records[0].status)
                if len(records) == 1
                else None,
                "evidence": evidence,
            }
        ],
    }


# ---------------------------------------------------------------------------
# HTTP endpoint
#
# The validator expects a fast 2xx ACK on this path
# (within send_timeout, ~1s) and then the actual result POSTed back to
# data.callback_url. So: ACK immediately, then run the task in the
# background and POST the response envelope from there.
# ---------------------------------------------------------------------------


async def _post_response(callback_url: str, response: ResponseEnvelope) -> None:
    target = urlparse(callback_url)
    if target.scheme != "http" or target.hostname != "127.0.0.1" or target.path != "/callback":
        raise ValueError("The fixture only calls local validator callback endpoints")
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            result = await client.post(callback_url, json=response.model_dump())
            result.raise_for_status()
            print(f"[miner] Responded to {response.request_id}")
        except Exception as exc:
            print(f"[miner] Failed to callback for {response.request_id}: {exc}")


async def _process_in_background(data: RequestEnvelope) -> None:
    behavior = miner_profile
    if behavior == "timeout":
        return
    if behavior in ("slow", "late"):
        await asyncio.sleep(0.5 if behavior == "slow" else 12)
    try:
        output = await asyncio.to_thread(run_task, data.input)
        if behavior == "malformed":
            output.pop("checks", None)
        response = ResponseEnvelope(request_id=data.request_id, output=output)
    except Exception as exc:
        response = ResponseEnvelope(request_id=data.request_id, error=str(exc))
    await _post_response(data.callback_url, response)
    if behavior == "duplicate":
        await _post_response(data.callback_url, response)


@post(TARGET_PATH, status_code=202)
async def ack_task(data: RequestEnvelope) -> Response[None]:
    print(f"[miner] Received request {data.request_id}")
    return Response(
        content=None,
        status_code=202,
        background=BackgroundTask(_process_in_background, data),
    )


# ---------------------------------------------------------------------------
# Self-registration and serving
# ---------------------------------------------------------------------------


def connect_subtensor() -> bt.Subtensor:
    for attempt in range(20):
        try:
            subtensor = bt.Subtensor(network=SUBTENSOR_NETWORK)
            subtensor.get_current_block()
            return subtensor
        except Exception:
            print(f"[miner] Waiting for subtensor... ({attempt + 1}/20)")
            time.sleep(2)
    print("[miner] Could not connect to subtensor")
    sys.exit(1)


def get_alice_wallet() -> Wallet:
    """Create a wallet backed by Alice's well-known devnet keypair."""
    alice_kp = Keypair.create_from_uri("//Alice")
    wallet = Wallet(name="alice", path=str(WALLETS_DIR))
    wallet.set_coldkey(keypair=alice_kp, encrypt=False, overwrite=True)
    wallet.set_coldkeypub(keypair=alice_kp, overwrite=True)
    wallet.set_hotkey(keypair=alice_kp, encrypt=False, overwrite=True)
    return wallet


def find_free_port() -> int:
    """Find a free port by trying random ports in the range."""
    lo, hi = PORT_RANGE
    while True:
        port = random.randint(lo, hi)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port


def setup_and_serve(instance_name: str) -> None:
    global miner_profile
    miner_profile = instance_name.rsplit("-", 1)[0]
    """Idempotent setup then serve. Runs in its own process."""
    port = find_free_port()
    print(f"[{instance_name}] Starting on port {port}...")
    subtensor = connect_subtensor()

    wallet = Wallet(name=instance_name, path=str(WALLETS_DIR))
    wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)

    # Fund from Alice if needed (retry — concurrent transfers from Alice get temporarily banned)
    balance = subtensor.get_balance(wallet.coldkey.ss58_address)
    if balance < Balance.from_tao(10.0):
        alice = get_alice_wallet()
        for attempt in range(5):
            print(f"[{instance_name}] Funding from Alice... (attempt {attempt + 1}/5)")
            response = subtensor.transfer(
                wallet=alice,
                destination_ss58=wallet.coldkey.ss58_address,
                amount=Balance.from_tao(FUND_AMOUNT_TAO),
                wait_for_inclusion=True,
                wait_for_finalization=True,
                mev_protection=False,
            )
            if response.success:
                break
            print(f"[{instance_name}] Funding failed: {response.message}, retrying...")
            time.sleep(3 + attempt * 2)
        else:
            print(f"[{instance_name}] Funding failed after 5 attempts")
            sys.exit(1)

    # Register on subnet (retry — same nonce contention can happen here)
    if not subtensor.is_hotkey_registered(wallet.hotkey.ss58_address, NETUID):
        for attempt in range(5):
            print(f"[{instance_name}] Registering on subnet {NETUID}... (attempt {attempt + 1}/5)")
            response = subtensor.burned_register(
                wallet=wallet,
                netuid=NETUID,
                wait_for_inclusion=True,
                wait_for_finalization=True,
                mev_protection=False,
            )
            if response.success:
                break
            print(f"[{instance_name}] Registration failed: {response.message}, retrying...")
            time.sleep(3 + attempt * 2)
        else:
            print(f"[{instance_name}] Registration failed after 5 attempts")
            sys.exit(1)
    else:
        print(f"[{instance_name}] Already registered")

    print(f"[{instance_name}] Setting axon info: 127.0.0.2:{port}")
    subtensor.serve_axon(
        netuid=NETUID,
        axon=bt.Axon(wallet=wallet, port=port, ip="127.0.0.2", external_ip="127.0.0.2"),
    )

    print(f"[{instance_name}] Serving on 127.0.0.2:{port}{TARGET_PATH}")
    app = Litestar(route_handlers=[ack_task, health])
    uvicorn.run(app, host="127.0.0.2", port=port, log_level="info")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@get("/health")
async def health() -> dict[str, str]:
    """Let local startup distinguish this fixture from a stale echo process."""
    return {"profile": f"validity-{miner_profile}-v1"}


def run(profile: str, count: int) -> None:
    if count == 1:
        setup_and_serve(f"{profile}-1")
        return

    processes: list[multiprocessing.Process] = []
    for i in range(count):
        instance_name = f"{profile}-{i + 1}"
        proc = multiprocessing.Process(target=setup_and_serve, args=(instance_name,))
        proc.start()
        processes.append(proc)

    try:
        for proc in processes:
            proc.join()
    except KeyboardInterrupt:
        for proc in processes:
            proc.terminate()
