"""Automated test doubles only; no deployed local object store or upload gateway."""

from pathlib import Path
from uuid import uuid4

import httpx

from validator.reporting.protocol import MAX_REPORT_BYTES, SignedScoreReport, UploadReceipt
from validator.reporting.service import ScoreService
from validator.reporting.storage import ObjectPage


class LocalReportStore:
    """Filesystem test double for isolated transport regressions."""

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


class GatewayUploader:
    """Explicit development transport retained for local harness tests only."""

    def __init__(self, client: httpx.Client, gateway_url: str) -> None:
        self.client = client
        self.url = f"{gateway_url.rstrip('/')}/v1/reports"

    def upload(self, signed: SignedScoreReport) -> UploadReceipt:
        response = self.client.post(
            self.url,
            content=signed.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return UploadReceipt.model_validate_json(response.content)

    def close(self) -> None:
        self.client.close()


class ServiceUploader:
    """In-process test transport; no production upload server."""

    def __init__(self, service: ScoreService) -> None:
        self.service = service

    def upload(self, signed: SignedScoreReport) -> UploadReceipt:
        return self.service.upload(signed.model_dump_json().encode())

    def close(self) -> None:
        pass
