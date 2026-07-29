from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import threading
import time
import os
from typing import Any, Dict, Optional
from contextlib import contextmanager

from core.Web.http_client import get
from core.logger import setup_logger, write_product_result, log_global
from core.utils.clean_text_parser import normalize_availability

from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database

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

        # Connections (initialized in scrape_site)
        self.cache = None
        self.db = None

        # Stats tracking
        self.stats = {
            "new_products": 0,
            "skipped_duplicates": 0,
            "errors": 0,
            "redis_saved": 0,
            "postgres_saved": 0,
            "total_products_found": 0
        }

    # ---------------------------------
    # CONNECTION MANAGEMENT
    # ---------------------------------
    def _init_connections(self):
        """Initialize Redis and PostgreSQL connections"""
        try:
            self.cache = ProductCache()
            logger.info(f"{self.name} | REDIS_CONNECTED")
        except Exception as e:
            error_logger.error(f"{self.name} | REDIS_CONNECTION_ERROR | {e}")
            raise

        try:
            self.db = Database()
            logger.info(f"{self.name} | POSTGRES_CONNECTED")
        except Exception as e:
            error_logger.error(f"{self.name} | POSTGRES_CONNECTION_ERROR | {e}")
            raise

    def _close_connections(self):
        """Close PostgreSQL connection"""
        try:
            if self.db:
                self.db.close()
                logger.info(f"{self.name} | POSTGRES_CLOSED")
        except Exception as e:
            error_logger.error(f"{self.name} | POSTGRES_CLOSE_ERROR | {e}")

    @contextmanager
    def _connections(self):
        """Context manager for database connections"""
        self._init_connections()
        try:
            yield
        finally:
            self._close_connections()

    # ---------------------------------
    # JSON SERIALIZATION
    # ---------------------------------
    def _make_json_serializable(self, obj: Any) -> Any:
        """Convert non-serializable objects to JSON serializable format"""
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {k: self._make_json_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._make_json_serializable(item) for item in obj]
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        try:
            return str(obj)
        except Exception:
            return None

    # ---------------------------------
    # CHECK IF PRODUCT EXISTS
    # ---------------------------------
    def _product_exists(self, product_id: str) -> bool:
        """Check if product already exists in Redis or PostgreSQL"""
        site = self.name.lower()

        # Check Redis first (fast)
        if self.cache:
            try:
                if self.cache.get_product(site, product_id):
                    return True
            except Exception as e:
                error_logger.error(f"{self.name} | CACHE_CHECK_ERROR | {product_id} | {e}")

        # Check PostgreSQL
        if self.db:
            try:
                if self.db.get_product_by_site_and_main_id(site, product_id):
                    return True
            except Exception as e:
                error_logger.error(f"{self.name} | DB_CHECK_ERROR | {product_id} | {e}")

        return False

    # ---------------------------------
    # SAVE NEW PRODUCT
    # ---------------------------------
    def _save_new_product(self, product: Dict, product_id: str) -> bool:
        """Save ONLY new product to Redis cache and PostgreSQL"""
        site = self.name.lower()
        try:
            # Prepare Redis cache payload (essential fields only)
            cache_payload = {
                "url": str(product.get("url", "")),
                "price": float(product.get("price") or 0),
                "discount_price": float(product.get("discount_price") or 0),
                "discount_amount": float(product.get("discount_amount") or 0),
                "availability": normalize_availability(product.get("availability")),
                "special_id": str(product.get("special_id")) if product.get("special_id") else None,
                "price_valid_until": str(product.get("price_valid_until")) if product.get("price_valid_until") else None,
                "name": product.get("name"),
                "brand": product.get("brand"),
                "category": product.get("category"),
                "last_checked": int(time.time()),
                "is_enabled": True
            }

            # Save to Redis
            if self.cache:
                self.cache.set_product(site, product_id, cache_payload, ttl=None)
                with self.lock:
                    self.stats["redis_saved"] += 1

            # Save to PostgreSQL
            if self.db:
                db_product = {
                    "site": site,
                    "main_product_id": product_id,
                    "special_id": product.get("special_id"),
                    "url": product.get("url"),
                    "name": product.get("name"),
                    "price": float(product.get("price") or 0),
                    "discount_price": float(product.get("discount_price") or 0),
                    "discount_amount": float(product.get("discount_amount") or 0),
                    "availability": normalize_availability(product.get("availability")),
                    "price_valid_until": product.get("price_valid_until"),
                    "brand": product.get("brand"),
                    "category": product.get("category"),
                    "description": product.get("description", ""),
                    "short_description": product.get("short_description", []),
                    "images": product.get("images", []),
                    "main_image": product.get("main_image"),
                    "is_enabled": True
                }

                db_product = self._make_json_serializable(db_product)
                self.db.upsert_products([db_product])
                with self.lock:
                    self.stats["postgres_saved"] += 1

            return True

        except Exception as e:
            error_logger.error(f"{self.name} | SAVE_ERROR | {product_id} | {e}")
            return False

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

        try:
            product_id = product.get("main_product_id")
            product_url = product.get("product_link")

            if not product_id or not product_url:
                return None

            if self.handle_duplicate(product_id, source, seen_products):
                return None

            # =====================================================
            # SKIP IF PRODUCT ALREADY EXISTS (only add NEW products)
            # =====================================================
            if self._product_exists(product_id):
                with self.lock:
                    self.stats["skipped_duplicates"] += 1
                logger.debug(f"{self.name} | SKIP_EXISTING | {product_id}")
                return None

            details = self.parse_product(product_url, product_id)

            if not details:
                logger.warning(f"{self.name} | PARSE_FAILED | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            # =====================================================
            # NEW PRODUCT - SAVE IT (Redis + PostgreSQL)
            # =====================================================
            details["is_new"] = True
            logger.info(f"{self.name} | NEW_PRODUCT | {product_id}")

            if self._save_new_product(details, product_id):
                with self.lock:
                    self.stats["new_products"] += 1
            else:
                with self.lock:
                    self.stats["errors"] += 1

            write_product_result(details, self.name)

            return details

        except Exception as e:
            error_logger.error(
                f"{self.name} | WORKER_ERROR | {product.get('main_product_id')} | {e}"
            )
            with self.lock:
                self.stats["errors"] += 1
            return None

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

        # Reset stats for this run
        self.stats = {
            "new_products": 0,
            "skipped_duplicates": 0,
            "errors": 0,
            "redis_saved": 0,
            "postgres_saved": 0,
            "total_products_found": 0
        }

        results = []
        seen_products = {}

        # Initialize Redis + PostgreSQL connections
        try:
            self._init_connections()
        except Exception as e:
            error_logger.error(f"{self.name} | INIT_CONNECTIONS_FAILED | {e}")
            log_global(f"{self.name} | INIT_CONNECTIONS_FAILED | {e}")
            return {
                "site": self.name,
                "total_products": 0,
                "products": [],
                "stats": self.stats
            }

        try:
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

        except Exception as e:
            error_logger.error(f"{self.name} | SCRAPE_SITE_ERROR | {e}")
        finally:
            self._close_connections()

        total = len(results)
        self.stats["total_products_found"] = total

        logger.info(f"{self.name} | SCRAPE_DONE | total={total}")
        log_global(f"{self.name} | SCRAPE_DONE | total={total}")

        print("\n==============================")
        print(f"SITE: {self.name}")
        print(f"TOTAL PRODUCTS: {total}")
        print(f"NEW PRODUCTS: {self.stats['new_products']}")
        print(f"SKIPPED (EXISTING): {self.stats['skipped_duplicates']}")
        print(f"REDIS SAVED: {self.stats['redis_saved']}")
        print(f"POSTGRES SAVED: {self.stats['postgres_saved']}")
        print(f"ERRORS: {self.stats['errors']}")
        print("==============================\n")

        return {
            "site": self.name,
            "total_products": total,
            "products": results,
            "stats": self.stats
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