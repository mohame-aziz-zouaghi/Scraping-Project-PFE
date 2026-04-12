from bs4 import BeautifulSoup
from core.logger import setup_logger

logger = setup_logger("spacenet", "scraper")

BASE_URL = "https://spacenet.tn"


def parse_spacenet_categories(html):

    soup = BeautifulSoup(html, "lxml")

    categories = {}

    # ✅ STEP 1: scope to mobile header
    container = soup.select_one("#mobile-header")

    if not container:
        logger.warning("SPACENET | CATEGORY_PARSER | mobile-header not found")
        return categories

    # ✅ STEP 2: extract ONLY category items inside it
    items = container.select("li.item-1.vertical-cat.parent")

    if not items:
        logger.warning("SPACENET | CATEGORY_PARSER | no category items found")
        return categories

    for item in items:

        a = item.select_one("a")

        if not a:
            continue

        href = a.get("href")
        if not href:
            continue

        # ✅ CLEAN TEXT (fix your previous bug)
        name = " ".join(a.stripped_strings)

        if not name:
            continue

        # ✅ FIX URL
        if href.startswith("/"):
            url = BASE_URL + href
        else:
            url = href

        # ✅ CLEAN KEY
        key = (
            name.lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace("|", "")
        )

        categories[key] = url

    logger.info(
        f"SPACENET | CATEGORY_PARSER | extracted {len(categories)} categories"
    )

    return categories