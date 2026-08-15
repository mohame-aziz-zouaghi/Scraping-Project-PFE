# parsers/spacenet/spacenet_product_parser.py

import json
import re
from typing import Dict, Any, Optional, List, Union, Set
from bs4 import BeautifulSoup
from datetime import datetime
from core.logger import setup_logger
from core.Web.product_schema import create_product_schema
from core.utils.price_cleaner import clean_price_tunisianet

logger = setup_logger("spacenet", "scraper")


def parse_spacenet_product_page(html: str, url: str, site_name: str, product_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Parse Spacenet product page - returns None if product data is invalid.
    ALWAYS returns a dictionary or None. NEVER returns a set.
    """
    # =====================================================
    # STEP 1: Validate HTML input
    # =====================================================
    if not html:
        logger.warning(f"{site_name} | EMPTY_HTML | {url}")
        return None
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site_name} | BEAUTIFULSOUP_ERROR | {url} | {e}")
        return None

    # =====================================================
    # STEP 2: Initialize product with schema
    # =====================================================
    try:
        product = create_product_schema(site_name, url, product_id)
    except Exception as e:
        logger.error(f"{site_name} | CREATE_SCHEMA_ERROR | {url} | {e}")
        return None
    
    # =====================================================
    # STEP 3: CRITICAL - Validate product type
    # =====================================================
    if product is None:
        logger.error(f"{site_name} | SCHEMA_RETURNED_NONE | {url}")
        return None
    
    # Check if product is a set (the problem we're fixing)
    if isinstance(product, set):
        logger.error(f"{site_name} | SCHEMA_RETURNED_SET | {url} | product is a set")
        # Try to convert set to dict
        try:
            for item in product:
                if isinstance(item, dict):
                    product = item
                    logger.info(f"{site_name} | SCHEMA_SET_CONVERTED | {url}")
                    break
        except:
            pass
        # If still a set, return None
        if isinstance(product, set):
            return None
    
    if not isinstance(product, dict):
        logger.error(f"{site_name} | SCHEMA_NOT_DICT | {url} | type={type(product)}")
        return None
    
    # =====================================================
    # STEP 4: Set basic product fields
    # =====================================================
    product["url"] = url
    product["main_product_id"] = product_id

    # =====================================================
    # STEP 5: Extract JSON-LD Product Script
    # =====================================================
    product_json = None
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            if script.string:
                data = json.loads(script.string)
                if data.get("@type") == "Product":
                    product_json = data
                    break
        except Exception as e:
            logger.debug(f"{site_name} | JSON parse error | {e}")

    # =====================================================
    # STEP 6: If no JSON-LD found, extract from HTML
    # =====================================================
    if not product_json:
        logger.warning(f"{site_name} | PRODUCT_JSON_NOT_FOUND | {url}")
        
        # Extract product name from HTML
        name_elem = soup.find("h1", class_="product-name")
        if not name_elem:
            name_elem = soup.find("h1")
        
        if name_elem:
            product["name"] = name_elem.text.strip()
        else:
            logger.warning(f"{site_name} | NO_PRODUCT_NAME | {url}")
            return None
        
        # Extract price from HTML
        price_elem = soup.find("span", class_="price")
        if price_elem:
            try:
                price_text = price_elem.text.strip()
                product["price"] = clean_price_tunisianet(price_text)
            except:
                product["price"] = 0
        else:
            product["price"] = 0
        
        # Extract availability
        avail_elem = soup.find("span", class_="availability")
        if avail_elem:
            product["availability"] = "InStock" if "disponible" in avail_elem.text.lower() else "OutOfStock"
        else:
            product["availability"] = "OutOfStock"
        
        # Validate
        if not product.get("name") or product.get("price", 0) <= 0:
            logger.warning(f"{site_name} | INVALID_PRODUCT_NO_DATA | {url}")
            return None
        
        # =====================================================
        # FINAL VALIDATION for HTML fallback
        # =====================================================
        if isinstance(product, set):
            logger.error(f"{site_name} | FALLBACK_PRODUCT_IS_SET | {url}")
            return None
        
        if not isinstance(product, dict):
            logger.error(f"{site_name} | FALLBACK_PRODUCT_NOT_DICT | {url} | type={type(product)}")
            return None
        
        return product

    # =====================================================
    # STEP 7: Extract Base Fields from JSON
    # =====================================================
    product["name"] = product_json.get("name")
    product["brand"] = product_json.get("brand", {}).get("name") if isinstance(product_json.get("brand"), dict) else product_json.get("brand")
    product["category"] = product_json.get("category")
    
    if not product_id:
        product["main_product_id"] = product_json.get("productID")

    product["special_id"] = product_json.get("sku")
    product["description"] = product_json.get("description")
    
    images = product_json.get("image")
    if images:
        product["images"] = images if isinstance(images, list) else [images]
        product["main_image"] = product["images"][0] if product["images"] else None

    # =====================================================
    # STEP 8: Price Extraction from HTML
    # =====================================================
    price_block = soup.find("div", {"class": "product-prices"})

    product["price"] = 0.0
    product["discount_price"] = 0.0
    product["discount_amount"] = 0.0

    if price_block:
        discounted_div = price_block.find("div", {"class": "product-price has-discount"})
        
        if discounted_div:
            # Current discounted price
            current_price_div = discounted_div.find("div", {"class": "current-price"})
            if current_price_div:
                discount_price_span = current_price_div.find("span", {"content": True})
                if discount_price_span:
                    product["discount_price"] = clean_price_tunisianet(discount_price_span.text)
                
                discount_amount_span = current_price_div.find("span", {"class": "discount discount-amount"})
                if discount_amount_span:
                    product["discount_amount"] = clean_price_tunisianet(discount_amount_span.text)
            
            # Original/regular price
            regular_price_div = price_block.find("div", {"class": "product-discount"})
            if regular_price_div:
                regular_price_span = regular_price_div.find("span", {"class": "regular-price"})
                if regular_price_span:
                    product["price"] = clean_price_tunisianet(regular_price_span.text)
        else:
            # No discount, fallback to JSON script price
            offers = product_json.get("offers", {})
            if isinstance(offers, dict):
                product["price"] = float(offers.get("price", 0))
            elif isinstance(offers, list) and len(offers) > 0:
                product["price"] = float(offers[0].get("price", 0))
            else:
                product["price"] = 0

    # =====================================================
    # STEP 9: If price is still 0, try to extract from JSON again
    # =====================================================
    if product["price"] == 0:
        offers = product_json.get("offers", {})
        if isinstance(offers, dict):
            product["price"] = float(offers.get("price", 0))
        elif isinstance(offers, list) and len(offers) > 0:
            product["price"] = float(offers[0].get("price", 0))

    # =====================================================
    # STEP 10: Validate product has required fields
    # =====================================================
    if not product.get("name"):
        logger.warning(f"{site_name} | INVALID_PRODUCT_NO_NAME | {url}")
        return None
    
    if product["price"] <= 0:
        # Try one more time from HTML
        price_elem = soup.find("span", class_="price")
        if price_elem:
            try:
                product["price"] = clean_price_tunisianet(price_elem.text.strip())
            except:
                pass
        
        if product["price"] <= 0:
            logger.warning(f"{site_name} | INVALID_PRODUCT_NO_PRICE | {url}")
            return None

    # =====================================================
    # STEP 11: Extract Availability
    # =====================================================
    offers = product_json.get("offers", {})
    if isinstance(offers, dict):
        availability = offers.get("availability")
        if availability:
            product["availability"] = availability.split("/")[-1]
    elif isinstance(offers, list) and len(offers) > 0:
        availability = offers[0].get("availability")
        if availability:
            product["availability"] = availability.split("/")[-1]

    if not product.get("availability"):
        avail_elem = soup.find("span", class_="availability")
        if avail_elem:
            product["availability"] = "InStock" if "disponible" in avail_elem.text.lower() else "OutOfStock"
        else:
            product["availability"] = "OutOfStock"

    # =====================================================
    # STEP 12: Special ID fallback
    # =====================================================
    if not product.get("special_id"):
        reference_elem = soup.find("span", class_="product-reference")
        if reference_elem:
            product["special_id"] = reference_elem.text.strip()

    # =====================================================
    # STEP 13: FINAL VALIDATION - Ensure we return a dict
    # =====================================================
    if isinstance(product, set):
        logger.error(f"{site_name} | FINAL_PRODUCT_IS_SET | {url} | product is a set")
        # Try to convert set to dict one last time
        try:
            for item in product:
                if isinstance(item, dict):
                    product = item
                    logger.info(f"{site_name} | FINAL_SET_CONVERTED | {url}")
                    break
        except:
            pass
        if isinstance(product, set):
            return None
    
    if not isinstance(product, dict):
        logger.error(f"{site_name} | FINAL_PRODUCT_NOT_DICT | {url} | type={type(product)}")
        return None

    # =====================================================
    # STEP 14: Log success and return
    # =====================================================
    logger.debug(f"{site_name} | PRODUCT_PARSED | {product.get('name', 'Unknown')[:50]}...")
    
    return product


def fast_check_spacenet_product(url: str, product_id: str = None) -> Optional[Dict[str, Any]]:
    """Quick check function for Spacenet products."""
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
            logger.warning(f"SPACENET_FAST | FETCH_FAILED | product_id={product_id}")
            return None
        
        return parse_spacenet_product_page(html, url, "spacenet", product_id)
        
    except Exception as e:
        logger.error(f"SPACENET_FAST | CHECK_ERROR | product_id={product_id} | {e}")
        return None