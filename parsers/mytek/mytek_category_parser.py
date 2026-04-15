from bs4 import BeautifulSoup
from core.logger import setup_logger

logger = setup_logger("mytek", "scraper")


def parse_mytek_categories(html):

    soup = BeautifulSoup(html, "lxml")

    categories = {}

    # ---------------------------------
    # ROOT NAV CONTAINER
    # ---------------------------------
    root = soup.find("ul", class_="vertical-list clearfix")

    if not root:
        logger.warning("MYTEK | CATEGORY_PARSER | ROOT_NOT_FOUND")
        return categories

    # ---------------------------------
    # CATEGORY BLOCKS
    # ---------------------------------
    items = root.find_all("div", class_="grid-item-6 clearfix")

    if not items:
        logger.warning("MYTEK | CATEGORY_PARSER | NO_ITEMS_FOUND")
        return categories

    logger.info(f"MYTEK | CATEGORY_PARSER | found={len(items)}")

    for item in items:

        a = item.find("a", href=True)

        if not a:
            continue

        name = a.get_text(strip=True)
        link = a["href"].strip()

        if not name or not link:
            continue

        # Clean name
        name = " ".join(name.split())

        categories[name] = link

    logger.info(f"MYTEK | CATEGORY_PARSER | parsed={len(categories)}")

    return categories