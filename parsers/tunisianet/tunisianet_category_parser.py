from bs4 import BeautifulSoup
from core.logger import setup_logger,log_global

logger = setup_logger(site_name="tunisianet",log_type="scraper")


def parse_tunisianet_categories(html):

    soup = BeautifulSoup(html, "lxml")

    categories = {}

    # -----------------------------
    # HEADER MENU CATEGORIES
    # -----------------------------
    menu_items = soup.select(".menu-item.item-header a")

    for a in menu_items:

        name = a.text.strip().lower()
        url = a.get("href")

        if not name or not url:
            continue

        key = name.replace(" ", "_").replace("-", "_")

        categories[key] = url

    logger.info(f"TUNISIA_NET | menu categories extracted: {len(categories)}")
    log_global(f"TUNISIA_NET | menu categories extracted: {len(categories)}")

    return categories


def parse_tunisianet_category_ids(html):

    soup = BeautifulSoup(html, "lxml")

    category_ids = {}

    options = soup.select("#search_category option")

    for opt in options:

        value = opt.get("value")
        name = opt.text.strip().lower()

        if not value or value == "0":
            continue

        key = name.replace(" ", "_").replace("-", "_")

        category_ids[value] = key

    logger.info(f"TUNISIA_NET | category IDs extracted: {len(category_ids)}")
    log_global(f"TUNISIA_NET | category IDs extracted: {len(category_ids)}")

    return category_ids