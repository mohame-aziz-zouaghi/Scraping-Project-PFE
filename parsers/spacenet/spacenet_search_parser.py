# parsers/spacenet/spacenet_search_parser.py

from bs4 import BeautifulSoup
import re
from typing import List, Dict, Optional, Any
from core.logger import setup_logger

logger = setup_logger("spacenet_parser", "parser")


def parse_spacenet_products(html: str, site: str) -> List[Dict[str, Any]]:
    """
    Parse products from Spacenet search/category page.
    ALWAYS returns a list of dictionaries. NEVER returns None or a set.
    """
    if not html:
        logger.warning(f"{site} | SEARCH_PARSER | EMPTY_HTML")
        return []
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site} | SEARCH_PARSER | BEAUTIFULSOUP_ERROR | {e}")
        return []

    products: List[Dict[str, Any]] = []
    seen_ids: set = set()

    # Find product containers
    containers = soup.find("div", class_="horizontal_mode")
    
    if not containers:
        containers = soup.find("div", class_="product-list")
    
    if not containers:
        containers = soup.find("div", class_="category-products")
    
    if not containers:
        # Fallback: find all product items directly
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
                
                if href.startswith('/'):
                    product_link = f"https://spacenet.tn{href}"
                else:
                    product_link = href
                
                product_id = None
                match = re.search(r'/product/(\d+)', href)
                if match:
                    product_id = match.group(1)
                
                if not product_id:
                    match = re.search(r'id_product[=:](\d+)', href)
                    if match:
                        product_id = match.group(1)
                
                if not product_id:
                    continue
                
                product_id = str(product_id)
                
                if not product_id.isdigit():
                    continue
                
                if product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                
                # Build dictionary - NOT a set
                product_data: Dict[str, Any] = {
                    "site": site,
                    "main_product_id": product_id,
                    "product_link": product_link
                }
                
                name_elem = item.select_one(".product-name, .product-title, h2, h3")
                if name_elem:
                    product_data["name"] = name_elem.text.strip()
                
                price_elem = item.select_one(".price, .product-price, .current-price")
                if price_elem:
                    product_data["price"] = price_elem.text.strip()
                
                products.append(product_data)
                
            except Exception as e:
                logger.debug(f"{site} | SEARCH_PARSER | ITEM_ERROR | {e}")
                continue
        
        logger.info(f"{site} | SEARCH_PARSER | parsed={len(products)}")
        return products
    
    # Process left-product elements
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

            if href.startswith('/'):
                product_link = f"https://spacenet.tn{href}"
            else:
                product_link = href

            product_id = None
            
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

            # Build dictionary - NOT a set
            product_data: Dict[str, Any] = {
                "site": site,
                "main_product_id": product_id,
                "product_link": product_link
            }
            
            name_elem = left.select_one(".product-name, .product-title, h2, h3")
            if name_elem:
                product_data["name"] = name_elem.text.strip()
            
            price_elem = left.select_one(".price, .product-price, .current-price")
            if price_elem:
                product_data["price"] = price_elem.text.strip()
            
            products.append(product_data)

        except Exception as e:
            logger.debug(f"{site} | SEARCH_PARSER | ELEMENT_ERROR | {e}")
            continue

    logger.info(f"{site} | SEARCH_PARSER | parsed={len(products)}")
    return products