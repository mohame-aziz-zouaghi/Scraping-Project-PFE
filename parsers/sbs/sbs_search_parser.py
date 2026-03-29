from bs4 import BeautifulSoup
from core.logger import setup_logger,log_global

logger = setup_logger(site_name="sbs", log_type="scraper")


def parse_products(html, site):

    soup = BeautifulSoup(html, "lxml")

    products = []

    articles = soup.select("article.product-miniature")

    logger.info(f"{site} | parsing search results")

    for article in articles:

        product_id = article.get("data-id-product")

        a = article.select_one(".tvproduct-image a")
        img = article.select_one(".tvproduct-image img")

        if not a:
            continue

        product = {
            "site": site,
            "main_product_id": product_id,
            "product_link": a["href"],
            "product_img": img["src"] if img else None
        }

        products.append(product)

    logger.info(f"{site} | search products parsed: {len(products)}")
    log_global(f"{site} | search products parsed: {len(products)}")

    return products