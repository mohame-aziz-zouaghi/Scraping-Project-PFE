from core.Web.http_client import get
from core.logger import setup_logger, write_product_result, log_global
import os
import time

from parsers.tunisianet.tunisianet_category_parser import parse_tunisianet_categories, parse_tunisianet_category_ids
from parsers.tunisianet.tunisianet_search_parser import parse_tunisianet_products
from parsers.tunisianet.tunisianet_product_parser import parse_tunisianet_product_page

from sites.base_site import BaseSite

from config.categoriesTunisiaNet import CATEGORIES
from config.brands import SEARCH_BRANDS

from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from tqdm import tqdm
from core.performance import PerformanceTracker

import threading


logger = setup_logger("tunisianet", "scraper")
dup_logger = setup_logger("tunisianet", "duplication")


class TunisiaNetSite(BaseSite):

    name = "TUNISIA_NET"

    def __init__(self):
        self.lock = threading.Lock()
        self.performance = PerformanceTracker()

    # ---------------------------------
    # SEARCH
    # ---------------------------------

    def search(self, query):
        """
        Search method for Tunisianet with fallback handling:
        - Tracks first product ID per page to detect repeated last page
        """
        logger.info(f"{self.name} | SEARCH_START | {query}")

        SEARCH_URL = (
            "https://www.tunisianet.com.tn/recherche?s={query}&submit_search=&page={page}&order=product.price.asc"
        )

        page = 1
        results = []
        seen_first_ids = set()  # track first product of each page

        while True:
            url = SEARCH_URL.format(query=query, page=page)
            html = get(url)

            if not html:
                logger.warning(f"{self.name} | SEARCH_FAILED | page={page} | query={query}")
                break

            products = parse_tunisianet_products(html, self.name)

            if not products:
                break

            first_pid = products[0].get("main_product_id")
            if first_pid in seen_first_ids:
                logger.info(f"{self.name} | SEARCH_LOOP_DETECTED | page={page} | query={query}")
                break  # we've already seen this page

            seen_first_ids.add(first_pid)
            results.extend(products)
            page += 1

        logger.info(f"{self.name} | SEARCH_FINISHED | {query} | total_products={len(results)}")
        return {
            "site": self.name,
            "query": query,
            "total_results": len(results),
            "products": results
        }

    # ---------------------------------
    # DUPLICATION HANDLER
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

            else:
                seen_products[product_id] = [source]
                return False

    # ---------------------------------
    # PRODUCT PARSER
    # ---------------------------------
    def parse_product(self, url, product_id=None):

        html = get(url)

        if not html:
            return None

        return parse_tunisianet_product_page(html, url, self.name, product_id)

    # ---------------------------------
    # CATEGORY PAGE COUNT
    # ---------------------------------
    def get_total_pages(self, html):

        soup = BeautifulSoup(html, "lxml")

        pages = soup.select(".page-list li a")
        
        numbers = []

        for p in pages:
            try:
                numbers.append(int(p.text.strip()))
            except:
                continue

        return max(numbers) if numbers else 1

    # ---------------------------------
    # WORKER
    # ---------------------------------
    def scrape_product_worker(self, product, seen_products, source):

        start = time.time()
        pid = product.get("main_product_id")
        url = product.get("product_link")

        if self.handle_duplicate(pid, source, seen_products):
            return None

        try:

            details = self.parse_product(url, pid)


            if details:
                write_product_result(details, source)
                self.performance.add_product()

            duration = time.time() - start

            if duration > 2:
                logger.warning(
                    f"{self.name} | SLOW_PRODUCT | {url} | {duration:.2f}s"
                )

        except Exception as e:
            logger.error(f"{self.name} | WORKER_ERROR | {url} | {e}")
            return None

    # ---------------------------------
    # CATEGORY SCRAPER
    # ---------------------------------



    def scrape_category(self, category_name, category_url, seen_products, index, total_categories):
        results = []
        first_page = get(f"{category_url}?page=1")

        if not first_page:
            return results

        total_pages = self.get_category_page_count(first_page)
        logger.info(f"{self.name} | CATEGORY_START | {category_name} | pages={total_pages}")

        category_start_time = time.time()

        # -------------------------
        # Category/Page Progress Bar
        # -------------------------
        for page in tqdm(range(1, total_pages + 1),
                        desc=f"{self.name} | {category_name} | Page",
                        unit="page",
                        dynamic_ncols=True):
            page_start_time = time.time()
            html = get(f"{category_url}?page={page}")
            if not html:
                continue

            products = parse_tunisianet_products(html, self.name)
            if not products:
                continue

            # -------------------------
            # Product-level progress bar
            # -------------------------
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(self.scrape_product_worker, p, seen_products, self.name)
                    for p in products
                ]

                with tqdm(total=len(products),
                        desc=f"{self.name} | {category_name} | Page {page}",
                        unit="prod",
                        leave=False,
                        dynamic_ncols=True) as prod_bar:
                    for future in as_completed(futures):
                        result = future.result()
                        if result:
                            results.append(result)
                        prod_bar.update(1)

            # -------------------------
            # Page stats & ETA
            # -------------------------
            elapsed = time.time() - category_start_time
            pages_done = page
            pages_remaining = total_pages - pages_done
            avg_time_per_page = elapsed / pages_done
            eta = avg_time_per_page * pages_remaining

            log_global(
                f"{self.name} | {category_name} | page {page}/{total_pages} | "
                f"parsed {len(products)} products | "
                f"avg_page_time={avg_time_per_page:.2f}s | ETA={eta/60:.1f} min"
            )

        logger.info(f"{self.name} | CATEGORY_FINISHED | {category_name} | collected={len(results)}")
        return results

    # ---------------------------------
    # BRAND FALLBACK
    # ---------------------------------


    def scrape_brands(self, seen_products):
        logger.info(f"{self.name} | BRAND_SCRAPE_START")
        log_global(f"{self.name} | BRAND_SCRAPE_START")

        results = []
        total_brands = len(SEARCH_BRANDS)
        start_time = time.time()

        # -------------------------
        # Brand-level progress bar
        # -------------------------
        for index, brand in enumerate(tqdm(SEARCH_BRANDS,
                                        desc=f"{self.name} | Brands",
                                        unit="brand",
                                        dynamic_ncols=True)):
            brand_start_time = time.time()
            logger.info(f"{self.name} | BRAND_START | {brand}")

            # -------------------------
            # GET SEARCH RESULTS
            # -------------------------
            products = self.search(brand)["products"]

            if not products:
                logger.info(f"{self.name} | BRAND_EMPTY | {brand}")
                continue

            logger.info(f"{self.name} | BRAND_PRODUCTS | {brand} | count={len(products)}")

            # -------------------------
            # Product-level progress bar
            # -------------------------
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(self.scrape_product_worker, product, seen_products, self.name)
                    for product in products
                ]

                with tqdm(total=len(products),
                        desc=f"{self.name} | {brand}",
                        unit="prod",
                        leave=False,
                        dynamic_ncols=True) as prod_bar:
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                            if result:
                                results.append(result)
                        except Exception as e:
                            logger.error(f"{self.name} | BRAND_WORKER_ERROR | {brand} | {e}")
                        finally:
                            prod_bar.update(1)

            # -------------------------
            # ETA for brands
            # -------------------------
            elapsed = time.time() - start_time
            brands_done = index + 1
            brands_remaining = total_brands - brands_done
            avg_time_per_brand = elapsed / brands_done
            eta = avg_time_per_brand * brands_remaining

            log_global(
                f"{self.name} | {brand} | brand {brands_done}/{total_brands} | "
                f"products={len(products)} | avg_brand_time={avg_time_per_brand:.2f}s | "
                f"ETA={eta/60:.1f} min"
            )

            logger.info(f"{self.name} | BRAND_DONE | {brand}")

        logger.info(f"{self.name} | BRAND_SCRAPE_FINISHED | total_collected={len(results)}")
        log_global(f"{self.name} | BRAND_SCRAPE_FINISHED | total={len(results)}")

        return results

    # ---------------------------------
    # FULL SCRAPER
    # ---------------------------------

    def scrape_site(self):

        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        results = []
        seen_products = {}

        total_categories = len(CATEGORIES)

        # ---------------------------------
        # CATEGORY SCRAPING
        # ---------------------------------
        for i, (category_name, category_url) in enumerate(CATEGORIES.items(), start=1):

            category_results = self.scrape_category(
                category_name,
                category_url,
                seen_products,
                i,
                total_categories
            )

            results.extend(category_results)

        logger.info(
            f"{self.name} | CATEGORY_PHASE_DONE | collected={len(results)}"
        )
        log_global(
            f"{self.name} | CATEGORY_PHASE_DONE | collected={len(results)}"
        )

        # ---------------------------------
        # BRAND SCRAPING (FALLBACK)
        # ---------------------------------
        brand_results = self.scrape_brands(seen_products)

        results.extend(brand_results)

        logger.info(
            f"{self.name} | BRAND_PHASE_DONE | collected={len(brand_results)}"
        )
        log_global(
            f"{self.name} | BRAND_PHASE_DONE | collected={len(brand_results)}"
        )

        # ---------------------------------
        # FINAL STATS
        # ---------------------------------
        total_products = len(results)

        logger.info(
            f"{self.name} | SCRAPE_FINISHED | total_products={total_products}"
        )
        log_global(
            f"{self.name} | SCRAPE_FINISHED | total_products={total_products}"
        )

        print("\n=================================")
        print(f" SCRAPING FINISHED : {self.name}")
        print(f" TOTAL PRODUCTS    : {total_products}")
        print("=================================\n")

        return {
            "site": self.name,
            "total_products": total_products,
            "products": results
        }
    
        
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

        categories = parse_tunisianet_categories(html)
        category_ids = parse_tunisianet_category_ids(html)

        os.makedirs("config", exist_ok=True)

        file_path = "config/categoriesTunisiaNet.py"

        with open(file_path, "w", encoding="utf-8") as f:

            # -------------------------
            # CATEGORY LINKS
            # -------------------------
            f.write("CATEGORIES = {\n")

            for name, url in categories.items():
                f.write(f'    "{name}": "{url}",\n')

            f.write("}\n\n")

            # -------------------------
            # CATEGORY IDS
            # -------------------------
            f.write("CATEGORY_IDS = {\n")

            for cid, name in category_ids.items():
                f.write(f'    "{cid}": "{name}",\n')

            f.write("}\n")

        logger.info(
            f"{self.name} | CATEGORY_DISCOVERY_FINISHED | "
            f"categories={len(categories)} | ids={len(category_ids)}"
        )

        log_global(
            f"{self.name} | CATEGORY_DISCOVERY_FINISHED | "
            f"categories={len(categories)} | ids={len(category_ids)}"
        )

        print("\n=================================")
        print(f" {self.name} CATEGORIES EXTRACTED")
        print(f" TOTAL CATEGORIES : {len(categories)}")
        print(" Saved to config/categoriesTunisiaNet.py")
        print("=================================\n")


    def get_category_page_count(self, html):

        soup = BeautifulSoup(html, "lxml")

        page_numbers = []

        # -------------------------
        # Try classic pagination
        # -------------------------
        pages = soup.select(".page-list li a")

        for p in pages:
            text = p.text.strip()

            if text.isdigit():
                page_numbers.append(int(text))

        # -------------------------
        # Fallback (sometimes different structure)
        # -------------------------
        if not page_numbers:

            pages = soup.select(".pagination a")

            for p in pages:
                text = p.text.strip()

                if text.isdigit():
                    page_numbers.append(int(text))

        # -------------------------
        # Final result
        # -------------------------
        if not page_numbers:
            return 1

        return max(page_numbers)