"""Fail-closed operator configuration for the synthetic testnet release."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SYNTHETIC_DATA = Path(__file__).parent / "synthetic"


class WalletSettings(BaseSettings):
    """One signing identity per operator; wallet files remain read-only mounts."""

    model_config = SettingsConfigDict(
        env_prefix="VALIDATOR_", extra="ignore", hide_input_in_errors=True, populate_by_name=True
    )
    wallet_path: Path = Path("~/.bittensor/wallets")
    wallet_name: str = Field(default="default", min_length=1)
    hotkey_name: str = Field(default="default", min_length=1)

    @field_validator("wallet_path")
    @classmethod
    def expand_wallet_path(cls, path: Path) -> Path:
        """Resolve home-relative paths for every wallet consumer."""
        return path.expanduser()


class Settings(WalletSettings):
    """Synthetic evaluation is an explicit release scope, never real RN verification."""

    network: Literal["test"] = "test"
    netuid: int = Field(default=568, ge=1, le=65535)
    chain_genesis: Literal["0x8f9cf856bf558a14440e75569c9e58594757048d7b3a84b5d25f6bd978263105"]
    mode: Literal["synthetic"] = "synthetic"
    fixture_dir: Path = SYNTHETIC_DATA
    ledger_path: Path = Path("/var/lib/validity/credentials.sqlite3")
    total_processing_timeout: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0), le=timedelta(minutes=2))
    max_score_age: timedelta = Field(default=timedelta(minutes=30), gt=timedelta(0))
    tempo: int = Field(ge=20)
