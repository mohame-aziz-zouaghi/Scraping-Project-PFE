# parsers/sbs/sbs_product_parser.py

import json
import re
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Optional, List, Any

from core.logger import setup_logger
from core.Web.product_schema import create_product_schema
from core.utils.price_cleaner import clean_price, clean_price_generic
from core.utils.clean_text_parser import (
    clean_and_decode_text, 
    normalize_availability,
    clean_product_name,
    clean_brand_name,
    clean_category_name,
    clean_special_id
)
from core.utils.Dclean_html import clean_html
from core.utils.cleaning_url_logic import normalize_url
from core.utils.clean_date_logic import clean_date
from core.utils.clean_parse_specs import parse_specs

logger = setup_logger("sbs_parser", "parser")


def clean_price_sbs(val: Any) -> Optional[float]:
    """
    Clean SBS price format using the universal price cleaner.
    """
    if not val:
        return None
    return clean_price(val, decimals=2, currency="TND")


def clean_json_ld_text(text: str) -> str:
    """
    Clean JSON-LD text by removing invalid control characters.
    """
    if not text:
        return text
    
    # Remove invalid control characters (except valid whitespace)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    # Fix common unescaped characters in JSON strings
    text = re.sub(r'(?<!\\)\n', '\\n', text)
    text = re.sub(r'(?<!\\)\r', '\\r', text)
    text = re.sub(r'(?<!\\)\t', '\\t', text)
    
    return text


def safe_json_loads(text: str) -> Optional[Dict]:
    """
    Safely load JSON from string with error handling and cleaning.
    """
    if not text:
        return None
    
    # Try normal parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.debug(f"JSON_PARSE_ERROR | {e}")
        
        # Try cleaning and re-parsing
        try:
            cleaned = clean_json_ld_text(text)
            return json.loads(cleaned)
        except json.JSONDecodeError as e2:
            logger.debug(f"JSON_PARSE_ERROR_AFTER_CLEANING | {e2}")
            
            # Try regex extraction for critical fields
            try:
                return _extract_product_from_text(text)
            except Exception as e3:
                logger.debug(f"REGEX_EXTRACTION_FAILED | {e3}")
                return None


def _extract_product_from_text(text: str) -> Optional[Dict]:
    """
    Extract product data from text using regex as fallback.
    """
    result = {}
    
    # Extract name
    name_match = re.search(r'"name"\s*:\s*"([^"]*)"', text)
    if name_match:
        result['name'] = clean_and_decode_text(name_match.group(1))
    
    # Extract description
    desc_match = re.search(r'"description"\s*:\s*"([^"]*)"', text)
    if desc_match:
        result['description'] = clean_html(desc_match.group(1))
    
    # Extract price
    price_match = re.search(r'"price"\s*:\s*"([^"]*)"', text)
    if price_match:
        result['price'] = clean_price_sbs(price_match.group(1))
    
    # Extract sku/mpn
    sku_match = re.search(r'"sku"\s*:\s*"([^"]*)"', text)
    if sku_match:
        result['sku'] = clean_special_id(sku_match.group(1))
    
    mpn_match = re.search(r'"mpn"\s*:\s*"([^"]*)"', text)
    if mpn_match:
        result['mpn'] = clean_special_id(mpn_match.group(1))
    
    # Extract brand
    brand_match = re.search(r'"brand"\s*:\s*{[^}]*"name"\s*:\s*"([^"]*)"', text)
    if brand_match:
        result['brand'] = clean_brand_name(brand_match.group(1))
    
    # Extract availability
    avail_match = re.search(r'"availability"\s*:\s*"([^"]*)"', text)
    if avail_match:
        result['availability'] = normalize_availability(avail_match.group(1).split('/')[-1])
    
    # Extract price valid until
    valid_match = re.search(r'"priceValidUntil"\s*:\s*"([^"]*)"', text)
    if valid_match:
        result['priceValidUntil'] = clean_date(valid_match.group(1))
    
    # Extract category
    cat_match = re.search(r'"category"\s*:\s*"([^"]*)"', text)
    if cat_match:
        result['category'] = clean_category_name(cat_match.group(1))
    
    # Extract images
    image_matches = re.findall(r'"image"\s*:\s*"([^"]*)"', text)
    if image_matches:
        result['images'] = [normalize_url(img) for img in image_matches if img]
    
    return result if result.get('name') else None


