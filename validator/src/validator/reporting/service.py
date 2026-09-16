"""Verify validator identities before storing or serving signed score summaries."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

import structlog
from opentelemetry import metrics

from validator.credentials.protocol import WireModel
from validator.reporting.protocol import MAX_REPORT_BYTES, SignedScoreReport, UploadReceipt
from validator.reporting.snapshot import check_replacement
from validator.reporting.storage import ReportStore

logger = structlog.get_logger(__name__)
meter = metrics.get_meter("validity.reporting")
events = meter.create_counter("validity.report.events")
duration = meter.create_histogram("validity.report.operation.duration", unit="s")


class ReportPage(WireModel):
    """Verified reports from one epoch, retaining every validator's individual scores."""

    reports: tuple[SignedScoreReport, ...]
    next_token: str | None


class ScoreService:
    """Localnet admission uses an explicit operator-managed validator hotkey allowlist."""

    def __init__(self, store: ReportStore, chain_genesis: str, netuid: int, validators: frozenset[str]) -> None:
        self.store = store
        self.chain_genesis = chain_genesis
        self.netuid = netuid
        self.validators = validators

    def authorize(self, signed: SignedScoreReport) -> None:
        """Authenticate the complete report and bind it to the configured subnet.

        Raises:
            PermissionError: If the signer or chain context is not admitted.
        """
        signed.verify()
        report = signed.report
        if (report.chain_genesis, report.netuid) != (self.chain_genesis, self.netuid):
            raise PermissionError("Report chain/subnet is not admitted")
        if report.validator_hotkey not in self.validators:
            raise PermissionError("Validator hotkey is not admitted")

    def upload(self, data: bytes) -> UploadReceipt:
        """Authenticate before storage I/O; acknowledge only verified readback.

        Raises:
            ValueError: If the payload exceeds the protocol size limit.
            OSError: If readback does not match the requested signed snapshot.
        """
        start = perf_counter()
        try:
            if len(data) > MAX_REPORT_BYTES:
                raise ValueError("Report exceeds the protocol limit")
            signed = SignedScoreReport.model_validate_json(data)
            self.authorize(signed)
            previous = self.store.read(signed.object_key)
            if previous is not None:
                saved = SignedScoreReport.model_validate_json(previous)
                self.authorize(saved)
                check_replacement(saved, signed)
            self.store.write(signed.object_key, signed.model_dump_json().encode())
            previous = self.store.read(signed.object_key)
            if previous is None:
                raise OSError("Uploaded report is missing from storage")
            saved = SignedScoreReport.model_validate_json(previous)
            self.authorize(saved)
            if saved.report_id != signed.report_id:
                raise OSError("Stored epoch snapshot does not match the submitted report")
            events.add(1, {"stage": "stored"})
            logger.info("Score report stored", report_id=signed.report_id, validator=signed.report.validator_hotkey)
            return UploadReceipt(report_id=signed.report_id, object_key=signed.object_key, stored_at=datetime.now(UTC))
        except Exception:
            events.add(1, {"stage": "upload_failed"})
            raise
        finally:
            duration.record(perf_counter() - start, {"operation": "upload"})

    def read_page(self, epoch_start: int, token: str | None = None, limit: int = 100) -> ReportPage:
        """Read a page for the leaderboard; reject invalid, foreign or misplaced objects.

        Invalid objects are omitted with an observable rejection; storage failures propagate.

        Raises:
            ValueError: If the requested page bounds are invalid.
        """
        if epoch_start < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid epoch or page limit")
        start = perf_counter()
        if token is not None and token not in self.validators:
            raise ValueError("Invalid validator page token")
        remaining = sorted(hotkey for hotkey in self.validators if token is None or hotkey > token)
        selected = remaining[:limit]
        reports: list[SignedScoreReport] = []
        try:
            for hotkey in selected:
                key = f"{hotkey}/{epoch_start}.json"
                try:
                    data = self.store.read(key)
                    if data is None:
                        continue
                    signed = SignedScoreReport.model_validate_json(data)
                    self.authorize(signed)
                    if signed.object_key != key:
                        raise ValueError("Report stored under the wrong validator or epoch")
                except ValueError, PermissionError:
                    events.add(1, {"stage": "read_rejected"})
                    logger.warning("Stored score report rejected", object_key=key)
                    continue
                reports.append(signed)
            events.add(1, {"stage": "read"})
            return ReportPage(reports=tuple(reports), next_token=selected[-1] if len(remaining) > limit else None)
        finally:
            duration.record(perf_counter() - start, {"operation": "read"})
