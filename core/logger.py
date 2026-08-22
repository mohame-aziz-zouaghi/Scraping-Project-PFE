# core/logger.py

import json
import logging
import os
import re
from datetime import datetime
import time
from typing import Optional

BASE_LOG_DIR = "logs"


# =====================================================
# LOG LEVEL CONFIGURATION
# =====================================================

# Set this to logging.DEBUG for development, logging.INFO for production
DEBUG_MODE = True  # Set to False for production

# Log levels
LOG_LEVEL = logging.DEBUG if DEBUG_MODE else logging.INFO

# =====================================================
# CONSOLE LOG LEVEL - COMPLETELY DISABLE CONSOLE LOGGING
# =====================================================
# Set to logging.CRITICAL to only show CRITICAL (essentially nothing)
# Set to logging.ERROR to show errors only
# Set to logging.WARNING for warnings and above
CONSOLE_LOG_LEVEL = logging.CRITICAL  # <-- Only CRITICAL (almost nothing)
# OR use logging.ERROR if you want to see errors
# CONSOLE_LOG_LEVEL = logging.ERROR


# =====================================================
# UNIVERSAL LOGGER SETUP
# =====================================================

def setup_logger(site_name: str = "global", log_type: str = "scraper", level: Optional[int] = None) -> logging.Logger:
    """
    Setup a logger with file and console handlers.
    
    Args:
        site_name: "sbs", "tunisianet", "global", etc.
        log_type: "scraper", "duplication", "monitor", etc.
        level: Logging level (default: LOG_LEVEL)
    
    Returns:
        logging.Logger instance
    """
    site_name = site_name.lower()
    
    # Use provided level or default
    if level is None:
        level = LOG_LEVEL
    
    # ---------------------------------
    # GLOBAL LOGGER
    # ---------------------------------
    if site_name == "global":
        os.makedirs(BASE_LOG_DIR, exist_ok=True)
        file_path = os.path.join(BASE_LOG_DIR, "global.log")
        logger_name = "global_logger"

    # ---------------------------------
    # SITE LOGGER
    # ---------------------------------
    else:
        site_dir = os.path.join(BASE_LOG_DIR, site_name)
        os.makedirs(site_dir, exist_ok=True)
        
        file_name = f"{log_type}.log"
        file_path = os.path.join(site_dir, file_name)
        logger_name = f"{site_name}_{log_type}"

    # ---------------------------------
    # CREATE LOGGER
    # ---------------------------------
    logger = logging.getLogger(logger_name)
    
    # Remove existing handlers to avoid duplicates
    if logger.handlers:
        logger.handlers.clear()
    
    logger.setLevel(level)
    # Prevent propagation to root logger
    logger.propagate = False

    # ---------------------------------
    # FILE HANDLER (All logs go to file)
    # ---------------------------------
    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(level)  # All levels go to file
    
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # ---------------------------------
    # CONSOLE HANDLER - COMPLETELY DISABLED (CRITICAL level)
    # ---------------------------------
    # Only add console handler if we want to show anything
    if CONSOLE_LOG_LEVEL < logging.CRITICAL:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(CONSOLE_LOG_LEVEL)
        
        console_formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    # Add daily separator
    add_daily_separator(file_path)

    return logger


# =====================================================
# SILENT LOGGER - No console output at all
# =====================================================

def setup_silent_logger(site_name: str = "global", log_type: str = "scraper") -> logging.Logger:
    """
    Setup a logger with NO console output at all.
    All logs go only to files.
    """
    # Save current console level
    global CONSOLE_LOG_LEVEL
    original_level = CONSOLE_LOG_LEVEL
    
    # Temporarily set to CRITICAL to disable console
    CONSOLE_LOG_LEVEL = logging.CRITICAL
    
    logger = setup_logger(site_name, log_type)
    
    # Restore original level
    CONSOLE_LOG_LEVEL = original_level
    
    return logger


# =====================================================
# VERBOSE LOGGER - For debugging with console output
# =====================================================

