# monitoring/extractors/sbs_fast.py

from typing import Optional, Dict, Any

from core.logger import setup_logger
from core.utils.clean_text_parser import clean_special_id, normalize_availability
from parsers.sbs.sbs_product_parser import parse_sbs_product_page

logger = setup_logger("sbs_fast", "monitor")


# =====================================================
# MAIN EXTRACTION
# =====================================================

def extract_sbs_fast(
    html: str,
    url: str = "",
    site: str = "sbs",
    main_product_id: str = None,
) -> Optional[Dict[str, Any]]:
    """
    Extract monitored product data from an SBS product page HTML.

    Reuses `parse_sbs_product_page` so field conventions are IDENTICAL
    to what the scraper stored (avoids false-positive change detection).

    SBS price convention (from the shared parser):
      - discount present -> price = discounted, discount_price = original,
        discount_amount = discounted - original (negative / opposite of megapc)
      - no discount      -> price = original, discount_price = None, discount_amount = None

    Returns None if no price could be found.
    """
    if not html:
        logger.warning("SBS_FAST | EMPTY_HTML")
        return None

    try:
        product = parse_sbs_product_page(html, url, site, main_product_id)
    except Exception as e:
        logger.error(f"SBS_FAST | PARSE_ERROR | {main_product_id} | {e}")
        return None

    if not product:
        logger.warning(f"SBS_FAST | NO_PRODUCT | {main_product_id}")
        return None

    try:
        # Coerce None numerics -> 0.0 (SBS parser returns None when no discount)
        price = float(product.get("price") or 0)
        discount_price = float(product.get("discount_price") or 0)
        discount_amount = float(product.get("discount_amount") or 0)

        # If nothing could be priced, treat as failure
        if price <= 0 and discount_price <= 0:
            logger.warning(f"SBS_FAST | NO_PRICE_FOUND | {main_product_id}")
            return None

        raw_special_id = product.get("special_id")
        special_id = clean_special_id(raw_special_id) if raw_special_id else None

        raw_valid_until = product.get("price_valid_until")
        price_valid_until = str(raw_valid_until) if raw_valid_until else None

        return {
            "price": price,
            "discount_price": discount_price,
            "discount_amount": discount_amount,
            "availability": normalize_availability(product.get("availability")),
            "special_id": special_id,
            "price_valid_until": price_valid_until,
        }

    except Exception as e:
        logger.error(f"SBS_FAST | EXTRACTION_ERROR | {main_product_id} | {e}")
        return None


# =====================================================
# FAST CHECK (For worker compatibility)
# =====================================================

def fast_check_sbs_product(
    url: str,
    product_id: str = None,
) -> Optional[Dict[str, Any]]:
    """Fetch and extract monitored product data in one call."""
    from core.Web.http_client import get

    html = get(
        url=url,
        max_retries=4,
        timeout=45,
        requests_per_minute=20,
        use_referer=True,
        verify_ssl=True,
    )

    if not html:
        logger.warning(f"SBS_FAST | FETCH_FAILED | product_id={product_id}")
        return None

    return extract_sbs_fast(html, url=url, main_product_id=product_id)
