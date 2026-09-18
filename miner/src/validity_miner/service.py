"""Authenticate and bind assignments before running the synthetic RN resolver."""

from collections.abc import Callable
from datetime import datetime, timedelta
from time import perf_counter

from bittensor.sp_core import Keypair
from opentelemetry import metrics
from pylon_client.artanis import Config, Hotkey, NetUid, PylonAuthToken, PylonClient, PylonTimeout
from validity_protocol.exchange import (
    MAX_EXCHANGE_BYTES,
    RESPONSE_DOMAIN,
    BoundResponse,
    SignedRequest,
    SignedResponse,
    canonical,
)

from validity_miner.config import Settings
from validity_miner.journal import RequestJournal
from validity_miner.resolver import SyntheticResolver

meter = metrics.get_meter("validity.miner")
events = meter.create_counter("validity.miner.events")
duration = meter.create_histogram("validity.miner.request.duration", unit="s")


class AdmissionDenied(ValueError):
    """The request is not from an admitted validator for this miner."""


class ExpiredRequest(ValueError):
    """The signed assignment has expired or lies outside the accepted time window."""


def chain_admission(settings: Settings, miner_hotkey: str, validator_hotkey: str) -> None:
    """Require both participants on this subnet and a current validator permit.

    Raises:
        AdmissionDenied: If registration or validator permissions are missing.
    """
    config = Config(
        address=settings.pylon_address,
        open_access_token=PylonAuthToken(settings.pylon_open_access_token.get_secret_value()),
        timeout=PylonTimeout(connect=3, read=5, write=3, pool=3),
    )
    with PylonClient(config) as client:
        neurons = client.v1.open_access.get_latest_neurons(NetUid(settings.netuid)).neurons
    validator = neurons.get(Hotkey(validator_hotkey))
    if Hotkey(miner_hotkey) not in neurons or validator is None or not validator.validator_permit:
        raise AdmissionDenied("Miner registration or validator permit is missing")


class MinerService:
    """The service owns the signing key, pinned public source and durable replay journal."""

    def __init__(self, settings: Settings, key: Keypair, admission: Callable[[str], None]) -> None:
        self.settings = settings
        self.key = key
        self.admission = admission
        self.resolver = SyntheticResolver(settings.source_version)
        self.journal = RequestJournal(
            settings.journal_path,
            settings.chain_genesis,
            settings.netuid,
            key.ss58_address,
            settings.requests_per_minute,
        )
        self.journal.initialize()

    def evaluate(self, body: bytes, now: datetime) -> bytes:
        """Verify the entire exchange before contacting the chain or scoring source.

        Raises:
            ValueError: If the body is oversized, malformed or forged.
            AdmissionDenied: If the signed chain/subnet/miner/validator context is not admitted.
            ExpiredRequest: If the assignment is expired, future-dated or excessively long.
        """
        started = perf_counter()
        try:
            if len(body) > MAX_EXCHANGE_BYTES:
                raise ValueError("Request exceeds the protocol limit")
            signed = SignedRequest.model_validate_json(body)
            signed.verify()
            task = signed.task
            if (task.chain_genesis, task.netuid, task.miner_hotkey) != (
                self.settings.chain_genesis,
                self.settings.netuid,
                self.key.ss58_address,
            ) or task.validator_hotkey not in self.settings.allowed_validators:
                raise AdmissionDenied("Assignment context is not admitted")
            if (
                task.request.deadline_at <= now
                or task.request.issued_at > now + timedelta(seconds=5)
                or task.request.deadline_at - task.request.issued_at > timedelta(minutes=2)
            ):
                raise ExpiredRequest("Assignment time window is invalid")
            self.admission(task.validator_hotkey)
            if now + timedelta(seconds=perf_counter() - started) >= task.request.deadline_at:
                raise ExpiredRequest("Assignment expired during admission")

            def create() -> SignedResponse:
                result = BoundResponse(request_hash=signed.request_hash, response=self.resolver.resolve(task.request))
                return SignedResponse(result=result, signature=self.key.sign(RESPONSE_DOMAIN + canonical(result)).hex())

            response = self.journal.respond(signed, now, create)
            events.add(1, {"outcome": "served"})
            return response
        except Exception:
            events.add(1, {"outcome": "rejected"})
            raise
        finally:
            duration.record(perf_counter() - started)
