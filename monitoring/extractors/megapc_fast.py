# monitoring/extractors/megapc_fast.py
"""
MegaPC fast extractor - Extracts the six tracked fields from HTML.
PURE PARSER - HTML only, no HTTP fetching!
"""

import json
from typing import Dict, Optional, Any
from bs4 import BeautifulSoup
from core.utils.clean_date_logic import clean_date
from core.logger import setup_logger

logger = setup_logger("megapc_fast", "monitor")


def clean_price(value: Any) -> Optional[float]:
    """Clean and parse price value."""
    if value is None:
        return None
    
    try:
        if isinstance(value, (int, float)):
            return float(value)
        
        cleaned = str(value)
        cleaned = cleaned.replace("TND", "").replace("DT", "").replace(",", ".").strip()
        
        import re
        cleaned = re.sub(r'[^\d.]', '', cleaned)
        
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def get_special_id(product: Dict[str, Any]) -> Optional[str]:
    """Extract special_id with fallback logic."""
    fields = ["sku", "referenceInterne", "mpn"]
    
    for field in fields:
        value = product.get(field)
        if value and isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned[:30]
    
    return None


def normalize_availability(stock: int) -> str:
    """Normalize availability based on stock value."""
    return "InStock" if stock and stock > 0 else "OutOfStock"


def extract_megapc_fast(html: str) -> Optional[Dict[str, Any]]:
    """
    Extract the six tracked fields from MegaPC product HTML.
    PURE PARSER - extracts from __NEXT_DATA__ script only.
    
    Args:
        html: Product page HTML
    
    Returns:
        Dict with: price, discount_price, discount_amount, availability, special_id, price_valid_until
    """
    if not html:
        logger.warning("MEGAPC_FAST | EMPTY_HTML")
        return None
    
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"MEGAPC_FAST | BEAUTIFULSOUP_ERROR | {e}")
        return None
    
    script = soup.find("script", id="__NEXT_DATA__")

    if not script:
        logger.warning("MEGAPC_FAST | NO_NEXT_DATA_SCRIPT")
        return None
    
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError as e:
        logger.error(f"MEGAPC_FAST | JSON_PARSE_ERROR | {e}")
        return None
    
    try:
        product = data.get("props", {}).get("pageProps", {}).get("product", {})
    except (AttributeError, TypeError) as e:
        logger.error(f"MEGAPC_FAST | PRODUCT_EXTRACTION_ERROR | {e}")
        return None
    
    if not product or not isinstance(product, dict):
        logger.warning("MEGAPC_FAST | NO_PRODUCT_DATA")
        return None
    
    try:
        normal_price = clean_price(product.get("price")) or 0
        promo_price = clean_price(product.get("prixEnPromo"))
        
        final_price = normal_price
        discount_price = 0.0
        discount_amount = 0.0
        
        if promo_price and promo_price < normal_price:
            final_price = normal_price
            discount_price = promo_price
            discount_amount = round(normal_price - promo_price, 2)
        
        stock = product.get("stock", 0)
        availability = normalize_availability(stock)
        special_id = get_special_id(product)
        
        raw_date = product.get("priceValidUntil") or product.get("price_valid_until")
        price_valid_until = clean_date(raw_date) if raw_date else None
        
        return {
            "price": final_price,
            "discount_price": discount_price,
            "discount_amount": discount_amount,
            "availability": availability,
            "special_id": special_id,
            "price_valid_until": price_valid_until
        }
        
    except Exception as e:
        logger.error(f"MEGAPC_FAST | EXTRACTION_ERROR | {e}")
        return None