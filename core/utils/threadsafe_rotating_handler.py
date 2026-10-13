# core/utils/threadsafe_rotating_handler.py
"""
Thread-safe rotating file handler for Windows.

The standard library `RotatingFileHandler.doRollover()` is not
thread-safe: when several worker threads call `logger.xxx()` at the
same time and the file hits `maxBytes`, each one tries to rename
`scraper.log` -> `scraper.log.1` at once, and Windows rejects the
second rename with:

    PermissionError: [WinError 32] The process cannot access the
    file because it is being used by another process

This subclass wraps the rollover in a lock and treats a racing
rename as a benign error: we simply skip the rollover this time,
and the next write will trigger it again. Nothing is lost — the
current file just lives a few bytes past `maxBytes` until the
next quiet moment.

Usage:
    from core.utils.threadsafe_rotating_handler import (
        ThreadSafeRotatingFileHandler,
    )
    handler = ThreadSafeRotatingFileHandler(
        path, maxBytes=10 * 1024 * 1024, backupCount=5,
        encoding="utf-8", delay=True,
    )
"""
from __future__ import annotations

import os
import threading
from logging.handlers import RotatingFileHandler


class ThreadSafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that survives concurrent rollovers on Windows."""

    _rollover_lock = threading.RLock()

    def emit(self, record) -> None:  # type: ignore[override]
        try:
            super().emit(record)
        except PermissionError:
            # Another thread is mid-rollover. The record is still in
            # the stream buffer; it will be flushed on the next emit.
            # Nothing to do — do not raise, do not lose the record.
            pass

    def doRollover(self) -> None:  # type: ignore[override]
        with self._rollover_lock:
            try:
                super().doRollover()
            except PermissionError:
                # A different process (or an antivirus scanner, or
                # SearchIndexer) has the file open. Skip this
                # rotation; the next emit() that crosses maxBytes
                # will retry.
                pass

    def shouldRollover(self, record) -> bool:  # type: ignore[override]
        # Guard against a race where two threads both see the file
        # as "over the limit" and both try to rotate. The lock in
        # doRollover handles the actual rotation; this override
        # just avoids unnecessary contention.
        try:
            return super().shouldRollover(record)
        except OSError:
            return False


def build(path: str, *, max_bytes: int, backup_count: int, encoding: str = "utf-8"):
    """Convenience factory used by the logger setup."""
    return ThreadSafeRotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding=encoding,
        delay=True,
    )