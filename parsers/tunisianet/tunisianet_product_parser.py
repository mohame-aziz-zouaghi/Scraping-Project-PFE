# parsers/tunisianet/tunisianet_product_parser.py
from datetime import datetime, timedelta
import json
import re
from typing import Dict, Any, Optional, List, Tuple
from bs4 import BeautifulSoup
from core.logger import setup_logger
from core.Web.product_schema import create_product_schema
from core.utils.clean_text_parser import (
    clean_product_text,
    clean_product_name,
    clean_brand_name,
    clean_category_name,
    clean_special_id,
    normalize_availability,
    clean_json_string,
    is_empty_or_placeholder
)
from core.utils.price_cleaner import clean_price_tunisianet, validate_price

logger = setup_logger("tunisianet_parser", "scraper")


# =====================================================
# PRICE EXTRACTION (SITE-SPECIFIC)
# =====================================================

def extract_prices_from_json(
    data: dict,
    decimals: int = 2,
    price_field: str = "price_without_reduction",
    discount_field: str = "price",
    reduction_field: str = "reduction"
) -> dict:
    result = {
        "price": 0.0,
        "discount_price": 0.0,
        "discount_amount": 0.0,
        "has_discount": False
    }
    
    price_without_reduction = data.get(price_field)
    if price_without_reduction:
        result["price"] = clean_price_tunisianet(price_without_reduction, decimals) or 0.0
    else:
        price_amount = data.get("price_amount")
        if price_amount:
            result["price"] = round(float(price_amount), decimals)
    
    current_price = data.get(discount_field)
    if current_price:
        result["discount_price"] = clean_price_tunisianet(current_price, decimals) or 0.0
    else:
        price_amount = data.get("price_amount")
        if price_amount:
            result["discount_price"] = round(float(price_amount), decimals)
    
    reduction = data.get(reduction_field)
    if reduction:
        result["discount_amount"] = clean_price_tunisianet(reduction, decimals) or 0.0
        result["has_discount"] = True
        
        if result["price"] > 0 and result["discount_amount"] > 0:
            result["discount_price"] = round(result["price"] - result["discount_amount"], decimals)
        elif result["discount_price"] == 0 and result["price"] > 0:
            result["discount_price"] = round(result["price"] - result["discount_amount"], decimals)
    else:
        result["discount_price"] = 0.0
        result["discount_amount"] = 0.0
        result["has_discount"] = False
    
    if decimals == 0:
        result["price"] = int(round(result["price"]))
        result["discount_price"] = int(round(result["discount_price"]))
        result["discount_amount"] = int(round(result["discount_amount"]))
    else:
        result["price"] = round(result["price"], decimals)
        result["discount_price"] = round(result["discount_price"], decimals)
        result["discount_amount"] = round(result["discount_amount"], decimals)
    
    if not validate_price(result["price"]):
        result["price"] = 0.0
    
    if not validate_price(result["discount_price"]):
        result["discount_price"] = 0.0
    
    return result


def extract_prices_from_html(soup: BeautifulSoup) -> dict:
    result = {
        "price": 0.0,
        "discount_price": 0.0,
        "discount_amount": 0.0,
        "has_discount": False
    }
    
    prices_container = soup.select_one(".product-prices")
    if not prices_container:
        return result
    
    regular_price_element = prices_container.select_one(".regular-price")
    if regular_price_element:
        regular_price_text = regular_price_element.text.strip()
        result["price"] = clean_price_tunisianet(regular_price_text)
        result["has_discount"] = True
        
        discount_amount_element = prices_container.select_one(".discount-amount")
        if discount_amount_element:
            discount_text = discount_amount_element.text.strip()
            match = re.search(r'(\d+[\.,]?\d*)', discount_text)
            if match:
                result["discount_amount"] = clean_price_tunisianet(match.group(1))
    
    current_price_element = prices_container.select_one(".current-price .price, .current-price span[itemprop='price']")
    if current_price_element:
        current_price_text = current_price_element.text.strip()
        current_price_text = current_price_text.replace("TTC", "").strip()
        result["discount_price"] = clean_price_tunisianet(current_price_text)
    
    if not result["has_discount"] and result["discount_price"]:
        result["price"] = result["discount_price"]
        result["discount_price"] = 0.0
        result["discount_amount"] = 0.0
    
    if result["has_discount"] and result["price"] > 0 and result["discount_price"] > 0:
        if result["discount_amount"] == 0:
            result["discount_amount"] = round(result["price"] - result["discount_price"], 2)
    
    if not validate_price(result["price"]):
        result["price"] = 0.0
    
    return result


