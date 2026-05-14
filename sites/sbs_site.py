from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import threading
import os

from core.Web.http_client import get
from core.logger import setup_logger, write_product_result, log_global

from parsers.sbs.sbs_search_parser import parse_products
from parsers.sbs.sbs_product_parser import parse_sbs_product_page
from parsers.sbs.sbs_category_parser import parse_sbs_categories

from sites.base_site import BaseSite
from config.brands import SEARCH_BRANDS
from config.categoriesSBS import CATEGORIES


# ---------------------------------
# LOGGERS
# ---------------------------------
logger = setup_logger("sbs", "scraper")
dup_logger = setup_logger("sbs", "duplication")
error_logger = setup_logger("sbs", "error")


class SBSSite(BaseSite):

    name = "SBS"
    SEARCH_URL = "https://www.sbsinformatique.com/recherche?controller=search&s={query}&page={page}"

    def __init__(self, workers=10):
        self.lock = threading.Lock()
        self.workers = workers

    # ---------------------------------
    # UTILS
    # ---------------------------------
    def build_category_url(self, base_url, page):
        return base_url if page == 1 else f"{base_url}?page={page}"

    def safe_get(self, url):
        try:
            return get(url)
        except Exception as e:
            error_logger.error(f"{self.name} | HTTP_ERROR | {url} | {e}")
            return None

    # ---------------------------------
    # DUPLICATION (THREAD SAFE)
    # ---------------------------------
    def handle_duplicate(self, product_id, source, seen_products):

        with self.lock:
            if product_id in seen_products:

                dup_logger.warning(
                    f"{self.name} | DUPLICATE | id={product_id} | "
                    f"first_seen={seen_products[product_id]} | duplicate_in={source}"
                )

                seen_products[product_id].append(source)
                return True

            seen_products[product_id] = [source]
            return False

    # ---------------------------------
    # PRODUCT PARSER
    # ---------------------------------
    def parse_product(self, url, product_id):

        html = self.safe_get(url)
        if not html:
            return None

        try:
            return parse_sbs_product_page(html, url, self.name, product_id)
        except Exception as e:
            error_logger.error(f"{self.name} | PARSE_PRODUCT_ERROR | {url} | {e}")
            return None

    # ---------------------------------
    # WORKER
    # ---------------------------------
    def scrape_product_worker(self, product, seen_products, source):

        product_id = product.get("main_product_id")
        product_url = product.get("product_link")

        if not product_id or not product_url:
            return None

        if self.handle_duplicate(product_id, source, seen_products):
            return None

        details = self.parse_product(product_url, product_id)

        if details:
            write_product_result(details,self.name)

        return details

    # ---------------------------------
    # CATEGORY SCRAPER
    # ---------------------------------
    def scrape_category(self, category_name, category_url, seen_products):

        results = []

        logger.info(f"{self.name} | CATEGORY_START | {category_name}")

        first_page = self.safe_get(category_url)
        if not first_page:
            error_logger.error(f"{self.name} | CATEGORY_FAIL | {category_name}")
            return results

        total_pages = self.get_category_page_count(first_page)

        logger.info(
            f"{self.name} | CATEGORY_INFO | {category_name} | pages={total_pages}"
        )

        page_bar = tqdm(
            total=total_pages,
            desc=f"{self.name} | {category_name}",
            unit="page"
        )

        for page in range(1, total_pages + 1):

            url = self.build_category_url(category_url, page)

            logger.info(
                f"{self.name} | PAGE_FETCH | {category_name} | {page}/{total_pages}"
            )

            html = self.safe_get(url)

            if not html:
                error_logger.warning(
                    f"{self.name} | PAGE_FAIL | {category_name} | page={page}"
                )
                page_bar.update(1)
                continue

            try:
                products = parse_products(html, self.name)
            except Exception as e:
                error_logger.error(
                    f"{self.name} | PARSE_CATEGORY_ERROR | page={page} | {e}"
                )
                page_bar.update(1)
                continue

            if not products:
                logger.info(
                    f"{self.name} | NO_PRODUCTS | {category_name} | page={page}"
                )
                page_bar.update(1)
                continue

            # ---------------------------------
            # WORKERS WITH PROGRESS
            # ---------------------------------
            with ThreadPoolExecutor(max_workers=self.workers) as executor:

                futures = [
                    executor.submit(
                        self.scrape_product_worker,
                        p,
                        seen_products,
                        category_name
                    )
                    for p in products
                ]

                for future in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=f"{category_name} | page {page}",
                    leave=False
                ):
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception as e:
                        error_logger.error(
                            f"{self.name} | WORKER_ERROR | {category_name} | {e}"
                        )

            page_bar.update(1)

        page_bar.close()

        logger.info(
            f"{self.name} | CATEGORY_DONE | {category_name} | collected={len(results)}"
        )

        return results

    # ---------------------------------
    # BRAND SCRAPER
    # ---------------------------------
    def scrape_brands(self, seen_products):

        logger.info(f"{self.name} | BRAND_START")

        results = []

        for brand in tqdm(SEARCH_BRANDS, desc="SBS | Brands"):

            products = self.search(brand)["products"]

            with ThreadPoolExecutor(max_workers=self.workers) as executor:

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
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception as e:
                        error_logger.error(
                            f"{self.name} | BRAND_WORKER_ERROR | {brand} | {e}"
                        )

        logger.info(f"{self.name} | BRAND_DONE | collected={len(results)}")

        return results

    # ---------------------------------
    # SEARCH
    # ---------------------------------
    def search(self, query):

        logger.info(f"{self.name} | SEARCH_START | {query}")

        results = []
        page = 1

        while True:

            url = self.SEARCH_URL.format(query=query, page=page)
            html = self.safe_get(url)

            if not html:
                break

            try:
                products = parse_products(html, self.name)
            except Exception as e:
                error_logger.error(f"{self.name} | SEARCH_PARSE_ERROR | {e}")
                break

            if not products:
                break

            results.extend(products)
            page += 1

        logger.info(
            f"{self.name} | SEARCH_DONE | {query} | results={len(results)}"
        )

        return {
            "site": self.name,
            "query": query,
            "products": results
        }

    # ---------------------------------
    # MAIN SCRAPER
    # ---------------------------------
    def scrape_site(self):

        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        results = []
        seen_products = {}

        category_bar = tqdm(
            total=len(CATEGORIES),
            desc="SBS | Categories"
        )

        for name, url in CATEGORIES.items():

            results.extend(
                self.scrape_category(name, url, seen_products)
            )

            category_bar.update(1)

        category_bar.close()

        # BRAND FALLBACK
        results.extend(self.scrape_brands(seen_products))

        total = len(results)

        logger.info(f"{self.name} | SCRAPE_DONE | total={total}")
        log_global(f"{self.name} | SCRAPE_DONE | total={total}")

        print("\n==============================")
        print(f"SITE: {self.name}")
        print(f"TOTAL PRODUCTS: {total}")
        print("==============================\n")

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

        html = self.safe_get("https://www.sbsinformatique.com/")
        if not html:
            return

        categories = parse_sbs_categories(html)

        os.makedirs("config", exist_ok=True)

        with open("config/categoriesSBS.py", "w", encoding="utf-8") as f:
            f.write("CATEGORIES = {\n")
            for name, url in categories.items():
                f.write(f'    "{name}": "{url}",\n')
            f.write("}\n")

        logger.info(
            f"{self.name} | CATEGORY_DISCOVERY_DONE | {len(categories)}"
        )

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