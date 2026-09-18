"""Versioned, hotkey-signed summaries without provider identifiers or evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self
from uuid import UUID

from bittensor.sp_core import Keypair
from pydantic import AwareDatetime, Field, field_validator, model_validator
from validity_protocol.identity import is_valid_ss58_address

from validator.credentials.protocol import Digest, WireModel

type ChainId = Annotated[str, Field(pattern=r"^0x[0-9a-f]{64}$")]
type HotkeyAddress = Annotated[str, Field(min_length=47, max_length=48)]
DOMAIN = b"validity.score-report.v1\n"
MAX_REPORT_BYTES = 256 * 1024
OBJECT_LAYOUT = "hotkey-epoch-v1"


class MinerScore(WireModel):
    """Integer totals preserve the exact raw score before reward normalization."""

    miner_hotkey: HotkeyAddress
    miner_uid: int = Field(ge=0, le=65535)
    score_sum: int
    assigned_tasks: int = Field(ge=1, le=100000)
    correct_tasks: int = Field(ge=0)

    @property
    def score(self) -> float:
        """Return the unnormalized mean evaluation score."""
        return self.score_sum / self.assigned_tasks

    @model_validator(mode="after")
    def check_totals(self) -> Self:
        if not is_valid_ss58_address(self.miner_hotkey):
            raise ValueError("Invalid miner hotkey")
        if (
            self.correct_tasks > self.assigned_tasks
            or not self.correct_tasks - 5 * (self.assigned_tasks - self.correct_tasks)
            <= self.score_sum
            <= self.correct_tasks
        ):
            raise ValueError("Score totals are outside the RN policy bounds")
        return self


class ScoreReport(WireModel):
    """One completed synthetic RN round, grouped by chain epoch and evaluation context."""

    schema_version: Literal["validity.score-report.v1"] = "validity.score-report.v1"
    scope: Literal["synthetic-rn"] = "synthetic-rn"
    chain_genesis: ChainId
    netuid: int = Field(ge=1, le=65535)
    validator_hotkey: HotkeyAddress
    instance_id: UUID
    round_id: int = Field(ge=1)
    epoch_start: int = Field(ge=0)
    epoch_end: int = Field(ge=0)
    completed_block: int = Field(ge=1)
    policy_version: Literal["rn-equal-cases-v1"] = "rn-equal-cases-v1"
    source_version: str = Field(pattern=r"^fixture-board-v[12]$")
    source_digest: Digest
    started_at: AwareDatetime
    completed_at: AwareDatetime
    miners: tuple[MinerScore, ...] = Field(min_length=1, max_length=256)

    @field_validator("started_at", "completed_at")
    @classmethod
    def utc_only(cls, value: AwareDatetime) -> AwareDatetime:
        if value.utcoffset():
            raise ValueError("Report timestamps must use UTC")
        return value

    @model_validator(mode="after")
    def check_context(self) -> Self:
        if not is_valid_ss58_address(self.validator_hotkey):
            raise ValueError("Invalid validator hotkey")
        if not self.epoch_start <= self.completed_block <= self.epoch_end:
            raise ValueError("Completion block is outside the evaluation epoch")
        if self.completed_at < self.started_at:
            raise ValueError("Completion precedes the round start")
        hotkeys = [miner.miner_hotkey for miner in self.miners]
        if hotkeys != sorted(set(hotkeys)) or len({miner.miner_uid for miner in self.miners}) != len(self.miners):
            raise ValueError("Report miners must have unique UIDs and sorted, unique hotkeys")
        return self

    def canonical_bytes(self) -> bytes:
        """Encode sorted compact JSON; signed scores use integer totals, never floats."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()

    @property
    def report_id(self) -> str:
        """Content identifier for the canonical unsigned report."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def window_id(self) -> str:
        """Group only reports sharing a chain, subnet, epoch, policy and source."""
        return f"{self.chain_genesis}/{self.netuid}/{self.epoch_start}/{self.policy_version}/{self.source_digest}"


class SignedScoreReport(WireModel):
    """A report authenticated independently of the storage account."""

    report: ScoreReport
    report_id: Digest
    signature_algorithm: Literal["sr25519"] = "sr25519"
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    def verify(self) -> None:
        """Reject changed payloads and signatures that do not match the claimed validator.

        Raises:
            ValueError: If the report ID or signature is invalid.
        """
        if self.report_id != self.report.report_id:
            raise ValueError("Report digest mismatch")
        key = Keypair(ss58_address=self.report.validator_hotkey)
        if not key.verify(DOMAIN + self.report.canonical_bytes(), bytes.fromhex(self.signature)):
            raise ValueError("Invalid validator signature")

    @classmethod
    def sign(cls, report: ScoreReport, key: Keypair) -> SignedScoreReport:
        """Sign with the validator hotkey; private key material never enters the report.

        Raises:
            ValueError: If the signer is not the report's validator.
        """
        if key.ss58_address != report.validator_hotkey or key.crypto_type != 1:
            raise ValueError("Signer does not match validator hotkey")
        signed = cls(
            report=report,
            report_id=report.report_id,
            signature=key.sign(DOMAIN + report.canonical_bytes()).hex(),
        )
        signed.verify()
        return signed

    @property
    def object_key(self) -> str:
        """One signed snapshot containing every miner's score for this validator and epoch."""
        return f"{self.report.validator_hotkey}/{self.report.epoch_start}.json"


class UploadReceipt(WireModel):
    """Distinguish upload acceptance from verified readback; neither confirms chain weights."""

    report_id: Digest
    object_key: str
    stored_at: AwareDatetime
    verification: Literal["upload_accepted", "readback_verified"] = "readback_verified"
