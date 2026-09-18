"""Resolve public fictional board records independently of validator scoring labels."""

from hashlib import sha256
from pathlib import Path
from typing import Literal

from validity_protocol.credentials import (
    CheckResult,
    CredentialRequest,
    CredentialResponse,
    Evidence,
    Finding,
    Identity,
    RecordId,
    WireModel,
)


class BoardRecord(WireModel):
    record_id: RecordId
    license_number: str
    status: Finding


class Board(WireModel):
    source_version: Literal["fixture-board-v1", "fixture-board-v2"]
    profession: Literal["RN"]
    unavailable_licenses: tuple[str, ...]
    records: tuple[BoardRecord, ...]


class SyntheticResolver:
    """Pin the source snapshot at startup; never read validator expected-answer files."""

    def __init__(self, version: Literal["v1", "v2"]) -> None:
        raw = (Path(__file__).parent / "synthetic" / f"board-{version}.json").read_bytes()
        self.board = Board.model_validate_json(raw)
        self.digest = sha256(raw).hexdigest()

    def resolve(self, request: CredentialRequest) -> CredentialResponse:
        license_number = request.provider_query.license_number
        available = license_number not in self.board.unavailable_licenses
        records = tuple(r for r in self.board.records if r.license_number == license_number) if available else ()
        identity: Identity
        if not available:
            identity = "unresolved"
        elif not records:
            identity = "not_found"
        else:
            identity = "matched" if len(records) == 1 else "ambiguous"
        evidence = (
            Evidence(
                source_version=self.board.source_version,
                snapshot_sha256=self.digest,
                record_ids=tuple(r.record_id for r in records),
            )
            if available
            else None
        )
        return CredentialResponse(
            task_id=request.task_id,
            provider_ref=request.provider_query.provider_ref,
            identity_resolution=identity,
            checks=(
                CheckResult(
                    availability="available" if available else "unavailable",
                    finding=records[0].status if len(records) == 1 else None,
                    evidence=evidence,
                ),
            ),
        )
