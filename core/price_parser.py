import re
from venv import logger


def parse_price(price_str):
    if not price_str:
        return 0.0

    # Remove all non-digit, non-comma, non-dot characters
    cleaned = re.sub(r"[^\d,\.]", "", price_str)

    # Replace comma with nothing (assuming prices like "53,100" are thousands)
    cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except Exception as e:
        logger.warning(f"Price parse error: {price_str} | {e}")
        return 0.0
