from bs4 import BeautifulSoup
import json
import re

from core.logger import setup_logger

logger = setup_logger("mytek", "scraper")


def parse_mytek_products(html, site):

    soup = BeautifulSoup(html, "lxml")

    products = []

    # ---------------------------------
    # FIND SCRIPT CONTAINING PRODUCTS
    # ---------------------------------
    scripts = soup.find_all("script", {"type": "text/javascript"})

    target_script = None

    for script in scripts:
        if script.string and "INITIAL_PRODUCTS_DATA" in script.string:
            target_script = script.string
            break

    if not target_script:
        logger.warning(f"{site} | SEARCH_PARSER | SCRIPT_NOT_FOUND")
        return products

    # ---------------------------------
    # EXTRACT JSON FROM SCRIPT
    # ---------------------------------
    try:
        match = re.search(
            r'INITIAL_PRODUCTS_DATA\s*=\s*(\[[^\]]*\])',
            target_script
        )

        if not match:
            logger.warning(f"{site} | SEARCH_PARSER | JSON_NOT_FOUND")
            return products

        json_text = match.group(1)

        data = json.loads(json_text)

    except Exception as e:
        logger.error(f"{site} | JSON_PARSE_ERROR | {e}")
        return products

    # ---------------------------------
    # BUILD PRODUCTS
    # ---------------------------------
    for item in data:

        product_id = item.get("id")
        url = item.get("url")

        if not product_id or not url:
            continue

        products.append({
            "site": site,
            "main_product_id": str(product_id),
            "product_link": url
        })

    logger.info(f"{site} | SEARCH_PARSER | parsed={len(products)}")

    return products