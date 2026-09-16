"""Versioned local fixture contract, independent of the Nexus callback envelope."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

type Identity = Literal["matched", "ambiguous", "not_found", "unresolved"]
type Availability = Literal["available", "unavailable"]
type Finding = Literal["active", "suspended"]
type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type RecordId = Annotated[str, Field(pattern=r"^fixture-record-[0-9]+$")]


class WireModel(BaseModel):
    """Reject unknown fields rather than silently accepting a different contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderQuery(WireModel):
    """Fictional identifiers that cannot be mistaken for a supported real board."""

    provider_ref: str = Field(pattern=r"^fictional-[0-9]+$")
    profession: Literal["RN"]
    license_number: str = Field(pattern=r"^TEST-[0-9]+$")
    jurisdiction: Literal["ZZ-TEST"] = "ZZ-TEST"


class CredentialRequest(WireModel):
    """One local license lookup with an absolute UTC deadline."""

    protocol_version: Literal["validity.localnet.v1"] = "validity.localnet.v1"
    task_id: UUID
    provider_query: ProviderQuery
    checks: tuple[Literal["fixture_license"], ...] = Field(min_length=1, max_length=1)
    issued_at: AwareDatetime
    deadline_at: AwareDatetime

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.issued_at.utcoffset() or self.deadline_at.utcoffset():
            raise ValueError("timestamps must use UTC")
        if self.deadline_at <= self.issued_at:
            raise ValueError("deadline must follow issue time")
        return self


class Evidence(WireModel):
    """The fixture snapshot and records supporting an observation."""

    source: Literal["fixture-board"] = "fixture-board"
    source_version: Literal["fixture-board-v1", "fixture-board-v2"] = "fixture-board-v1"
    snapshot_sha256: Digest
    record_ids: tuple[RecordId, ...] = Field(max_length=4)


class CheckResult(WireModel):
    """Availability is separate from the license finding."""

    check: Literal["fixture_license"] = "fixture_license"
    availability: Availability
    finding: Finding | None
    evidence: Evidence | None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.availability == "unavailable" and (self.finding is not None or self.evidence is not None):
            raise ValueError("unavailable sources cannot support findings or evidence")
        if self.availability == "available" and self.evidence is None:
            raise ValueError("available results require evidence")
        return self


class CredentialResponse(WireModel):
    """Untrusted miner output; request binding and truth are checked separately."""

    protocol_version: Literal["validity.localnet.v1"] = "validity.localnet.v1"
    task_id: UUID
    provider_ref: str = Field(pattern=r"^fictional-[0-9]+$")
    identity_resolution: Identity
    checks: tuple[CheckResult, ...] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        check = self.checks[0]
        if check.availability == "unavailable" and self.identity_resolution != "unresolved":
            raise ValueError("source unavailability leaves identity unresolved")
        if check.availability == "available" and self.identity_resolution == "unresolved":
            raise ValueError("available fixture evidence must resolve the candidate set")
        if self.identity_resolution == "matched" and check.finding is None:
            raise ValueError("a matched fixture record requires a finding")
        if self.identity_resolution != "matched" and any(check.finding is not None for check in self.checks):
            raise ValueError("unresolved identities cannot support a license finding")
        return self
