import json
import html
from core.http_client import get
from bs4 import BeautifulSoup
from core.logger import setup_logger

logger = setup_logger("tunisianet", "scraper")


def extract_and_clean_tunisianet_product(url):
    """
    Fetch product page and extract cleaned data-product JSON
    """

    logger.info(f"TUNISIANET DEBUG | fetching | {url}")

    html_content = get(url)

    if not html_content:
        print("❌ Failed to fetch page")
        logger.error("TUNISIANET DEBUG | request failed")
        return

    soup = BeautifulSoup(html_content, "lxml")

    container = soup.select_one("#product-details")

    if not container:
        print("❌ product-details not found")
        logger.error("TUNISIANET DEBUG | product-details missing")
        return

    raw_data = container.get("data-product")

    if not raw_data:
        print("❌ data-product attribute not found")
        logger.error("TUNISIANET DEBUG | data-product missing")
        return

    try:
        # Clean HTML encoding
        cleaned = html.unescape(raw_data)

        # Convert to JSON
        data = json.loads(cleaned)

        print("\n========== CLEANED DATA-PRODUCT ==========\n")
        print(json.dumps(data, indent=4, ensure_ascii=False))
        print("\n=========================================\n")

        logger.info("TUNISIANET DEBUG | extraction success")

    except Exception as e:
        print(f"❌ Error parsing JSON: {e}")
        logger.error(f"TUNISIANET DEBUG | parse error | {e}")