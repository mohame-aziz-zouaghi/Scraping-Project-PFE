# monitoring/extractors/tunisianet_fast.py - Simplified extractor

import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup

from core.logger import setup_logger
from core.utils.clean_date_logic import clean_date
from core.utils.clean_text_parser import (
    clean_special_id,
    normalize_availability,
    clean_json_string,
)
from parsers.tunisianet.tunisianet_product_parser import parse_prices

logger = setup_logger("tunisianet_fast", "monitor")


# =====================================================
# HELPERS
# =====================================================

def get_special_id(data: Dict[str, Any], main_product_id: str = None) -> Optional[str]:
    """Extract special_id (reference / SKU) with fallback logic."""
    raw_special_id = data.get("reference") or data.get("supplier_reference") or data.get("mpn")
    special_id = clean_special_id(raw_special_id) if raw_special_id else ""

    if not special_id and main_product_id:
        special_id = clean_special_id(str(main_product_id))

    return special_id or None


def resolve_availability(data: Dict[str, Any], soup: BeautifulSoup) -> str:
    """Normalize availability from JSON data, falling back to HTML/quantity."""
    availability_text = None

    if data.get("availability_message"):
        availability_text = data.get("availability_message")
    elif data.get("availability"):
        availability_text = data.get("availability")
    else:
        stock_span = soup.select_one("#stock_availability span, .availability span")
        if stock_span:
            availability_text = stock_span.text.strip()
        else:
            quantity = data.get("quantity", 0)
            try:
                availability_text = "InStock" if int(quantity) > 0 else "OutOfStock"
            except (ValueError, TypeError):
                availability_text = "OutOfStock"

    return normalize_availability(availability_text)


def extract_price_valid_until(data: Dict[str, Any]) -> Optional[str]:
    """
    Extract the raw price_valid_until from the page data.
    Returns None if not found.
    """
    raw_date = data.get("price_valid_until") or data.get("valid_until")
    
    if raw_date:
        cleaned = clean_date(raw_date)
        if cleaned:
            return cleaned
    
    return None


# =====================================================
# MAIN EXTRACTION
# =====================================================

def extract_tunisianet_fast(html: str) -> Optional[Dict[str, Any]]:
    """
    Extract monitored product data from a Tunisianet product page HTML.
    Returns raw extracted data WITHOUT date resolution logic.
    """
    if not html:
        logger.warning("EMPTY_HTML")
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"BEAUTIFULSOUP_ERROR | {e}")
        return None

    # =====================================================
    # EXTRACT PRODUCT JSON
    # =====================================================
    data: Dict[str, Any] = {}
    product_div = soup.select_one("#product-details")

    if product_div and product_div.has_attr("data-product"):
        try:
            raw_data = clean_json_string(product_div["data-product"])
            data = json.loads(raw_data)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON_PARSE_ERROR | {e}")
        except Exception as e:
            logger.warning(f"JSON_EXTRACT_ERROR | {e}")
    else:
        logger.debug("NO_PRODUCT_DETAILS | falling back to HTML")

    # =====================================================
    # PRODUCT ID (for special_id fallback)
    # =====================================================
    main_product_id = data.get("id") or data.get("id_product")

    # =====================================================
    # EXTRACT MONITORED FIELDS
    # =====================================================
    try:
        # Prices (JSON-first / HTML-fallback)
        prices = parse_prices(data, soup)
        final_price = prices.get("price", 0.0) or 0.0
        discount_price = prices.get("discount_price", 0.0) or 0.0
        discount_amount = prices.get("discount_amount", 0.0) or 0.0

        # If nothing could be priced, treat as failure
        if final_price <= 0 and discount_price <= 0:
            logger.warning("NO_PRICE_FOUND")
            return None

        availability = resolve_availability(data, soup)
        special_id = get_special_id(data, main_product_id)
        
        # Extract the date from the page (or None if not found)
        price_valid_until = extract_price_valid_until(data)

        return {
            "price": final_price,
            "discount_price": discount_price,
            "discount_amount": discount_amount,
            "availability": availability,
            "special_id": special_id,
            "price_valid_until": price_valid_until,  # May be None
        }

    except Exception as e:
        logger.error(f"EXTRACTION_ERROR | {e}")
        return None


# =====================================================
# FAST CHECK (For worker compatibility)
# =====================================================

def fast_check_tunisianet_product(url: str, product_id: str = None) -> Optional[Dict[str, Any]]:
    """Fetch and extract monitored product data in one call."""
    from core.Web.http_client import get

    html = get(
        url=url,
        max_retries=4,
        timeout=45,
        requests_per_minute=10,
        use_referer=True,
        verify_ssl=True,
    )

    if not html:
        logger.warning(f"FETCH_FAILED | product_id={product_id}")
        return None

    return extract_tunisianet_fast(html)