"""Explicit per-operator ACL credentials loaded from .env, with redacted errors."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageCredentials(BaseSettings):
    """No file profiles, shared token bundle, or ambient AWS credential fallback."""

    model_config = SettingsConfigDict(env_prefix="HIPPIUS_", extra="ignore", hide_input_in_errors=True)
    access_key_id: SecretStr = Field(min_length=1)
    secret_access_key: SecretStr = Field(min_length=1)
