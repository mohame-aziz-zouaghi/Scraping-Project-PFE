# sites/tunisianet.py
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any, Set
from contextlib import contextmanager

from bs4 import BeautifulSoup

from Database.Redis.product_cache import ProductCache
from core.Web.smart_requester import SmartRequester
from core.logger import setup_logger, write_product_result, log_global
from core.performance import PerformanceTracker
from Database.Postgresql.db import Database

from parsers.tunisianet.tunisianet_product_parser import parse_tunisianet_product_page
from parsers.tunisianet.tunisianet_search_parser import parse_tunisianet_products

from config.categoriesTunisiaNet import CATEGORIES
from config.brands import SEARCH_BRANDS
from sites.base_site import BaseSite


logger = setup_logger("tunisianet", "scraper")
dup_logger = setup_logger("tunisianet", "duplication")


class TunisiaNetSite(BaseSite):
    """Tunisianet scraper - ONLY adds NEW products, skips existing ones"""

    def __init__(self):
        super().__init__()

        self.name = "TUNISIANET"
        self.scrape_interval = 3600
        self.performance = PerformanceTracker()

        self.base_url = "https://www.tunisianet.com.tn"
        self.search_url = "https://www.tunisianet.com.tn/recherche"

        self.requester = SmartRequester(
            site_name="tunisianet",
            requests_per_minute=100,
            max_retries=4,
            timeout=45,
            use_session=True,
            use_referer=True,
            delay_between_requests=(0.5, 2.0)
        )

        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        self.cache = None
        self.db = None
        self.redis_client = None
        self.test_mode = False
        self.test_limit = 5

    def _init_connections(self):
        try:
            self.cache = ProductCache()
            self.redis_client = self.cache.redis
            logger.info(f"{self.name} | REDIS_CONNECTED")
        except Exception as e:
            logger.error(f"{self.name} | REDIS_CONNECTION_ERROR | {e}")
            raise

        try:
            self.db = Database()
            logger.info(f"{self.name} | POSTGRES_CONNECTED")
        except Exception as e:
            logger.error(f"{self.name} | POSTGRES_CONNECTION_ERROR | {e}")
            raise

    def _close_connections(self):
        try:
            if self.db:
                self.db.close()
                logger.info(f"{self.name} | POSTGRES_CLOSED")
        except Exception as e:
            logger.error(f"{self.name} | POSTGRES_CLOSE_ERROR | {e}")

    def _make_json_serializable(self, obj: Any) -> Any:
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
        except:
            return None

    def search(self, query: str) -> Dict:
        logger.info(f"{self.name} | SEARCH_START | {query}")
        page = 1
        results = []
        seen_first_ids = set()

        while True:
            url = f"{self.search_url}?s={query}&submit_search=&page={page}&order=product.price.asc"
            html = self.requester.get_html(url)

            if not html:
                logger.warning(f"{self.name} | SEARCH_FAILED | page={page}")
                break

            products = parse_tunisianet_products(html, self.name.lower())
            if not products:
                break

            first_pid = products[0].get("main_product_id")
            if first_pid in seen_first_ids:
                logger.info(f"{self.name} | SEARCH_LOOP_DETECTED | page={page}")
                break

            seen_first_ids.add(first_pid)
            results.extend(products)
            page += 1

        return {
            "site": self.name.lower(),
            "query": query,
            "total_results": len(results),
            "products": results
        }

    def get_category_page_count(self, html: str) -> int:
        soup = BeautifulSoup(html, "lxml")
        page_numbers = []
        pages = soup.select(".page-list li a")

        for p in pages:
            text = p.text.strip()
            if text.isdigit():
                page_numbers.append(int(text))

        if not page_numbers:
            pages = soup.select(".pagination a")
            for p in pages:
                text = p.text.strip()
                if text.isdigit():
                    page_numbers.append(int(text))

        return max(page_numbers) if page_numbers else 1

    def get_category_products(self, category_url: str, page: int) -> List[Dict]:
        url = f"{category_url}?page={page}"
        html = self.requester.get_html(url)

        if not html:
            logger.warning(f"{self.name} | CATEGORY_PAGE_FAILED | {url}")
            return []

        try:
            products = parse_tunisianet_products(html, self.name.lower())
        except Exception as e:
            logger.error(f"{self.name} | PARSE_CATEGORY_ERROR | {url} | {e}")
            return []

        if products is None:
            return []

        valid_products = []
        for p in products:
            if p is not None and isinstance(p, dict):
                if p.get("main_product_id") and p.get("product_link"):
                    valid_products.append(p)

        return valid_products

    def parse_product(self, url: str, product_id: str = None) -> Optional[Dict]:
        html = self.requester.get_html(url)
        if not html:
            return None
        
        try:
            return parse_tunisianet_product_page(html, url, self.name.lower(), product_id)
        except Exception as e:
            logger.error(f"{self.name} | PARSE_EXCEPTION | {product_id} | {e}")
            return None

    def _product_exists(self, product_id: str) -> bool:
        if self.cache:
            try:
                cached = self.cache.get_product(self.name.lower(), product_id)
                if cached:
                    return True
            except:
                pass

        if self.db:
            try:
                existing = self.db.get_product_by_site_and_main_id(self.name.lower(), product_id)
                if existing:
                    return True
            except:
                pass

        return False

    def _save_new_product(self, product: Dict, product_id: str) -> bool:
        try:
            if product is None:
                logger.warning(f"{self.name} | SAVE_EMPTY_PRODUCT | {product_id}")
                return False
            
            if not isinstance(product, dict):
                logger.warning(f"{self.name} | SAVE_INVALID_PRODUCT | {product_id} | type={type(product)}")
                return False
            
            url = product.get("url", "")
            if not url:
                logger.warning(f"{self.name} | SAVE_NO_URL | {product_id}")
                return False
            
            price = product.get("price", 0)
            if price is None or price <= 0:
                logger.warning(f"{self.name} | SAVE_NO_PRICE | {product_id} | price={price}")
                return False
            
            cache_payload = {
                "url": str(url),
                "price": float(price),
                "discount_price": float(product.get("discount_price", 0) or 0),
                "discount_amount": float(product.get("discount_amount", 0) or 0),
                "availability": str(product.get("availability", "OutOfStock") or "OutOfStock"),
                "special_id": str(product.get("special_id")) if product.get("special_id") else None,
                "price_valid_until": str(product.get("price_valid_until")) if product.get("price_valid_until") else None,
                "last_checked": int(time.time()),
                "is_enabled": True
            }
            
            saved_to_redis = False
            saved_to_pg = False
            
            if self.cache:
                try:
                    self.cache.set_product(self.name.lower(), product_id, cache_payload, ttl=None)
                    saved_to_redis = True
                    with self.lock:
                        self.stats["redis_saved"] += 1
                except Exception as redis_error:
                    logger.error(f"{self.name} | REDIS_SAVE_ERROR | {product_id} | {redis_error}")
            
            if self.db:
                try:
                    db_product = {
                        "site": self.name.lower(),
                        "main_product_id": product_id,
                        "special_id": product.get("special_id"),
                        "url": url,
                        "name": product.get("name", ""),
                        "price": float(price),
                        "discount_price": float(product.get("discount_price", 0) or 0),
                        "discount_amount": float(product.get("discount_amount", 0) or 0),
                        "availability": product.get("availability", "OutOfStock") or "OutOfStock",
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
                    saved_to_pg = True
                    with self.lock:
                        self.stats["postgres_saved"] += 1
                except Exception as pg_error:
                    logger.error(f"{self.name} | PG_SAVE_ERROR | {product_id} | {pg_error}")
            
            if saved_to_redis and saved_to_pg:
                with self.lock:
                    self.stats["new_products"] += 1
                write_product_result(product, self.name)
                return True
            else:
                logger.warning(f"{self.name} | PARTIAL_SAVE | {product_id} | Redis={saved_to_redis}, PG={saved_to_pg}")
                return False
                
        except Exception as e:
            logger.error(f"{self.name} | SAVE_ERROR | {product_id} | {e}")
            return False

    def process_product(self, product_data: Dict) -> Optional[Dict]:
        try:
            if product_data is None:
                logger.warning(f"{self.name} | SKIP_NONE")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            if not isinstance(product_data, dict):
                logger.warning(f"{self.name} | SKIP_INVALID | type={type(product_data)}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            product_id = product_data.get("main_product_id")
            url = product_data.get("product_link")

            if not product_id or not url:
                logger.warning(f"{self.name} | SKIP_NO_ID | product_id={product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            product_id = str(product_id)

            with self.lock:
                if product_id in self.seen_products:
                    self.stats["skipped_duplicates"] += 1
                    return None
                self.seen_products.add(product_id)

            if self._product_exists(product_id):
                with self.lock:
                    self.stats["skipped_duplicates"] += 1
                return None

            # Parse product with safety
            details = None
            try:
                details = self.parse_product(url, product_id)
            except Exception as parse_error:
                logger.error(f"{self.name} | PARSE_EXCEPTION | {product_id} | {parse_error}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            # =====================================================
            # CRITICAL: Validate details BEFORE using .get()
            # =====================================================
            if details is None:
                logger.warning(f"{self.name} | PARSE_FAILED_NONE | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            if not isinstance(details, dict):
                logger.warning(f"{self.name} | DETAILS_NOT_DICT | {product_id} | type={type(details)}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            # =====================================================
            # NOW SAFE to use .get() on details
            # =====================================================
            if not details.get("name"):
                logger.warning(f"{self.name} | DETAILS_NO_NAME | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            if not details.get("price") or details.get("price") <= 0:
                logger.warning(f"{self.name} | DETAILS_NO_PRICE | {product_id} | price={details.get('price')}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            if not details.get("url"):
                logger.warning(f"{self.name} | DETAILS_NO_URL | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            logger.info(f"{self.name} | NEW_PRODUCT | {product_id}")
            details['is_new'] = True

            try:
                save_result = self._save_new_product(details, product_id)
                if not save_result:
                    logger.warning(f"{self.name} | SAVE_FAILED | {product_id}")
                    with self.lock:
                        self.stats["errors"] += 1
                    return None
            except Exception as save_error:
                logger.error(f"{self.name} | SAVE_EXCEPTION | {product_id} | {save_error}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            return details

        except Exception as e:
            # =====================================================
            # SAFE error handling - don't call .get() on None
            # =====================================================
            product_id = "unknown"
            try:
                if product_data and isinstance(product_data, dict):
                    product_id = product_data.get("main_product_id", "unknown")
            except:
                pass
            
            logger.error(f"{self.name} | PRODUCT_ERROR | {product_id} | {e}")
            with self.lock:
                self.stats["errors"] += 1
            return None

    def process_batch(self, products: List[Dict], batch_name: str = "Batch") -> List[Dict]:
        results = []

        if not products:
            return results

        valid_products = []
        for p in products:
            if p is not None and isinstance(p, dict):
                if p.get("main_product_id") and p.get("product_link"):
                    valid_products.append(p)

        if not valid_products:
            logger.info(f"{self.name} | {batch_name} | No valid products")
            return results

        logger.info(f"{self.name} | {batch_name} | Processing {len(valid_products)} products")

        self.stats['current_category'] = batch_name
        self.stats['current_product'] = f"Products: {len(valid_products)}"

        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)

        completed = 0

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {
                executor.submit(self.process_product, product): product
                for product in valid_products
            }

            for future in as_completed(futures):
                try:
                    result = future.result(timeout=60)
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error(f"{self.name} | BATCH_ERROR | {e}")
                    with self.lock:
                        self.stats["errors"] += 1

                completed += 1

                self.stats['current_product'] = f"Products: {completed}/{len(valid_products)}"

                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)

        return results

    def scrape_category_with_progress(self, category_name: str, category_url: str) -> List[Dict]:
        logger.info(f"{self.name} | CATEGORY_START | {category_name}")
        results = []

        first_page_html = self.requester.get_html(f"{category_url}?page=1")
        if not first_page_html:
            logger.warning(f"{self.name} | CATEGORY_NO_PAGE")
            return results

        total_pages = self.get_category_page_count(first_page_html)
        logger.info(f"{self.name} | CATEGORY_PAGES | pages={total_pages}")

        self.start_category(category_name, total_pages)

        for page in range(1, total_pages + 1):
            products = self.get_category_products(category_url, page)

            if not products:
                continue

            self.stats["total_products_found"] += len(products)
            self.update_category_page(page)

            page_results = self.process_batch(products, f"{category_name}")
            results.extend(page_results)

        logger.info(f"{self.name} | CATEGORY_FINISHED | new_products={len(results)}")
        return results

    def scrape_brands(self) -> List[Dict]:
        logger.info(f"{self.name} | BRAND_SCRAPE_START")
        results = []

        for brand in SEARCH_BRANDS:
            logger.info(f"{self.name} | BRAND_START | {brand}")

            search_results = self.search(brand)
            products = search_results.get("products", [])

            if not products:
                logger.info(f"{self.name} | BRAND_EMPTY")
                continue

            self.stats["total_products_found"] += len(products)

            brand_results = self.process_batch(products, f"Brand: {brand}")
            results.extend(brand_results)

            logger.info(f"{self.name} | BRAND_DONE | new_products={len(brand_results)}")

        return results

    def _print_final_stats(self, results: List[Dict]):
        print(f"\n{'='*60}")
        print(f"📊 SCRAPE COMPLETE: {self.name}")
        print(f"{'='*60}")
        print(f"✅ New products: {self.stats['new_products']:,}")
        print(f"⏭️ Existing (skipped): {self.stats['skipped_duplicates']:,}")
        print(f"💾 Redis saves: {self.stats['redis_saved']:,}")
        print(f"🗄️ PostgreSQL saves: {self.stats['postgres_saved']:,}")
        print(f"❌ Errors: {self.stats['errors']:,}")
        print(f"📦 Total found: {self.stats['total_products_found']:,}")
        print(f"{'='*60}\n")

    def scrape_site(self) -> List[Dict]:
        logger.info(f"{self.name} | SCRAPE_START (NEW PRODUCTS ONLY)")
        log_global(f"{self.name} | SCRAPE_START (NEW PRODUCTS ONLY)")

        self.performance = PerformanceTracker()
        results = []

        try:
            self._init_connections()
        except Exception as e:
            logger.error(f"{self.name} | CONNECTION_FAILED | {e}")
            return []

        try:
            total_categories = len(CATEGORIES)

            self.stats = {
                "new_products": 0,
                "skipped_duplicates": 0,
                "errors": 0,
                "redis_saved": 0,
                "postgres_saved": 0,
                "total_products_found": 0,
                "completed_categories": 0,
                "total_categories": total_categories,
                "current_category": "",
                "current_product": "",
                "current_page": 0,
                "total_pages": 0,
                "category_progress": 0
            }
            self.seen_products.clear()

            logger.info(f"SCRAPING {total_categories} CATEGORIES")

            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)

            for i, (category_name, category_url) in enumerate(CATEGORIES.items(), start=1):
                if self.test_mode and i > 1:
                    break

                logger.info(f"Category {i}/{total_categories}: {category_name}")

                self.stats['current_category'] = category_name
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)

                category_results = self.scrape_category_with_progress(category_name, category_url)
                results.extend(category_results)

                self.complete_category()

                logger.info(f"Progress: {i}/{total_categories} categories completed")

            logger.info("SCRAPING BRANDS (FALLBACK)")
            self.stats['current_category'] = "Brands (Fallback)"

            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)

            brand_results = self.scrape_brands()
            results.extend(brand_results)

            self.complete_all_categories()
            self._print_final_stats(results)

        except KeyboardInterrupt:
            logger.warning(f"{self.name} | INTERRUPTED")
        except Exception as e:
            logger.error(f"{self.name} | SCRAPE_ERROR | {e}")
        finally:
            self._close_connections()

        self.performance.print_summary(self.name)
        logger.info(f"{self.name} | SCRAPE_DONE | total_new={len(results)} | stats={self.stats}")
        log_global(f"{self.name} | SCRAPE_DONE | total_new={len(results)}")

        return results

    def test_scrape(self, limit: int = 5) -> List[Dict]:
        print(f"\n🧪 TEST MODE: Scraping first {limit} products only")
        self.test_mode = True
        self.test_limit = limit
        results = self.scrape_site()
        print(f"\n✅ Test completed: {len(results)} new products found")
        return results

    def extract_categories(self):
        from parsers.tunisianet.tunisianet_category_parser import parse_tunisianet_categories
        logger.info(f"{self.name} | CATEGORY_EXTRACTION_START")
        html = self.requester.get_html(self.base_url)
        if not html:
            logger.error(f"{self.name} | CATEGORY_EXTRACTION_FAILED")
            return
        categories = parse_tunisianet_categories(html)
        print(f"\n✅ Found {len(categories)} categories")
        return categories


def create_tunisianet_site() -> TunisiaNetSite:
    return TunisiaNetSite()