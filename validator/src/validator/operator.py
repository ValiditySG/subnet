"""Local process ownership and private configuration checks."""

from __future__ import annotations

import fcntl
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


def check_env_file(path: Path) -> None:
    """Fail before loading credentials from a broadly readable file.

    Raises:
        ValueError: If group or other users can access the operator environment.
    """
    if path.stat().st_mode & 0o077:
        raise ValueError("The .env file must have mode 0600; run chmod 600 on it")


@contextmanager
def exclusive_state(path: Path) -> Generator[None]:
    """Prevent two processes from using one validator journal.

    Raises:
        ValueError: If another publisher already holds the journal lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another validator is using this state directory") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