def parse_sbs_product_page(html: str, url: str, site: str, main_product_id: Optional[str] = None) -> Optional[Dict]:
    """
    Parse SBS product page with proper error handling and cleaning using utility methods.
    """
    if not html:
        logger.warning(f"{site} | EMPTY_HTML | {url}")
        return None
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site} | BEAUTIFULSOUP_ERROR | {url} | {e}")
        return None

    # Extract product id if not provided
    if not main_product_id:
        body = soup.find("body")
        if body:
            classes = body.get("class", [])
            for c in classes:
                if "product-id-" in c:
                    main_product_id = c.split("product-id-")[-1]
                    break

    # Create product schema
    try:
        product = create_product_schema(site, url, main_product_id)
    except Exception as e:
        logger.error(f"{site} | CREATE_SCHEMA_ERROR | {e}")
        return None

    # =====================================================
    # PARSE JSON-LD DATA WITH ERROR HANDLING
    # =====================================================
    scripts = soup.find_all("script", type="application/ld+json")
    json_data = None

    for script in scripts:
        if not script.string:
            continue
        
        try:
            data = safe_json_loads(script.string)
            if data and data.get("@type") == "Product":
                json_data = data
                break
        except Exception as e:
            logger.debug(f"{site} | JSON_LOAD_ERROR | {e}")
            continue

    # =====================================================
    # EXTRACT DATA FROM JSON
    # =====================================================
    if json_data:
        try:
            # Name - using clean_product_name
            if json_data.get("name"):
                product["name"] = clean_product_name(json_data["name"])
            
            # Description - using clean_html
            if json_data.get("description"):
                product["description"] = clean_html(json_data["description"])
            
            # Category - using clean_category_name
            if json_data.get("category"):
                product["category"] = clean_category_name(json_data["category"])
            
            # Brand - using clean_brand_name
            brand = json_data.get("brand", {})
            if isinstance(brand, dict):
                product["brand"] = clean_brand_name(brand.get("name"))
            elif isinstance(brand, str):
                product["brand"] = clean_brand_name(brand)
            
            # SKU / MPN - using clean_special_id
            if json_data.get("mpn"):
                product["special_id"] = clean_special_id(json_data["mpn"])
            elif json_data.get("sku"):
                product["special_id"] = clean_special_id(json_data["sku"])
            
            # Offers
            offers = json_data.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            
            if offers:
                # Price - using clean_price_sbs
                if offers.get("price"):
                    product["price"] = clean_price_sbs(offers["price"])
                
                # Price valid until - using clean_date
                if offers.get("priceValidUntil"):
                    product["price_valid_until"] = clean_date(offers["priceValidUntil"])
                
                # Availability - using normalize_availability
                if offers.get("availability"):
                    product["availability"] = normalize_availability(offers["availability"].split("/")[-1])
            
            # Images - using normalize_url
            images = json_data.get("image", [])
            if isinstance(images, str):
                images = [images]
            elif isinstance(images, dict):
                if images.get("url"):
                    images = [images["url"]]
                else:
                    images = []
            
            if images:
                product["images"] = [normalize_url(img) for img in images if img]
                product["main_image"] = product["images"][0] if product["images"] else None
            
        except Exception as e:
            logger.error(f"{site} | JSON_PARSING_ERROR | {e}")

    # =====================================================
    # PRICE LOGIC - Handle discount from HTML
    # =====================================================
    json_price = product.get("price")
    
    # Look for discount block in HTML
    discount_block = soup.select_one(".product-price.has-discount")
    
    if discount_block:
        # Try to get discounted price from HTML
        regular_price_el = soup.select_one(".product-discount .regular-price")
        if regular_price_el:
            html_discounted_price = clean_price_sbs(regular_price_el.text)
            
            if html_discounted_price:
                # SBS discount convention: 
                # price = discounted price (main price)
                # discount_price = original price (for reference)
                product["price"] = html_discounted_price
                product["discount_price"] = json_price
                
                if json_price and html_discounted_price:
                    product["discount_amount"] = round(html_discounted_price - json_price, 2)
                else:
                    product["discount_amount"] = 0
    
    # If no discount, use JSON price
    if not discount_block:
        product["discount_price"] = 0
        product["discount_amount"] = 0

    # =====================================================
    # SHORT DESCRIPTION / SPECS - using parse_specs
    # =====================================================
    product["short_description"] = {}
    
    # Try to get specs from table
    specs_table = soup.select_one("table.data.table.additional-attributes")
    if specs_table:
        rows = specs_table.find_all("tr")
        for row in rows:
            th = row.find("th")
            td = row.find("td")
            if th and td:
                key = clean_and_decode_text(th.text)
                value = clean_and_decode_text(td.text)
                if key and value:
                    product["short_description"][key] = value
    
    # Fallback: get specs from product description short
    if not product["short_description"] and main_product_id:
        rows = soup.select(f"#product-description-short-{main_product_id} tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                value = clean_and_decode_text(cols[1].text)
                if value:
                    key = f"spec_{len(product['short_description']) + 1}"
                    product["short_description"][key] = value
    
    # Second fallback: get specs from list items
    if not product["short_description"]:
        desc_items = soup.select("#product-description-short li")
        for li in desc_items:
            text = clean_and_decode_text(li.text)
            if text:
                key = f"spec_{len(product['short_description']) + 1}"
                product["short_description"][key] = text
    
    # =====================================================
    # VALIDATE PRODUCT
    # =====================================================
    if not product.get("name"):
        logger.warning(f"{site} | DETAILS_NO_NAME | {main_product_id} | url={url}")
        return None
    
    if not product.get("price") or product.get("price") <= 0:
        logger.warning(f"{site} | DETAILS_NO_PRICE | {main_product_id} | price={product.get('price')}")
        return None
    
    if not product.get("url"):
        product["url"] = normalize_url(url)

    # =====================================================
    # LOG SUCCESS
    # =====================================================
    if product.get("name"):
        logger.info(f"{site} | PRODUCT_PARSED | {product['name'][:50]}... | id={main_product_id}")
    
    return product


def fast_check_sbs_product(url: str, product_id: Optional[str] = None) -> Optional[Dict]:
    """
    Fast check function for SBS products.
    """
    from core.Web.http_client import get
    
    try:
        html = get(
            url=url,
            max_retries=4,
            timeout=45,
            requests_per_minute=10,
            use_referer=True,
            verify_ssl=True
        )
        
        if not html:
            logger.warning(f"SBS_FAST | FETCH_FAILED | product_id={product_id}")
            return None
        
        return parse_sbs_product_page(html, url, "sbs", product_id)
        
    except Exception as e:
        logger.error(f"SBS_FAST | CHECK_ERROR | product_id={product_id} | {e}")
        return None