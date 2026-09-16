"""Read back scored localnet weight batches directly from subtensor."""

from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bittensor as bt
import click
from bittensor_wallet import Wallet

LOCALNET = Path(__file__).resolve().parent
PROFILES = (
    "honest",
    "slow",
    "stale",
    "unsafe",
    "timeout",
    "malformed",
    "duplicate",
    "late",
)


def expected_scores(version: str) -> dict[str, float]:
    """Independent, hand-calculated profile expectations for five equally weighted cases."""
    return {
        "honest": 1,
        "slow": 1,
        "duplicate": 1,
        "stale": 1 if version == "v1" else -1.0,
        "unsafe": -0.2,
        "timeout": 0,
        "malformed": 0,
        "late": 0,
    }


def verify(
    version: str,
    timeout: int,
    report: str,
    after_round: int = 0,
    *,
    validator_wallet: str = "validator",
    ledger_path: Path | None = None,
) -> None:
    """Require a complete expected round and a matching recent direct-chain weight row.

    Raises:
        click.ClickException: If the measured profile scores differ or confirmation times out.
    """
    database = ledger_path or LOCALNET / "state/credentials.sqlite3"
    deadline = time.monotonic() + timeout
    profiles = {
        Wallet(name=f"{profile}-1", path=str(LOCALNET / "wallets")).hotkey.ss58_address: profile for profile in PROFILES
    }
    validator_key = Wallet(name=validator_wallet, path=str(LOCALNET / "wallets")).hotkey.ss58_address
    last_status = "no queued batch"
    with bt.Subtensor(network="ws://127.0.0.1:9944") as chain:
        while time.monotonic() < deadline:
            with closing(sqlite3.connect(database)) as db:
                rows = db.execute(
                    "SELECT b.epoch,b.proposal_json,r.catalog_json,b.round_id,b.queued_at,b.prepared_block "
                    "FROM weight_batches b JOIN rounds r ON r.id=b.round_id "
                    "WHERE b.queued_at IS NOT NULL ORDER BY b.epoch DESC LIMIT 1"
                ).fetchall()
            if not rows:
                time.sleep(2)
                continue
            epoch, proposal_json, catalog_json, round_id, queued_at, prepared_block = rows[0]
            if round_id <= after_round:
                last_status = "waiting for a newly completed round"
                time.sleep(2)
                continue
            proposal: dict[str, Any] = json.loads(proposal_json)
            if json.loads(catalog_json)["label_version"] != f"fixture-labels-{version}":
                last_status = "waiting for a batch from the requested source version"
                time.sleep(2)
                continue
            scores = {profiles.get(hotkey, hotkey): value for hotkey, value in proposal["scores"].items()}
            expected = expected_scores(version)
            if set(scores) != set(expected) or any(
                not math.isclose(scores[key], value, abs_tol=1e-9) for key, value in expected.items()
            ):
                raise click.ClickException(f"Unexpected measured scores: {scores}; expected {expected}")
            block = chain.get_current_block()
            neurons = chain.neurons_lite(2, block=block)
            validator = next(n for n in neurons if n.hotkey == validator_key)
            actual_roster = {n.hotkey: n.uid for n in neurons if n.hotkey in profiles}
            if (
                actual_roster != proposal["roster"]
                or not validator.validator_permit
                or not chain.is_subnet_active(2, block=block)
            ):
                raise click.ClickException("Current chain roster or validator permit differs from the scored context")
            raw = dict(chain.weights(2, block=block)).get(validator.uid, [])
            total = sum(value for _, value in raw)
            actual = {int(uid): value / total for uid, value in raw if value > 0} if total else {}
            expected_weights = {actual_roster[hotkey]: float(value) for hotkey, value in proposal["weights"].items()}
            matches = set(actual) == set(expected_weights) and all(
                math.isclose(actual[uid], value, abs_tol=2 / 65535) for uid, value in expected_weights.items()
            )
            if not matches or validator.last_update <= max(epoch - 1, prepared_block):
                last_status = f"waiting for chain confirmation: block={block}, actual={actual}"
                time.sleep(2)
                continue
            with closing(sqlite3.connect(database)) as db, db:
                coverage = db.execute(
                    "SELECT s.hotkey,count(*),count(DISTINCT s.case_index) FROM round_slots s "
                    "JOIN assignments a ON a.task_id=s.task_id WHERE s.round_id=? "
                    "AND a.outcome IS NOT NULL GROUP BY s.hotkey",
                    (round_id,),
                ).fetchall()
                if len(coverage) != len(PROFILES) or any(count != 5 or cases != 5 for _, count, cases in coverage):
                    raise click.ClickException("Incomplete or duplicate case coverage")
                db.execute(
                    "UPDATE weight_batches SET confirmed_block=?,chain_weights_json=? WHERE epoch=?",
                    (block, json.dumps(raw), epoch),
                )
            output = {
                "checked_at": datetime.now(UTC).isoformat(),
                "source_version": version,
                "round_id": round_id,
                "epoch_first_block": epoch,
                "queued_at": queued_at,
                "chain_block": block,
                "validator_uid": validator.uid,
                "validator_wallet": validator_wallet,
                "validator_hotkey": validator_key,
                "validator_last_update": validator.last_update,
                "scores": scores,
                "cases_per_miner": 5,
                "chain_weights": actual,
                "profiles_by_uid": {actual_roster[key]: name for key, name in profiles.items()},
                "direct_chain_readback": True,
            }
            suffix = "" if validator_wallet == "validator" else f"-{validator_wallet}"
            path = LOCALNET / "state" / f"{report}{suffix}.json"
            path.write_text(json.dumps(output, indent=2) + "\n")
            click.echo(json.dumps(output, indent=2))
            return
    raise click.ClickException(f"MVP confirmation timed out: {last_status}")


