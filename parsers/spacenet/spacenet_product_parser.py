# parsers/spacenet/spacenet_product_parser.py

import json
import re
from bs4 import BeautifulSoup
from datetime import datetime
from core.logger import setup_logger
from core.Web.product_schema import create_product_schema
from core.utils.price_cleaner import clean_price_tunisianet

logger = setup_logger("spacenet", "scraper")


def parse_spacenet_product_page(html, url, site_name, product_id=None):
    soup = BeautifulSoup(html, "lxml")

    # ---------------------------------
    # Initialize product with schema
    # ---------------------------------
    product = create_product_schema(site_name,url,product_id)
    product["url"] = url
    product["main_product_id"] = product_id

    # ---------------------------------
    # Extract JSON-LD Product Script
    # ---------------------------------
    product_json = None
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            data = json.loads(script.string)
            if data.get("@type") == "Product":
                product_json = data
                break
        except Exception as e:
            logger.warning(f"{site_name} | JSON parse error | {e}")

    if not product_json:
        logger.warning(f"{site_name} | PRODUCT_JSON_NOT_FOUND | {url}")
        return product

    # ---------------------------------
    # Base Fields from JSON
    # ---------------------------------
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
        product["main_image"] = product["images"][0]

    # ---------------------------------
    # Price Extraction from HTML
    # ---------------------------------
    price_block = soup.find("div", {"class": "product-prices"})

    # Initialize all price fields
    product["price"] = None
    product["discount_price"] = None
    product["discount_amount"] = None

    if price_block:

        # Check if there's a discounted price
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
            product["price"] = float(product_json.get("offers", {}).get("price", 0))

    # ---------------------------------
    # Availability
    # ---------------------------------
    offers = product_json.get("offers", {})
    availability = offers.get("availability")
    if availability:
        product["availability"] = availability.split("/")[-1]  # e.g., InStock

    return product