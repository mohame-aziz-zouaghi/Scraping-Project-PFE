# parsers/sbs/sbs_product_parser.py - UPDATED with redirect detection

import json
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
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


def clean_json_for_parsing(text: str) -> str:
    """
    Clean JSON string by removing/replacing problematic characters.
    """
    if not text:
        return text
    
    # Replace tab characters with spaces
    text = text.replace('\t', ' ')
    
    # Remove other control characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    return text


def is_category_page(soup: BeautifulSoup, url: str) -> bool:
    """
    Detect if the page is a category page (not a product page).
    SBS redirects to category when product is removed.
    """
    # Check for category page indicators
    # 1. Check for "Cartes graphiques" or category title
    category_title = soup.select_one(".page-heading .h1, h1.page-heading")
    if category_title:
        # Check if the title contains category words
        text = category_title.text.lower().strip()
        # Common category titles: "Cartes graphiques", "Alimentations", etc.
        # If there's no product name in the title, it's likely a category
        if "produits" in text or "catégorie" in text or "catégories" in text:
            return True
        # If the title has no product-specific words, check other indicators
    
    # 2. Check for product grid on the page
    product_grid = soup.select_one(".products, .product-list, .product-grid")
    if product_grid and not soup.select_one("#product-details, .product-cover"):
        # If there's a product grid but no product details, it's a category page
        return True
    
    # 3. Check if there are multiple products listed
    product_items = soup.select(".product-miniature, .product-item, .tvproduct-wrapper")
    if len(product_items) > 1 and not soup.select_one("#product-details"):
        return True
    
    # 4. Check for "23 produits" text in the page
    page_text = soup.get_text()
    if re.search(r'\d+\s+produits?', page_text) and not soup.select_one("#product-details"):
        return True
    
    return False


def is_product_redirected(html: str, url: str) -> bool:
    """
    Check if the page is a redirect to a category page.
    """
    if not html:
        return False
    
    # Check if the URL changed (redirect detection)
    # Since we get the HTML directly, we need to check content
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return False
    
    # Check if it's a category page
    return is_category_page(soup, url)


def parse_sbs_product_page(html: str, url: str, site: str, main_product_id: Optional[str] = None) -> Optional[Dict]:
    """
    Parse SBS product page with redirect detection.
    Returns None if the product is removed/redirected.
    """
    if not html:
        logger.warning(f"{site} | EMPTY_HTML | {url}")
        return None
    
    # ─── CHECK FOR REDIRECT / CATEGORY PAGE ──────────────────
    if is_product_redirected(html, url):
        logger.warning(f"{site} | PRODUCT_REMOVED_REDIRECTED | {url}")
        return None
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site} | BEAUTIFULSOUP_ERROR | {url} | {e}")
        return None

    # ─── CHECK FOR PRODUCT DETAILS ────────────────────────────
    # If no product details div, it's not a product page
    product_details = soup.select_one("#product-details, .product-cover, .product-prices")
    if not product_details:
        logger.warning(f"{site} | NO_PRODUCT_DETAILS | {url}")
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
        
        # Try to extract from URL if still not found
        if not main_product_id:
            match = re.search(r'/(\d+)(?:/|$)', url)
            if match:
                main_product_id = match.group(1)

    # Create product schema
    try:
        product = create_product_schema(site, url, main_product_id)
    except Exception as e:
        logger.error(f"{site} | CREATE_SCHEMA_ERROR | {e}")
        return None

    # =====================================================
    # FIXED: JSON-LD EXTRACTION - HANDLE MALFORMED JSON
    # =====================================================
    json_data = None
    
    # Find all JSON-LD scripts
    scripts = soup.find_all("script", type="application/ld+json")
    
    for script in scripts:
        if not script.string:
            continue
        
        try:
            # Get the raw script content
            raw_content = script.string
            
            # Clean the content before parsing
            cleaned_content = clean_json_for_parsing(raw_content)
            
            # Parse the JSON
            data = json.loads(cleaned_content)
            
            # Check if it's a Product schema
            if data.get("@type") == "Product":
                json_data = data
                logger.debug(f"{site} | FOUND_PRODUCT_JSON")
                break
                
        except json.JSONDecodeError as e:
            logger.debug(f"{site} | JSON_DECODE_ERROR | {e}")
            continue
        except Exception as e:
            logger.debug(f"{site} | JSON_LOAD_ERROR | {e}")
            continue

    # ─── LOG WHAT WAS FOUND (FOR DEBUGGING) ─────────────────────
    if json_data:
        logger.debug(f"{site} | PRODUCT_JSON_FOUND | name={json_data.get('name', '')[:30]}")
    else:
        logger.warning(f"{site} | NO_PRODUCT_JSON_FOUND | url={url}")
        return None

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
                else:
                    # Default: 7 days from now
                    product["price_valid_until"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                
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
                product["discount_price"] = json_price if json_price else 0.0
                
                if json_price and html_discounted_price:
                    product["discount_amount"] = round(html_discounted_price - json_price, 2)
                else:
                    product["discount_amount"] = 0.0
    
    # If no discount, use JSON price
    if not discount_block:
        product["discount_price"] = 0.0
        product["discount_amount"] = 0.0

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