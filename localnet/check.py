"""Direct-chain readiness checks for local RN miner fixtures."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

import bittensor as bt
import click
import httpx
from bittensor_wallet import Wallet

LOCALNET = Path(__file__).resolve().parent
NETUID = 2


def local_hotkey(name: str) -> str:
    """Read the requested local wallet, without creating or exposing private keys."""
    return Wallet(name=name, path=str(LOCALNET / "wallets")).hotkey.ss58_address


def wait_for_miner(timeout: int, profile: str = "honest") -> None:
    """Require the chain's advertised endpoint to serve this fixture's health marker.

    Raises:
        click.ClickException: If the fixture fails to become ready.
    """
    deadline = time.monotonic() + timeout
    with (
        bt.Subtensor(network="ws://127.0.0.1:9944") as chain,
        httpx.Client(trust_env=False, timeout=2) as client,
    ):
        while time.monotonic() < deadline:
            wallet_file = LOCALNET / f"wallets/{profile}-1/hotkeys/default"
            if wallet_file.exists():
                hotkey = local_hotkey(f"{profile}-1")
                neurons = chain.neurons_lite(NETUID)
                miner = next((neuron for neuron in neurons if neuron.hotkey == hotkey), None)
                if miner and miner.axon_info and miner.axon_info.ip == "127.0.0.2" and miner.axon_info.port > 0:
                    try:
                        response = client.get(f"http://127.0.0.2:{miner.axon_info.port}/health")
                        if response.status_code == 200 and response.json() == {"profile": f"validity-{profile}-v1"}:
                            click.echo(f"Fixture ready on chain: uid={miner.uid}, port={miner.axon_info.port}")
                            return
                    except httpx.HTTPError:
                        pass
            time.sleep(2)
    raise click.ClickException("Fixture readiness timed out; inspect localnet/logs/miner.log")


@click.command()
@click.argument("mode", type=click.Choice(["wait-miner"]))
@click.option("--profile", default="honest")
@click.option(
    "--timeout",
    default=120,
    type=click.IntRange(1, 600),
    help="Maximum wait in seconds.",
)
def main(mode: Literal["wait-miner"], timeout: int, profile: str) -> None:
    """Check a fixture endpoint against its current chain registration."""
    wait_for_miner(timeout, profile)


if __name__ == "__main__":
    main()
