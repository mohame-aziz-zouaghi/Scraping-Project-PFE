"""
Tunisianet fast extractor - Extracts the six tracked fields from HTML.
Fixed: Proper error handling and logging.
"""

from typing import Dict, Optional
from datetime import datetime, timedelta

from core.logger import setup_logger
from parsers.tunisianet.tunisianet_product_parser import parse_tunisianet_product_page
from core.utils.clean_text_parser import normalize_availability

logger = setup_logger("tunisianet_fast", "monitor")


def extract_tunisianet_fast(html: str) -> Optional[Dict]:
    """
    Extract the six tracked fields from Tunisianet product HTML.
    PURE PARSER - extracts fields from HTML only.
    
    Args:
        html: Product page HTML
    
    Returns:
        Dict with: price, discount_price, discount_amount, availability, special_id, price_valid_until
    """
    if not html:
        logger.warning("EXTRACT | Empty HTML")
        return None
    
    try:
        # Try to parse the product page
        result = parse_tunisianet_product_page(html, "", "tunisianet", None)
        
        if not result or not isinstance(result, dict):
            logger.warning("EXTRACT | Parser returned no result")
            return None
        
        # Extract tracked fields
        tracked_fields = {
            "price": result.get("price", 0.0),
            "discount_price": result.get("discount_price", 0.0),
            "discount_amount": result.get("discount_amount", 0.0),
            "availability": normalize_availability(result.get("availability", "OutOfStock")),
            "special_id": result.get("special_id"),
            "price_valid_until": result.get("price_valid_until"),
        }
        
        # Log extraction result
        logger.debug(
            f"EXTRACT | price={tracked_fields['price']} | "
            f"discount={tracked_fields['discount_price']} | "
            f"availability={tracked_fields['availability']}"
        )
        
        return tracked_fields
        
    except Exception as e:
        logger.error(f"EXTRACT_ERROR | {e}")
        import traceback
        logger.error(f"EXTRACT_TRACEBACK | {traceback.format_exc()[:500]}")
        return None