def setup_verbose_logger(site_name: str = "global", log_type: str = "scraper") -> logging.Logger:
    """
    Setup a logger with console output at INFO level for debugging.
    Use this for specific loggers that need console visibility.
    """
    global CONSOLE_LOG_LEVEL
    original_level = CONSOLE_LOG_LEVEL
    CONSOLE_LOG_LEVEL = logging.INFO
    
    logger = setup_logger(site_name, log_type)
    
    CONSOLE_LOG_LEVEL = original_level
    
    return logger


# =====================================================
# DEBUG LOGGER (Always DEBUG level, file only)
# =====================================================

def setup_debug_logger(site_name: str = "global", log_type: str = "debug") -> logging.Logger:
    """
    Setup a logger specifically for debugging with DEBUG level.
    No console output.
    """
    return setup_logger(site_name, log_type, level=logging.DEBUG)


# =====================================================
# DAILY SEPARATOR
# =====================================================

def add_daily_separator(file_path: str):
    """Add a daily separator to the log file"""
    today = datetime.now().strftime("%Y-%m-%d")

    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"LOG START: {today}\n")
            f.write(f"{'='*60}\n")
        return

    # Check if today's separator already exists
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        if today in content:
            return

    # Add separator
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"LOG DATE: {today}\n")
        f.write(f"{'='*60}\n")


# =====================================================
# GLOBAL LOGGER (SHARED)
# =====================================================

_global_logger = setup_logger("global")


def log_global(message: str):
    """Log to global log file"""
    _global_logger.info(message)


# =====================================================
# CONSOLE VERBOSITY CONTROL
# =====================================================

def set_console_verbosity(level: int):
    """
    Dynamically change console log level.
    
    Usage:
        set_console_verbosity(logging.CRITICAL)  # Disable console (default)
        set_console_verbosity(logging.ERROR)     # Show errors only
        set_console_verbosity(logging.WARNING)   # Show warnings and errors
        set_console_verbosity(logging.INFO)      # Show everything
    """
    global CONSOLE_LOG_LEVEL
    CONSOLE_LOG_LEVEL = level
    
    # Update all existing loggers
    for logger_name in logging.root.manager.loggerDict:
        logger_obj = logging.getLogger(logger_name)
        for handler in logger_obj.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(level)
    
    _global_logger.info(f"CONSOLE_VERBOSITY | set to {logging.getLevelName(level)}")


def disable_console_logging():
    """Completely disable all console logging"""
    set_console_verbosity(logging.CRITICAL)


def enable_console_logging(level: int = logging.INFO):
    """Enable console logging at specified level"""
    set_console_verbosity(level)


def get_console_verbosity() -> str:
    """Get current console log level"""
    return logging.getLevelName(CONSOLE_LOG_LEVEL)


# =====================================================
# DEBUG LOGGING HELPERS
# =====================================================

def log_debug(logger: logging.Logger, message: str, *args, **kwargs):
    """
    Log a debug message with [DEBUG] prefix for easy filtering.
    """
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"[DEBUG] {message}", *args, **kwargs)


def log_debug_info(logger: logging.Logger, message: str, *args, **kwargs):
    """
    Log a debug message with [INFO] level but [DEBUG] prefix.
    This ensures debug messages appear even if DEBUG level is not enabled.
    """
    logger.info(f"[DEBUG] {message}", *args, **kwargs)


# =====================================================
# RESULT WRITER PER SITE
# =====================================================

def sanitize_name(name: str) -> str:
    """
    Remove underscores and any other invalid filename characters.
    """
    name = name.replace("_", "")  # remove underscores
    # remove anything not alphanumeric, dash, or space
    name = re.sub(r"[^a-zA-Z0-9\s\-]", "", name)
    # replace spaces with dash if needed
    name = name.strip().replace(" ", "-")
    return name.lower()


def write_product_result(product: dict, site_name: str):
    """
    Write product result to file with performance logging
    """
    site_name = sanitize_name(site_name)
    site_dir = os.path.join(BASE_LOG_DIR, site_name)
    os.makedirs(site_dir, exist_ok=True)
    file_path = os.path.join(site_dir, "results.txt")

    start = time.time()
    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(product, ensure_ascii=False) + "\n")
            f.flush()
    except Exception as e:
        log_global(f"ERROR | WRITE_PRODUCT_FAILED | {site_name} | {e}")
        return

    duration = time.time() - start

    # Log slow writes
    if duration > 0.1:
        log_global(f"WARNING | SLOW_WRITE | {site_name} | {duration:.3f}s")


