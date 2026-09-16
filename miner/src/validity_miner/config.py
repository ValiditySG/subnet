"""Explicit per-operator settings for the synthetic testnet miner."""

from pathlib import Path
from typing import Literal

from bittensor_wallet.utils import is_valid_ss58_address
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service credentials live in a private .env; wallet keys are read-only mounts."""

    model_config = SettingsConfigDict(env_prefix="MINER_", extra="ignore", hide_input_in_errors=True)
    network: Literal["test"] = "test"
    chain_genesis: Literal["0x8f9cf856bf558a14440e75569c9e58594757048d7b3a84b5d25f6bd978263105"]
    netuid: int = Field(default=568, ge=1, le=65535)
    wallet_path: Path = Path("/wallets")
    wallet_name: str = Field(min_length=1)
    hotkey_name: str = Field(min_length=1)
    allowed_validators: frozenset[str] = Field(min_length=1)
    pylon_address: str
    pylon_open_access_token: SecretStr = Field(min_length=1)
    bind_host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1024, le=65535)
    source_version: Literal["v1", "v2"] = "v1"
    journal_path: Path = Path("/var/lib/validity-miner/requests.sqlite3")
    requests_per_minute: int = Field(default=120, ge=1, le=1000)

    @field_validator("allowed_validators")
    @classmethod
    def valid_hotkeys(cls, values: frozenset[str]) -> frozenset[str]:
        if not all(is_valid_ss58_address(value) for value in values):
            raise ValueError("Allowed validators must be valid hotkey addresses")
        return values