@click.group()
def main() -> None:
    """Control fictional sources and verify the scored subnet loop."""


@main.command("verify")
@click.option("--version", type=click.Choice(["v1", "v2"]), required=True)
@click.option("--timeout", type=click.IntRange(1, 900), default=300)
@click.option("--after-round", type=click.IntRange(0), default=0)
@click.option("--validator-wallet", type=click.Choice(["validator", "validator-2", "validator-3"]), default="validator")
@click.option("--ledger-path", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option(
    "--report",
    default="mvp-report",
    type=click.Choice(["mvp-report", "baseline", "transition", "recovery", "score-reporting", "hippius"]),
)
def verify_command(
    version: str,
    timeout: int,
    report: str,
    after_round: int,
    validator_wallet: str,
    ledger_path: Path | None,
) -> None:
    """Wait for profile scores and matching weights, then save a local report."""
    verify(version, timeout, report, after_round, validator_wallet=validator_wallet, ledger_path=ledger_path)


@main.command("source")
@click.argument("version", type=click.Choice(["v1", "v2"]))
def source_command(version: str) -> None:
    """Atomically switch the fictional board and independently reviewed labels."""
    pointer = LOCALNET / "fixtures/active-version"
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(version + "\n")
    temporary.replace(pointer)
    click.echo(f"Active fictional source: {version}")


@main.command("restart-validator")
def restart_validator() -> None:
    """Restart the running validator and verify durable terminal results and interrupted work.

    Raises:
        click.ClickException: If no pending work is observed or recovery invariants fail.
    """
    database = LOCALNET / "state/credentials.sqlite3"
    deadline = time.monotonic() + 45
    pending: list[str] = []
    completed: list[tuple[Any, ...]] = []
    while time.monotonic() < deadline:
        with closing(sqlite3.connect(database)) as db:
            pending = [str(row[0]) for row in db.execute("SELECT task_id FROM assignments WHERE outcome IS NULL")]
            completed = db.execute(
                "SELECT task_id,outcome,response_json,completed_at FROM assignments "
                "WHERE outcome IS NOT NULL ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
            latest_round = int(db.execute("SELECT coalesce(max(id),0) FROM rounds").fetchone()[0])
        if pending:
            break
        time.sleep(0.5)
    else:
        raise click.ClickException("No in-flight work found to exercise recovery")
    command = (
        "UV_CACHE_DIR=/tmp/validity-uv-cache uv run --frozen validator --env-file ../localnet/.env "
        "2>&1 | tee ../localnet/logs/validator.log"
    )
    subprocess.run(
        [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            "localnet:main.1",
            "-c",
            str(LOCALNET.parent / "validator"),
            command,
        ],
        check=True,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        with closing(sqlite3.connect(database)) as db:
            interrupted = [
                task
                for task in pending
                if db.execute("SELECT outcome FROM assignments WHERE task_id=?", (task,)).fetchone()[0] == "interrupted"
            ]
            preserved = all(
                db.execute(
                    "SELECT task_id,outcome,response_json,completed_at FROM assignments WHERE task_id=?",
                    (row[0],),
                ).fetchone()
                == row
                for row in completed
            )
        if not preserved:
            raise click.ClickException("A completed result changed across restart")
        if interrupted:
            output = {
                "checked_at": datetime.now(UTC).isoformat(),
                "completed_preserved": len(completed),
                "interrupted_tasks": interrupted,
                "round_before_restart": latest_round,
            }
            (LOCALNET / "state/restart-report.json").write_text(json.dumps(output, indent=2) + "\n")
            click.echo(json.dumps(output, indent=2))
            return
        time.sleep(0.5)
    raise click.ClickException("Validator did not recover in-flight assignments")


@main.command("restart-miner")
@click.argument("profile", type=click.Choice(["honest", "slow", "duplicate"]))
def restart_miner(profile: str) -> None:
    """Stop a correct miner, require a failed assigned task, and verify service recovery.

    Raises:
        click.ClickException: If no failed assignment or successful recovered response is observed.
    """
    hotkey = Wallet(name=f"{profile}-1", path=str(LOCALNET / "wallets")).hotkey.ss58_address
    pane = "localnet:main.0" if profile == "honest" else f"localnet:{profile}.0"
    database = LOCALNET / "state/credentials.sqlite3"
    stopped_at = datetime.now(UTC).isoformat()
    failed_task: str | None = None
    subprocess.run(["tmux", "respawn-pane", "-k", "-t", pane, "bash"], check=True)
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            with closing(sqlite3.connect(database)) as db:
                row = db.execute(
                    "SELECT task_id FROM assignments WHERE miner_hotkey=? "
                    "AND json_extract(request_json,'$.issued_at')>=? "
                    "AND outcome='transport_error' ORDER BY rowid DESC LIMIT 1",
                    (hotkey, stopped_at),
                ).fetchone()
            if row:
                failed_task = str(row[0])
                break
            time.sleep(1)
    finally:
        command = (
            f"UV_CACHE_DIR=/tmp/validity-uv-cache PYTHONUNBUFFERED=1 uv run --frozen python "
            f"../localnet/miners/miner-{profile}.py 2>&1 | tee ../localnet/logs/miner-{profile}.log"
        )
        subprocess.run(
            ["tmux", "respawn-pane", "-k", "-t", pane, "-c", str(LOCALNET.parent / "miner"), command], check=True
        )
    if failed_task is None:
        raise click.ClickException("No assigned miner failure observed during the outage")
    subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(LOCALNET.parent / "miner"),
            "python",
            str(LOCALNET / "check.py"),
            "wait-miner",
            "--profile",
            profile,
        ],
        check=True,
    )
    subprocess.run(["docker", "compose", "restart", "pylon"], cwd=LOCALNET, check=True)
    recovered_since = datetime.now(UTC).isoformat()
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        with closing(sqlite3.connect(database)) as db:
            row = db.execute(
                "SELECT task_id FROM assignments WHERE miner_hotkey=? AND outcome='verified' "
                "AND completed_at>=? ORDER BY rowid DESC LIMIT 1",
                (hotkey, recovered_since),
            ).fetchone()
            latest_round = int(db.execute("SELECT max(id) FROM rounds").fetchone()[0])
        if row:
            output = {
                "checked_at": datetime.now(UTC).isoformat(),
                "profile": profile,
                "failed_task": failed_task,
                "recovered_task": str(row[0]),
                "round_at_recovery": latest_round,
            }
            (LOCALNET / "state/miner-restart-report.json").write_text(json.dumps(output, indent=2) + "\n")
            click.echo(json.dumps(output, indent=2))
            return
        time.sleep(1)
    raise click.ClickException("Restarted miner did not return a verified response")


if __name__ == "__main__":
    main()
