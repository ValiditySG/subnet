"""Private Hippius S3 access and an explicit local-only storage substitute."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.session import Session as CredentialSession

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

from validator.reporting.credentials import load_credentials
from validator.reporting.protocol import MAX_REPORT_BYTES


@dataclass(frozen=True)
class ObjectPage:
    """One bounded storage listing; tokens are opaque to callers."""

    keys: tuple[str, ...]
    next_token: str | None


class ReportStore(Protocol):
    """Minimal object operations required by the score service."""

    def read(self, key: str) -> bytes | None: ...
    def write(self, key: str, data: bytes) -> None: ...
    def list_page(self, prefix: str, token: str | None, limit: int) -> ObjectPage: ...


class HippiusStore:
    """Use SigV4, the decentralized region and path addressing without changing bucket ACLs."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def connect(
        cls,
        bucket: str,
        profile: str | None = None,
        credentials_file: Path | None = None,
        timeout_seconds: float = 10,
    ) -> HippiusStore:
        """Load a dedicated ACL profile without changing another client's credential environment.

        Raises:
            ValueError: If a specified credential file or profile is missing required S3 credentials.
        """
        if credentials_file is not None:
            if not profile:
                raise ValueError("A credential file requires an existing file and an explicit profile")
            credentials = load_credentials(credentials_file, profile)
            # File profiles are selected by our loader, independently of ambient AWS_PROFILE settings.
            session = CredentialSession(session_vars={"profile": (None, None, None, None)})
            session.set_credentials(
                credentials.aws_access_key_id.get_secret_value(),
                credentials.aws_secret_access_key.get_secret_value(),
                credentials.aws_session_token.get_secret_value() if credentials.aws_session_token else None,
            )
        else:
            session = CredentialSession(profile=profile)
        client = Session(botocore_session=session).client(
            "s3",
            endpoint_url="https://s3.hippius.com",
            region_name="decentralized",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3,
                read_timeout=timeout_seconds,
                retries={"mode": "standard", "total_max_attempts": 2},
            ),
        )
        return cls(cast("S3Client", client), bucket)

    def read(self, key: str) -> bytes | None:
        """Read a bounded report; missing objects return None.

        Raises:
            ClientError: If storage fails for a reason other than a missing object.
            ValueError: If the object exceeds the protocol limit.
        """
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        body = result["Body"]
        try:
            data = body.read(MAX_REPORT_BYTES + 1)
        finally:
            body.close()
        if len(data) > MAX_REPORT_BYTES:
            raise ValueError("Stored report exceeds the protocol limit")
        return data

    def write(self, key: str, data: bytes) -> None:
        """Store JSON without granting public access or requiring unsupported conditional PUTs."""
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="application/json")

    def list_page(self, prefix: str, token: str | None, limit: int) -> ObjectPage:
        """List one page below a server-derived epoch prefix."""
        if token:
            result = self.client.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, MaxKeys=limit, ContinuationToken=token
            )
        else:
            result = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=limit)
        return ObjectPage(
            tuple(item["Key"] for item in result.get("Contents", []) if "Key" in item),
            result.get("NextContinuationToken"),
        )


class LocalReportStore:
    """Localnet test substitute; this is not a Hippius upload or durability claim."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def read(self, key: str) -> bytes | None:
        """Read bounded local objects using the same size limit as the S3 adapter.

        Raises:
            ValueError: If a local object exceeds the protocol limit.
        """
        path = self.directory / key
        if not path.exists():
            return None
        with path.open("rb") as source:
            data = source.read(MAX_REPORT_BYTES + 1)
        if len(data) > MAX_REPORT_BYTES:
            raise ValueError("Stored report exceeds the protocol limit")
        return data

    def write(self, key: str, data: bytes) -> None:
        path = self.directory / key
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4()}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def list_page(self, prefix: str, token: str | None, limit: int) -> ObjectPage:
        keys = sorted(str(path.relative_to(self.directory)) for path in (self.directory / prefix).rglob("*.json"))
        remaining = [key for key in keys if token is None or key > token]
        page = remaining[:limit]
        return ObjectPage(tuple(page), page[-1] if len(remaining) > limit else None)
