"""Read-only operator checks before enabling evaluation or chain weight submission."""

from __future__ import annotations

from pathlib import Path

import click
from bittensor_wallet import Wallet
from dotenv import load_dotenv

from validator.chain import registered_neurons
from validator.config import Settings
from validator.credentials.fixtures import FixtureCatalog
from validator.credentials.pipeline import public_http_neurons
from validator.operator import check_env_file
from validator.reporting.credentials import StorageCredentials
from validator.reporting.publisher import ReportingSettings
from validator.reporting.storage import HippiusStore


@click.command()
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def main(env_file: Path | None) -> None:
    """Check identity, registration and read access without writing reports or weights.

    Raises:
        click.ClickException: If any check fails, with no secret input in the error.
    """
    try:
        if env_file is not None:
            check_env_file(env_file)
            load_dotenv(env_file)
        settings = Settings.model_validate({})
        reporting = ReportingSettings.model_validate({})
        credentials = StorageCredentials.model_validate({})
        FixtureCatalog.load(settings.fixture_dir)
        key = Wallet(
            path=str(settings.wallet_path), name=settings.wallet_name, hotkey=settings.hotkey_name
        ).get_hotkey()
        neurons = registered_neurons(settings.netuid, key.ss58_address)
        store = HippiusStore.connect(reporting.bucket, credentials)
        try:
            store.read(f"{key.ss58_address}/0.json")
        finally:
            store.client.close()
    except Exception as exc:
        raise click.ClickException(
            f"Preflight failed ({type(exc).__name__}); check private operator configuration"
        ) from None
    click.echo(
        f"Preflight passed: netuid={settings.netuid}, validator={key.ss58_address}, "
        f"public_endpoints={len(public_http_neurons(neurons))}, scope=synthetic-rn"
    )


if __name__ == "__main__":
    main()
