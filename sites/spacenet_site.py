from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from tqdm import tqdm
import os
import threading
import time

from config.brands import SEARCH_BRANDS
from config.categoriesSpacenet import CATEGORIES
from core.load_ids import load_existing_product_ids
from core.Web.http_client import get
from core.logger import setup_logger, log_global, write_product_result
from parsers.spacenet.spacenet_category_parser import parse_spacenet_categories
from parsers.spacenet.spacenet_product_parser import parse_spacenet_product_page
from parsers.spacenet.spacenet_search_parser import parse_spacenet_products
from core.performance import PerformanceTracker
from sites.base_site import BaseSite

# ---------------------------------
# LOGGERS
# ---------------------------------
logger = setup_logger("spacenet", "scraper")
dup_logger = setup_logger("spacenet", "duplication")

class SpacenetSite(BaseSite):

    name = "SPACENET"
    BASE_URL = "https://spacenet.tn/"
    SEARCH_URL = "https://spacenet.tn/module/ambjolisearch/jolisearch?orderby=position&orderway=desc&search_query={query}&submit_search="

    def __init__(self):
        self.lock = threading.Lock()
        self.performance = PerformanceTracker()

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

        categories = parse_spacenet_categories(html)
        if not categories:
            logger.warning(f"{self.name} | NO_CATEGORIES_FOUND")
            return

        os.makedirs("config", exist_ok=True)
        file_path = "config/categoriesSpacenet.py"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("CATEGORIES = {\n")
            for name, url in categories.items():
                f.write(f'    "{name}": "{url}",\n')
            f.write("}\n")

        logger.info(f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}")

    # ---------------------------------
    # DUPLICATION HANDLER
    # ---------------------------------
    def handle_duplicate(self, product_id, source, seen_products):
        with self.lock:
            if product_id in seen_products:
                first_sources = seen_products[product_id]
                dup_logger.warning(f"{self.name} | DUPLICATE | id={product_id} | first_seen={first_sources} | duplicate_in={source}")
                seen_products[product_id].append(source)
                return True
            else:
                if product_id != "#" :
                    seen_products[product_id] = [source]
                    return False
                else: 
                    return False

    # ---------------------------------
    # SEARCH
    # ---------------------------------
    def search(self, query):
        logger.info(f"{self.name} | SEARCH_START | {query}")
        results = []
        seen_ids = set()
        page = 1

        while True:
            url = self.SEARCH_URL.format(query=query)
            if page > 1:
                url += f"&page={page}"
            html = get(url)
            if not html:
                break

            products = parse_spacenet_products(html, self.name)
            if not products:
                break

            # fallback check
            first_id = products[0].get("main_product_id")
            if first_id in seen_ids:
                logger.warning(f"{self.name} | SEARCH_FALLBACK_DETECTED | page={page}")
                break

            for p in products:
                seen_ids.add(p.get("main_product_id"))

            results.extend(products)
            page += 1

        logger.info(f"{self.name} | SEARCH_DONE | {query} | {len(results)}")
        return {"site": self.name, "query": query, "total_results": len(results), "products": results}

    # ---------------------------------
    # PRODUCT PARSER
    # ---------------------------------
    def parse_product(self, url, product_id=None):
        """
        Parses a Spacenet product page using the standard product schema.

        Args:
            url (str): The URL of the product page.
            product_id (str|int, optional): The main product ID for duplication handling.

        Returns:
            dict: A fully populated product schema dictionary, or None if failed.
        """
        logger.info(f"{self.name} | PRODUCT_PARSE_START | {url}")

        html = get(url)

        if not html:
            logger.warning(f"{self.name} | PRODUCT_PARSE_FAILED | {url}")
            return None

        try:
            # Use the parser that populates the product schema
            product = parse_spacenet_product_page(html, url, self.name, product_id)

            if not product:
                logger.warning(f"{self.name} | PRODUCT_PARSE_EMPTY | {url}")
            else:
                logger.info(f"{self.name} | PRODUCT_PARSED | id={product.get('main_product_id')} | {url}")

            return product

        except Exception as e:
            logger.error(f"{self.name} | PRODUCT_PARSE_ERROR | {url} | {e}")
            return None

    # ---------------------------------
    # CATEGORY PAGE COUNT
    # ---------------------------------
    def get_category_page_count(self, html):
        soup = BeautifulSoup(html, "lxml")
        pages = soup.select(".pagination a")
        nums = []
        for p in pages:
            try:
                nums.append(int(p.text.strip()))
            except:
                continue
        return max(nums) if nums else 1

    # ---------------------------------
    # CATEGORY SCRAPER
    # ---------------------------------
    def scrape_category(self, category_name, category_url, seen_products, existing_ids):

        logger.info(f"{self.name} | CATEGORY_START | {category_name}")
        log_global(f"{self.name} | CATEGORY_START | {category_name}")

        results = []

        first_page = get(category_url)
        if not first_page:
            return results

        total_pages = self.get_category_page_count(first_page)

        for page in tqdm(
            range(1, total_pages + 1),
            desc=f"{self.name} | {category_name}",
            unit="page"
        ):

            url = f"{category_url}?page={page}"
            html = get(url)

            if not html:
                logger.warning(
                    f"{self.name} | EMPTY_HTML | {category_name} | page={page}"
                )
                break  # 🔥 stop category

            products = parse_spacenet_products(html, self.name)

            # 🔥 STOP if no products (THIS replaces fallback logic)
            if not products:
                logger.warning(
                    f"{self.name} | NO_PRODUCTS | {category_name} | page={page} → stopping"
                )
                break

            # ---------------------------------
            # FILTER EXISTING IDS (performance boost)
            # ---------------------------------
            filtered_products = [
                p for p in products
                if str(p.get("main_product_id")) not in existing_ids
            ]

            if not filtered_products:
                log_global(
                    f"{self.name} | {category_name} | page {page} | ALL_SKIPPED"
                )
                continue

            # ---------------------------------
            # THREAD WORKERS + PRODUCT PROGRESS
            # ---------------------------------
            with ThreadPoolExecutor(max_workers=20) as executor:

                futures = [
                    executor.submit(
                        self.scrape_product_worker,
                        p,
                        seen_products,
                        category_name,
                        existing_ids
                    )
                    for p in filtered_products
                ]

                for future in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=f"{self.name} | {category_name} | page {page}",
                    unit="prod",
                    leave=False
                ):
                    try:
                        result = future.result()

                        if result:
                            results.append(result)
                            self.performance.add_product()

                    except Exception as e:
                        logger.error(f"{self.name} | WORKER_ERROR | {e}")

            # ---------------------------------
            # LOGGING
            # ---------------------------------
            speed = self.performance.get_speed()

            remaining_products = (total_pages - page) * len(products)

            log_global(
                f"{self.name} | {category_name} | page {page}/{total_pages} | "
                f"processed {len(filtered_products)} | "
                f"speed={speed:.2f} prod/s | "
                f"ETA={self.performance.get_eta(remaining_products)}"
            )

        logger.info(f"{self.name} | CATEGORY_DONE | {category_name}")

        return results

    # ---------------------------------
    # BRAND SCRAPER
    # ---------------------------------
    def scrape_brands(self, seen_products, existing_ids):
        logger.info(f"{self.name} | BRAND_START")
        log_global(f"{self.name} | BRAND_START")
        results = []

        for brand in SEARCH_BRANDS:
            logger.info(f"{self.name} | BRAND | {brand}")
            products = self.search(brand)["products"]
            if not products:
                continue

            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(self.scrape_product_worker, p, seen_products, brand) for p in products]
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        results.append(result)
                        self.performance.add_product()

        logger.info(f"{self.name} | BRAND_DONE | collected={len(results)}")
        log_global(f"{self.name} | BRAND_DONE | collected={len(results)}")
        return results

    # ---------------------------------
    # PRODUCT WORKER
    # ---------------------------------
    def scrape_product_worker(self, product, seen_products, source, existing_ids):

        pid = product.get("main_product_id")
        url = product.get("product_link")

        if not pid or not url:
            return None

        pid = str(pid)

        # ---------------------------------
        # 🔥 SKIP ALREADY SAVED PRODUCTS
        # ---------------------------------
        if pid in existing_ids:
            logger.info(f"{self.name} | SKIP_EXISTING | {pid}")
            return None

        # ---------------------------------
        # DUPLICATION CHECK (SESSION)
        # ---------------------------------
        if self.handle_duplicate(pid, source, seen_products):
            return None

        try:
            details = self.parse_product(url, pid)

            if details:
                write_product_result(details, self.name)
                existing_ids.add(pid)  # 🔥 IMPORTANT → avoid reprocessing in same run

            return details

        except Exception as e:
            logger.error(f"{self.name} | WORKER_ERROR | {url} | {e}")
            return None

    # ---------------------------------
    # FULL SITE SCRAPER
    # ---------------------------------
    def scrape_site(self):
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")
        results = []
        seen_products = {}

        # 🔥 LOAD EXISTING IDS HERE (ONLY ONCE)
        existing_ids = load_existing_product_ids(self.name)

        logger.info(
            f"{self.name} | RESUME_MODE | loaded_existing={len(existing_ids)}"
        )
        log_global(
            f"{self.name} | RESUME_MODE | loaded_existing={len(existing_ids)}"
        )

        # CATEGORY SCRAPE
        for name, url in tqdm(CATEGORIES.items(), desc=f"{self.name} | Scraping categories", unit="category"):
            res = self.scrape_category(name, url, seen_products, existing_ids)
            results.extend(res)

        # BRAND SCRAPE
        brand_results = self.scrape_brands(seen_products, existing_ids)
        results.extend(brand_results)

        logger.info(f"{self.name} | SCRAPE_DONE | total_products={len(results)}")
        log_global(f"{self.name} | SCRAPE_DONE | total_products={len(results)}")

        print("\n==============================")
        print(f" {self.name} SCRAPING DONE")
        print(f" TOTAL PRODUCTS: {len(results)}")
        print("==============================\n")

        return {"site": self.name, "total_products": len(results), "products": results}