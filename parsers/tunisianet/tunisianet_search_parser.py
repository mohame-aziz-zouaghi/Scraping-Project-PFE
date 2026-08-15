# parsers/tunisianet/tunisianet_search_parser.py
from bs4 import BeautifulSoup
import re
from typing import List, Dict, Optional, Any
from core.logger import setup_logger

logger = setup_logger("tunisianet_parser", "parser")


def parse_tunisianet_products(html: str, site: str) -> List[Dict[str, Any]]:
    """
    Parse products from Tunisianet search/category page.
    ALWAYS returns a list of dictionaries. NEVER returns None.
    """
    if not html:
        logger.warning(f"{site} | parse_tunisianet_products: Empty HTML")
        return []
    
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.error(f"{site} | parse_tunisianet_products: BeautifulSoup error: {e}")
        return []

    products: List[Dict[str, Any]] = []
    seen_ids: set = set()

    # Try multiple selectors for product items
    articles = soup.select(".product-miniature")
    
    if not articles:
        articles = soup.select(".product-item, .product-container, .product-block, .product_list .ajax_block_product")
    
    if not articles:
        product_links = soup.select('a[href*="/produit/"], a[href*="id_product="]')
        for link in product_links:
            parent = link.parent
            if parent:
                wrapper = BeautifulSoup(f'<div class="product-miniature">{parent}</div>', 'html.parser')
                articles.append(wrapper.find('div'))

    for article in articles:
        try:
            if article is None:
                continue
            
            a = article.select_one("a")
            if not a:
                continue

            href = a.get("href")
            if not href:
                continue

            product_id = None
            
            match = re.search(r'/produit/(\d+)', href)
            if match:
                product_id = match.group(1)
            
            if not product_id:
                match = re.search(r'id_product[=:](\d+)', href)
                if match:
                    product_id = match.group(1)
            
            if not product_id:
                try:
                    parts = href.split("/")
                    for part in parts:
                        if part and part[0].isdigit():
                            product_id = part.split("-")[0]
                            break
                except:
                    pass
            
            if not product_id:
                continue

            if href.startswith('/'):
                product_link = f"https://www.tunisianet.com.tn{href}"
            else:
                product_link = href

            if product_id and product_link and product_id not in seen_ids:
                seen_ids.add(product_id)
                products.append({
                    "site": site,
                    "main_product_id": str(product_id),
                    "product_link": product_link
                })
            
        except Exception as e:
            logger.debug(f"{site} | parse_tunisianet_products: Error parsing article: {e}")
            continue

    logger.info(f"{site} | parsed {len(products)} products")
    return products