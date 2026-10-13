# parsers/sbs/sbs_product_parser.py
"""
SBS product parser.

Price fields come from the JSON-LD Product block, cross-checked
against the HTML `.product-price.has-discount` block when present.
All price strings are normalized through `core.utils.price_cleaner`.

Storage contract:
    price           = original (strikethrough / "was") price
    discount_price  = discounted price (what the user pays), 0.0 if no discount
    discount_amount = price - discount_price (positive), 0.0 if no discount

Price format:
    Values are cleaned by `clean_price` (currency symbols, NBSPs,
    thousands separators, decimal commas) and stored as floats.
    No millime → dinar rescaling is applied here; the raw cleaned
    number is preserved.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup

from core.logger import setup_logger
from core.Web.product_schema import create_product_schema
from core.utils.price_cleaner import clean_price
from core.utils.clean_text_parser import (
    clean_and_decode_text,
    normalize_availability,
    clean_product_name,
    clean_brand_name,
    clean_category_name,
    clean_special_id,
)
from core.utils.Dclean_html import clean_html
from core.utils.cleaning_url_logic import normalize_url
from core.utils.clean_date_logic import clean_date

logger = setup_logger("sbs_parser", "parser")


# ═══════════════════════════════════════════════════════════
# Price helper
# ═══════════════════════════════════════════════════════════
def _clean_price_value(value: Any) -> Optional[float]:
    """
    Clean a raw price value and return it as a float.

    `clean_price` handles currency symbols, thousands separators,
    decimal commas and NBSPs. The result is returned unchanged —
    no rescaling, no rounding to a fixed unit.

    Examples:
        "2,999,000"  -> 2999000.0
        "1 299,00"   -> 1299.0
        "499.000 DT" -> 499000.0
    """
    if value is None:
        return None
    cleaned = clean_price(value)
    if cleaned is None or cleaned <= 0:
        return None
    return float(cleaned)


def _clean_json(text: str) -> str:
    if not text:
        return text
    text = text.replace("\t", " ")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def is_category_page(soup: BeautifulSoup, url: str) -> bool:
    """Detect SBS's silent redirect-to-category behaviour."""
    title = soup.select_one(".page-heading .h1, h1.page-heading")
    if title:
        lowered = title.text.lower().strip()
        if any(w in lowered for w in ("produits", "catégorie", "catégories")):
            return True

    grid = soup.select_one(".products, .product-list, .product-grid")
    if grid and not soup.select_one("#product-details, .product-cover"):
        return True

    items = soup.select(
        ".product-miniature, .product-item, .tvproduct-wrapper"
    )
    if len(items) > 1 and not soup.select_one("#product-details"):
        return True

    if (
        re.search(r"\d+\s+produits?", soup.get_text())
        and not soup.select_one("#product-details")
    ):
        return True

    return False


def is_product_redirected(html: str, url: str) -> bool:
    if not html:
        return False
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return False
    return is_category_page(soup, url)


