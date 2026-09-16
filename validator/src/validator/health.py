"""Container health reflects recent successful evaluation ticks, not process existence."""

from __future__ import annotations

import os
from pathlib import Path
from time import time


def main() -> None:
    """Check the latest successful evaluation tick.

    Raises:
        SystemExit: If the heartbeat is missing or stale.
    """
    ledger = Path(os.environ.get("VALIDATOR_LEDGER_PATH", "/var/lib/validity/credentials.sqlite3"))
    heartbeat = ledger.with_suffix(".heartbeat")
    if not heartbeat.is_file() or time() - heartbeat.stat().st_mtime > 180:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
