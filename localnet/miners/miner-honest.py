# /// script
# requires-python = ">=3.14"
# dependencies = ["bittensor", "bittensor-wallet", "litestar[standard]", "httpx", "click"]
# ///
"""Validity honest local RN fixture; fictional records only."""

import click
from fixture_runtime import run

MINER_NAME = "honest"


@click.command()
@click.option("-n", "count", type=click.IntRange(1, 20), default=1)
def main(count: int) -> None:
    """Start independently registered instances of this fixture profile."""
    run(MINER_NAME, count)


if __name__ == "__main__":
    main()
