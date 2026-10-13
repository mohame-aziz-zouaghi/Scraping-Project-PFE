# parsers/mytek/mytek_product_parser.py
"""
Mytek product parser.

Price extraction is script-only: the Magento `text/x-magento-init`
payload. There is deliberately no DOM fallback for prices. If the
script is missing, is malformed, or does not carry the product's
price, the parser raises (scraper) or reports a price error
(monitor) rather than emit a wrong number.

Availability and SKU still use the DOM because the Magento payload
does not carry them.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from core.logger import setup_logger
from core.utils.clean_text_parser import (
    clean_and_decode_text,
    normalize_availability,
)
from core.utils.Dclean_html import clean_html
from core.utils.cleaning_url_logic import normalize_url
from core.Web.product_schema import create_product_schema

logger = setup_logger("mytek", "scraper")

PARSER_VERSION = "mytek_product_parser_v3_magento_only"


# ═══════════════════════════════════════════════════════════
# MAGENTO PRICE EXTRACTION (script only — no DOM fallback)
# ═══════════════════════════════════════════════════════════
def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iter_magento_payloads(soup: BeautifulSoup):
    """
    Yield every parsed JSON payload from `text/x-magento-init` scripts.

    Fixes the two failure modes that silently returned None before:

    1. `script.string` is unreliable on `<script>` tags whose content
       has leading whitespace or is wrapped. We use
       `script.string or script.get_text()`.

    2. Mytek sometimes ships more than one
       `text/x-magento-init` script on a product page (theme,
       review widget, recently-viewed widget). `soup.find(...)`
       grabs the first one, which is often the wrong one. We
       iterate all of them.
    """
    scripts = soup.find_all("script", {"type": "text/x-magento-init"})
    for script in scripts:
        raw = script.string or script.get_text()
        if not raw:
            continue
        raw = raw.strip()
        if not raw:
            continue
        # Some Magento builds wrap the JSON in HTML comment markers.
        raw = re.sub(r"^<!--\s*|\s*-->$", "", raw).strip()
        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.debug(
                f"MAGENTO_PAYLOAD_PARSE_FAILED | "
                f"preview={raw[:120]!r} error={exc}"
            )
            continue


def _extract_price_info_from_items(items: dict) -> Optional[dict]:
    """Extract normalized price info from a Magento `items` dict."""
    if not isinstance(items, dict) or not items:
        return None

    for _item_id, item in items.items():
        if not isinstance(item, dict):
            continue
        price_info = item.get("price_info") or {}
        if not isinstance(price_info, dict):
            continue

        final_price = _coerce_float(price_info.get("final_price"))
        regular_price = _coerce_float(price_info.get("regular_price"))

        if final_price is None and regular_price is None:
            continue

        # Effective vs original:
        #   regular > final  -> discount active
        #   else             -> no discount
        if (
            regular_price is not None
            and final_price is not None
            and regular_price > final_price
        ):
            price = regular_price
            discount_price = final_price
            discount_amount = round(max(price - discount_price, 0.0), 2)
        else:
            price = final_price if final_price is not None else (regular_price or 0.0)
            discount_price = 0.0
            discount_amount = 0.0

        price = float(price or 0.0)
        discount_price = float(discount_price or 0.0)
        
        return {
            "price": price,
            "discount_price": discount_price,
            "discount_amount": discount_amount,
        }

    return None


def _find_price_block(payload: Any) -> Optional[dict]:
    """
    Walk one `text/x-magento-init` payload and return normalized price
    info, or None.

    Two shapes are handled:

    Shape A — provider at the top of "*":
        { "*": { "Magento_Catalog/js/product/view/provider": {
            "data": { "items": {...} }
        } } }

    Shape B — provider nested deeper (some widget wrappers):
        { "*": { "some/other/widget": { ... },
                 "Magento_Catalog/js/product/view/provider": {
            "data": { "items": {...} }
        } } }
    """
    if not isinstance(payload, dict):
        return None

    # Try the standard "*" branch first.
    for key in ("*", ""):
        branch = payload.get(key) if key else payload
        if not isinstance(branch, dict):
            continue
        provider = branch.get("Magento_Catalog/js/product/view/provider")
        if isinstance(provider, dict):
            data = provider.get("data")
            if isinstance(data, dict):
                items = data.get("items")
                if isinstance(items, dict):
                    result = _extract_price_info_from_items(items)
                    if result is not None:
                        return result

    # Fallback: scan every value under "*" for the provider key. This
    # catches payloads where the provider sits one level deeper.
    star = payload.get("*")
    if isinstance(star, dict):
        for _widget_key, widget in star.items():
            if not isinstance(widget, dict):
                continue
            provider = widget.get("Magento_Catalog/js/product/view/provider")
            if not isinstance(provider, dict):
                continue
            data = provider.get("data")
            if not isinstance(data, dict):
                continue
            items = data.get("items")
            if not isinstance(items, dict):
                continue
            result = _extract_price_info_from_items(items)
            if result is not None:
                return result

    return None


def _extract_magento_price_info(soup: BeautifulSoup) -> Optional[dict]:
    """
    Return {price, discount_price, discount_amount} from the Magento
    `text/x-magento-init` payload, or None.

    No DOM fallback. If this returns None the caller must treat it as
    a hard failure and not emit a price.
    """
    payloads_scanned = 0
    for payload in _iter_magento_payloads(soup):
        payloads_scanned += 1
        result = _find_price_block(payload)
        if result is not None:
            logger.debug(
                f"MAGENTO_PRICE_FOUND | payloads_scanned={payloads_scanned} "
                f"price={result['price']} "
                f"discount_price={result['discount_price']}"
            )
            return result

    logger.warning(
        f"MAGENTO_PRICE_BLOCK_MISSING | payloads_scanned={payloads_scanned}"
    )
    return None


# ═══════════════════════════════════════════════════════════
# AVAILABILITY (DOM, unchanged from before)
# ═══════════════════════════════════════════════════════════
def _extract_availability(soup: BeautifulSoup) -> Optional[str]:
    availability_text = None

    availability_link = soup.find("link", {"itemprop": "availability"})
    if availability_link:
        href = availability_link.get("href", "")
        if "InStock" in href:
            availability_text = "InStock"
        elif "OutOfStock" in href:
            availability_text = "OutOfStock"
        elif "PreOrder" in href:
            availability_text = "OnOrder"

    if not availability_text:
        stock_div = soup.find("div", class_="stock")
        if stock_div:
            availability_text = normalize_availability(stock_div.text.strip())

    if not availability_text:
        product_data = soup.find("div", {"data-product": True})
        if product_data:
            try:
                data_json = product_data.get("data-product")
                if data_json:
                    data = json.loads(data_json)
                    stock_status = data.get("stock_status", "")
                    availability_text = normalize_availability(stock_status)
            except Exception:
                pass

    if not availability_text:
        table = soup.find("table", class_="data table additional-attributes")
        if table:
            for row in table.find_all("tr"):
                th = row.find("th")
                td = row.find("td")
                if th and td and "disponibilit" in th.text.lower():
                    availability_text = normalize_availability(td.text)
                    break

    return availability_text or "OutOfStock"


# ═══════════════════════════════════════════════════════════
# SKU
# ═══════════════════════════════════════════════════════════
def _extract_sku(soup: BeautifulSoup) -> Optional[str]:
    sku_block = soup.find("div", class_="product-info-stock-sku")
    if sku_block:
        sku_value = sku_block.find("div", class_="value")
        if sku_value:
            sku = clean_and_decode_text(sku_value.text)
            if sku:
                return sku

    sku_meta = soup.find("meta", {"property": "product:retailer_item_id"})
    if sku_meta:
        sku = clean_and_decode_text(sku_meta.get("content", ""))
        if sku:
            return sku

    return None


# ═══════════════════════════════════════════════════════════
# ATTRIBUTES TABLE
# ═══════════════════════════════════════════════════════════
def _parse_attributes_table(soup: BeautifulSoup, product: dict, source: str) -> dict:
    table = soup.find("table", class_="data table additional-attributes")
    if not table:
        if not product.get("short_description"):
            product["short_description"] = {}
        return product

    if not product.get("short_description"):
        product["short_description"] = {}

    category_found = False

    for row in table.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if not th or not td:
            continue

        key = clean_and_decode_text(th.text)
        value = clean_and_decode_text(td.text)
        if not value:
            continue

        key_lower = key.lower()
        product["short_description"][key] = value

        if "marque" in key_lower and not product.get("brand"):
            product["brand"] = value

        if key_lower == "type" and not category_found:
            product["category"] = value
            category_found = True

    if not product.get("category"):
        product["category"] = source

    return product


# ═══════════════════════════════════════════════════════════
# MAIN PARSER
# ═══════════════════════════════════════════════════════════
def parse_mytek_product_page(
    html: str,
    url: str,
    site: str,
    source: str,
    product_id: Any = None,
) -> dict:
    """
    Parse a Mytek product page.

    Raises ValueError if the Magento price block is missing. This is
    deliberate: the caller would otherwise persist a wrong price.
    """
    soup = BeautifulSoup(html, "lxml")
    product = create_product_schema(site, url, product_id)

    # ── NAME
    name_tag = soup.find("h1", class_="page-title")
    if name_tag:
        product["name"] = clean_and_decode_text(name_tag.text)

    # ── PRICE (Magento script only)
    price_info = _extract_magento_price_info(soup)
    if price_info is None:
        raise ValueError(
            f"Mytek parser: no Magento price block found "
            f"(product_id={product_id!r}, url={url!r}). "
            f"Refusing to emit a price derived from the DOM."
        )

    product["price"] = price_info["price"]
    product["discount_price"] = price_info["discount_price"]
    product["discount_amount"] = price_info["discount_amount"]

    # ── SKU / SPECIAL ID
    sku = _extract_sku(soup)
    if sku:
        product["special_id"] = sku

    # ── AVAILABILITY
    product["availability"] = _extract_availability(soup)

    # ── BRAND
    brand_meta = soup.find("meta", {"property": "product:brand"})
    if brand_meta:
        product["brand"] = clean_and_decode_text(brand_meta.get("content", ""))

    if not product["brand"]:
        product_data = soup.find("div", {"data-product": True})
        if product_data:
            try:
                data_json = product_data.get("data-product")
                if data_json:
                    data = json.loads(data_json)
                    product["brand"] = clean_and_decode_text(data.get("brand", ""))
            except Exception:
                pass

    if not product["brand"]:
        product = _parse_attributes_table(soup, product, source)

    # ── DESCRIPTION
    meta_desc = soup.find("meta", {"property": "og:description"})
    if meta_desc:
        desc = meta_desc.get("content", "")
        desc = desc.split("Retrait")[0]
        desc = desc.split("En savoir")[0]
        product["description"] = clean_html(desc.strip())

    if not product["description"]:
        desc_div = soup.find("div", class_="product-description")
        if desc_div:
            product["description"] = clean_html(desc_div.text)

    if not product["description"]:
        product_data = soup.find("div", {"data-product": True})
        if product_data:
            try:
                data_json = product_data.get("data-product")
                if data_json:
                    data = json.loads(data_json)
                    product["description"] = clean_html(data.get("description", ""))
            except Exception:
                pass

    # ── ATTRIBUTES TABLE (fills short_description, and possibly brand/category)
    product = _parse_attributes_table(soup, product, source)

    # ── IMAGES
    images: list[str] = []
    main_image: Optional[str] = None

    meta_img = soup.find("meta", property="og:image")
    if meta_img and meta_img.get("content"):
        main_image = normalize_url(meta_img["content"].split("?")[0])

    for img in soup.select(".product-image-gallery img"):
        img_url = img.get("src") or img.get("data-src")
        if img_url:
            img_url = normalize_url(img_url.split("?")[0])
            if img_url and img_url not in images:
                images.append(img_url)

    if not images:
        product_img = soup.select_one(".product-image-container img")
        if product_img:
            img_url = product_img.get("src") or product_img.get("data-src")
            if img_url:
                img_url = normalize_url(img_url.split("?")[0])
                if img_url and img_url not in images:
                    images.append(img_url)

    if not main_image and images:
        main_image = images[0]

    product["main_image"] = main_image
    product["images"] = images

    # ── PRICE VALID UNTIL
    valid_until = soup.find("meta", {"property": "product:price:valid_until"})
    if valid_until:
        product["price_valid_until"] = valid_until.get("content")

    # ── CATEGORY
    if not product.get("category"):
        breadcrumbs = soup.select(".breadcrumbs .items .item")
        if breadcrumbs:
            category_text = breadcrumbs[-1].text.strip()
            if category_text:
                product["category"] = clean_and_decode_text(category_text)

    if not product.get("category"):
        product["category"] = source

    # ── MAIN_PRODUCT_ID / URL
    if not product["main_product_id"] and product_id:
        product["main_product_id"] = str(product_id)

    product["url"] = normalize_url(url)

    logger.info(
        f"{site} | PRODUCT_PARSED | "
        f"id={product['main_product_id']} | "
        f"price={product['price']} | "
        f"discount={product['discount_price']}"
    )

    return product


# ═══════════════════════════════════════════════════════════
# FAST CHECK (monitoring worker)
# ═══════════════════════════════════════════════════════════
def fast_check_mytek_product(
    html: str,
    url: str | None = None,
    main_product_id: Any = None,
) -> dict:
    """
    Fast product check for the monitoring pipeline.

    Price fields are script-only. On failure `price` is set to None
    and a `price_error` key is added, so the monitor can skip the
    comparison instead of recording a bogus change.
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict = {}

    # ── PRICE
    price_info = _extract_magento_price_info(soup)
    if price_info is None:
        logger.error(
            f"MAGENTO_PRICE_BLOCK_MISSING | "
            f"id={main_product_id} url={url}"
        )
        result["price"] = None
        result["discount_price"] = None
        result["discount_amount"] = None
        result["price_error"] = "magento_price_block_missing"
    else:
        result["price"] = price_info["price"]
        result["discount_price"] = price_info["discount_price"]
        result["discount_amount"] = price_info["discount_amount"]

    # ── AVAILABILITY
    result["availability"] = _extract_availability(soup)

    # ── SKU
    sku = _extract_sku(soup)
    if sku:
        result["special_id"] = sku

    # ── PRICE VALID UNTIL
    valid_until = soup.find("meta", {"property": "product:price:valid_until"})
    if valid_until:
        result["price_valid_until"] = valid_until.get("content")

    return result


logger.info(f"Loaded {PARSER_VERSION}")