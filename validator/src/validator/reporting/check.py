"""Read real Hippius objects and verify a window contains all expected validator signatures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click
from dotenv import load_dotenv
from pydantic import AwareDatetime

from validator.credentials.protocol import WireModel
from validator.reporting.gateway import GatewaySettings
from validator.reporting.service import ScoreService
from validator.reporting.storage import HippiusStore


class VerifiedReports(WireModel):
    """Readback evidence for a complete signature set, without claiming chain-weight verification."""

    checked_at: AwareDatetime
    backend: str = "hippius"
    bucket: str
    window_id: str
    validator_hotkeys: tuple[str, ...]
    report_ids: tuple[str, ...]


def verify_window(service: ScoreService, epoch_start: int, bucket: str) -> VerifiedReports:
    """Require all configured validators in one source/policy window after verified storage reads.

    Raises:
        ValueError: If pagination repeats or a complete set of matching validator reports is absent.
    """
    windows: dict[str, dict[str, set[str]]] = {}
    token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        page = service.read_page(epoch_start, token)
        for signed in page.reports:
            authors = windows.setdefault(signed.report.window_id, {})
            authors.setdefault(signed.report.validator_hotkey, set()).add(signed.report_id)
        token = page.next_token
        if token is None:
            break
        if token in seen_tokens:
            raise ValueError("Storage listing repeated its continuation token")
        seen_tokens.add(token)
    complete = sorted(window for window, authors in windows.items() if frozenset(authors) == service.validators)
    if not complete:
        raise ValueError("No matching window contains verified reports from every configured validator")
    selected = complete[-1]
    return VerifiedReports(
        checked_at=datetime.now(UTC),
        bucket=bucket,
        window_id=selected,
        validator_hotkeys=tuple(sorted(windows[selected])),
        report_ids=tuple(sorted(report_id for ids in windows[selected].values() for report_id in ids)),
    )


@click.command()
@click.option("--env-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--epoch-start", required=True, type=click.IntRange(0))
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), default=None)
def main(env_file: Path, epoch_start: int, output: Path | None) -> None:
    """Verify all three test validators from the real bucket using a READ/LIST credential profile.

    Raises:
        click.ClickException: If configuration or real Hippius readback cannot establish the expected set.
        ValueError: Caught and reported as a CLI failure for incomplete reader configuration.
    """
    load_dotenv(env_file)
    try:
        settings = GatewaySettings.model_validate({})
        if settings.backend != "hippius" or not settings.bucket or len(settings.validators) != 3:
            raise ValueError("Require Hippius backend, bucket and exactly three expected hotkeys")
        if settings.credentials_file is None or not settings.profile:
            raise ValueError("Require an explicit reader credential file and profile")
        store = HippiusStore.connect(settings.bucket, settings.profile, settings.credentials_file)
        try:
            service = ScoreService(store, settings.chain_genesis, settings.netuid, settings.validators)
            result = verify_window(service, epoch_start, settings.bucket)
        finally:
            store.client.close()
    except Exception as exc:
        raise click.ClickException(
            f"Hippius readback did not pass ({type(exc).__name__}); check reader configuration and reports"
        ) from None
    data = result.model_dump_json(indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(data + "\n")
    click.echo(data)


if __name__ == "__main__":
    main()
