# core/logger.py
"""
Centralized logging for the scraper.

Every logger writes to a rotating file capped at MAX_FILE_MB.
Backups are limited to BACKUP_COUNT. Old logs are cleaned at
startup so the folder self-heals.

Public API is a superset of the previous module. Existing imports
work unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from core.utils.threadsafe_rotating_handler import ThreadSafeRotatingFileHandler


# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════
BASE_LOG_DIR = Path("logs")

# Log level for file writes.
DEBUG_MODE = True
LOG_LEVEL = logging.DEBUG if DEBUG_MODE else logging.INFO

# Console handler level. CRITICAL effectively disables console.
CONSOLE_LOG_LEVEL = logging.CRITICAL

# Rotation caps — hard limits on disk usage.
# Each file is at most MAX_FILE_MB. Each logger keeps at most
# BACKUP_COUNT rolled files. Total per logger: MAX_FILE_MB * (BACKUP_COUNT + 1).
MAX_FILE_MB = 10
BACKUP_COUNT = 5
_MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

# Per-product result files (results.txt) get the same rotation.
RESULTS_MAX_FILE_MB = 20
RESULTS_BACKUP_COUNT = 5

# Suppress the libraries that spam INFO on every HTTP call.
_NOISY_LIBRARIES = (
    "urllib3",
    "urllib3.connectionpool",
    "urllib3.util.retry",
    "requests",
    "httpx",
    "httpcore",
    "asyncio",
    "charset_normalizer",
    "playwright",
    "selenium",
)


# ═══════════════════════════════════════════════════════════
# THREAD SAFETY
# ═══════════════════════════════════════════════════════════
_logger_cache: dict[str, logging.Logger] = {}
_cache_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════
# STARTUP CLEANUP
# ═══════════════════════════════════════════════════════════
def _purge_oversized_logs(
    root: Path, max_mb: int = MAX_FILE_MB
) -> int:
    """
    Delete any *.log file larger than max_mb found under root.
    Runs once at import time.

    This is the safety net for a log file created by the previous
    version of this module (which had no rotation). Without it, a
    3 GB log would still be opened, appended to, and reread.
    """
    if not root.exists():
        return 0
    limit = max_mb * 1024 * 1024
    removed = 0
    for path in root.rglob("*.log*"):
        try:
            if path.is_file() and path.stat().st_size > limit:
                path.unlink()
                removed += 1
        except OSError:
            # Locked or permission denied — skip. The rotating
            # handler will still cap the active file.
            pass
    return removed


def _ensure_clean_root() -> int:
    """Prepare BASE_LOG_DIR and purge any oversized logs."""
    BASE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _purge_oversized_logs(BASE_LOG_DIR)


# ═══════════════════════════════════════════════════════════
# LOGGER FACTORY
# ═══════════════════════════════════════════════════════════
def _make_rotating_handler(
    file_path: Path,
    *,
    level: int,
    max_bytes: int = _MAX_FILE_BYTES,
    backup_count: int = BACKUP_COUNT,
    fmt: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt: str = "%Y-%m-%d %H:%M:%S",
) -> ThreadSafeRotatingFileHandler:
    """Build a rotating file handler with the standard format."""
    handler = ThreadSafeRotatingFileHandler(
        file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    return handler


def _make_console_handler(
    level: int,
) -> logging.StreamHandler:
    """Build a console handler. Returns CRITICAL-level if disabled."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    return handler


def _resolve_log_path(
    site_name: str, log_type: str
) -> tuple[Path, str]:
    """
    Resolve the file path and the logger name for a site/type pair.
    Global logs go to logs/global.log. Everything else lands in
    logs/<site>/<type>.log.
    """
    site_name = site_name.lower()
    if site_name == "global":
        return BASE_LOG_DIR / "global.log", "global_logger"
    site_dir = BASE_LOG_DIR / site_name
    return site_dir / f"{log_type}.log", f"{site_name}_{log_type}"


