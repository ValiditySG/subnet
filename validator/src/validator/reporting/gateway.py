"""Localnet signed score upload/read HTTP service; the bucket stays private."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast, override
from urllib.parse import parse_qs, urlsplit

import click
import structlog
from bittensor_wallet.utils import is_valid_ss58_address
from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from validator.logging_config import LoggingSettings, configure_logging
from validator.reporting.protocol import MAX_REPORT_BYTES, ChainId
from validator.reporting.service import ScoreService
from validator.reporting.storage import HippiusStore, LocalReportStore, ReportStore

logger = structlog.get_logger(__name__)


class GatewaySettings(BaseSettings):
    """Credentials belong to this service, never validator or browser configuration."""

    model_config = SettingsConfigDict(env_prefix="HIPPIUS_", extra="ignore")
    chain_genesis: ChainId = Field()
    netuid: int = Field(ge=1, le=65535)
    validators: frozenset[str] = Field(min_length=1)
    backend: Literal["hippius", "local"] = "local"
    bucket: str = ""
    credentials_file: Path | None = None
    profile: str | None = None
    local_directory: Path = Path("../localnet/state/score-objects")
    host: str = "127.0.0.1"
    port: int = Field(default=8090, ge=1, le=65535)

    @field_validator("validators")
    @classmethod
    def check_hotkeys(cls, values: frozenset[str]) -> frozenset[str]:
        if not all(is_valid_ss58_address(value) for value in values):
            raise ValueError("Allowlist must contain valid validator hotkey addresses")
        return values


class ScoreHTTPServer(ThreadingHTTPServer):
    """Supporting localnet service with bounded request bodies and read timeouts."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: ScoreService, *, allow_uploads: bool = True) -> None:
        self.score_service = service
        self.allow_uploads = allow_uploads
        super().__init__(address, ScoreHandler)


class ScoreHandler(BaseHTTPRequestHandler):
    """Expose signed report transport, without accepting object keys from callers."""

    @property
    def service(self) -> ScoreService:
        return cast(ScoreHTTPServer, self.server).score_service

    @override
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def respond(self, status: int, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        """Accept a complete signed envelope and return its verified storage receipt.

        Raises:
            ValueError: Handled here as HTTP 400 for invalid framing or reports.
        """
        if self.path != "/v1/reports":
            self.respond(404, b'{"error":"not found"}')
            return
        if not cast(ScoreHTTPServer, self.server).allow_uploads:
            self.respond(405, b'{"error":"upload directly to Hippius with validator ACL credentials"}')
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked requests are not supported")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REPORT_BYTES:
                self.respond(413, b'{"error":"invalid report size"}')
                return
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete request body")
            receipt = self.service.upload(data)
            self.respond(200, receipt.model_dump_json().encode())
        except PermissionError:
            self.respond(403, b'{"error":"validator or chain not admitted"}')
        except ValueError:
            self.respond(400, b'{"error":"invalid signed report"}')
        except Exception as exc:
            logger.warning("Score upload failed", error_type=type(exc).__name__)
            self.respond(503, b'{"error":"storage unavailable; retry the same report"}')

    def do_GET(self) -> None:
        """Serve paginated, verified reports for one explicit epoch."""
        url = urlsplit(self.path)
        if url.path != "/v1/reports":
            self.respond(404, b'{"error":"not found"}')
            return
        try:
            query = parse_qs(url.query, strict_parsing=True)
            epoch = int(query["epoch_start"][0])
            limit = int(query.get("limit", ["100"])[0])
            token = query.get("token", [None])[0]
            page = self.service.read_page(epoch, token, limit)
            self.respond(200, page.model_dump_json().encode())
        except KeyError, ValueError:
            self.respond(400, json.dumps({"error": "require epoch_start >= 0 and limit 1..100"}).encode())
        except Exception as exc:
            logger.warning("Score read failed", error_type=type(exc).__name__)
            self.respond(503, b'{"error":"storage unavailable"}')

    @override
    def log_message(self, format: str, *args: object) -> None:
        # Do not log caller-controlled query strings or request bodies.
        logger.debug("Score service HTTP request")


@click.command()
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
def main(env_file: Path | None) -> None:
    """Run the localnet upload gateway (use a TLS reverse proxy for remote validators).

    Raises:
        click.ClickException: If the Hippius bucket or credential path is missing.
    """
    load_dotenv(env_file)
    configure_logging(LoggingSettings())
    settings = GatewaySettings.model_validate({})
    store: ReportStore
    if settings.backend == "local":
        store = LocalReportStore(settings.local_directory)
    else:
        if not settings.bucket:
            raise click.ClickException("HIPPIUS_BUCKET is required for the Hippius backend")
        store = HippiusStore.connect(settings.bucket, settings.profile, settings.credentials_file)
    service = ScoreService(store, settings.chain_genesis, settings.netuid, settings.validators)
    with ScoreHTTPServer((settings.host, settings.port), service, allow_uploads=settings.backend == "local") as server:
        logger.info("Score service ready", backend=settings.backend, port=settings.port)
        server.serve_forever()


if __name__ == "__main__":
    main()
