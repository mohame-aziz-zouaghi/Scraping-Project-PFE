from bs4 import BeautifulSoup
from core.logger import setup_logger

logger = setup_logger("tunisianet", "scraper")


def parse_tunisianet_products(html, site):

    soup = BeautifulSoup(html, "lxml")

    products = []

    articles = soup.select(".product-miniature")

    for article in articles:

        a = article.select_one("a")

        if not a:
            continue

        href = a.get("href")

        if not href:
            continue

        try:
            product_id = href.split("/")[-1].split("-")[0]
        except:
            product_id = None

        products.append({
            "site": site,
            "main_product_id": product_id,
            "product_link": href
        })

    logger.info(f"{site} | parsed {len(products)} products")

    return products