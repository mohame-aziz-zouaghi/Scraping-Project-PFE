# parsers/spacenet/spacenet_search_parser.py

from bs4 import BeautifulSoup
import re
from typing import List, Dict, Optional, Any
from core.logger import setup_logger
from core.utils.clean_text_parser import clean_and_decode_text
from core.utils.cleaning_url_logic import normalize_url

logger = setup_logger("spacenet_parser", "parser")


def parse_spacenet_products(html: str, site: str) -> List[Dict[str, Any]]:
    """
    Parse products from Spacenet search/category page using utility functions.
    ALWAYS returns a list of dictionaries. NEVER returns None or a set.
    """
    if not html:
        logger.warning(f"{site} | SEARCH_PARSER | EMPTY_HTML")
        return []

        # Log HTML snippet for debugging
    html_preview = html[:500] if html else ""
    logger.debug(f"{site} | HTML_PREVIEW | {html_preview[:200]}...")
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site} | SEARCH_PARSER | BEAUTIFULSOUP_ERROR | {e}")
        return []

    products: List[Dict[str, Any]] = []
    seen_ids: set = set()

    # =====================================================
    # METHOD 1: Find product containers
    # =====================================================
    containers = soup.find("div", class_="horizontal_mode")
    
    if not containers:
        containers = soup.find("div", class_="product-list")
    
    if not containers:
        containers = soup.find("div", class_="category-products")
    
    if not containers:
        # =====================================================
        # METHOD 2: Fallback - find all product items directly
        # =====================================================
        product_items = soup.find_all("div", class_=re.compile(r'product', re.I))
        for item in product_items:
            try:
                if item is None:
                    continue
                
                a = item.find("a", href=True)
                if not a:
                    continue
                
                href = a.get("href", "").strip()
                if not href or href == "#":
                    continue
                
                product_link = normalize_url(href)
                
                product_id = None
                match = re.search(r'/product/(\d+)', href)
                if match:
                    product_id = match.group(1)
                
                if not product_id:
                    match = re.search(r'id_product[=:](\d+)', href)
                    if match:
                        product_id = match.group(1)
                
                if not product_id:
                    # Try from data attribute
                    product_id = item.get("data-id-product") or item.get("data-product-id")
                
                if not product_id:
                    continue
                
                product_id = str(product_id)
                
                if not product_id.isdigit():
                    continue
                
                if product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                
                # Build dictionary with cleaned data
                product_data: Dict[str, Any] = {
                    "site": site,
                    "main_product_id": product_id,
                    "product_link": product_link
                }
                
                name_elem = item.select_one(".product-name, .product-title, h2, h3")
                if name_elem:
                    product_data["name"] = clean_and_decode_text(name_elem.text)
                
                price_elem = item.select_one(".price, .product-price, .current-price")
                if price_elem:
                    product_data["price"] = clean_and_decode_text(price_elem.text)
                
                products.append(product_data)
                
            except Exception as e:
                logger.debug(f"{site} | SEARCH_PARSER | ITEM_ERROR | {e}")
                continue
        
        logger.info(f"{site} | SEARCH_PARSER | parsed={len(products)}")
        return products
    
    # =====================================================
    # METHOD 3: Process left-product elements
    # =====================================================
    lefts = containers.find_all("div", class_="left-product")
    
    if not lefts:
        lefts = containers.find_all("div", class_=re.compile(r'product', re.I))
    
    for left in lefts:
        try:
            if left is None:
                continue
            
            a = left.find("a", href=True)
            if not a:
                continue

            href = a.get("href", "").strip()
            if not href or href == "#":
                continue

            product_link = normalize_url(href)

            product_id = None
            
            # Try multiple methods to extract product ID
            match = re.search(r'/product/(\d+)', href)
            if match:
                product_id = match.group(1)
            
            if not product_id:
                match = re.search(r'id_product[=:](\d+)', href)
                if match:
                    product_id = match.group(1)
            
            if not product_id:
                product_id = left.get("data-id-product")
                if not product_id:
                    product_id = left.get("data-product-id")
            
            if not product_id:
                try:
                    last_part = href.rstrip("/").split("/")[-1]
                    match = re.search(r'(\d+)', last_part)
                    if match:
                        product_id = match.group(1)
                except Exception:
                    pass
            
            if not product_id:
                continue
            
            product_id = str(product_id)
            
            if not product_id.isdigit():
                continue

            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            # Build dictionary with cleaned data
            product_data: Dict[str, Any] = {
                "site": site,
                "main_product_id": product_id,
                "product_link": product_link
            }
            
            name_elem = left.select_one(".product-name, .product-title, h2, h3")
            if name_elem:
                product_data["name"] = clean_and_decode_text(name_elem.text)
            
            price_elem = left.select_one(".price, .product-price, .current-price")
            if price_elem:
                product_data["price"] = clean_and_decode_text(price_elem.text)
            
            # Extract image if available
            img_elem = left.select_one("img")
            if img_elem:
                img_url = img_elem.get("src") or img_elem.get("data-src")
                if img_url:
                    product_data["image"] = normalize_url(img_url)
            
            products.append(product_data)

        except Exception as e:
            logger.debug(f"{site} | SEARCH_PARSER | ELEMENT_ERROR | {e}")
            continue

    logger.info(f"{site} | SEARCH_PARSER | parsed={len(products)}")
    return products


def parse_spacenet_category_products(html: str, site: str, category_name: str = "") -> List[Dict[str, Any]]:
    """
    Parse products from a Spacenet category page.
    This is an alias for parse_spacenet_products for clarity.
    """
    return parse_spacenet_products(html, site)