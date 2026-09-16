"""Direct ACL-authenticated uploads; hotkey signatures authenticate the report itself."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol

from validator.reporting.protocol import MAX_REPORT_BYTES, SignedScoreReport, UploadReceipt
from validator.reporting.service import duration, events
from validator.reporting.snapshot import check_replacement
from validator.reporting.storage import HippiusStore


class ReportUploader(Protocol):
    """An upload destination for the validator's durable outbox."""

    def upload(self, signed: SignedScoreReport) -> UploadReceipt: ...
    def close(self) -> None: ...


class HippiusUploader:
    """Use one team's ACL credentials to upload envelopes signed by its validator hotkey."""

    def __init__(self, store: HippiusStore, hotkey: str, *, verify_readback: bool = False) -> None:
        self.store = store
        self.hotkey = hotkey
        self.verify_readback = verify_readback

    def upload(self, signed: SignedScoreReport) -> UploadReceipt:
        """Support WRITE-only credentials; readers independently verify persisted signatures later.

        Raises:
            ValueError: If the envelope is oversized or belongs to another validator.
            OSError: If requested readback fails to match the submitted report.
        """
        started = perf_counter()
        try:
            signed.verify()
            if signed.report.validator_hotkey != self.hotkey:
                raise ValueError("Report signer differs from this validator's hotkey")
            data = signed.model_dump_json().encode()
            if len(data) > MAX_REPORT_BYTES:
                raise ValueError("Report exceeds the protocol limit")
            if self.verify_readback:
                previous = self.store.read(signed.object_key)
                if previous is not None:
                    check_replacement(SignedScoreReport.model_validate_json(previous), signed)
            self.store.write(signed.object_key, data)
            verification = "upload_accepted"
            if self.verify_readback:
                saved = self.store.read(signed.object_key)
                if saved is None:
                    raise OSError("Uploaded report is missing")
                verified = SignedScoreReport.model_validate_json(saved)
                verified.verify()
                if verified.report_id != signed.report_id:
                    raise OSError("Stored report differs from the submitted report")
                verification = "readback_verified"
            events.add(1, {"stage": verification, "transport": "hippius"})
            return UploadReceipt(
                report_id=signed.report_id,
                object_key=signed.object_key,
                stored_at=datetime.now(UTC),
                verification=verification,
            )
        except Exception:
            events.add(1, {"stage": "upload_failed", "transport": "hippius"})
            raise
        finally:
            duration.record(perf_counter() - started, {"operation": "direct_upload"})

    def close(self) -> None:
        self.store.client.close()
