from bs4 import BeautifulSoup
from core.logger import setup_logger

logger = setup_logger("spacenet", "scraper")


def parse_spacenet_products(html, site):

    soup = BeautifulSoup(html, "lxml")

    products = []

    # ---------------------------------
    # ALL PRODUCT CONTAINERS
    # ---------------------------------
    containers = soup.find("div", class_="horizontal_mode")

    if not containers:
        logger.warning(f"{site} | SEARCH_PARSER | NO_PRODUCTS_FOUND")
        return products
    
    lefts = containers.find_all("div", class_="left-product")
    
    for left in lefts:

        # ---------------------------------
        # REAL PRODUCT BLOCK
        # ---------------------------------

        if not left:
            continue

        # ---------------------------------
        # PRODUCT LINK
        # ---------------------------------
        a = left.find("a", href=True)

        if not a:
            continue

        href = a["href"].strip()

        # Skip invalid links
        if not href or href == "#":
            logger.warning(f"{site} | INVALID_LINK | skipped")
            continue

        # ---------------------------------
        # EXTRACT PRODUCT ID
        # ---------------------------------
        product_id = None

        try:
            last_part = href.rstrip("/").split("/")[-1]
            product_id = last_part.split("-")[0]

            if not product_id.isdigit():
                logger.warning(f"{site} | INVALID_ID | {product_id}")
                continue

        except Exception as e:
            logger.warning(f"{site} | ID_PARSE_ERROR | {href} | {e}")
            continue

        # ---------------------------------
        # BUILD PRODUCT
        # ---------------------------------
        products.append({
            "site": site,
            "main_product_id": product_id,
            "product_link": href
        })

    logger.info(f"{site} | SEARCH_PARSER | parsed={len(products)}")

    return products