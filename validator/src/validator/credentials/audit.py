"""Replay a completed reward window from its persisted requests and observations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import click

from validator.credentials.evaluation import POLICY, ROSTER, SCORES, score_response
from validator.credentials.fixtures import ExpectedCase, FixtureCatalog
from validator.credentials.protocol import CredentialRequest, CredentialResponse


def replay(path: Path, round_id: int) -> dict[str, float]:
    """Recompute scores from immutable observations and reject inconsistent stored summaries.

    Raises:
        ValueError: If the round is incomplete, belongs to another policy, or fails replay.
    """
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        row = db.execute(
            "SELECT status,policy,catalog_json,roster_json,scores_json FROM rounds WHERE id=?", (round_id,)
        ).fetchone()
        slots = db.execute(
            "SELECT s.hotkey,s.case_index,a.request_json,a.expected_json,a.response_json,a.completed_at,a.outcome "
            "FROM round_slots s JOIN assignments a ON a.task_id=s.task_id "
            "WHERE s.round_id=? ORDER BY s.case_index,s.hotkey",
            (round_id,),
        ).fetchall()
    if row is None or row[0] != "complete" or row[1] != POLICY:
        raise ValueError("Replay requires a completed round using the current policy")
    catalog = FixtureCatalog.model_validate_json(row[2])
    scores = dict.fromkeys(ROSTER.validate_json(row[3]), 0.0)
    if len(slots) != len(scores) * len(catalog.cases):
        raise ValueError("Incomplete scored slots")
    for hotkey, index, request_json, expected_json, response_json, completed_at, outcome in slots:
        expected = ExpectedCase.model_validate_json(expected_json)
        if outcome in (None, "interrupted") or expected != catalog.cases[int(index)]:
            raise ValueError("Assignment truth or terminal status differs from the scored window")
        request = CredentialRequest.model_validate_json(request_json)
        if request.provider_query != expected.provider_query:
            raise ValueError("Assignment query differs from its expected case")
        response = CredentialResponse.model_validate_json(response_json) if response_json else None
        scores[str(hotkey)] += score_response(
            request, response, expected, catalog, datetime.fromisoformat(completed_at), str(outcome)
        ) / len(catalog.cases)
    if scores != SCORES.validate_json(row[4]):
        raise ValueError("Replayed scores differ from the persisted summary")
    return scores


@click.command()
@click.argument("database", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("round_id", type=click.IntRange(1))
def main(database: Path, round_id: int) -> None:
    """Read a completed round without changing the ledger and verify exact score replay."""
    click.echo(
        json.dumps({"round_id": round_id, "scores": replay(database, round_id), "replay_matches": True}, indent=2)
    )


if __name__ == "__main__":
    main()
