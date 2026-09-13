# sites/sbs.py
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import threading
import time
import os
import tempfile
import atexit
from typing import Any, Dict, Optional, List
from datetime import datetime
import logging

from core.logger import setup_logger, write_product_result, log_global
from core.utils.clean_text_parser import normalize_availability

from parsers.sbs.sbs_search_parser import parse_products
from parsers.sbs.sbs_product_parser import parse_sbs_product_page
from parsers.sbs.sbs_category_parser import parse_sbs_categories

from sites.base_site import BaseSite
from config.brands import SEARCH_BRANDS
from config.categoriesSBS import CATEGORIES


logger = setup_logger("sbs", "scraper")
dup_logger = setup_logger("sbs", "duplication")
error_logger = setup_logger("sbs", "error")

# Set duplicate logger to DEBUG level (only goes to file, not console)
dup_logger.setLevel(logging.DEBUG)
for handler in dup_logger.handlers[:]:
    if isinstance(handler, logging.StreamHandler):
        dup_logger.removeHandler(handler)


class SBSSite(BaseSite):

    name = "SBS"
    SEARCH_URL = "https://www.sbsinformatique.com/recherche?controller=search&s={query}&page={page}"

    def __init__(self, workers=30):
        super().__init__()
        
        self.name = "SBS"
        self.lock = threading.Lock()
        self.workers = workers
        
        # Batch size for this site
        self.batch_size = 50
        
        # Temp file for duplicate logs
        self.dup_log_path = None
        self._init_dup_temp_file()
        atexit.register(self._cleanup_dup_log)

        self.stats.update({
            "new_products": 0,
            "skipped_duplicates": 0,
            "errors": 0,
            "redis_saved": 0,
            "postgres_saved": 0,
            "total_products_found": 0
        })
        
        self.dup_counts = {}
        self.dup_total = 0
        

    def get_requester_config(self) -> Dict:
        return {
            "requests_per_minute": 100,
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
    # TEMP FILE MANAGEMENT FOR DUPLICATE LOGS
    # =====================================================
    
    def _init_dup_temp_file(self):
        """Initialize a temporary file for duplicate logs"""
        try:
            self.dup_log_file = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.log',
                prefix='sbs_duplicates_',
                delete=False,
                encoding='utf-8'
            )
            self.dup_log_path = self.dup_log_file.name
            self.dup_log_file.write(f"{'='*80}\n")
            self.dup_log_file.write(f"SBS DUPLICATE LOG - {datetime.now().isoformat()}\n")
            self.dup_log_file.write(f"{'='*80}\n\n")
            self.dup_log_file.flush()
            logger.info(f"SBS | DUP_TEMP_FILE | {self.dup_log_path}")
        except Exception as e:
            logger.error(f"SBS | DUP_TEMP_FILE_ERROR | {e}")
            self.dup_log_path = None
    
    def _write_dup_log(self, message: str):
        """Write a duplicate log entry to the temp file"""
        if self.dup_log_path:
            try:
                with open(self.dup_log_path, 'a', encoding='utf-8') as f:
                    f.write(f"{datetime.now().isoformat()} | {message}\n")
            except Exception as e:
                pass
    
    def _cleanup_dup_log(self):
        """Clean up the temp duplicate log file"""
        if self.dup_log_path and os.path.exists(self.dup_log_path):
            try:
                os.unlink(self.dup_log_path)
                logger.info(f"SBS | DUP_TEMP_CLEANED | {self.dup_log_path}")
            except Exception as e:
                pass
    
    def _auto_cleanup_dup_log(self):
        """Check if temp file is > 1 hour old and clean it up"""
        if self.dup_log_path and os.path.exists(self.dup_log_path):
            try:
                mtime = os.path.getmtime(self.dup_log_path)
                age = time.time() - mtime
                if age > 3600:
                    os.unlink(self.dup_log_path)
                    self._init_dup_temp_file()
            except Exception as e:
                pass

    # =====================================================
    # UTILS
    # =====================================================
    
    def build_category_url(self, base_url, page):
        return base_url if page == 1 else f"{base_url}?page={page}"

    def safe_get(self, url):
        """Use SmartRequester instead of get()"""
        try:
            return self.requester.get_html(url)
        except Exception as e:
            error_logger.error(f"{self.name} | HTTP_ERROR | {url} | {e}")
            self._add_error(f"HTTP error: {str(e)[:50]}")
            return None

    # =====================================================
    # DUPLICATION (THREAD SAFE)
    # =====================================================
    
    def handle_duplicate(self, product_id, source, seen_products):
        """Handle duplicate detection - logs only to temp file"""
        with self.lock:
            self._auto_cleanup_dup_log()
            
            if product_id in seen_products:
                self.dup_total += 1
                
                if source not in self.dup_counts:
                    self.dup_counts[source] = 0
                self.dup_counts[source] += 1
                
                first_seen = seen_products[product_id]
                dup_msg = (
                    f"DUPLICATE | id={product_id} | "
                    f"first_seen={first_seen} | duplicate_in={source}"
                )
                self._write_dup_log(dup_msg)
                dup_logger.debug(f"{self.name} | {dup_msg}")
                
                seen_products[product_id].append(source)
                return True

            seen_products[product_id] = [source]
            return False

    def _get_dup_stats(self) -> str:
        """Get duplicate statistics for summary"""
        if self.dup_total == 0:
            return "No duplicates"
        top_sources = sorted(self.dup_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        sources_str = ", ".join([f"{s}: {c}" for s, c in top_sources])
        return f"Total: {self.dup_total} | Top sources: {sources_str}"

    # =====================================================
    # PRODUCT PARSER
    # =====================================================
    
    def parse_product(self, url, product_id):
        html = self.safe_get(url)
        logger.warning(f"html : {html}")
        if not html:
            return None

        try:
            return parse_sbs_product_page(html, url, self.name, product_id)
        except Exception as e:
            error_logger.error(f"{self.name} | PARSE_PRODUCT_ERROR | {url} | {e}")
            self._add_error(f"Parse error {product_id}: {str(e)[:50]}")
            return None

    # =====================================================
    # WORKER
    # =====================================================
    
    def scrape_product_worker(self, product, seen_products, source):
        try:
            product_id = product.get("main_product_id")
            product_url = product.get("product_link")

            if not product_id or not product_url:
                return None

            if self.handle_duplicate(product_id, source, seen_products):
                return None

            # Use inherited _product_exists from BaseSite
            if self._product_exists(product_id):
                with self.lock:
                    self.stats["skipped_duplicates"] += 1
                logger.debug(f"{self.name} | SKIP_EXISTING | {product_id}")
                return None

            details = self.parse_product(product_url, product_id)
            logger.warning(f"\n details : {details}")

            if not details:
                logger.warning(f"{self.name} | PARSE_FAILED | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"Parse failed: {product_id}")
                return None

            # Validate - allow price 0 for SBS
            if not details.get("name"):
                logger.warning(f"{self.name} | DETAILS_NO_NAME | {product_id}")
                logger.warning(f"name of the product : {details.get("name")} | url : {product_url}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            price = details.get("price")
            if price is None:
                logger.warning(f"{self.name} | DETAILS_NO_PRICE | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None

            details["is_new"] = True
            logger.info(f"{self.name} | NEW_PRODUCT | {product_id} | price={price}")

            # USE PARENT'S _save_new_product (which uses batching)
            if super()._save_new_product(details, product_id):
                with self.lock:
                    self.stats["new_products"] += 1
                batch_size = self.get_pending_batch_size()
                if batch_size > 0 and batch_size % 10 == 0:
                    logger.info(f"{self.name} | BATCH_PROGRESS | {batch_size} products queued")
            else:
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"Save failed: {product_id}")

            write_product_result(details, self.name)

            return details

        except Exception as e:
            error_logger.error(
                f"{self.name} | WORKER_ERROR | {product.get('main_product_id')} | {e}"
            )
            with self.lock:
                self.stats["errors"] += 1
            self._add_error(f"Worker error: {str(e)[:50]}")
            return None

    # =====================================================
    # CATEGORY SCRAPER
    # =====================================================

    def scrape_category(self, category_name, category_url, seen_products):
        results = []
        
        logger.info(f"{self.name} | CATEGORY_START | {category_name}")
        
        self.stats['current_category'] = category_name
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
        
        first_page = self.safe_get(category_url)
        if not first_page:
            error_logger.error(f"{self.name} | CATEGORY_FAIL | {category_name}")
            self._add_error(f"Category failed: {category_name}")
            return results
        
        total_pages = self.get_category_page_count(first_page)
        logger.info(f"{self.name} | CATEGORY_INFO | {category_name} | pages={total_pages}")
        
        for page in range(1, total_pages + 1):
            url = self.build_category_url(category_url, page)
            
            self.stats['current_product'] = f"Page {page}/{total_pages}"
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
            
            html = self.safe_get(url)
            if not html:
                error_logger.warning(f"{self.name} | PAGE_FAIL | {category_name} | page={page}")
                self._add_error(f"Page {page} failed in {category_name}")
                continue
            
            try:
                products = parse_products(html, self.name)
            except Exception as e:
                error_logger.error(f"{self.name} | PARSE_CATEGORY_ERROR | page={page} | {e}")
                self._add_error(f"Parse error {category_name}: {str(e)[:50]}")
                continue
            
            if not products:
                continue
            
            valid_products = []
            for p in products:
                if p and isinstance(p, dict):
                    if p.get("main_product_id") and p.get("product_link"):
                        valid_products.append(p)
            
            if not valid_products:
                continue
            
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = [
                    executor.submit(self.scrape_product_worker, p, seen_products, category_name)
                    for p in valid_products
                ]
                
                for future in as_completed(futures):
                    try:
                        result = future.result(timeout=60)
                        if result:
                            results.append(result)
                    except Exception as e:
                        error_logger.error(f"{self.name} | WORKER_ERROR | {category_name} | {e}")
                        with self.lock:
                            self.stats["errors"] += 1
                        self._add_error(f"Worker error {category_name}: {str(e)[:50]}")
            
            batch_size = self.get_pending_batch_size()
            if batch_size > 0:
                logger.info(f"{self.name} | PAGE_DONE | {category_name} | page={page} | batch={batch_size}")
        
        logger.info(f"{self.name} | CATEGORY_DONE | {category_name} | collected={len(results)}")
        return results

    # =====================================================
    # BRAND SCRAPER
    # =====================================================
    
    def scrape_brands(self, seen_products):
        logger.info(f"{self.name} | BRAND_START")
        results = []

        for brand in SEARCH_BRANDS:
            try:
                search_result = self.search(brand)
                products = search_result.get("products", [])
                
                if not products:
                    continue
                
                valid_products = []
                for p in products:
                    if p and isinstance(p, dict):
                        if p.get("main_product_id") and p.get("product_link"):
                            valid_products.append(p)
                
                if not valid_products:
                    continue

                with ThreadPoolExecutor(max_workers=self.workers) as executor:
                    futures = [
                        executor.submit(
                            self.scrape_product_worker,
                            p,
                            seen_products,
                            brand
                        )
                        for p in valid_products
                    ]

                    for future in as_completed(futures):
                        try:
                            result = future.result(timeout=60)
                            if result:
                                results.append(result)
                        except Exception as e:
                            error_logger.error(
                                f"{self.name} | BRAND_WORKER_ERROR | {brand} | {e}"
                            )
                            self._add_error(f"Brand worker {brand}: {str(e)[:50]}")
            except Exception as e:
                error_logger.error(f"{self.name} | BRAND_ERROR | {brand} | {e}")
                self._add_error(f"Brand {brand} failed: {str(e)[:50]}")

        logger.info(f"{self.name} | BRAND_DONE | collected={len(results)}")
        return results

    # =====================================================
    # SEARCH - Using SmartRequester
    # =====================================================
    
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
                self._add_error(f"Search parse error: {str(e)[:50]}")
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

    # =====================================================
    # MAIN SCRAPER
    # =====================================================

    def scrape_site(self):
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

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

        results = []
        seen_products = {}
        self.dup_counts = {}
        self.dup_total = 0

        # Initialize connections
        try:
            if not self._init_connections():
                error_logger.error(f"{self.name} | CONNECTION_FAILED")
                log_global(f"{self.name} | CONNECTION_FAILED")
                self._cleanup_dup_log()
                return {
                    "site": self.name,
                    "total_products": 0,
                    "products": [],
                    "stats": self.stats
                }
            
            status = self.get_connection_status()
            logger.info(f"{self.name} | CONNECTION_STATUS | Redis: {status['redis']} | DB: {status['db']}")
            
        except Exception as e:
            error_logger.error(f"{self.name} | INIT_CONNECTIONS_FAILED | {e}")
            log_global(f"{self.name} | INIT_CONNECTIONS_FAILED | {e}")
            self._cleanup_dup_log()
            return {
                "site": self.name,
                "total_products": 0,
                "products": [],
                "stats": self.stats
            }

        try:
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
            
            for i, (name, url) in enumerate(CATEGORIES.items(), start=1):
                self.stats['current_category'] = name
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                try:
                    category_results = self.scrape_category(name, url, seen_products)
                    results.extend(category_results)
                except Exception as e:
                    error_msg = f"Category '{name}' failed: {str(e)[:50]}"
                    error_logger.error(f"{self.name} | CATEGORY_ERROR | {name} | {e}")
                    self._add_error(error_msg)
                    continue
                
                self.stats['completed_categories'] = i
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                batch_size = self.get_pending_batch_size()
                logger.info(f"{self.name} | CATEGORY_COMPLETE | {name} | new={len(category_results)} | batch={batch_size}")

            logger.info(f"{self.name} | BRAND_FALLBACK_START")
            try:
                brand_results = self.scrape_brands(seen_products)
                results.extend(brand_results)
            except Exception as e:
                error_msg = f"Brands fallback failed: {str(e)[:50]}"
                error_logger.error(f"{self.name} | BRANDS_ERROR | {e}")
                self._add_error(error_msg)

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
            error_logger.error(f"{self.name} | SCRAPE_SITE_ERROR | {e}")
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
                    error_logger.error(f"{self.name} | FLUSH_ERROR | {e}")
                    self._add_error(error_msg)
            
            # Log final summary
            self.log_final_summary(results)
            
            self._close_connections()
            
            dup_summary = self._get_dup_stats()
            logger.info(f"{self.name} | DUPLICATE_SUMMARY | {dup_summary}")
            
            if self.dup_log_path:
                try:
                    with open(self.dup_log_path, 'a', encoding='utf-8') as f:
                        f.write(f"\n{'='*80}\n")
                        f.write(f"DUPLICATE SUMMARY: {dup_summary}\n")
                        f.write(f"{'='*80}\n")
                except:
                    pass
            self._cleanup_dup_log()

        total = len(results)
        self.stats["total_products_found"] = total
        self.stats['completed_categories'] = total_categories
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)

        logger.info(f"{self.name} | SCRAPE_DONE | total={total}")
        log_global(f"{self.name} | SCRAPE_DONE | total={total}")

        print("\n" + "="*60)
        print(f" SBS SCRAPING COMPLETE")
        print("="*60)
        print(f" TOTAL PRODUCTS: {total}")
        print(f" NEW PRODUCTS: {self.stats['new_products']}")
        print(f" SKIPPED (EXISTING): {self.stats['skipped_duplicates']}")
        print(f" REDIS SAVED: {self.stats['redis_saved']}")
        print(f" POSTGRES SAVED: {self.stats['postgres_saved']}")
        print(f" ERRORS: {self.stats['errors']}")
        print(f" DUPLICATES: {self.dup_total}")
        print("="*60 + "\n")

        return {
            "site": self.name,
            "total_products": total,
            "products": results,
            "stats": self.stats
        }

    # =====================================================
    # CATEGORY DISCOVERY
    # =====================================================
    
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

    # =====================================================
    # PAGINATION DETECTION
    # =====================================================
    
    def get_category_page_count(self, html):
        soup = BeautifulSoup(html, "lxml")
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


def create_sbs_site() -> SBSSite:
    return SBSSite()