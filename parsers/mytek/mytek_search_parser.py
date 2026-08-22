# parsers/mytek/mytek_search_parser.py

from bs4 import BeautifulSoup
import json
import re
from typing import List, Dict, Optional

from core.logger import setup_logger

logger = setup_logger("mytek", "scraper")


def parse_mytek_products(html: str, site: str) -> List[Dict]:
    """
    Parse Mytek product listings from HTML.
    Uses the new layout with seo-product-data div containing data-* attributes.
    """
    soup = BeautifulSoup(html, "lxml")
    products = []

    # =====================================================
    # METHOD 1: Parse from seo-product-data div (NEW LAYOUT)
    # =====================================================
    seo_div = soup.find("div", id="seo-product-data")
    
    if seo_div:
        product_divs = seo_div.find_all("div", attrs={"data-id": True})
        
        for div in product_divs:
            try:
                product_id = div.get("data-id")
                product_url = div.get("data-url")
                product_name = div.get("data-name")
                product_price = div.get("data-price")
                product_sku = div.get("data-sku")
                product_stock = div.get("data-erpstock")
                product_manufacturer = div.get("data-manufacturer")
                
                if not product_id or not product_url:
                    continue
                
                # Convert price to float if exists
                try:
                    price = float(product_price) if product_price else 0
                except:
                    price = 0
                
                products.append({
                    "site": site,
                    "main_product_id": str(product_id),
                    "product_link": product_url,
                    "name": product_name,
                    "price": price,
                    "sku": product_sku,
                    "availability": _normalize_availability(product_stock),
                    "brand": product_manufacturer
                })
                
            except Exception as e:
                logger.debug(f"PARSE_PRODUCT_ERROR | {site} | {e}")
                continue
        
        if products:
            logger.info(f"{site} | SEARCH_PARSER | parsed {len(products)} products from seo-product-data")
            return products
    
    # =====================================================
    # METHOD 2: Fallback - Parse from product cards (OLD LAYOUT)
    # =====================================================
    product_cards = soup.select(".product-container")
    
    for card in product_cards:
        try:
            # Look for product link
            link = card.find("a", href=True)
            if not link:
                continue
            
            # Try to get product ID from href
            href = link.get("href", "")
            product_id = _extract_product_id_from_url(href)
            
            if not product_id:
                continue
            
            products.append({
                "site": site,
                "main_product_id": str(product_id),
                "product_link": href
            })
            
        except Exception as e:
            logger.debug(f"PARSE_CARD_ERROR | {site} | {e}")
            continue
    
    # =====================================================
    # METHOD 3: Fallback - Parse from script (OLD OLD LAYOUT)
    # =====================================================
    if not products:
        products = _parse_from_script(soup, site)
    
    logger.info(f"{site} | SEARCH_PARSER | parsed {len(products)} products")
    return products


def _normalize_availability(stock_text: str) -> str:
    """Normalize availability text to standard format."""
    if not stock_text:
        return "OutOfStock"
    
    stock_text = stock_text.strip().lower()
    
    in_stock_keywords = ["en stock", "disponible", "in stock", "en ligne"]
    out_of_stock_keywords = ["épuisé", "epuise", "out of stock", "rupture"]
    preorder_keywords = ["en précommande", "precommande", "pre-order", "en arrivage", "arrivage"]
    
    for keyword in in_stock_keywords:
        if keyword in stock_text:
            return "InStock"
    
    for keyword in preorder_keywords:
        if keyword in stock_text:
            return "OnOrder"
    
    for keyword in out_of_stock_keywords:
        if keyword in stock_text:
            return "OutOfStock"
    
    return "OutOfStock"


def _extract_product_id_from_url(url: str) -> Optional[str]:
    """Extract product ID from Mytek URL."""
    if not url:
        return None
    
    # Try to match pattern like /pc-portable-...-12345.html
    match = re.search(r'-(\d+)\.html$', url)
    if match:
        return match.group(1)
    
    # Try to match pattern like ?id_product=12345
    match = re.search(r'id_product=(\d+)', url)
    if match:
        return match.group(1)
    
    return None


def _parse_from_script(soup: BeautifulSoup, site: str) -> List[Dict]:
    """Fallback: Parse from INITIAL_PRODUCTS_DATA script (old layout)."""
    products = []
    
    scripts = soup.find_all("script", {"type": "text/javascript"})
    
    for script in scripts:
        if script.string and "INITIAL_PRODUCTS_DATA" in script.string:
            try:
                match = re.search(
                    r'INITIAL_PRODUCTS_DATA\s*=\s*(\[[^\]]*\])',
                    script.string
                )
                
                if match:
                    json_text = match.group(1)
                    data = json.loads(json_text)
                    
                    for item in data:
                        product_id = item.get("id")
                        url = item.get("url")
                        
                        if product_id and url:
                            products.append({
                                "site": site,
                                "main_product_id": str(product_id),
                                "product_link": url
                            })
                    
                    break
                    
            except Exception as e:
                logger.debug(f"SCRIPT_PARSE_ERROR | {site} | {e}")
                continue
    
    return products


def parse_mytek_category_products(html: str, site: str, category_name: str = "") -> List[Dict]:
    """
    Parse products from a Mytek category page.
    This is an alias for parse_mytek_products for clarity.
    """
    return parse_mytek_products(html, site)