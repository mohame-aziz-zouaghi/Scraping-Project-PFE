# core/utils/price_cleaner.py
"""
Universal price cleaner.

Normalizes any price string to a plain float in the site's base
currency unit (dinar for Tunisian sites, dollar for USD, ...).

Never returns a value that has been inflated by a thousand-separator
misread. The rule is simple:

  - Space-like characters (regular, thin, narrow no-break, no-break)
    are always thousand separators.
  - "." and "," are candidates for the decimal separator. We pick
    the right one based on position and digit count.

Examples (Tunisian sites, base unit = dinar):
    "2 715,000 DT"       -> 2715.00
    "499,000 DT"         -> 499.00
    "1,369,000 DT"       -> 1369.00
    "3 219,00 TND"       -> 3219.00
    "2\\u202f989,000 DT" -> 2989.00
    "1,234.50 USD"       -> 1234.50
    "€ 1.234,50"         -> 1234.50
    "1958.999799"        -> 1959.00
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Union


# ═══════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════
_CURRENCY_SYMBOLS = (
    "TND", "DT", "د.ت",
    "$", "USD",
    "€", "EUR",
    "£", "GBP",
    "¥", "JPY",
    "₹", "INR",
    "₪", "ILS",
    "₩", "KRW",
    "₦", "NGN",
    "₽", "RUB",
    "₺", "TRY",
    "₴", "UAH",
    "R$", "BRL",
    "MX$", "MXN",
    "C$", "CAD",
    "A$", "AUD",
    "CHF", "SEK", "NOK", "DKK", "PLN", "HUF", "CZK",
)

# Only space-like characters count as thousand separators.
# Never put "." or "," in this list — they may be the decimal.
_SPACE_SEPARATORS = (" ", "\u202f", "\u2009", "\xa0")

_UNIT_WORDS_RE = re.compile(
    r"\b(TTC|HT|tax included|excl\.|incl\.|VAT|inc\.\s*VAT)\b",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════
def clean_price(
    value: Any,
    decimals: int = 2,
    return_type: str = "float",
) -> Optional[Union[float, int, str]]:
    """
    Parse any price string or number into a normalized value.

    Args:
        value: raw price (str, int, float, or None).
        decimals: how many decimal places to keep (default 2).
        return_type: "float" (default), "int", or "str".

    Returns:
        The cleaned price, or None if the input is empty, non-numeric,
        negative, or otherwise unparseable.
    """
    if value is None:
        return None

    # Already numeric.
    if isinstance(value, (int, float)):
        return _round_and_format(float(value), decimals, return_type)

    text = str(value).strip()
    if not text:
        return None

    # ── Strip currency labels and unit words.
    for sym in _CURRENCY_SYMBOLS:
        text = text.replace(sym, " ")
    text = _UNIT_WORDS_RE.sub(" ", text).strip()
    if not text:
        return None

    # ── Collapse space-like thousand separators.
    for sp in _SPACE_SEPARATORS:
        text = text.replace(sp, "")
    if not text:
        return None

    # ── Resolve the decimal separator.
    text = _resolve_decimal(text)
    if text is None:
        return None

    # ── Strip any residual non-numeric characters.
    text = re.sub(r"[^\d.]", "", text)
    if not text or text == ".":
        return None

    # Collapse multiple dots if any slipped through.
    if text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail

    try:
        num = float(text)
    except ValueError:
        return None

    if num < 0:
        return None

    return _round_and_format(num, decimals, return_type)


# ═══════════════════════════════════════════════════════════
# Internals
# ═══════════════════════════════════════════════════════════
def _resolve_decimal(text: str) -> Optional[str]:
    """
    Rewrite `text` so that its decimal separator is a single dot,
    and every thousand separator is stripped.

    Called after space-like separators have been removed, so the
    only candidates left are "." and ",".
    """
    has_comma = "," in text
    has_dot = "." in text

    if has_comma and has_dot:
        # The rightmost one is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            # comma is decimal
            return text.replace(".", "").replace(",", ".")
        # dot is decimal
        return text.replace(",", "")

    if has_comma:
        return _resolve_single_separator(text, ",")

    if has_dot:
        return _resolve_single_separator(text, ".")

    return text


def _resolve_single_separator(text: str, sep: str) -> Optional[str]:
    """
    Decide whether a single-occurring separator is decimal or
    thousand, based on how many digits follow it.
    """
    count = text.count(sep)

    if count > 1:
        # Multiple occurrences -> thousand separator.
        return text.replace(sep, "")

    head, _, tail = text.partition(sep)

    # Exactly one separator: look at the tail.
    if len(tail) in (1, 2):
        # decimal separator
        return f"{head}.{tail}"
    if len(tail) == 3:
        # thousand separator
        return f"{head}{tail}"

    # Ambiguous tail length (0, 4, 5, ...): treat as thousand.
    return f"{head}{tail}"


def _round_and_format(
    value: float, decimals: int, return_type: str
) -> Union[float, int, str]:
    if decimals <= 0:
        rounded: Union[float, int] = int(round(value))
    else:
        quant = Decimal("0." + "0" * decimals)
        rounded = float(
            Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
        )

    if return_type == "int":
        return int(rounded)
    if return_type == "str":
        if decimals <= 0:
            return str(int(rounded))
        return f"{rounded:.{decimals}f}"
    return float(rounded)


def validate_price(
    price: Optional[float],
    min_value: float = 0,
    max_value: float = 1_000_000,
) -> bool:
    """Return True if `price` is a positive value within range."""
    if price is None:
        return False
    return min_value <= price <= max_value