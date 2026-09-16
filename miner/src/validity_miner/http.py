"""Bounded HTTPS request handling; TLS client authentication is enforced by the server."""

import asyncio
import json
from datetime import UTC, datetime

import structlog
from uvicorn._types import ASGIReceiveCallable, ASGISendCallable, Scope
from validity_protocol.exchange import MAX_EXCHANGE_BYTES

from validity_miner.journal import RateLimited, ReplayConflict
from validity_miner.service import AdmissionDenied, ExpiredRequest, MinerService

logger = structlog.get_logger(__name__)


async def respond(send: ASGISendCallable, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class MinerApp:
    """No redirects, callbacks, public scores, secrets or provider data in request logs."""

    def __init__(self, service: MinerService) -> None:
        self.service = service

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        if scope["type"] != "http":
            return
        if scope["scheme"] != "https":
            await respond(send, 403, b'{"error":"mutual TLS is required"}')
            return
        if scope["path"] == "/health" and scope["method"] == "GET":
            await respond(send, 200, b'{"status":"ok","scope":"synthetic-rn"}')
            return
        if scope["path"] != "/v1/evaluate":
            await respond(send, 404, b'{"error":"not found"}')
            return
        if scope["method"] != "POST":
            await respond(send, 405, b'{"error":"POST required"}')
            return
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if headers.get(b"transfer-encoding") or headers.get(b"content-encoding", b"identity") != b"identity":
            await respond(send, 400, b'{"error":"unsupported request framing"}')
            return
        if not 0 < length <= MAX_EXCHANGE_BYTES:
            await respond(send, 413, b'{"error":"invalid request size"}')
            return
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    event = await receive()
                    if event["type"] != "http.request":
                        return
                    body.extend(event["body"])
                    if len(body) > length:
                        await respond(send, 413, b'{"error":"request too large"}')
                        return
                    if not event["more_body"]:
                        break
            if len(body) != length:
                await respond(send, 400, b'{"error":"incomplete request"}')
                return
            result = await asyncio.to_thread(self.service.evaluate, bytes(body), datetime.now(UTC))
            await respond(send, 200, result)
            return
        except AdmissionDenied:
            status, reason = 403, "assignment not admitted"
        except ExpiredRequest, TimeoutError:
            status, reason = 408, "assignment expired"
        except ReplayConflict:
            status, reason = 409, "task ID conflict"
        except RateLimited:
            status, reason = 429, "request limit reached"
        except ValueError:
            status, reason = 400, "invalid signed assignment"
        except Exception as exc:
            logger.warning("Miner request failed", error_type=type(exc).__name__)
            status, reason = 503, "service unavailable"
        await respond(send, status, json.dumps({"error": reason}).encode())
