"""Run one synthetic RN miner with HTTP and hotkey-authenticated requests."""

import fcntl
import os
from functools import partial
from pathlib import Path

import click
import structlog
import uvicorn
from dotenv import load_dotenv
from validity_protocol.identity import load_signing_key

from validity_miner.config import Settings
from validity_miner.http import MinerApp
from validity_miner.service import MinerService, chain_admission


def server_config(app: MinerApp, settings: Settings) -> uvicorn.Config:
    """Serve signed HTTP without certificates; never honor forwarded scheme headers."""
    return uvicorn.Config(
        app,
        host=settings.bind_host,
        port=settings.port,
        lifespan="off",
        ws="none",
        proxy_headers=False,
        access_log=False,
        limit_concurrency=32,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=30,
        h11_max_incomplete_event_size=16 * 1024,
    )


@click.command()
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def main(env_file: Path | None) -> None:
    """Serve synthetic RN evaluations using this operator's private .env configuration.

    Raises:
        click.ClickException: If configuration, key access or exclusive state setup fails.
        ValueError: Caught and sanitized for insecure environment file permissions.
    """
    os.umask(0o077)
    structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
    try:
        selected = env_file or Path(".env")
        if selected.exists():
            if selected.stat().st_mode & 0o077:
                raise ValueError("The .env file must have mode 0600")
            load_dotenv(selected)
        settings = Settings.model_validate({})
        key = load_signing_key(settings.wallet_path, settings.wallet_name, settings.hotkey_name)
        settings.journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with settings.journal_path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            admission = partial(chain_admission, settings, key.ss58_address)
            service = MinerService(settings, key, admission)
            config = server_config(MinerApp(service), settings)
            config.load()
            structlog.get_logger().info(
                "Miner starting",
                hotkey=key.ss58_address,
                netuid=settings.netuid,
                scope="synthetic-rn",
                port=settings.port,
            )
            uvicorn.Server(config).run()
    except Exception as exc:
        raise click.ClickException(
            f"Miner startup failed ({type(exc).__name__}); check private operator configuration"
        ) from None


if __name__ == "__main__":
    main()