# =====================================================
# SITE LOGGER CACHE
# =====================================================

_logger_cache = {}


def get_site_logger(site_name: str, log_type: str = "scraper") -> logging.Logger:
    """
    Get or create a site logger with caching.
    """
    key = f"{site_name}_{log_type}"
    if key not in _logger_cache:
        _logger_cache[key] = setup_logger(site_name, log_type)
    return _logger_cache[key]


# =====================================================
# FORMATTED LOGGING HELPERS
# =====================================================

def log_step(logger: logging.Logger, step_name: str, site: str = "", product_id: str = ""):
    """Log a step with consistent formatting"""
    if site and product_id:
        logger.info(f"{site} | {product_id} | STEP: {step_name}")
    elif site:
        logger.info(f"{site} | STEP: {step_name}")
    else:
        logger.info(f"STEP: {step_name}")


def log_result(logger: logging.Logger, site: str, product_id: str, result: bool, message: str = ""):
    """Log a result with consistent formatting"""
    status = "SUCCESS" if result else "FAILED"
    if message:
        logger.info(f"{site} | {product_id} | {status} | {message}")
    else:
        logger.info(f"{site} | {product_id} | {status}")


def log_error(logger: logging.Logger, site: str, product_id: str, error: Exception, context: str = ""):
    """Log an error with consistent formatting"""
    error_msg = str(error)
    if context:
        logger.error(f"{site} | {product_id} | ERROR | {context} | {error_msg}")
    else:
        logger.error(f"{site} | {product_id} | ERROR | {error_msg}")
    
    # Also log to debug for traceback
    if DEBUG_MODE:
        import traceback
        logger.debug(f"{site} | {product_id} | TRACEBACK: {traceback.format_exc()}")


# =====================================================
# SESSION LOGGING
# =====================================================

def log_session_start(logger: logging.Logger, site: str, session_id: str = None):
    """Log session start"""
    if session_id:
        logger.info(f"{site} | SESSION_START | {session_id}")
    else:
        logger.info(f"{site} | SESSION_START")


def log_session_end(logger: logging.Logger, site: str, session_id: str = None, stats: dict = None):
    """Log session end with stats"""
    if session_id:
        logger.info(f"{site} | SESSION_END | {session_id}")
    else:
        logger.info(f"{site} | SESSION_END")
    
    if stats:
        logger.info(f"{site} | SESSION_STATS | {json.dumps(stats, default=str)}")


# =====================================================
# PROGRESS LOGGING - Dashboard only
# =====================================================

def log_progress(message: str, level: int = logging.INFO):
    """
    Log progress messages - only to file, not console.
    """
    # Create a logger that only writes to file
    temp_logger = logging.getLogger("progress")
    
    if not temp_logger.handlers:
        temp_logger.setLevel(logging.DEBUG)
        temp_logger.propagate = False
        
        # File handler only (no console)
        os.makedirs(BASE_LOG_DIR, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(BASE_LOG_DIR, "progress.log"), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        temp_logger.addHandler(file_handler)
        
        # NO console handler - progress only goes to file
    
    temp_logger.log(level, message)


# =====================================================
# SUPPRESS HTTP LOGGING
# =====================================================

def suppress_http_logging():
    """Suppress verbose HTTP request logging"""
    # Suppress urllib3 and requests logging
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("urllib3.util.retry").setLevel(logging.WARNING)


# Call this on import to suppress HTTP logging by default
suppress_http_logging()


# =====================================================
# EXPORTS
# =====================================================

__all__ = [
    'setup_logger',
    'setup_silent_logger',
    'setup_debug_logger',
    'setup_verbose_logger',
    'get_site_logger',
    'log_global',
    'log_debug',
    'log_debug_info',
    'log_step',
    'log_result',
    'log_error',
    'log_session_start',
    'log_session_end',
    'log_progress',
    'write_product_result',
    'sanitize_name',
    'set_console_verbosity',
    'disable_console_logging',
    'enable_console_logging',
    'get_console_verbosity',
    'suppress_http_logging',
    'DEBUG_MODE',
    'LOG_LEVEL',
    'CONSOLE_LOG_LEVEL',
]