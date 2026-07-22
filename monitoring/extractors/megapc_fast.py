# monitoring/extractors/megapc_fast.py
"""
Fast extractor for MegaPC product data from Next.js __NEXT_DATA__ script.
"""
import json
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from core.utils.clean_date_logic import clean_date
from core.logger import setup_logger

logger = setup_logger("megapc_fast", "monitor")


# =====================================================
# HELPERS
# =====================================================

def clean_price(value: Any) -> Optional[float]:
    """Clean and parse price value"""
    if value is None:
        return None
    
    try:
        if isinstance(value, (int, float)):
            return float(value)
        
        cleaned = str(value)
        cleaned = cleaned.replace("TND", "").replace("DT", "").replace(",", ".").strip()
        
        # Extract only numbers and decimal point
        import re
        cleaned = re.sub(r'[^\d.]', '', cleaned)
        
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def get_special_id(product: Dict[str, Any]) -> Optional[str]:
    """
    Extract special_id with fallback logic.
    Returns first non-empty value, max 20 characters.
    """
    fields = ["sku", "referenceInterne", "mpn"]
    
    for field in fields:
        value = product.get(field)
        if value and isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                # Take first 20 characters
                return cleaned[:30]
    
    return None


def normalize_availability(stock: int) -> str:
    """Normalize availability based on stock value"""
    if stock and stock > 0:
        return "InStock"
    return "OutOfStock"


# =====================================================
# MAIN EXTRACTION
# =====================================================

def extract_megapc_fast(html: str) -> Optional[Dict[str, Any]]:
    """
    Extract product data from MegaPC Next.js page HTML
    
    Args:
        html: Raw HTML from MegaPC product page
    
    Returns:
        Dictionary with extracted product data or None on failure
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
    
    # Extract product data
    try:
        product = data.get("props", {}).get("pageProps", {}).get("product", {})
    except (AttributeError, TypeError) as e:
        logger.error(f"MEGAPC_FAST | PRODUCT_EXTRACTION_ERROR | {e}")
        return None
    
    if not product or not isinstance(product, dict):
        logger.warning("MEGAPC_FAST | NO_PRODUCT_DATA")
        return None
    
    # =====================================================
    # EXTRACT DATA
    # =====================================================
    try:
        # Price
        normal_price = clean_price(product.get("price")) or 0
        promo_price = clean_price(product.get("prixEnPromo"))
        
        final_price = normal_price
        discount_price = 0.0
        discount_amount = 0.0
        
        if promo_price and promo_price < normal_price:
            final_price = normal_price
            discount_price = promo_price
            discount_amount = round(normal_price - promo_price, 2)
        
        # Availability
        stock = product.get("stock", 0)
        availability = normalize_availability(stock)
        
        # Special ID (max 20 chars)
        special_id = get_special_id(product)
        
        # Valid until date
        raw_date = product.get("priceValidUntil") or product.get("price_valid_until")
        cleaned_date = clean_date(raw_date) if raw_date else None
        
        return {
            "price": final_price,
            "discount_price": discount_price,
            "discount_amount": discount_amount,
            "availability": availability,
            "special_id": special_id,
            "price_valid_until": cleaned_date
        }
        
    except Exception as e:
        logger.error(f"MEGAPC_FAST | EXTRACTION_ERROR | {e}")
        return None


# =====================================================
# FAST CHECK (For worker compatibility)
# =====================================================

def fast_check_megapc_product(url: str, product_id: str = None) -> Optional[Dict[str, Any]]:
    """Fetch and extract product data in one call"""
    from core.Web.http_client import get
    
    html = get(
        url=url,
        max_retries=3,
        timeout=30,
        requests_per_minute=15,
        use_referer=True,
        verify_ssl=True
    )
    
    if not html:
        logger.warning(f"MEGAPC_FAST | FETCH_FAILED | product_id={product_id}")
        return None
    
    return extract_megapc_fast(html)