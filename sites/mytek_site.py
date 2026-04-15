from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from core.http_client import get
from core.logger import setup_logger, log_global, write_product_result

from parsers.mytek.mytek_category_parser import parse_mytek_categories

from parsers.mytek.mytek_search_parser import parse_mytek_products
from sites.base_site import BaseSite

import os
import threading


# ---------------------------------
# LOGGERS
# ---------------------------------
logger = setup_logger("mytek", "scraper")
dup_logger = setup_logger("mytek", "duplication")


class MytekSite(BaseSite):

    name = "MYTEK"
    BASE_URL = "https://www.mytek.tn/"

    def __init__(self):
        self.lock = threading.Lock()

    # ---------------------------------
    # CATEGORY DISCOVERY
    # ---------------------------------
    def extract_categories(self):

        logger.info(f"{self.name} | CATEGORY_DISCOVERY_START")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_START")

        html = get(self.BASE_URL)

        if not html:
            logger.error(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            log_global(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            return

        categories = parse_mytek_categories(html)

        if not categories:
            logger.warning(f"{self.name} | NO_CATEGORIES_FOUND")
            return

        os.makedirs("config", exist_ok=True)
        file_path = "config/categoriesMytek.py"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("CATEGORIES = {\n")

            for name, url in categories.items():
                f.write(f'    "{name}": "{url}",\n')

            f.write("}\n")

        logger.info(
            f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}"
        )

        log_global(
            f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}"
        )

        print("\n=================================")
        print(f" {self.name} CATEGORIES EXTRACTED")
        print(f" TOTAL CATEGORIES : {len(categories)}")
        print(" Saved to config/categoriesMytek.py")
        print("=================================\n")

    # ---------------------------------
    # DUPLICATION HANDLER
    # ---------------------------------
    def handle_duplicate(self, product_id, source, seen_products):

        with self.lock:

            if product_id in seen_products:

                dup_logger.warning(
                    f"{self.name} | DUPLICATE | id={product_id} | source={source}"
                )

                return True

            else:
                seen_products[product_id] = source
                return False

    # ---------------------------------
    # SEARCH (NOT IMPLEMENTED)
    # ---------------------------------
    def search(self, query):

        logger.info(f"{self.name} | SEARCH_START | {query}")

        page = 1
        results = []
        seen_ids = set()

        while True:

            url = f"{self.BASE_URL}myteksearch/index/productsearch/?q={query}&p={page}"

            print(f"\n {url} \n")

            html = get(url)

            if not html:
                logger.warning(f"{self.name} | SEARCH_EMPTY_HTML | page={page}")
                break

            products = parse_mytek_products(html, self.name)

            if not products:
                logger.info(
                    f"{self.name} | SEARCH_NO_PRODUCTS | page={page} → stopping"
                )
                break

            # ---------------------------------
            # 🔥 FALLBACK DETECTION (Tunisianet-style)
            # ---------------------------------
            first_id = products[0].get("main_product_id")

            if first_id in seen_ids:
                logger.warning(
                    f"{self.name} | SEARCH_FALLBACK | page={page}"
                )
                break

            for p in products:
                seen_ids.add(p.get("main_product_id"))

            results.extend(products)

            page += 1

        logger.info(
            f"{self.name} | SEARCH_DONE | {query} | total={len(results)}"
        )

        return {
            "site": self.name,
            "query": query,
            "total_results": len(results),
            "products": results
        }

    # ---------------------------------
    # PRODUCT PARSER (NOT IMPLEMENTED)
    # ---------------------------------
    def parse_product(self, url, product_id=None):
        logger.warning(f"{self.name} | PRODUCT_PARSE | NOT_IMPLEMENTED")
        return None

    # ---------------------------------
    # CATEGORY PAGE COUNT (NOT IMPLEMENTED)
    # ---------------------------------
    def get_category_page_count(self, html):
        logger.warning(f"{self.name} | PAGE_COUNT | NOT_IMPLEMENTED")
        return 1

    # ---------------------------------
    # CATEGORY SCRAPER (NOT IMPLEMENTED)
    # ---------------------------------
    def scrape_category(self, category_name, category_url, seen_products, existing_ids):
        logger.warning(f"{self.name} | SCRAPE_CATEGORY | NOT_IMPLEMENTED")
        return []

    # ---------------------------------
    # BRAND SCRAPER (NOT IMPLEMENTED)
    # ---------------------------------
    def scrape_brands(self, seen_products, existing_ids):
        logger.warning(f"{self.name} | SCRAPE_BRANDS | NOT_IMPLEMENTED")
        return []

    # ---------------------------------
    # WORKER (NOT IMPLEMENTED)
    # ---------------------------------
    def scrape_product_worker(self, product, seen_products, source, existing_ids):
        logger.warning(f"{self.name} | WORKER | NOT_IMPLEMENTED")
        return None

    # ---------------------------------
    # FULL SCRAPER
    # ---------------------------------
    def scrape_site(self):

        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        results = []
        seen_products = {}

        logger.warning(f"{self.name} | FULL_SCRAPE | NOT_IMPLEMENTED")

        return {
            "site": self.name,
            "total_products": 0,
            "products": results
        }