def parse_prices(data: dict, soup: BeautifulSoup) -> dict:
    prices = {}
    
    if data:
        json_prices = extract_prices_from_json(data)
        if json_prices["price"] > 0:
            return json_prices
    
    html_prices = extract_prices_from_html(soup)
    if html_prices["price"] > 0:
        return html_prices
    
    price_element = soup.select_one("[itemprop='price']")
    if price_element:
        price_text = price_element.get("content") or price_element.text.strip()
        result = {
            "price": clean_price_tunisianet(price_text) or 0.0,
            "discount_price": 0.0,
            "discount_amount": 0.0,
            "has_discount": False
        }
        return result
    
    return {
        "price": 0.0,
        "discount_price": 0.0,
        "discount_amount": 0.0,
        "has_discount": False
    }


# =====================================================
# HELPER FUNCTIONS
# =====================================================

def extract_text_from_html(html_content):
    if not html_content:
        return ""
    try:
        soup = BeautifulSoup(html_content, "lxml")
        return soup.text.strip()
    except:
        return ""


def parse_features_to_short_description(features: list) -> list:
    short_description = []
    
    if not features:
        return short_description
    
    for feature in features:
        name = feature.get("name", "")
        value = feature.get("value", "")
        
        if name and value:
            clean_name = clean_product_text(name)
            clean_value = clean_product_text(value)
            if clean_name and clean_value:
                short_description.append(f"{clean_name}: {clean_value}")
    
    return short_description


def extract_images_from_data(data: dict) -> Tuple[List[str], Optional[str]]:
    images_list = []
    main_image = None
    
    images = data.get("images", [])
    
    for img in images:
        try:
            url_img = None
            
            if "urls" in img:
                for size in ["large_default", "medium_default", "home_default"]:
                    if size in img["urls"]:
                        url_img = img["urls"][size]
                        break
            elif "bySize" in img:
                for size in ["large", "large_default", "medium_default", "home_default"]:
                    if size in img["bySize"]:
                        url_img = img["bySize"][size]["url"]
                        break
            elif "url" in img:
                url_img = img["url"]
            
            if url_img:
                url_img = clean_product_text(url_img)
                images_list.append(url_img)
                
                if img.get("cover") == "1" or img.get("cover") is True:
                    main_image = url_img
                    
        except Exception as e:
            logger.debug(f"Image extraction error: {e}")
            continue
    
    if not main_image and images_list:
        main_image = images_list[0]
    
    return images_list, main_image


# =====================================================
# MAIN PARSER - RETURNS Dict OR None
# =====================================================

