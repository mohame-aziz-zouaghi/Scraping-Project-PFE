from core.http_client import get
from core.logger import setup_logger, write_product_result, log_global

from parsers.sbs.sbs_search_parser import parse_products
from parsers.sbs.sbs_product_parser import parse_sbs_product_page
from parsers.sbs.sbs_category_parser import parse_sbs_categories

from sites.base_site import BaseSite
from config.brands import SEARCH_BRANDS
from config.categoriesSBS import CATEGORIES

from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import os


# ---------------------------------
# LOGGERS
# ---------------------------------
logger = setup_logger("sbs","scraper")
dup_logger = setup_logger("sbs","duplication")


class SBSSite(BaseSite):

    name = "SBS"

    SEARCH_URL = "https://www.sbsinformatique.com/recherche?controller=search&s={query}&page={page}"

    def __init__(self):
        self.lock = threading.Lock()

    # ---------------------------------
    # SEARCH (used in brand fallback)
    # ---------------------------------
    def search(self, query):

        logger.info(f"{self.name} | SEARCH_START | {query}")

        page = 1
        results = []

        while True:

            url = self.SEARCH_URL.format(query=query, page=page)
            dup_logger.info(f"url link : {url}")

            html = get(url)

            if not html:
                break

            products = parse_products(html, self.name)

            if not products:
                break

            results.extend(products)
            page += 1

        logger.info(f"{self.name} | SEARCH_FINISHED | {query} | {len(results)}")

        return {
            "site": self.name,
            "query": query,
            "total_results": len(results),
            "products": results
        }

    # ---------------------------------
    # PRODUCT PARSER
    # ---------------------------------
    def parse_product(self, url, product_id=None):

        html = get(url)

        if not html:
            return None

        return parse_sbs_product_page(html, url, self.name, product_id)

    # ---------------------------------
    # DUPLICATION HANDLER (THREAD SAFE)
    # ---------------------------------
    def handle_duplicate(self, product_id, source, seen_products):

        with self.lock:

            if product_id in seen_products:

                first_sources = seen_products[product_id]

                dup_logger.warning(
                    f"{self.name} | DUPLICATE | id={product_id} | "
                    f"first_seen={first_sources} | duplicate_in={source}"
                )

                seen_products[product_id].append(source)

                return True

            else:
                seen_products[product_id] = [source]
                return False

    # ---------------------------------
    # WORKER (PARALLEL SAFE)
    # ---------------------------------
    def scrape_product_worker(self, product, seen_products, source):

        product_id = product.get("main_product_id")
        product_url = product.get("product_link")

        if not product_id:
            return None

        # DUPLICATION CHECK
        if self.handle_duplicate(product_id, source,  ):
            return None

        details = self.parse_product(product_url, product_id)

        if details:
            write_product_result(details)

        return details

    # ---------------------------------
    # CATEGORY SCRAPER
    # ---------------------------------
    def scrape_category(self, category_name, category_url, seen_products):

        results = []

        first_page = get(f"{category_url}?page=1")

        if not first_page:
            return results

        total_pages = self.get_category_page_count(first_page)

        logger.info(
            f"{self.name} | CATEGORY_START | {category_name} | pages={total_pages}"
        )

        for page in range(1, total_pages + 1):

            logger.info(
                f"{self.name} | CATEGORY_PROGRESS | {category_name} | page {page}/{total_pages}"
            )

            html = get(f"{category_url}?page={page}")

            if not html:
                continue

            products = parse_products(html, self.name)

            with ThreadPoolExecutor(max_workers=10) as executor:

                futures = [
                    executor.submit(
                        self.scrape_product_worker,
                        p,
                        seen_products,
                        category_name
                    )
                    for p in products
                ]

                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        results.append(result)

        logger.info(
            f"{self.name} | CATEGORY_FINISHED | {category_name} | collected={len(results)}"
        )

        return results

    # ---------------------------------
    # BRAND SCRAPER (FALLBACK)
    # ---------------------------------
    def scrape_brands(self, seen_products):

        logger.info(f"{self.name} | BRAND_SEARCH_START")

        results = []

        for brand in SEARCH_BRANDS:

            logger.info(f"{self.name} | BRAND | {brand}")

            products = self.search(brand)["products"]

            with ThreadPoolExecutor(max_workers=10) as executor:

                futures = [
                    executor.submit(
                        self.scrape_product_worker,
                        p,
                        seen_products,
                        brand
                    )
                    for p in products
                ]

                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        results.append(result)

        logger.info(
            f"{self.name} | BRAND_SEARCH_FINISHED | collected={len(results)}"
        )

        return results

    # ---------------------------------
    # MAIN SCRAPER
    # ---------------------------------
    def scrape_site(self):

        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        results = []
        seen_products = {}

        total_categories = len(CATEGORIES)

        for i, (category_name, category_url) in enumerate(CATEGORIES.items(), start=1):

            logger.info(
                f"{self.name} | CATEGORY {i}/{total_categories} | {category_name}"
            )

            results.extend(
                self.scrape_category(category_name, category_url, seen_products)
            )

        # BRAND FALLBACK
        results.extend(self.scrape_brands(seen_products))

        total = len(results)

        logger.info(f"{self.name} | SCRAPE_FINISHED | total={total}")
        log_global(f"{self.name} | SCRAPE_FINISHED | total={total}")

        print("\n=================================")
        print(f" SCRAPING FINISHED : {self.name}")
        print(f" TOTAL PRODUCTS    : {total}")
        print("=================================\n")

        return {
            "site": self.name,
            "total_products": total,
            "products": results
        }

    # ---------------------------------
    # CATEGORY DISCOVERY
    # ---------------------------------
    def extract_categories(self):

        logger.info(f"{self.name} | CATEGORY_DISCOVERY_START")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_START")

        html = get("https://www.sbsinformatique.com/")

        if not html:
            logger.error(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            return

        categories = parse_sbs_categories(html)

        os.makedirs("config", exist_ok=True)

        with open("config/categoriesSBS.py", "w", encoding="utf-8") as f:

            f.write("CATEGORIES = {\n")

            for name, url in categories.items():
                f.write(f'    "{name}": "{url}",\n')

            f.write("}\n")

        logger.info(
            f"{self.name} | CATEGORY_DISCOVERY_FINISHED | {len(categories)}"
        )
        log_global(f"{self.name} | CATEGORY_DISCOVERY_FINISHED")

    # ---------------------------------
    # PAGINATION DETECTION
    # ---------------------------------
    def get_category_page_count(self, html):

        soup = BeautifulSoup(html, "lxml")

        pages = soup.select(".page-list li a")

        nums = []

        for p in pages:
            try:
                nums.append(int(p.text.strip()))
            except:
                continue

        return max(nums) if nums else 1