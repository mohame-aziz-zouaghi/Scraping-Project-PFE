from concurrent.futures import ThreadPoolExecutor

from tqdm import tqdm
from bs4 import BeautifulSoup

from config.categoriesMytek import CATEGORIES
from core.http_client import get
from core.load_ids import load_existing_product_ids
from core.logger import setup_logger, log_global, write_product_result

from core.performance import PerformanceTracker
from core.thread_manager import ThreadManager

from parsers.mytek.mytek_search_parser import parse_mytek_products
from parsers.mytek.mytek_product_parser import parse_mytek_product_page

from config.brands import SEARCH_BRANDS

from sites.base_site import BaseSite

import threading
import time
import os


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
        self.total_products = 0
        self.start_time = time.time()
        self.performance = PerformanceTracker()

        self.thread_manager = ThreadManager(
            self.name,
            self.start_time,
            lambda: self.total_products
        )

    # ---------------------------------
    # CATEGORY DISCOVERY
    # ---------------------------------
    def extract_categories(self):

        logger.info(f"{self.name} | CATEGORY_DISCOVERY_START")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_START")

        html = get(self.BASE_URL)

        if not html:
            logger.error(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            return

        soup = BeautifulSoup(html, "lxml")
        categories = {}

        parents = soup.find_all("li", class_="rootverticalnav nav-1 category-item")

        for parent in parents:

            items = parent.find_all("div", class_="grid-item-6 clearfix")

            for item in items:

                a = item.find("a", href=True)
                if not a:
                    continue

                name = " ".join(a.text.split())
                url = a["href"]

                if url.startswith("/"):
                    url = self.BASE_URL + url.lstrip("/")

                categories[name] = url

        if not categories:
            logger.warning(f"{self.name} | NO_CATEGORIES_FOUND")
            return

        os.makedirs("config", exist_ok=True)

        with open("config/mytekcategories.py", "w", encoding="utf-8") as f:
            f.write("CATEGORIES = {\n")
            for k, v in categories.items():
                f.write(f'    "{k}": "{v}",\n')
            f.write("}\n")

        logger.info(f"{self.name} | CATEGORY_DISCOVERY_DONE | {len(categories)}")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_DONE | {len(categories)}")

    # ---------------------------------
    # DUPLICATE HANDLER
    # ---------------------------------
    def handle_duplicate(self, product_id, source, seen_products):

        with self.lock:

            if product_id in seen_products:

                dup_logger.warning(
                    f"{self.name} | DUPLICATE | id={product_id} | source={source}"
                )

                return True

            seen_products[product_id] = source
            return False

    # ---------------------------------
    # SEARCH
    # ---------------------------------
    def search(self, query):

        logger.info(f"{self.name} | SEARCH_START | {query}")

        page = 1
        results = []
        seen_ids = set()

        while True:

            url = f"{self.BASE_URL}myteksearch/index/productsearch/?q={query}&p={page}"

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
    # PRODUCT PARSER
    # ---------------------------------
    def parse_product(self, url, source, product_id=None):

        html = get(url)

        if not html:
            return None

        return parse_mytek_product_page(html, url, self.name,source, product_id)

    # ---------------------------------
    # PRODUCT WORKER
    # ---------------------------------
    def scrape_product_worker(self, product, seen_products, source, existing_ids):

        pid = product.get("main_product_id")
        url = product.get("product_link")

        if not pid or not url:
            return None

        if str(pid) in existing_ids:
            return None

        if self.handle_duplicate(pid, source, seen_products):
            return None

        try:
            result = self.parse_product(url, source, pid)

            if result:
                self.performance.add_product()
                self.total_products += 1
                write_product_result(result, self.name)

            return result

        except Exception as e:
            logger.error(f"{self.name} | WORKER_ERROR | {url} | {e}")
            return None

    # ---------------------------------
    # CATEGORY SCRAPER
    # ---------------------------------
    

    def scrape_category(self, category_name, category_url, seen_products, existing_ids):

        logger.info(f"{self.name} | CATEGORY_START | {category_name}")

        results = []
        seen_ids = set()

        first_html = get(category_url)
        if not first_html:
            return results

        total_pages = self.get_category_page_count(first_html)

        # ---------------------------------
        # PAGE PROGRESS BAR (CATEGORY LEVEL)
        # ---------------------------------
        for page in tqdm(
            range(1, total_pages + 1),
            desc=f"{self.name} | {category_name} | Pages",
            unit="page",
            leave=False
        ):

            html = get(f"{category_url}?p={page}")

            if not html:
                continue

            products = parse_mytek_products(html, self.name)

            if not products:
                continue

            # avoid loop fallback
            first_id = products[0].get("main_product_id")
            if first_id in seen_ids:
                break

            for p in products:
                seen_ids.add(p.get("main_product_id"))

            # filter cache
            filtered = [
                p for p in products
                if str(p.get("main_product_id")) not in existing_ids
            ]

            if not filtered:
                continue

            # ---------------------------------
            # PRODUCT PROGRESS BAR (THREADS)
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
                    for p in filtered
                ]

                for future in tqdm(
                    futures,
                    desc=f"{self.name} | Products",
                    total=len(futures),
                    leave=False,
                    unit="prod"
                ):
                    result = future.result()

                    if result:
                        results.append(result)
                        self.performance.add_product()

            # ---------------------------------
            # LOGGING
            # ---------------------------------
            speed = self.performance.get_speed()

            log_global(
                f"{self.name} | {category_name} | page {page}/{total_pages} | "
                f"speed={speed:.2f} prod/s | total={self.performance.total_products}"
            )

        return results

    # ---------------------------------
    # BRAND SCRAPER
    # ---------------------------------
    def scrape_brands(self, seen_products, existing_ids):

        logger.info(f"{self.name} | BRAND_START")

        results = []

        for brand in SEARCH_BRANDS:

            logger.info(f"{self.name} | BRAND_START | {brand}")

            products = self.search(brand)["products"]

            if not products:
                logger.info(f"{self.name} | BRAND_EMPTY | {brand}")
                continue

            # ---------------------------------
            # FILTER EXISTING IDS (SMART CACHE)
            # ---------------------------------
            filtered = [
                p for p in products
                if str(p.get("main_product_id")) not in existing_ids
            ]

            tasks = [
                (self.scrape_product_worker, p, seen_products, brand, existing_ids)
                for p in filtered
            ]

            brand_results = self.thread_manager.run_tasks(
                tasks,
                desc=f"{self.name} | BRAND | {brand}"
            )

            results.extend(brand_results)

            logger.info(
                f"{self.name} | BRAND_DONE | {brand} | collected={len(brand_results)}"
            )

        logger.info(f"{self.name} | BRAND_DONE | total={len(results)}")
        return results

    # ---------------------------------
    # FULL SCRAPER
    # ---------------------------------
    def scrape_site(self):

        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        self.start_time = time.time()

        results = []
        seen_products = {}

        existing_ids = load_existing_product_ids(self.name)
