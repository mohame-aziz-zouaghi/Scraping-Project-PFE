from bs4 import BeautifulSoup
from core.logger import setup_logger,log_global

logger = setup_logger(site_name="sbs", log_type="scraper")

STOP_CATEGORY = "jeux video"


def parse_sbs_categories(html):

    soup = BeautifulSoup(html, "lxml")

    categories = {}

    wrapper = soup.select_one("li.tv-leftcategory-wrapper")

    if not wrapper:
        logger.warning("CATEGORY_PARSER | left category wrapper not found")
        return categories

    links = wrapper.select("li a")

    for a in links:

        name = a.text.strip().lower()
        url = a.get("href")

        if not url:
            continue

        # stop condition
        if STOP_CATEGORY in name:
            logger.info("CATEGORY_PARSER | stop category reached")
            break

        # normalize name
        key = name.replace(" ", "_").replace("-", "_")

        categories[key] = url

    logger.info(f"CATEGORY_PARSER | extracted {len(categories)} categories")
    log_global.info(f"CATEGORY_PARSER | extracted {len(categories)} categories")

    return categories

