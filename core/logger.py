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
CONSOLE_LOG_LEVEL = logging.INFO  # Console shows INFO and above, DEBUG goes to files


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

    # ---------------------------------
    # FILE HANDLER (All logs)
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
    # CONSOLE HANDLER (INFO and above only)
    # ---------------------------------
    console_handler = logging.StreamHandler()
    console_handler.setLevel(CONSOLE_LOG_LEVEL)  # Console shows INFO and above
    
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
# DEBUG LOGGER (Always DEBUG level)
# =====================================================

def setup_debug_logger(site_name: str = "global", log_type: str = "debug") -> logging.Logger:
    """
    Setup a logger specifically for debugging with DEBUG level.
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
# EXPORTS
# =====================================================

__all__ = [
    'setup_logger',
    'setup_debug_logger',
    'get_site_logger',
    'log_global',
    'log_debug',
    'log_debug_info',
    'log_step',
    'log_result',
    'log_error',
    'log_session_start',
    'log_session_end',
    'write_product_result',
    'sanitize_name',
    'DEBUG_MODE',
    'LOG_LEVEL',
]