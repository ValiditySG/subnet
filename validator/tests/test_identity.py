"""SDK wallet compatibility and signing boundaries, without operator key material."""

import json
from pathlib import Path

import pytest
from bittensor.sp_core import Keypair, serialized_keypair_to_keyfile_data, ss58_encode
from validity_protocol.identity import is_valid_ss58_address, load_signing_key


def write_key(tmp_path: Path, key: Keypair) -> Path:
    directory = tmp_path / "default" / "hotkeys"
    directory.mkdir(parents=True)
    path = directory / "default"
    path.write_bytes(serialized_keypair_to_keyfile_data(key))
    path.chmod(0o600)
    return path


def test_current_wallet_format_loads_without_rewriting_keys(tmp_path: Path) -> None:
    key = Keypair.create_from_uri("//Alice")
    path = write_key(tmp_path, key)
    original = path.read_bytes()
    assert json.loads(original)["cryptoType"] == 1
    loaded = load_signing_key(tmp_path, "default", "default")
    assert loaded.ss58_address == key.ss58_address
    assert key.verify(b"SDK compatibility", loaded.sign(b"SDK compatibility"))
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["public_only", "ed25519"])
def test_startup_rejects_non_signing_and_wrong_algorithm_keys(tmp_path: Path, kind: str) -> None:
    key = (
        Keypair(ss58_address=Keypair.create_from_uri("//Alice").ss58_address)
        if kind == "public_only"
        else Keypair.create_from_uri("//Alice", crypto_type=0)
    )
    path = write_key(tmp_path, key)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        load_signing_key(tmp_path, "default", "default")
    assert path.read_bytes() == original


def test_ss58_validation_checks_checksum_and_bittensor_network_prefix() -> None:
    key = Keypair.create_from_uri("//Alice")
    assert is_valid_ss58_address(key.ss58_address)
    assert not is_valid_ss58_address(ss58_encode(key.public_key, 0))
    assert not is_valid_ss58_address(key.ss58_address[:-1] + "Z")
    assert not is_valid_ss58_address("not-a-hotkey")
