"""Bittensor address validation using the SDK's native cryptographic primitives."""

from pathlib import Path

from bittensor.sp_core import CRYPTO_SR25519, Keypair, ss58_decode, ss58_encode
from bittensor.wallet import Wallet


def is_valid_ss58_address(value: str) -> bool:
    """Accept canonical Bittensor account addresses with a valid checksum."""
    try:
        return ss58_encode(ss58_decode(value), 42) == value
    except ValueError:
        return False


def load_signing_key(path: Path, name: str, hotkey: str) -> Keypair:
    """Load an existing SR25519 hotkey and prove signing access before startup.

    Raises:
        ValueError: If the key uses another algorithm or cannot sign and verify.
    """
    key = Wallet(path=str(path), name=name, hotkey=hotkey).get_hotkey()
    if key.crypto_type != CRYPTO_SR25519:
        raise ValueError("Validity requires an SR25519 signing hotkey")
    message = b"validity.hotkey-readiness.v1"
    if not key.verify(message, key.sign(message)):
        raise ValueError("Hotkey signing verification failed")
    return key