def parse_tunisianet_product_page(html: str, url: str, site: str, main_product_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Parse Tunisianet product page HTML - returns None if invalid"""
    
    if not html:
        logger.error(f"{site} | EMPTY_HTML | {url}")
        return None
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site} | BEAUTIFULSOUP_ERROR | {url} | {e}")
        return None

    product_div = soup.select_one("#product-details")
    if not product_div:
        logger.warning(f"{site} | NO_PRODUCT_DETAILS | {url}")
        return None

    # Extract JSON
    data = {}
    if product_div and product_div.has_attr("data-product"):
        try:
            raw_data = product_div["data-product"]
            raw_data = clean_json_string(raw_data)
            data = json.loads(raw_data)
            logger.debug(f"{site} | JSON_EXTRACTED | {url}")
        except json.JSONDecodeError as e:
            logger.warning(f"{site} | JSON_PARSE_ERROR | {e}")
        except Exception as e:
            logger.warning(f"{site} | JSON_EXTRACT_ERROR | {e}")

    # Extract product ID
    if not main_product_id:
        main_product_id = data.get("id") or data.get("id_product")
    
    if not main_product_id:
        url_match = re.search(r'/(\d+)-', url)
        if url_match:
            main_product_id = url_match.group(1)
    
    if not main_product_id:
        logger.warning(f"{site} | NO_PRODUCT_ID | {url}")
        return None

    # Initialize product
    product = create_product_schema(site, url, main_product_id)

    # Name
    raw_name = data.get("name", "")
    product["name"] = clean_product_name(raw_name)
    
    if not product["name"]:
        title_tag = soup.select_one("h1.product-name")
        if title_tag:
            product["name"] = clean_product_name(title_tag.text.strip())
    
    if not product["name"]:
        logger.warning(f"{site} | NO_PRODUCT_NAME | {url}")
        return None
    
    # Brand
    manufacturer_name = data.get("manufacturer_name")
    if manufacturer_name:
        product["brand"] = clean_brand_name(manufacturer_name)
    else:
        brand_img = soup.select_one(".manufacturer-logo")
        if brand_img:
            alt_text = brand_img.get("alt", "")
            product["brand"] = clean_brand_name(alt_text)
    
    if not product["brand"]:
        meta_brand = soup.select_one('meta[property="product:brand"]')
        if meta_brand:
            product["brand"] = clean_brand_name(meta_brand.get("content", ""))

    # Category
    category_name = data.get("category_name", "")
    product["category"] = clean_category_name(category_name)
    
    if not product["category"]:
        breadcrumb_items = soup.select(".breadcrumb li")
        if breadcrumb_items:
            for item in breadcrumb_items[-2:]:
                cat_text = item.text.strip()
                if cat_text and not is_empty_or_placeholder(cat_text):
                    product["category"] = clean_category_name(cat_text)
                    break
    
    # Special ID
    raw_special_id = data.get("reference", "")
    product["special_id"] = clean_special_id(raw_special_id)
    
    if not product["special_id"] and main_product_id:
        product["special_id"] = clean_special_id(str(main_product_id))

    # Prices
    prices = parse_prices(data, soup)
    
    product["price"] = prices.get("price", 0.0)
    product["discount_price"] = prices.get("discount_price", 0.0)
    product["discount_amount"] = prices.get("discount_amount", 0.0)
    product["has_discount"] = prices.get("has_discount", False)

    if product["price"] <= 0:
        logger.warning(f"{site} | NO_PRICE | {url} | price={product['price']}")
        return None

    # Description
    short_desc_html = data.get("description_short")
    if short_desc_html:
        try:
            clean_text = extract_text_from_html(short_desc_html)
            product["description"] = clean_product_text(clean_text)
        except:
            product["description"] = ""
    
    if not product["description"]:
        desc_html = data.get("description")
        if desc_html:
            try:
                clean_text = extract_text_from_html(desc_html)
                product["description"] = clean_product_text(clean_text)
            except:
                product["description"] = ""

    # Features
    features = data.get("features", [])
    product["short_description"] = parse_features_to_short_description(features)

    if not product["short_description"]:
        spec_table = soup.select_one(".product-features table, .data-sheet table")
        if spec_table:
            rows = spec_table.select("tr")
            for row in rows:
                cells = row.select("td")
                if len(cells) >= 2:
                    name = clean_product_text(cells[0].text.strip())
                    value = clean_product_text(cells[1].text.strip())
                    if name and value and not is_empty_or_placeholder(name) and not is_empty_or_placeholder(value):
                        product["short_description"].append(f"{name}: {value}")

    # Images
    images_list, main_image = extract_images_from_data(data)
    product["images"] = images_list
    product["main_image"] = main_image

    if not product["images"]:
        img_elements = soup.select(".product-images img, #product-images img")
        for img in img_elements:
            src = img.get("src") or img.get("data-src") or img.get("data-image")
            if src and not is_empty_or_placeholder(src):
                src = clean_product_text(src)
                if src.startswith("/"):
                    src = f"https://www.tunisianet.com.tn{src}"
                product["images"].append(src)
                if not product["main_image"]:
                    product["main_image"] = src

    # Availability
    availability_text = None
    
    if data.get("availability_message"):
        availability_text = data.get("availability_message")
    elif data.get("availability"):
        availability_text = data.get("availability")
    else:
        stock_span = soup.select_one("#stock_availability span, .availability span")
        if stock_span:
            availability_text = stock_span.text.strip()
        else:
            quantity = data.get("quantity", 0)
            if quantity > 0:
                availability_text = "InStock"
            else:
                availability_text = "OutOfStock"
    
    product["availability"] = normalize_availability(availability_text)

    # Price valid until
    price_valid_until = data.get("price_valid_until") or data.get("valid_until")
    
    if price_valid_until:
        product["price_valid_until"] = clean_product_text(price_valid_until)
    else:
        default_valid_until = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        product["price_valid_until"] = default_valid_until
        logger.debug(f"{site} | PRICE_VALID_UNTIL_DEFAULT_SET | {default_valid_until}")

    # Additional metadata
    product["reference"] = data.get("reference")
    product["ean"] = data.get("ean13")
    product["upc"] = data.get("upc")
    product["condition"] = data.get("condition", "new")
    
    quantity = data.get("quantity")
    if quantity is not None:
        try:
            product["stock_quantity"] = int(quantity)
        except:
            pass

    # Ensure URL
    if not product.get("url"):
        product["url"] = url

    logger.debug(f"{site} | PRODUCT_PARSED | {product['name'][:50]}..." if product['name'] else f"{site} | PRODUCT_PARSED | {main_product_id}")
    
    return product


# =====================================================
# FAST CHECK FOR MONITORING
# =====================================================

def fast_check_tunisianet_product(url: str, product_id: str = None) -> Optional[Dict[str, Any]]:
    """Quick check function for Tunisianet products."""
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
            logger.warning(f"TUNISIANET_FAST | FETCH_FAILED | product_id={product_id}")
            return None
        
        return parse_tunisianet_product_page(html, url, "tunisianet", product_id)
        
    except Exception as e:
        logger.error(f"TUNISIANET_FAST | CHECK_ERROR | product_id={product_id} | {e}")
        return None