# 
        logger.info(
            f"{self.name} | RESUME_MODE | existing={len(existing_ids)}"
        )

        for name, url in tqdm(
            CATEGORIES.items(),
            desc=f"{self.name} | Categories",
            unit="cat"
        ):
            res = self.scrape_category(name, url, seen_products, existing_ids)
            results.extend(res)

        brand_results = self.scrape_brands(seen_products, existing_ids)
        results.extend(brand_results)

        logger.info(f"{self.name} | SCRAPE_DONE | total={len(results)}")
        log_global(f"{self.name} | SCRAPE_DONE | total={len(results)}")

        print("\n==============================")
        print(f" {self.name} SCRAPING DONE")
        print(f" TOTAL PRODUCTS: {len(results)}")
        print("==============================\n")

        return {
            "site": self.name,
            "total_products": len(results),
            "products": results
        }
    

    def get_category_page_count(self, html):

        soup = BeautifulSoup(html, "lxml")

        max_page = 1

        # ---------------------------------
        # Extract pagination numbers
        # ---------------------------------
        for a in soup.select("a.page-link"):

            text = a.get_text(strip=True)

            if text.isdigit():
                try:
                    page_num = int(text)
                    if page_num > max_page:
                        max_page = page_num
                except:
                    continue

        return max_page