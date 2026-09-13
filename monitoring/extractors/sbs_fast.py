# monitoring/extractors/sbs_fast.py
"""
SBS fast extractor - Extracts the six tracked fields from HTML.
PURE PARSER - HTML only, no HTTP fetching!
"""

from typing import Dict, Optional

from core.logger import setup_logger
from parsers.sbs.sbs_product_parser import parse_sbs_product_page
from core.utils.clean_text_parser import normalize_availability

logger = setup_logger("sbs_fast", "monitor")


def extract_sbs_fast(html: str) -> Optional[Dict]:
    """
    Extract the six tracked fields from SBS product HTML.
    PURE PARSER - extracts fields from HTML only.
    
    Args:
        html: Product page HTML
    
    Returns:
        Dict with: price, discount_price, discount_amount, availability, special_id, price_valid_until
    """
    if not html:
        return None
    
    try:
        result = parse_sbs_product_page(html, "", "sbs", None)
        
        if not result or not isinstance(result, dict):
            return None
        
        tracked_fields = {
            "price": result.get("price", 0.0),
            "discount_price": result.get("discount_price", 0.0),
            "discount_amount": result.get("discount_amount", 0.0),
            "availability": normalize_availability(result.get("availability", "OutOfStock")),
            "special_id": result.get("special_id"),
            "price_valid_until": result.get("price_valid_until"),
        }
        
        return tracked_fields
        
    except Exception as e:
        logger.error(f"EXTRACT_ERROR | {e}")
        return None