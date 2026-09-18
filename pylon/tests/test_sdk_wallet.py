"""Offline compatibility checks against the real Pylon image and signing code."""

import hashlib
import json
import unittest
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bittensor.sp_core import Keypair, serialized_keypair_to_keyfile_data
from bittensor.wallet import Wallet
from litestar import Litestar
from pylon_service.bittensor.pool import WalletKey
from pylon_service.identities import Identity, settings
from pylon_service.main import app
from scalecodec import ScaleBytes
from turbobt.substrate.pallets.author import Author


class SDKWalletCompatibility(unittest.TestCase):
    """Use only public development keys, without chain access or operator files."""

    def setUp(self) -> None:
        directory = TemporaryDirectory(prefix="validity-pylon-sdk-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = Keypair.create_from_uri("//Alice")
        self.key_path = self.root / "default" / "hotkeys" / "default"
        self.key_path.parent.mkdir(parents=True)
        self.key_path.write_bytes(serialized_keypair_to_keyfile_data(self.key))
        self.key_path.chmod(0o400)
        public_key = Keypair(ss58_address=self.key.ss58_address)
        (self.root / "default" / "coldkeypub.txt").write_bytes(serialized_keypair_to_keyfile_data(public_key))
        self.original = self.key_path.read_bytes()
        setting_patch = patch.object(settings, "bittensor_wallet_path", str(self.root))
        setting_patch.start()
        self.addCleanup(setting_patch.stop)
        self.identity = Identity.model_validate(
            {
                "identity_name": "compatibility",
                "wallet_name": "default",
                "hotkey_name": "default",
                "netuid": 568,
                "token": "offline-test-identity-token",
            }
        )

    def tearDown(self) -> None:
        self.assertEqual(self.key_path.read_bytes(), self.original)
        self.assertFalse((self.root / "default" / "coldkey").exists())

    def test_pylon_identity_uses_current_sdk_and_unchanged_key_files(self) -> None:
        self.assertEqual(version("bittensor"), "11.1.0")
        self.assertEqual(version("turbobt"), "1.3.1+validity.sdk11")
        self.assertEqual(version("bittensor-pylon-service"), "2.3.2+validity.sdk11")
        with self.assertRaises(PackageNotFoundError):
            version("bittensor-wallet")
        self.assertIsInstance(app, Litestar)
        self.assertIsInstance(self.identity.wallet, Wallet)
        self.assertEqual(json.loads(self.original)["cryptoType"], 1)
        self.assertEqual(self.identity.wallet.hotkey.ss58_address, self.key.ss58_address)
        self.assertEqual(self.identity.wallet.coldkeypub.ss58_address, self.key.ss58_address)
        self.assertEqual(WalletKey.from_wallet(self.identity.wallet).path, str(self.root))
        message = b"validity.pylon-sdk.compatibility.v1"
        signature = self.identity.wallet.hotkey.sign(message)
        self.assertTrue(self.key.verify(message, signature))
        public_key = Keypair(ss58_address=self.key.ss58_address)
        self.assertTrue(public_key.verify(message, signature))
        self.assertFalse(public_key.verify(message + b"tampered", signature))

    def test_public_only_hotkey_cannot_sign(self) -> None:
        public_key = Keypair(ss58_address=self.key.ss58_address)
        self.key_path.chmod(0o600)
        self.key_path.write_bytes(serialized_keypair_to_keyfile_data(public_key))
        self.key_path.chmod(0o400)
        self.original = self.key_path.read_bytes()
        with self.assertRaises(ValueError):
            self.identity.wallet.hotkey.sign(b"must require a private key")

    def test_turbobt_transaction_signatures_with_sdk_key(self) -> None:
        # Exercise TurboBT's real signer, including its SCALE wrapper and >256-byte hash rule.
        # Stub only encoding/chain metadata; no RPC call or transaction submission occurs.
        for size in (32, 256, 257):
            with self.subTest(payload_bytes=size):
                payload_bytes = b"x" * size
                payload = MagicMock()
                payload.data = ScaleBytes(payload_bytes)
                extrinsic = MagicMock()
                substrate = MagicMock()
                substrate._metadata = [None, [None, {"extrinsic": {}}]]
                substrate._registry.create_scale_object.side_effect = [payload, extrinsic]
                call_fields = {
                    "call_module": "SubtensorModule",
                    "call_function": "set_weights",
                    "call_args": {"netuid": 568, "dests": [1], "weights": [65535], "version_key": 0},
                }
                call = SimpleNamespace(data=ScaleBytes(b"\x00\x01"), value=call_fields)
                result = Author(substrate)._sign(
                    call,
                    self.identity.wallet.hotkey,
                    nonce=7,
                    era="00",
                    block_hash="0x" + "11" * 32,
                    genesis_hash="0x" + "22" * 32,
                    runtime_version={"specVersion": 1, "transactionVersion": 1},
                )
                self.assertIs(result, extrinsic)
                encoded = extrinsic.encode.call_args.args[0]
                signature = bytes.fromhex(encoded["signature"].removeprefix("0x"))
                expected = hashlib.blake2b(payload_bytes, digest_size=32).digest() if size > 256 else payload_bytes
                self.assertTrue(self.key.verify(expected, signature))
                self.assertFalse(self.key.verify(expected + b"tampered", signature))
                self.assertEqual(encoded["signature_version"], 1)
                self.assertEqual(encoded["account_id"], "0x" + self.key.public_key.hex())
                self.assertEqual(encoded["nonce"], 7)
                self.assertEqual(encoded["call_args"], call_fields["call_args"])


if __name__ == "__main__":
    unittest.main()
