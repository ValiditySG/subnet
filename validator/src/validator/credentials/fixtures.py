"""Validator-owned labels, kept off the miner request and separate from its resolver."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from validator.credentials.protocol import (
    Availability,
    CredentialRequest,
    CredentialResponse,
    Digest,
    Finding,
    Identity,
    ProviderQuery,
    RecordId,
    WireModel,
)


class ExpectedCase(WireModel):
    """A reviewed local answer; never serialized into the task payload."""

    provider_query: ProviderQuery
    identity_resolution: Identity
    availability: Availability
    finding: Finding | None
    record_ids: tuple[RecordId, ...]


class FixtureCatalog(WireModel):
    """Version and evidence digest pinned with independent expected outcomes."""

    label_version: Literal["fixture-labels-v1", "fixture-labels-v2"] = "fixture-labels-v1"
    snapshot_sha256: Digest
    cases: tuple[ExpectedCase, ...] = Field(min_length=1)

    @classmethod
    def load(cls, directory: Path) -> FixtureCatalog:
        """Read only labels; the validator does not run the miner's board resolver.

        Raises:
            ValueError: If the active fixture version is unsupported.
        """
        pointer = directory / "active-version"
        version = pointer.read_text().strip() if pointer.exists() else "v1"
        if version not in ("v1", "v2"):
            raise ValueError(f"Unsupported fixture version: {version}")
        return cls.model_validate_json((directory / f"expected-{version}.json").read_bytes())


def rejection_reason(
    request: CredentialRequest,
    response: CredentialResponse,
    expected: ExpectedCase,
    snapshot_sha256: str,
    observed_at: datetime,
    source_version: str = "fixture-board-v1",
) -> str | None:
    """Check task binding, completeness, identity, source evidence and deadline."""
    if observed_at > request.deadline_at:
        return "deadline_exceeded"
    if response.task_id != request.task_id:
        return "task_mismatch"
    if response.provider_ref != request.provider_query.provider_ref:
        return "provider_mismatch"
    if tuple(check.check for check in response.checks) != request.checks:
        return "checks_mismatch"
    if response.identity_resolution != expected.identity_resolution:
        return "identity_mismatch"
    check = response.checks[0]
    if (check.availability, check.finding) != (expected.availability, expected.finding):
        return "finding_mismatch"
    if expected.availability == "available":
        evidence = check.evidence
        if evidence is None or evidence.snapshot_sha256 != snapshot_sha256:
            return "evidence_digest_mismatch"
        if evidence.source_version != source_version:
            return "evidence_version_mismatch"
        if sorted(evidence.record_ids) != sorted(expected.record_ids):
            return "evidence_records_mismatch"
    return None