# ═══════════════════════════════════════════════════════════
# Main parser
# ═══════════════════════════════════════════════════════════
def parse_sbs_product_page(
    html: str,
    url: str,
    site: str,
    main_product_id: Optional[str] = None,
) -> Optional[Dict]:
    if not html:
        logger.warning(f"{site} | EMPTY_HTML | {url}")
        return None

    if is_product_redirected(html, url):
        logger.warning(f"{site} | PRODUCT_REMOVED_REDIRECTED | {url}")
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site} | BEAUTIFULSOUP_ERROR | {url} | {e}")
        return None

    product_details = soup.select_one(
        "#product-details, .product-cover, .product-prices"
    )
    if not product_details:
        logger.warning(f"{site} | NO_PRODUCT_DETAILS | {url}")
        return None

    # ── Extract the numeric product id if not provided.
    if not main_product_id:
        body = soup.find("body")
        if body:
            for c in body.get("class", []):
                if "product-id-" in c:
                    main_product_id = c.split("product-id-")[-1]
                    break
        if not main_product_id:
            m = re.search(r"/(\d+)(?:/|$)", url)
            if m:
                main_product_id = m.group(1)

    try:
        product = create_product_schema(site, url, main_product_id)
    except Exception as e:
        logger.error(f"{site} | CREATE_SCHEMA_ERROR | {e}")
        return None

    # ── Locate the Product JSON-LD block.
    json_data: Optional[Dict] = None
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(_clean_json(script.string))
        except json.JSONDecodeError as e:
            logger.debug(f"{site} | JSON_DECODE_ERROR | {e}")
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            json_data = data
            break

    if not json_data:
        logger.warning(f"{site} | NO_PRODUCT_JSON_FOUND | url={url}")
        return None

    # ── Descriptive fields.
    if json_data.get("name"):
        product["name"] = clean_product_name(json_data["name"])
    if json_data.get("description"):
        product["description"] = clean_html(json_data["description"])
    if json_data.get("category"):
        product["category"] = clean_category_name(json_data["category"])

    brand = json_data.get("brand")
    if isinstance(brand, dict):
        product["brand"] = clean_brand_name(brand.get("name"))
    elif isinstance(brand, str):
        product["brand"] = clean_brand_name(brand)

    if json_data.get("mpn"):
        product["special_id"] = clean_special_id(json_data["mpn"])
    elif json_data.get("sku"):
        product["special_id"] = clean_special_id(json_data["sku"])

    # ── Offers block: price, availability, validity.
    offers = json_data.get("offers", {})
    if isinstance(offers, list) and offers:
        offers = offers[0]

    json_price: Optional[float] = None
    if isinstance(offers, dict):
        json_price = _clean_price_value(offers.get("price"))

        if offers.get("priceValidUntil"):
            product["price_valid_until"] = clean_date(
                offers["priceValidUntil"]
            )
        else:
            product["price_valid_until"] = (
                datetime.now() + timedelta(days=7)
            ).strftime("%Y-%m-%d")

        availability = offers.get("availability")
        if availability:
            product["availability"] = normalize_availability(
                availability.split("/")[-1]
            )

    # ── Images.
    images = json_data.get("image", [])
    if isinstance(images, str):
        images = [images]
    elif isinstance(images, dict):
        images = [images["url"]] if images.get("url") else []
    if images:
        product["images"] = [normalize_url(i) for i in images if i]
        product["main_image"] = product["images"][0]

    # ── PRICE LOGIC.
    #
    # Storage contract:
    #   price           = original (strikethrough) price
    #   discount_price  = discounted price (what the user pays), 0.0 if none
    #   discount_amount = price - discount_price, positive, 0.0 if none
    #
    # JSON-LD carries the *current* (discounted) price. The HTML
    # `.product-price.has-discount` block carries BOTH the current
    # and the original (strikethrough) price when a discount is on.
    html_current: Optional[float] = None
    html_original: Optional[float] = None

    discount_block = soup.select_one(".product-price.has-discount")
    if discount_block:
        regular_el = discount_block.select_one(
            ".product-discount .regular-price"
        )
        current_el = discount_block.select_one(
            ".current-price span[content], .current-price .price"
        )
        if current_el:
            html_current = _clean_price_value(
                current_el.get("content") or current_el.text
            )
        if regular_el:
            html_original = _clean_price_value(regular_el.text)

    # Prefer the HTML original (strikethrough) when available; the
    # JSON price is authoritative for the *current* price.
    original_price = html_original
    discounted_price = json_price if json_price is not None else html_current

    if json_price is not None and json_price > 0:
        discounted_price = json_price

    # Sanity: if we have both an original and a current, the original
    # must be the larger one. If they're inverted, swap them.
    if (
        original_price is not None
        and discounted_price is not None
        and original_price < discounted_price
    ):
        original_price, discounted_price = discounted_price, original_price

    # No discount case: original == current, and there's no separate
    # discounted price to store.
    if original_price is None:
        original_price = discounted_price
        if html_current is None and html_original is None:
            discounted_price = None

    # Final values (already cleaned, no rescaling).
    price_value = original_price or 0.0

    if discounted_price is not None and discounted_price < price_value:
        discount_price_value = discounted_price
        discount_amount_value = round(
            max(price_value - discount_price_value, 0.0), 2
        )
    else:
        # No discount → discount fields are zeroed.
        discount_price_value = 0.0
        discount_amount_value = 0.0

    product["price"] = round(price_value, 2)
    product["discount_price"] = round(discount_price_value, 2)
    product["discount_amount"] = discount_amount_value

    # ── SHORT DESCRIPTION (attributes table, then description list).
    product["short_description"] = {}

    specs_table = soup.select_one("table.data.table.additional-attributes")
    if specs_table:
        for row in specs_table.find_all("tr"):
            th, td = row.find("th"), row.find("td")
            if not th or not td:
                continue
            key = clean_and_decode_text(th.text)
            value = clean_and_decode_text(td.text)
            if key and value:
                product["short_description"][key] = value

    if not product["short_description"] and main_product_id:
        for row in soup.select(
            f"#product-description-short-{main_product_id} tr"
        ):
            cols = row.find_all("td")
            if len(cols) >= 2:
                value = clean_and_decode_text(cols[1].text)
                if value:
                    idx = len(product["short_description"]) + 1
                    product["short_description"][f"spec_{idx}"] = value

    if not product["short_description"]:
        for li in soup.select("#product-description-short li"):
            text = clean_and_decode_text(li.text)
            if text:
                idx = len(product["short_description"]) + 1
                product["short_description"][f"spec_{idx}"] = text

    # ── Validation.
    if not product.get("name"):
        logger.warning(f"{site} | DETAILS_NO_NAME | {main_product_id}")
        return None
    if not product.get("price") or product["price"] <= 0:
        logger.warning(
            f"{site} | DETAILS_NO_PRICE | {main_product_id} | "
            f"price={product.get('price')}"
        )
        return None
    if not product.get("url"):
        product["url"] = normalize_url(url)

    logger.info(
        f"{site} | PRODUCT_PARSED | "
        f"id={main_product_id} | "
        f"price={product['price']} | "
        f"discount_price={product['discount_price']} | "
        f"discount_amount={product['discount_amount']}"
    )
    return product


def fast_check_sbs_product(
    url: str, product_id: Optional[str] = None
) -> Optional[Dict]:
    """Monitoring helper (fetch + parse)."""
    from core.Web.smart_requester import SmartRequester

    try:
        html = SmartRequester(site_name="sbs").get_html(url, timeout=45)
        if not html:
            logger.warning(f"SBS_FAST | FETCH_FAILED | id={product_id}")
            return None
        return parse_sbs_product_page(html, url, "sbs", product_id)
    except Exception as e:
        logger.error(f"SBS_FAST | CHECK_ERROR | id={product_id} | {e}")
        return None