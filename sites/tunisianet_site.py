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


        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        self.test_mode = False
        self.test_limit = 5
        
        # Batch size for this site
        self.batch_size = 50



    def get_requester_config(self) -> Dict:
        return {
            "requests_per_minute": 100,
            "max_retries": 4,
            "timeout": 45,
            "use_session": True,
            "use_referer": True,
            "delay_between_requests": (0.5, 2.5),
        }

    def _add_error(self, error_msg: str):
        """Helper method to add error to dashboard"""
        try:
            from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
            progress = get_multi_scrape_progress()
            progress.increment_error(self.name, error_msg)
        except Exception as e:
            logger.debug(f"{self.name} | ERROR_DASHBOARD_FAILED | {e}")

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
            self._add_error(f"Category page parse error: {str(e)[:50]}")
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
                self._add_error(f"Product missing ID or URL: {product_id or 'unknown'}")
                return None

            product_id = str(product_id)

            with self.lock:
                if product_id in self.seen_products:
                    self.stats["skipped_duplicates"] += 1
                    return None
                self.seen_products.add(product_id)

            # Use inherited _product_exists from BaseSite
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
                self._add_error(f"Parse error {product_id}: {str(parse_error)[:50]}")
                return None
            
            if details is None:
                logger.warning(f"{self.name} | PARSE_FAILED_NONE | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"Parse returned None: {product_id}")
                return None
            
            if not isinstance(details, dict):
                logger.warning(f"{self.name} | DETAILS_NOT_DICT | {product_id} | type={type(details)}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

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

            # USE PARENT'S _save_new_product (which uses batching)
            try:
                save_result = super()._save_new_product(details, product_id)
                if not save_result:
                    logger.warning(f"{self.name} | SAVE_FAILED | {product_id}")
                    with self.lock:
                        self.stats["errors"] += 1
                    self._add_error(f"Save failed: {product_id}")
                    return None
            except Exception as save_error:
                logger.error(f"{self.name} | SAVE_EXCEPTION | {product_id} | {save_error}")
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"Save exception {product_id}: {str(save_error)[:50]}")
                return None

            return details

        except Exception as e:
            product_id = "unknown"
            try:
                if product_data and isinstance(product_data, dict):
                    product_id = product_data.get("main_product_id", "unknown")
            except:
                pass
            
            logger.error(f"{self.name} | PRODUCT_ERROR | {product_id} | {e}")
            with self.lock:
                self.stats["errors"] += 1
            self._add_error(f"Product error {product_id}: {str(e)[:50]}")
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
                    self._add_error(f"Batch worker error: {str(e)[:50]}")

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
            self._add_error(f"Category no page: {category_name}")
            return results

        total_pages = self.get_category_page_count(first_page_html)
        logger.info(f"{self.name} | CATEGORY_PAGES | pages={total_pages}")

        self.start_category(category_name, total_pages)

        for page in range(1, total_pages + 1):
            try:
                products = self.get_category_products(category_url, page)

                if not products:
                    continue

                self.stats["total_products_found"] += len(products)
                self.update_category_page(page)

                page_results = self.process_batch(products, f"{category_name}")
                results.extend(page_results)
            except Exception as e:
                error_msg = f"Page {page} failed in {category_name}: {str(e)[:50]}"
                logger.error(f"{self.name} | PAGE_ERROR | {category_name} | page={page} | {e}")
                self._add_error(error_msg)
                continue

        logger.info(f"{self.name} | CATEGORY_FINISHED | new_products={len(results)}")
        return results

    def scrape_brands(self) -> List[Dict]:
        logger.info(f"{self.name} | BRAND_SCRAPE_START")
        results = []

        for brand in SEARCH_BRANDS:
            try:
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
            except Exception as e:
                error_msg = f"Brand {brand} failed: {str(e)[:50]}"
                logger.error(f"{self.name} | BRAND_ERROR | {brand} | {e}")
                self._add_error(error_msg)
                continue

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

        # =====================================================
        # RESET DASHBOARD STATS FOR NEW SCRAPE CYCLE
        # =====================================================
        try:
            from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
            progress = get_multi_scrape_progress()
            progress.reset_site(self.name)
            logger.info(f"{self.name} | DASHBOARD_RESET | new cycle")
        except Exception as e:
            logger.debug(f"{self.name} | DASHBOARD_RESET_ERROR | {e}")

        # Initialize connections using inherited method
        try:
            if not self._init_connections():
                logger.error(f"{self.name} | CONNECTION_FAILED")
                return []
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

                try:
                    category_results = self.scrape_category_with_progress(category_name, category_url)
                    results.extend(category_results)
                except Exception as e:
                    error_msg = f"Category '{category_name}' failed: {str(e)[:50]}"
                    logger.error(f"{self.name} | CATEGORY_ERROR | {category_name} | {e}")
                    self._add_error(error_msg)
                    continue

                self.complete_category()
                
                batch_size = self.get_pending_batch_size()
                logger.info(f"Progress: {i}/{total_categories} categories completed | batch={batch_size}")

            logger.info("SCRAPING BRANDS (FALLBACK)")
            self.stats['current_category'] = "Brands (Fallback)"

            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)

            try:
                brand_results = self.scrape_brands()
                results.extend(brand_results)
            except Exception as e:
                error_msg = f"Brands fallback failed: {str(e)[:50]}"
                logger.error(f"{self.name} | BRANDS_ERROR | {e}")
                self._add_error(error_msg)

            self.complete_all_categories()
            self._print_final_stats(results)

        except KeyboardInterrupt:
            logger.warning(f"{self.name} | INTERRUPTED")
            try:
                from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
                progress = get_multi_scrape_progress()
                progress.set_status(self.name, "⚠️ Interrupted")
            except:
                pass
        except Exception as e:
            error_msg = f"Scrape failed: {str(e)[:50]}"
            logger.error(f"{self.name} | SCRAPE_ERROR | {e}")
            import traceback
            traceback.print_exc()
            self._add_error(error_msg)
        finally:
            # =====================================================
            # FLUSH ANY REMAINING PRODUCTS
            # =====================================================
            batch_size = self.get_pending_batch_size()
            if batch_size > 0:
                logger.info(f"{self.name} | FLUSHING_PENDING | {batch_size} products")
                try:
                    self.flush_pending_batch()
                except Exception as e:
                    error_msg = f"Flush failed: {str(e)[:50]}"
                    logger.error(f"{self.name} | FLUSH_ERROR | {e}")
                    self._add_error(error_msg)
            
            # Log final summary
            self.log_final_summary(results)
            
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