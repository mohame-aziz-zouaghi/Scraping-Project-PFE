# core/utils/price_cleaner.py
"""
Universal price cleaner - Shared across all sites
Handles multiple formats, currencies, and automatic rounding
"""
import re
from typing import Any, Optional, Union
from decimal import Decimal, ROUND_HALF_UP


# =====================================================
# CONSTANTS
# =====================================================

# Supported currency symbols
CURRENCY_SYMBOLS = [
    "TND", "DT", "د.ت",  # Tunisian Dinar
    "$", "USD",           # US Dollar
    "€", "EUR",           # Euro
    "£", "GBP",           # British Pound
    "¥", "JPY",           # Japanese Yen
    "₹", "INR",           # Indian Rupee
    "₪", "ILS",           # Israeli Shekel
    "₩", "KRW",           # South Korean Won
    "₦", "NGN",           # Nigerian Naira
    "₽", "RUB",           # Russian Ruble
    "₺", "TRY",           # Turkish Lira
    "₴", "UAH",           # Ukrainian Hryvnia
    "R$", "BRL",          # Brazilian Real
    "MX$", "MXN",         # Mexican Peso
    "C$", "CAD",          # Canadian Dollar
    "A$", "AUD",          # Australian Dollar
    "CHF",                # Swiss Franc
    "SEK",                # Swedish Krona
    "NOK",                # Norwegian Krone
    "DKK",                # Danish Krone
    "PLN",                # Polish Zloty
    "HUF",                # Hungarian Forint
    "CZK",                # Czech Koruna
]

# Supported thousand separators
THOUSAND_SEPARATORS = [",", ".", " ", "\u202f", "\u2009", "\xa0"]


# =====================================================
# CORE PRICE CLEANING FUNCTION (SHARED)
# =====================================================

def clean_price(
    price_value: Any,
    decimals: int = 2,
    currency: str = None,
    return_type: str = "float"
) -> Optional[Union[float, int, str]]:
    """
    Universal price cleaner - cleans any price format and rounds.
    
    Handles:
    - Multiple currencies (TND, $, €, £, etc.)
    - Multiple formats (1,234.50, 1.234,50, 1 234,50)
    - Thousand separators (comma, dot, space, thin space)
    - Decimal separators (dot, comma)
    - Automatic rounding
    
    Args:
        price_value: Price value (string, int, float, or None)
        decimals: Number of decimal places (default: 2)
        currency: Optional currency to remove (e.g., 'TND', '$', '€')
        return_type: 'float', 'int', or 'str'
    
    Returns:
        Cleaned price or None if invalid
    
    Examples:
        clean_price("2 715,000 DT") → 2715.0
        clean_price("$1,234.50") → 1234.5
        clean_price("€ 1.234,50") → 1234.5
        clean_price("1,234.50 USD") → 1234.5
        clean_price("1958.999799") → 1959.0
    """
    if price_value is None:
        return None
    
    # If it's already a number
    if isinstance(price_value, (int, float)):
        return _round_and_return(float(price_value), decimals, return_type)
    
    # Convert to string
    price_str = str(price_value).strip()
    
    if not price_str:
        return None
    
    # =====================================================
    # STEP 1: REMOVE CURRENCY SYMBOLS
    # =====================================================
    
    # Remove specified currency if provided
    if currency:
        price_str = price_str.replace(currency, "").strip()
    
    # Remove any currency symbol
    for symbol in CURRENCY_SYMBOLS:
        price_str = price_str.replace(symbol, "").strip()
    
    # Remove common price suffixes
    price_str = re.sub(r'\s*(TTC|HT|tax included|excl\.|incl\.|VAT|inc\. VAT)\s*', '', price_str, flags=re.IGNORECASE)
    
    # =====================================================
    # STEP 2: DETECT FORMAT AND NORMALIZE
    # =====================================================
    
    # Remove spaces (including thin spaces, non-breaking spaces) - they are thousand separators
    for sep in THOUSAND_SEPARATORS:
        price_str = price_str.replace(sep, "")
    
    # Now handle decimal separator detection
    has_comma = ',' in price_str
    has_dot = '.' in price_str
    
    if has_comma and has_dot:
        # Both exist - determine which is decimal
        last_comma_pos = price_str.rfind(',')
        last_dot_pos = price_str.rfind('.')
        
        if last_comma_pos > last_dot_pos:
            # Comma is decimal separator
            price_str = price_str.replace('.', '')
            price_str = price_str.replace(',', '.')
        else:
            # Dot is decimal separator
            price_str = price_str.replace(',', '')
    elif has_comma and not has_dot:
        # Only comma exists
        parts = price_str.split(',')
        if len(parts) == 2 and len(parts[1]) <= 2:
            # Comma is decimal separator
            price_str = price_str.replace(',', '.')
        else:
            # Comma is thousand separator
            price_str = price_str.replace(',', '')
    
    # Remove any non-numeric characters except dot
    price_str = re.sub(r'[^\d.]', '', price_str)
    
    if not price_str:
        return None
    
    # =====================================================
    # STEP 3: HANDLE MULTIPLE DOTS
    # =====================================================
    
    if price_str.count('.') > 1:
        parts = price_str.split('.')
        integer_part = ''.join(parts[:-1])
        decimal_part = parts[-1]
        price_str = f"{integer_part}.{decimal_part}"
    
    # =====================================================
    # STEP 4: PARSE AND ROUND
    # =====================================================
    
    try:
        value = float(price_str)
        
        if value < 0:
            return None
        
        return _round_and_return(value, decimals, return_type)
        
    except ValueError:
        return None


def _round_and_return(value: float, decimals: int, return_type: str) -> Union[float, int, str]:
    """Round the value and return in the specified format"""
    if decimals == 0:
        rounded = int(round(value))
    else:
        # Use Decimal for proper rounding
        rounded = float(Decimal(str(value)).quantize(Decimal('0.' + '0' * decimals), rounding=ROUND_HALF_UP))
    
    if return_type == "int":
        return int(rounded)
    elif return_type == "str":
        if decimals == 0:
            return str(int(rounded))
        else:
            return f"{rounded:.{decimals}f}"
    else:  # float
        return float(rounded)


# =====================================================
# SITE-SPECIFIC CONVENIENCE FUNCTIONS
# =====================================================

def clean_price_tunisianet(price_str: Any, decimals: int = 2) -> Optional[float]:
    """Clean Tunisianet price format."""
    return clean_price(price_str, decimals=decimals, currency="TND")


def clean_price_megapc(price_str: Any, decimals: int = 2) -> Optional[float]:
    """Clean MegaPC price format."""
    return clean_price(price_str, decimals=decimals)


def clean_price_mytek(price_str: Any, decimals: int = 2) -> Optional[float]:
    """Clean MyTek price format."""
    return clean_price(price_str, decimals=decimals, currency="TND")


def clean_price_generic(price_str: Any, decimals: int = 2) -> Optional[float]:
    """Generic price cleaner for any format."""
    return clean_price(price_str, decimals=decimals)


# =====================================================
# PRICE VALIDATION (SHARED)
# =====================================================

def validate_price(price: Optional[float], min_value: float = 0, max_value: float = 1000000) -> bool:
    """Validate a price is within reasonable bounds."""
    if price is None:
        return False
    if price < min_value or price > max_value:
        return False
    return True