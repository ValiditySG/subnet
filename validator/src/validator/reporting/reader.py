"""Private signed score reader; validators upload directly to Hippius."""

from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast, override
from urllib.parse import parse_qs, urlsplit

import click
import structlog
from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from validity_protocol.identity import is_valid_ss58_address

from validator.logging_config import LoggingSettings, configure_logging
from validator.operator import check_env_file
from validator.reporting.credentials import StorageCredentials
from validator.reporting.protocol import ChainId
from validator.reporting.service import ScoreService
from validator.reporting.storage import HippiusStore

logger = structlog.get_logger(__name__)


class ReaderSettings(BaseSettings):
    """Credentials belong to this service, never validator or browser configuration."""

    model_config = SettingsConfigDict(env_prefix="HIPPIUS_", extra="ignore")
    chain_genesis: ChainId = Field()
    netuid: int = Field(default=568, ge=1, le=65535)
    validators: frozenset[str] = Field(min_length=1)
    bucket: str = Field(min_length=3)
    host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    port: int = Field(default=8090, ge=1, le=65535)

    @field_validator("validators")
    @classmethod
    def check_hotkeys(cls, values: frozenset[str]) -> frozenset[str]:
        if not all(is_valid_ss58_address(value) for value in values):
            raise ValueError("Allowlist must contain valid validator hotkey addresses")
        return values


class ScoreHTTPServer(ThreadingHTTPServer):
    """Private HTTP reader with bounded request bodies and read timeouts."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: ScoreService) -> None:
        self.score_service = service
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
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
        """Validators publish directly to Hippius; this reader never accepts writes."""
        self.respond(405, b'{"error":"upload directly to Hippius"}')

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
    """Run the private report reader behind an authenticated, rate-limited reverse proxy.

    Raises:
        click.ClickException: If the Hippius bucket or credential path is missing.
    """
    selected = env_file or Path(".env")
    if selected.exists():
        check_env_file(selected)
        load_dotenv(selected)
    configure_logging(LoggingSettings())
    settings = ReaderSettings.model_validate({})
    store = HippiusStore.connect(settings.bucket, StorageCredentials.model_validate({}))
    service = ScoreService(store, settings.chain_genesis, settings.netuid, settings.validators)
    with ScoreHTTPServer((settings.host, settings.port), service) as server:
        logger.info("Score reader ready", backend="hippius", port=settings.port)
        server.serve_forever()


if __name__ == "__main__":
    main()
