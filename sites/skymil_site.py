# sites/mytek.py
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict

from bs4 import BeautifulSoup

from config.categoriesMytek import CATEGORIES
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


logger = setup_logger("mytek", "scraper")
dup_logger = setup_logger("mytek", "duplication")


class MytekSite(BaseSite):

    name = "MYTEK"
    BASE_URL = "https://www.mytek.tn/"

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.total_products = 0
        self.start_time = time.time()
        self.performance = PerformanceTracker()

        self.thread_manager = ThreadManager(
            self.name,
            self.start_time,
            lambda: self.total_products
        )
        
        # =====================================================
        # BATCH CONFIGURATION - Use parent's batching
        # =====================================================
        self.batch_size = 50
        

    def get_requester_config(self) -> Dict:
        """Override to provide Mytek-specific requester config"""
        return {
            "requests_per_minute": 20,
            "max_retries": 4,
            "timeout": 45,
            "use_session": True,
            "use_referer": True,
            "delay_between_requests": (0.5, 2.0),
        }

    def _add_error(self, error_msg: str):
        """Helper method to add error to dashboard"""
        try:
            from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
            progress = get_multi_scrape_progress()
            progress.increment_error(self.name, error_msg)
        except Exception as e:
            logger.debug(f"{self.name} | ERROR_DASHBOARD_FAILED | {e}")

    # =====================================================
    # CATEGORY DISCOVERY - Using SmartRequester
    # =====================================================
    
    def extract_categories(self):
        logger.info(f"{self.name} | CATEGORY_DISCOVERY_START")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_START")

        # Use SmartRequester instead of get()
        html = self.requester.get_html(self.BASE_URL)

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

    # =====================================================
    # DUPLICATE HANDLER
    # =====================================================
    
    def handle_duplicate(self, product_id, source, seen_products):
        with self.lock:
            if product_id in seen_products:
                dup_logger.warning(
                    f"{self.name} | DUPLICATE | id={product_id} | source={source}"
                )
                return True

            seen_products[product_id] = source
            return False

    # =====================================================
    # SEARCH - Using SmartRequester
    # =====================================================
    
    def search(self, query):
        logger.info(f"{self.name} | SEARCH_START | {query}")

        page = 1
        results = []
        seen_ids = set()

        while True:
            url = f"{self.BASE_URL}myteksearch/index/productsearch/?q={query}&p={page}"

            # Use SmartRequester instead of get()
            html = self.requester.get_html(url)

            if not html:
                logger.warning(f"{self.name} | SEARCH_EMPTY_HTML | page={page}")
                break

            products = parse_mytek_products(html, self.name)

            if not products:
                logger.info(
                    f"{self.name} | SEARCH_NO_PRODUCTS | page={page} → stopping"
                )
                break

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

    # =====================================================
    # PRODUCT PARSER - Using SmartRequester
    # =====================================================
    
    def parse_product(self, url, source, product_id=None):
        # Use SmartRequester instead of get()
        html = self.requester.get_html(url)

        if not html:
            return None

        return parse_mytek_product_page(html, url, self.name, source, product_id)

    # =====================================================
    # PRODUCT WORKER - Redis/PostgreSQL only (no file skip)
    # =====================================================
    
    def scrape_product_worker(self, product, seen_products, source):
        pid = product.get("main_product_id")
        url = product.get("product_link")

        if not pid or not url:
            return None

        # =====================================================
        # CHECK 1: Duplicate in current session
        # =====================================================
        if self.handle_duplicate(pid, source, seen_products):
            with self.lock:
                self.stats["skipped_duplicates"] += 1
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
            return None

        # =====================================================
        # CHECK 2: Product already exists in Redis/PostgreSQL
        # =====================================================
        if self._product_exists(pid):
            with self.lock:
                self.stats["skipped_duplicates"] += 1
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
            logger.debug(f"{self.name} | SKIP_EXISTING_DB | {pid}")
            return None

        try:
            result = self.parse_product(url, source, pid)

            if result:
                # =====================================================
                # SAVE: Use parent's _save_new_product for Redis + Batching
                # =====================================================
                if super()._save_new_product(result, pid):
                    self.performance.add_product()
                    self.total_products += 1
                    
                    # Update stats for dashboard
                    self.stats["new_products"] = self.total_products
                    self.stats["total_products_found"] = self.total_products
                    
                    if hasattr(self, '_update_stats'):
                        self._update_stats(self.stats)
                    
                    # Also write to results file for backup
                    write_product_result(result, self.name)
                    
                    logger.info(f"{self.name} | PRODUCT_SAVED | {pid}")
                    return result
                else:
                    logger.warning(f"{self.name} | SAVE_FAILED | {pid}")
                    with self.lock:
                        self.stats["errors"] += 1
                        if hasattr(self, '_update_stats'):
                            self._update_stats(self.stats)
                    self._add_error(f"Save failed: {pid}")
                    return None

            return None

        except Exception as e:
            logger.error(f"{self.name} | WORKER_ERROR | {url} | {e}")
            with self.lock:
                self.stats["errors"] += 1
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
            self._add_error(f"Worker error: {str(e)[:50]}")
            return None

    # =====================================================
    # CATEGORY SCRAPER - Using SmartRequester
    # =====================================================

    def scrape_category(self, category_name, category_url, seen_products):
        logger.info(f"{self.name} | CATEGORY_START | {category_name}")
        
        self.stats['current_category'] = category_name
        
        results = []
        seen_ids = set()
        
        # Use SmartRequester instead of get()
        first_html = self.requester.get_html(category_url)
        if not first_html:
            self._add_error(f"No first page: {category_name}")
            return results
        
        total_pages = self.get_category_page_count(first_html)
        
        self.stats['total_pages'] = total_pages
        self.stats['current_page'] = 0
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
        
        for page in range(1, total_pages + 1):
            if '?' in category_url:
                url = f"{category_url}&p={page}"
            else:
                url = f"{category_url}?p={page}"
            
            # Use SmartRequester instead of get()
            html = self.requester.get_html(url)
            
            self.stats['current_page'] = page
            self.stats['current_product'] = f"Page {page}/{total_pages}"
            
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
            
            if not html:
                self._add_error(f"Page {page} empty: {category_name}")
                continue
            
            try:
                products = parse_mytek_products(html, self.name)
            except Exception as e:
                logger.error(f"{self.name} | PARSE_ERROR | {category_name} | page={page} | {e}")
                self._add_error(f"Parse error {category_name}: {str(e)[:50]}")
                continue
            
            if not products:
                continue
            
            first_id = products[0].get("main_product_id")
            if first_id in seen_ids:
                break
            
            for p in products:
                seen_ids.add(p.get("main_product_id"))
            
            # =====================================================
            # REMOVED: File-based existing_ids filter
            # All deduplication is now handled by Redis/PostgreSQL
            # =====================================================
            
            if not products:
                continue
            
            with ThreadPoolExecutor(max_workers=20) as executor:
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
                    try:
                        result = future.result(timeout=60)
                        if result:
                            results.append(result)
                            self.performance.add_product()
                    except Exception as e:
                        logger.error(f"{self.name} | FUTURE_ERROR | {e}")
                        with self.lock:
                            self.stats["errors"] += 1
                            if hasattr(self, '_update_stats'):
                                self._update_stats(self.stats)
                        self._add_error(f"Future error: {str(e)[:50]}")
            
            # Update batch status after each page
            batch_size = self.get_pending_batch_size()
            if batch_size > 0:
                logger.info(f"{self.name} | PAGE_BATCH | {category_name} | page={page} | batch={batch_size}")
            
            self.stats['completed_categories'] = page
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
        
        return results

    # =====================================================
    # BRAND SCRAPER
    # =====================================================
    
    def scrape_brands(self, seen_products):
        logger.info(f"{self.name} | BRAND_START")
        results = []

        for brand in SEARCH_BRANDS:
            try:
                logger.info(f"{self.name} | BRAND_START | {brand}")

                products = self.search(brand)["products"]

                if not products:
                    logger.info(f"{self.name} | BRAND_EMPTY | {brand}")
                    continue

                # =====================================================
                # REMOVED: File-based existing_ids filter
                # All deduplication is now handled by Redis/PostgreSQL
                # =====================================================

                tasks = [
                    (self.scrape_product_worker, p, seen_products, brand)
                    for p in products
                ]

                brand_results = self.thread_manager.run_tasks(
                    tasks,
                    desc=f"{self.name} | BRAND | {brand}"
                )

                results.extend(brand_results)

                logger.info(
                    f"{self.name} | BRAND_DONE | {brand} | collected={len(brand_results)}"
                )
            except Exception as e:
                logger.error(f"{self.name} | BRAND_ERROR | {brand} | {e}")
                self._add_error(f"Brand {brand} failed: {str(e)[:50]}")

        logger.info(f"{self.name} | BRAND_DONE | total={len(results)}")
        return results

    # =====================================================
    # FULL SCRAPER
    # =====================================================

    def scrape_site(self):
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        self.start_time = time.time()
        results = []
        seen_products = {}
        
        total_categories = len(CATEGORIES)

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
        
        logger.info(f"{self.name} | STARTING | total_categories={total_categories}")
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)

        try:
            for i, (name, url) in enumerate(CATEGORIES.items(), start=1):
                self.stats['current_category'] = name
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                try:
                    res = self.scrape_category(name, url, seen_products)
                    results.extend(res)
                except Exception as e:
                    error_msg = f"Category '{name}' failed: {str(e)[:50]}"
                    logger.error(f"{self.name} | CATEGORY_ERROR | {name} | {e}")
                    self._add_error(error_msg)
                    continue
                
                self.stats['completed_categories'] = i
                self.stats['category_progress'] = (i / total_categories) * 100
                
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                batch_size = self.get_pending_batch_size()
                logger.info(f"Progress: {i}/{total_categories} categories completed | new_products={len(results)} | batch={batch_size} | skipped={self.stats['skipped_duplicates']}")

            try:
                brand_results = self.scrape_brands(seen_products)
                results.extend(brand_results)
            except Exception as e:
                error_msg = f"Brands fallback failed: {str(e)[:50]}"
                logger.error(f"{self.name} | BRANDS_ERROR | {e}")
                self._add_error(error_msg)

            self.stats['completed_categories'] = total_categories
            self.stats['category_progress'] = 100
            
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)

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

        logger.info(f"{self.name} | SCRAPE_DONE | total={len(results)}")
        log_global(f"{self.name} | SCRAPE_DONE | total={len(results)}")

        print("\n==============================")
        print(f" {self.name} SCRAPING DONE")
        print(f" TOTAL PRODUCTS: {len(results)}")
        print(f" NEW PRODUCTS: {self.stats['new_products']}")
        print(f" SKIPPED (EXISTING): {self.stats['skipped_duplicates']}")
        print(f" REDIS SAVED: {self.stats['redis_saved']}")
        print(f" POSTGRES SAVED: {self.stats['postgres_saved']}")
        print(f" ERRORS: {self.stats['errors']}")
        print("==============================\n")

        return {
            "site": self.name,
            "total_products": len(results),
            "products": results,
            "stats": self.stats
        }

    # =====================================================
    # PAGINATION DETECTION
    # =====================================================
    
    def get_category_page_count(self, html):
        soup = BeautifulSoup(html, "lxml")
        
        pages = soup.select(".pagination li a")
        nums = []
        
        for p in pages:
            try:
                text = p.text.strip()
                if text.isdigit():
                    nums.append(int(text))
            except:
                continue
        
        if nums:
            return max(nums)
        
        pages = soup.select(".page-list li a")
        nums = []
        
        for p in pages:
            try:
                text = p.text.strip()
                if text.isdigit():
                    nums.append(int(text))
            except:
                continue
        
        return max(nums) if nums else 1


def create_mytek_site() -> MytekSite:
    return MytekSite()