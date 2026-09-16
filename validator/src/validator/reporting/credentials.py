"""Load one team's private ACL credential profile without exposing secret values in errors."""

from __future__ import annotations

from configparser import ConfigParser
from configparser import Error as ConfigError
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, RootModel, SecretStr, ValidationError


class StorageCredentials(BaseModel):
    """S3 credential pair, with values redacted from model representations."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    aws_access_key_id: SecretStr = Field(
        min_length=1, validation_alias=AliasChoices("aws_access_key_id", "access-key-id")
    )
    aws_secret_access_key: SecretStr = Field(
        min_length=1, validation_alias=AliasChoices("aws_secret_access_key", "secret-access-key")
    )
    aws_session_token: SecretStr | None = None


class CredentialProfiles(BaseModel):
    """Named profiles in the private JSON credential file."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    profiles: dict[str, StorageCredentials] = Field(min_length=1)


class FlatCredentialProfiles(RootModel[dict[str, StorageCredentials]]):
    """Accept a profile-name mapping without a surrounding profiles object."""

    model_config = ConfigDict(hide_input_in_errors=True)
    root: dict[str, StorageCredentials] = Field(min_length=1)


class JsonCredentials(RootModel[CredentialProfiles | FlatCredentialProfiles]):
    """The wrapped and flat JSON file layouts both select profiles explicitly."""

    model_config = ConfigDict(hide_input_in_errors=True)


def load_credentials(path: Path, profile: str) -> StorageCredentials:
    """Select explicit JSON or AWS INI credentials, never an ambient fallback.

    Raises:
        ValueError: If the file/profile is missing or malformed; messages omit input contents.
    """
    if not path.is_file() or not profile:
        raise ValueError("A credential file requires an existing file and an explicit profile")
    if path.suffix.lower() == ".json":
        try:
            document = JsonCredentials.model_validate_json(path.read_bytes()).root
        except ValidationError:
            raise ValueError(
                "Invalid JSON credential file; require named profiles with S3 access key ID and secret"
            ) from None
        profiles = document.profiles if isinstance(document, CredentialProfiles) else document.root
        selected = profiles.get(profile)
        if selected is None:
            raise ValueError("Selected credential profile is missing")
        return selected
    parser = ConfigParser(interpolation=None)
    try:
        with path.open() as source:
            parser.read_file(source)
        access = parser.get(profile, "aws_access_key_id", fallback=None)
        secret = parser.get(profile, "aws_secret_access_key", fallback=None)
        if not access or not secret:
            raise ValueError("Selected profile requires an S3 access key ID and secret access key")
        token = parser.get(profile, "aws_session_token", fallback=None)
    except ConfigError:
        raise ValueError("Invalid AWS credential file") from None
    return StorageCredentials(
        aws_access_key_id=SecretStr(access),
        aws_secret_access_key=SecretStr(secret),
        aws_session_token=SecretStr(token) if token else None,
    )