def setup_logger(
    site_name: str = "global",
    log_type: str = "scraper",
    level: Optional[int] = None,
) -> logging.Logger:
    """
    Create or refresh a logger with rotation.

    Idempotent. Calling twice for the same (site, type) returns
    the same logger without duplicating handlers.
    """
    site_name = site_name.lower()
    level = LOG_LEVEL if level is None else level
    file_path, logger_name = _resolve_log_path(site_name, log_type)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(logger_name)

    # If we already configured this logger, just return it.
    if getattr(logger, "_scraper_configured", False):
        return logger

    # Detach any prior handlers cleanly.
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    logger.setLevel(level)
    logger.propagate = False

    logger.addHandler(_make_rotating_handler(file_path, level=level))

    # Console handler — only if the level is above CRITICAL.
    if CONSOLE_LOG_LEVEL < logging.CRITICAL:
        logger.addHandler(_make_console_handler(CONSOLE_LOG_LEVEL))

    logger._scraper_configured = True  # type: ignore[attr-defined]
    return logger


# ═══════════════════════════════════════════════════════════
# VARIANT LOGGERS
# ═══════════════════════════════════════════════════════════
def setup_silent_logger(
    site_name: str = "global", log_type: str = "scraper"
) -> logging.Logger:
    """Logger with no console output. Same file rotation."""
    return _build_isolated_logger(
        site_name, log_type, file_level=LOG_LEVEL, console_level=None
    )


def setup_verbose_logger(
    site_name: str = "global", log_type: str = "scraper"
) -> logging.Logger:
    """Logger with console output at INFO. Same file rotation."""
    return _build_isolated_logger(
        site_name, log_type, file_level=LOG_LEVEL, console_level=logging.INFO
    )


def setup_debug_logger(
    site_name: str = "global", log_type: str = "debug"
) -> logging.Logger:
    """Logger forced to DEBUG level, file only."""
    return _build_isolated_logger(
        site_name, log_type, file_level=logging.DEBUG, console_level=None
    )


def _build_isolated_logger(
    site_name: str,
    log_type: str,
    *,
    file_level: int,
    console_level: Optional[int],
) -> logging.Logger:
    """
    Build a logger whose console verbosity is fixed at creation,
    without mutating the module-level CONSOLE_LOG_LEVEL.

    This replaces the old approach of temporarily toggling a
    global variable, which was racy under concurrent scrapers.
    """
    site_name = site_name.lower()
    file_path, logger_name = _resolve_log_path(site_name, log_type)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(logger_name)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    logger.setLevel(file_level)
    logger.propagate = False
    logger.addHandler(
        _make_rotating_handler(file_path, level=file_level)
    )

    if console_level is not None:
        logger.addHandler(_make_console_handler(console_level))

    logger._scraper_configured = True  # type: ignore[attr-defined]
    return logger


# ═══════════════════════════════════════════════════════════
# GLOBAL LOGGER
# ═══════════════════════════════════════════════════════════
_purged = _ensure_clean_root()
_global_logger = setup_logger("global")


def log_global(message: str) -> None:
    """Write to the global log file."""
    _global_logger.info(message)


# ═══════════════════════════════════════════════════════════
# CONSOLE VERBOSITY CONTROL
# ═══════════════════════════════════════════════════════════
def set_console_verbosity(level: int) -> None:
    """
    Change the console level of every handler this module created.

    Only touches handlers that were installed by this module —
    libraries that added their own StreamHandlers are left alone.
    """
    global CONSOLE_LOG_LEVEL
    CONSOLE_LOG_LEVEL = level

    for logger in logging.root.manager.loggerDict.values():
        if not isinstance(logger, logging.Logger):
            continue
        for handler in logger.handlers:
            if (
                isinstance(handler, logging.StreamHandler)
                and getattr(handler, "_scraper_console", False)
            ):
                handler.setLevel(level)

    _global_logger.info(
        "CONSOLE_VERBOSITY | set to %s",
        logging.getLevelName(level),
    )


