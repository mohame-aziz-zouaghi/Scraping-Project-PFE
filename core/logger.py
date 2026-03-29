import json
import logging
import os
import re
from datetime import datetime
import time


BASE_LOG_DIR = "logs"


# ---------------------------------
# UNIVERSAL LOGGER SETUP
# ---------------------------------
def setup_logger(site_name="global", log_type="scraper"):
    """
    site_name: "sbs", "tunisianet", etc.
    log_type: "scraper" or "duplication"
    """

    # Normalize
    site_name = site_name.lower()

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
    logger.setLevel(logging.INFO)

    # Prevent duplicate handlers
    if not logger.handlers:

        handler = logging.FileHandler(file_path, encoding="utf-8")

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Add daily separator
    add_daily_separator(file_path)

    return logger


# ---------------------------------
# DAILY SEPARATOR
# ---------------------------------
def add_daily_separator(file_path):

    today = datetime.now().strftime("%Y-%m-%d")

    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"\n---------------- {today} ----------------\n")
        return

    with open(file_path, "r+", encoding="utf-8") as f:

        content = f.read()

        if today not in content:
            f.write(f"\n---------------- {today} ----------------\n")


# ---------------------------------
# GLOBAL LOGGER (SHARED)
# ---------------------------------
_global_logger = setup_logger("global")


def log_global(message):
    _global_logger.info(message)


# ---------------------------------
# RESULT WRITER PER SITE
# ---------------------------------


def sanitize_name(name):
    """
    Remove underscores and any other invalid filename characters.
    """
    name = name.replace("_", "")  # remove underscores
    # remove anything not alphanumeric, dash, or space
    name = re.sub(r"[^a-zA-Z0-9\s\-]", "", name)
    # replace spaces with dash if needed
    name = name.strip().replace(" ", "-")
    return name.lower()


def write_product_result(product, site_name):
    site_name = sanitize_name(site_name)
    site_dir = os.path.join(BASE_LOG_DIR, site_name)
    os.makedirs(site_dir, exist_ok=True)
    file_path = os.path.join(site_dir, "results.txt")

    start = time.time()
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(product, ensure_ascii=False) + "\n")
        f.flush()
    duration = time.time() - start

    # LOG SLOW WRITES
    if duration > 0.1:
        log_global(f"WARNING | SLOW_WRITE | {duration:.3f}s")
        log_global(f"WARNING | SLOW_WRITE | {duration:.3f}s")