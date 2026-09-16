"""Canonical hotkey-signed exchanges shared by both participants."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from bittensor_wallet import Keypair
from pydantic import Field

from validity_protocol.credentials import CredentialRequest, CredentialResponse, Digest, WireModel

type ChainId = Annotated[str, Field(pattern=r"^0x[0-9a-f]{64}$")]
type HotkeyAddress = Annotated[str, Field(min_length=47, max_length=48)]
MAX_EXCHANGE_BYTES = 64 * 1024
REQUEST_DOMAIN = b"validity.rn-request.v1\n"
RESPONSE_DOMAIN = b"validity.rn-response.v1\n"
TESTNET_GENESIS = "0x8f9cf856bf558a14440e75569c9e58594757048d7b3a84b5d25f6bd978263105"


def canonical(model: WireModel) -> bytes:
    """Canonical JSON shared by independent miner implementations."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class TaskBinding(WireModel):
    """Bind the assignment to a chain and both participant hotkeys."""

    version: Literal["validity.rn-exchange.v1"] = "validity.rn-exchange.v1"
    chain_genesis: ChainId
    netuid: int = Field(ge=1, le=65535)
    validator_hotkey: HotkeyAddress
    miner_hotkey: HotkeyAddress
    request: CredentialRequest


class SignedRequest(WireModel):
    """The validator signature authenticates the complete assignment."""

    task: TaskBinding
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(canonical(self.task)).hexdigest()

    def verify(self) -> None:
        """Verify the validator signature.

        Raises:
            ValueError: If the assignment has been altered or impersonated.
        """
        if not Keypair(ss58_address=self.task.validator_hotkey).verify(
            REQUEST_DOMAIN + canonical(self.task), bytes.fromhex(self.signature)
        ):
            raise ValueError("Invalid assignment signature")

    @classmethod
    def sign(cls, task: TaskBinding, key: Keypair) -> SignedRequest:
        """Sign only assignments belonging to this hotkey.

        Raises:
            ValueError: If the signing identity differs.
        """
        if key.ss58_address != task.validator_hotkey or key.crypto_type != 1:
            raise ValueError("Assignment signing identity differs")
        return cls(task=task, signature=key.sign(REQUEST_DOMAIN + canonical(task)).hex())


class BoundResponse(WireModel):
    """The request hash prevents reuse across tasks, validators, subnets and chains."""

    request_hash: Digest
    response: CredentialResponse


class SignedResponse(WireModel):
    """The miner signs its result, including the complete assignment's digest."""

    result: BoundResponse
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    def verify(self, request: SignedRequest) -> CredentialResponse:
        """Authenticate the expected miner before accepting its response.

        Raises:
            ValueError: If binding or miner signature verification fails.
        """
        response = self.result.response
        if (
            self.result.request_hash != request.request_hash
            or response.task_id != request.task.request.task_id
            or response.provider_ref != request.task.request.provider_query.provider_ref
        ):
            raise ValueError("Response assignment binding differs")
        if not Keypair(ss58_address=request.task.miner_hotkey).verify(
            RESPONSE_DOMAIN + canonical(self.result), bytes.fromhex(self.signature)
        ):
            raise ValueError("Invalid miner signature")
        return response