def disable_console_logging() -> None:
    set_console_verbosity(logging.CRITICAL)


def enable_console_logging(level: int = logging.INFO) -> None:
    set_console_verbosity(level)


def get_console_verbosity() -> str:
    return logging.getLevelName(CONSOLE_LOG_LEVEL)


# ═══════════════════════════════════════════════════════════
# DEBUG HELPERS
# ═══════════════════════════════════════════════════════════
def log_debug(
    logger: logging.Logger, message: str, *args, **kwargs
) -> None:
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"[DEBUG] {message}", *args, **kwargs)


def log_debug_info(
    logger: logging.Logger, message: str, *args, **kwargs
) -> None:
    logger.info(f"[DEBUG] {message}", *args, **kwargs)


# ═══════════════════════════════════════════════════════════
# PRODUCT RESULTS
# ═══════════════════════════════════════════════════════════
_results_lock = threading.Lock()
_results_handlers: dict[str, logging.Logger] = {}


def sanitize_name(name: str) -> str:
    name = name.replace("_", "")
    name = re.sub(r"[^a-zA-Z0-9\s\-]", "", name)
    return name.strip().replace(" ", "-").lower()


def _results_logger(site_name: str) -> logging.Logger:
    """
    Return a per-site logger that writes JSON lines to
    logs/<site>/results.txt with rotation.

    Handlers are cached per site, so the file is opened once and
    reused for every product. The previous implementation opened
    and closed the file on every call.
    """
    if site_name in _results_handlers:
        return _results_handlers[site_name]

    with _results_lock:
        if site_name in _results_handlers:
            return _results_handlers[site_name]

        site_dir = BASE_LOG_DIR / site_name
        site_dir.mkdir(parents=True, exist_ok=True)
        path = site_dir / "results.txt"

        logger = logging.getLogger(f"{site_name}_results")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

        handler = ThreadSafeRotatingFileHandler(
            path,
            maxBytes=RESULTS_MAX_FILE_MB * 1024 * 1024,
            backupCount=RESULTS_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        # Raw message only — the JSON payload is the whole line.
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        _results_handlers[site_name] = logger
        return logger


def write_product_result(product: dict, site_name: str) -> None:
    """
    Append a product as a JSON line. Rotates at RESULTS_MAX_FILE_MB.
    Warns to the global log if the write takes over 100 ms.
    """
    site_name = sanitize_name(site_name)
    start = time.perf_counter()
    try:
        logger = _results_logger(site_name)
        logger.info(json.dumps(product, ensure_ascii=False))
    except Exception as exc:
        log_global(f"ERROR | WRITE_PRODUCT_FAILED | {site_name} | {exc}")
        return

    duration = time.perf_counter() - start
    if duration > 0.1:
        log_global(
            f"WARNING | SLOW_WRITE | {site_name} | {duration:.3f}s"
        )


# ═══════════════════════════════════════════════════════════
# SITE LOGGER CACHE
# ═══════════════════════════════════════════════════════════
def get_site_logger(
    site_name: str, log_type: str = "scraper"
) -> logging.Logger:
    key = f"{site_name}_{log_type}"
    cached = _logger_cache.get(key)
    if cached is not None:
        return cached

    with _cache_lock:
        cached = _logger_cache.get(key)
        if cached is not None:
            return cached
        logger = setup_logger(site_name, log_type)
        _logger_cache[key] = logger
        return logger


# ═══════════════════════════════════════════════════════════
# FORMATTED HELPERS
# ═══════════════════════════════════════════════════════════
def log_step(
    logger: logging.Logger,
    step_name: str,
    site: str = "",
    product_id: str = "",
) -> None:
    if site and product_id:
        logger.info(f"{site} | {product_id} | STEP: {step_name}")
    elif site:
        logger.info(f"{site} | STEP: {step_name}")
    else:
        logger.info(f"STEP: {step_name}")


def log_result(
    logger: logging.Logger,
    site: str,
    product_id: str,
    result: bool,
    message: str = "",
) -> None:
    status = "SUCCESS" if result else "FAILED"
    if message:
        logger.info(f"{site} | {product_id} | {status} | {message}")
    else:
        logger.info(f"{site} | {product_id} | {status}")


def log_error(
    logger: logging.Logger,
    site: str,
    product_id: str,
    error: Exception,
    context: str = "",
) -> None:
    msg = str(error)
    if context:
        logger.error(f"{site} | {product_id} | ERROR | {context} | {msg}")
    else:
        logger.error(f"{site} | {product_id} | ERROR | {msg}")

    if DEBUG_MODE:
        import traceback
        logger.debug(
            f"{site} | {product_id} | TRACEBACK: {traceback.format_exc()}"
        )


# ═══════════════════════════════════════════════════════════
# SESSION LOGGING
# ═══════════════════════════════════════════════════════════
def log_session_start(
    logger: logging.Logger,
    site: str,
    session_id: Optional[str] = None,
) -> None:
    if session_id:
        logger.info(f"{site} | SESSION_START | {session_id}")
    else:
        logger.info(f"{site} | SESSION_START")


def log_session_end(
    logger: logging.Logger,
    site: str,
    session_id: Optional[str] = None,
    stats: Optional[dict] = None,
) -> None:
    if session_id:
        logger.info(f"{site} | SESSION_END | {session_id}")
    else:
        logger.info(f"{site} | SESSION_END")
    if stats:
        logger.info(
            f"{site} | SESSION_STATS | {json.dumps(stats, default=str)}"
        )


# ═══════════════════════════════════════════════════════════
# PROGRESS LOGGING
# ═══════════════════════════════════════════════════════════
_progress_logger: Optional[logging.Logger] = None
_progress_lock = threading.Lock()


def log_progress(message: str, level: int = logging.INFO) -> None:
    """
    Log progress to logs/progress.log with rotation.

    Previously this created a new FileHandler lazily on first call
    and could duplicate handlers under multithreading. Now it uses
    a single cached logger protected by a lock.
    """
    global _progress_logger
    if _progress_logger is None:
        with _progress_lock:
            if _progress_logger is None:
                path = BASE_LOG_DIR / "progress.log"
                BASE_LOG_DIR.mkdir(parents=True, exist_ok=True)
                logger = logging.getLogger("progress")
                logger.setLevel(logging.DEBUG)
                logger.propagate = False
                for h in list(logger.handlers):
                    logger.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass
                handler = RotatingFileHandler(
                    path,
                    maxBytes=_MAX_FILE_BYTES,
                    backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                    delay=True,
                )
                handler.setLevel(logging.DEBUG)
                handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s | %(message)s", datefmt="%H:%M:%S"
                    )
                )
                logger.addHandler(handler)
                _progress_logger = logger

    _progress_logger.log(level, message)


# ═══════════════════════════════════════════════════════════
# HTTP LOGGING SUPPRESSION
# ═══════════════════════════════════════════════════════════
def suppress_http_logging() -> None:
    """Silence the chatty HTTP libraries."""
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════
# PUBLIC EXPORTS
# ═══════════════════════════════════════════════════════════
__all__ = [
    # factories
    "setup_logger",
    "setup_silent_logger",
    "setup_verbose_logger",
    "setup_debug_logger",
    "get_site_logger",
    # global
    "log_global",
    # debug
    "log_debug",
    "log_debug_info",
    # formatted helpers
    "log_step",
    "log_result",
    "log_error",
    "log_session_start",
    "log_session_end",
    "log_progress",
    # results
    "write_product_result",
    "sanitize_name",
    # console verbosity
    "set_console_verbosity",
    "disable_console_logging",
    "enable_console_logging",
    "get_console_verbosity",
    # noise control
    "suppress_http_logging",
    # config
    "DEBUG_MODE",
    "LOG_LEVEL",
    "CONSOLE_LOG_LEVEL",
    "MAX_FILE_MB",
    "BACKUP_COUNT",
    "BASE_LOG_DIR",
]