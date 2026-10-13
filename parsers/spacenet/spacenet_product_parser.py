# parsers/spacenet/spacenet_product_parser.py
"""
Spacenet product parser.

Price comes from the JSON-LD Product block; the HTML
`.product-prices` block is used when the JSON carries no price or
to recover a strikethrough original. All price strings are
normalized through `core.utils.price_cleaner`.

Storage contract:
    price           = original (strikethrough / "was") price
    discount_price  = discounted price (what the user pays), 0.0 if no discount
    discount_amount = price - discount_price (positive), 0.0 if no discount
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup

from core.logger import setup_logger
from core.Web.product_schema import create_product_schema
from core.utils.price_cleaner import clean_price
from core.utils.clean_text_parser import (
    clean_and_decode_text,
    normalize_availability,
)
from core.utils.Dclean_html import clean_html
from core.utils.cleaning_url_logic import normalize_url
from core.utils.clean_date_logic import clean_date

logger = setup_logger("spacenet", "scraper")


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════
def _to_float(value: Any) -> Optional[float]:
    """Run any price-ish value through clean_price, guarding None/empty."""
    if value is None:
        return None
    cleaned = clean_price(value)
    return cleaned if cleaned and cleaned > 0 else None


def _same_magnitude(
    reference: Optional[float], value: Optional[float]
) -> Optional[float]:
    """
    Normalize `value` so it lives on the same magnitude as `reference`.

    Spacenet renders the JSON-LD price in dinars (e.g. 1169.0) while
    the HTML strikethrough block renders it in millimes
    (e.g. 1369000). This helper rescales the HTML value to match the
    JSON value's magnitude so the two can be compared / subtracted.
    """
    if reference is None or value is None:
        return value
    if reference <= 0 or value <= 0:
        return value
    ratio = value / reference
    if ratio >= 100:                 # value is ~1000x larger → millimes
        return round(value / 1000.0, 2)
    if ratio <= 0.01:                # value is ~1000x smaller → dinars
        return round(value * 1000.0, 2)
    return value


# ═══════════════════════════════════════════════════════════
# Main parser
# ═══════════════════════════════════════════════════════════
def parse_spacenet_product_page(
    html: str,
    url: str,
    site_name: str,
    product_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not html:
        logger.warning(f"{site_name} | EMPTY_HTML | {url}")
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site_name} | BEAUTIFULSOUP_ERROR | {url} | {e}")
        return None

    try:
        product = create_product_schema(site_name, url, product_id)
    except Exception as e:
        logger.error(f"{site_name} | CREATE_SCHEMA_ERROR | {url} | {e}")
        return None

    if not isinstance(product, dict):
        logger.error(
            f"{site_name} | SCHEMA_NOT_DICT | {url} | type={type(product)}"
        )
        return None

    product["url"] = normalize_url(url)
    product["main_product_id"] = product_id

    # ── Locate the Product JSON-LD block.
    product_json: Optional[Dict] = None
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError as e:
            logger.debug(f"{site_name} | JSON parse error | {e}")
            continue
        if isinstance(data, dict):
            if data.get("@type") == "Product":
                product_json = data
                break
            for item in data.get("@graph", []):
                if isinstance(item, dict) and item.get("@type") == "Product":
                    product_json = item
                    break
            if product_json is not None:
                break

    # ── HTML-only fallback: name + price from the DOM.
    if product_json is None:
        logger.warning(f"{site_name} | PRODUCT_JSON_NOT_FOUND | {url}")
        name_el = soup.find("h1", class_="product-name") or soup.find("h1")
        if not name_el:
            logger.warning(f"{site_name} | NO_PRODUCT_NAME | {url}")
            return None
        product["name"] = clean_and_decode_text(name_el.text)

        price_el = soup.find("span", class_="price")
        raw_price = _to_float(price_el.text) if price_el else None

        product["price"] = raw_price or 0.0
        product["discount_price"] = 0.0
        product["discount_amount"] = 0.0

        avail_el = soup.find("span", class_="availability")
        product["availability"] = (
            normalize_availability(avail_el.text)
            if avail_el else "OutOfStock"
        )

        if not product.get("name") or product["price"] <= 0:
            logger.warning(
                f"{site_name} | INVALID_PRODUCT_NO_DATA | {url}"
            )
            return None
        return product

    # ── Descriptive fields from JSON.
    product["name"] = clean_and_decode_text(product_json.get("name", ""))
    brand = product_json.get("brand")
    if isinstance(brand, dict):
        product["brand"] = clean_and_decode_text(brand.get("name"))
    else:
        product["brand"] = clean_and_decode_text(brand)
    product["category"] = clean_and_decode_text(product_json.get("category"))

    if not product_id:
        pid = product_json.get("productID") or product_json.get("sku")
        if pid:
            product["main_product_id"] = str(pid)

    product["special_id"] = clean_and_decode_text(product_json.get("sku"))
    product["description"] = clean_html(product_json.get("description", ""))

    images = product_json.get("image")
    if images:
        if isinstance(images, str):
            images = [images]
        product["images"] = [normalize_url(i) for i in images if i]
        product["main_image"] = (
            product["images"][0] if product["images"] else None
        )

    # ── PRICE LOGIC.
    #
    # Storage contract:
    #   price           = original (strikethrough) price
    #   discount_price  = discounted price (what the user pays), 0.0 if none
    #   discount_amount = price - discount_price, positive, 0.0 if none
    #
    # JSON-LD carries the *current* price. The HTML `.product-prices`
    # block carries the strikethrough original (and sometimes the
    # current), in millimes — so we rescale it to match the JSON
    # magnitude before comparing.
    offers = product_json.get("offers", {})
    if isinstance(offers, list) and offers:
        offers = offers[0]

    json_price: Optional[float] = None
    if isinstance(offers, dict):
        json_price = _to_float(offers.get("price"))

    price_block = soup.find("div", {"class": "product-prices"})
    html_current: Optional[float] = None
    html_original: Optional[float] = None

    if price_block:
        discounted = price_block.find(
            "div", {"class": "product-price has-discount"}
        )
        if discounted:
            current_div = discounted.find(
                "div", {"class": "current-price"}
            )
            if current_div:
                span = current_div.find("span", {"content": True})
                if span:
                    html_current = _to_float(
                        span.get("content") or span.text
                    )
                else:
                    amount_span = current_div.find(
                        "span", {"class": "discount discount-amount"}
                    )
                    if amount_span:
                        html_current = _to_float(amount_span.text)

            regular_div = price_block.find(
                "div", {"class": "product-discount"}
            )
            if regular_div:
                span = regular_div.find(
                    "span", {"class": "regular-price"}
                )
                if span:
                    html_original = _to_float(span.text)

    # Reconcile units: JSON-LD (dinars) vs HTML (millimes).
    reference = json_price if json_price is not None else html_current
    html_current = _same_magnitude(reference, html_current)
    html_original = _same_magnitude(reference, html_original)

    # Resolve which value is the "current" (discounted) price and
    # which is the "original" (strikethrough) price.
    # JSON price is authoritative for the current price when present.
    if json_price is not None and json_price > 0:
        discounted_price = json_price
    elif html_current is not None and html_current > 0:
        discounted_price = html_current
    else:
        discounted_price = None

    original_price = html_original if html_original and html_original > 0 else None

    # If the "original" isn't actually higher than the discounted price,
    # there's no real discount — collapse to a single price.
    if (
        original_price is not None
        and discounted_price is not None
        and original_price <= discounted_price
    ):
        original_price = None

    # No discount case: price = discounted (current) price, discount = 0.
    if original_price is None:
        original_price = discounted_price
        discounted_price = None

    # Final normalized values via clean_price.
    price_value = _to_float(original_price) or 0.0

    if discounted_price is not None and discounted_price < price_value:
        discount_price_value = _to_float(discounted_price) or 0.0
        discount_amount_value = round(
            max(price_value - discount_price_value, 0.0), 2
        )
    else:
        discount_price_value = 0.0
        discount_amount_value = 0.0

    product["price"] = round(price_value, 2)
    product["discount_price"] = round(discount_price_value, 2)
    product["discount_amount"] = discount_amount_value

    # ── Validate.
    if not product.get("name"):
        logger.warning(f"{site_name} | INVALID_PRODUCT_NO_NAME | {url}")
        return None
    if product["price"] <= 0:
        logger.warning(
            f"{site_name} | INVALID_PRODUCT_NO_PRICE | {url}"
        )
        return None

    # ── Availability.
    if isinstance(offers, dict):
        avail = offers.get("availability")
        if avail:
            product["availability"] = normalize_availability(
                avail.split("/")[-1]
            )
    if not product.get("availability"):
        avail_el = soup.find("span", class_="availability")
        product["availability"] = (
            normalize_availability(avail_el.text)
            if avail_el else "OutOfStock"
        )

    # ── Price valid until.
    if isinstance(offers, dict) and offers.get("priceValidUntil"):
        product["price_valid_until"] = clean_date(
            offers["priceValidUntil"]
        )

    # ── Special ID fallback.
    if not product.get("special_id"):
        ref = soup.find("span", class_="product-reference")
        if ref:
            product["special_id"] = clean_and_decode_text(ref.text)
        if not product.get("special_id"):
            meta = soup.find(
                "meta", {"property": "product:retailer_item_id"}
            )
            if meta:
                product["special_id"] = clean_and_decode_text(
                    meta.get("content", "")
                )

    # ── Specs.
    product["short_description"] = {}
    specs_table = soup.find(
        "table", class_="data table additional-attributes"
    )
    if specs_table:
        for row in specs_table.find_all("tr"):
            th, td = row.find("th"), row.find("td")
            if not th or not td:
                continue
            key = clean_and_decode_text(th.text)
            value = clean_and_decode_text(td.text)
            if key and value:
                product["short_description"][key] = value

    logger.debug(
        f"{site_name} | PRODUCT_PARSED | "
        f"{product.get('name', 'Unknown')[:50]}..."
    )
    return product


def fast_check_spacenet_product(
    url: str, product_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Monitoring helper (fetch + parse)."""
    from core.Web.smart_requester import SmartRequester

    try:
        html = SmartRequester(site_name="spacenet").get_html(
            url, timeout=45
        )
        if not html:
            logger.warning(
                f"SPACENET_FAST | FETCH_FAILED | id={product_id}"
            )
            return None
        return parse_spacenet_product_page(
            html, url, "spacenet", product_id
        )
    except Exception as e:
        logger.error(
            f"SPACENET_FAST | CHECK_ERROR | id={product_id} | {e}"
        )
        return None