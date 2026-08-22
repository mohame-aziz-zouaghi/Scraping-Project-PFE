# sites/spacenet.py
"""
Spacenet Site Scraper - Optimized with Redis caching, batching, and comprehensive logging
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import threading
import time
from typing import Dict, List, Optional, Any, Set, Union

from config.brands import SEARCH_BRANDS
from config.categoriesSpacenet import CATEGORIES
from core.logger import setup_logger, log_global, write_product_result
from parsers.spacenet.spacenet_category_parser import parse_spacenet_categories
from parsers.spacenet.spacenet_product_parser import parse_spacenet_product_page
from parsers.spacenet.spacenet_search_parser import parse_spacenet_products
from core.performance import PerformanceTracker
from sites.base_site import BaseSite


logger = setup_logger("spacenet", "scraper")
dup_logger = setup_logger("spacenet", "duplication")


class SpacenetSite(BaseSite):
    """Spacenet scraper - ONLY adds NEW products, skips existing ones"""

    name = "SPACENET"
    BASE_URL = "https://spacenet.tn/"
    SEARCH_URL = "https://spacenet.tn/module/ambjolisearch/jolisearch?orderby=position&orderway=desc&search_query={query}&submit_search="

    def __init__(self):
        super().__init__()
        
        self.name = "SPACENET"
        self.scrape_interval = 3600
        self.performance = PerformanceTracker()
        
        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        
        # Batch size for this site
        self.batch_size = 50
        
        # Performance tracking
        self.start_time = None
        self.products_processed = 0
        self.batch_count = 0
        
        # =====================================================
        # CONFIGURE SMART REQUESTER FOR SPACENET
        # =====================================================
        self.set_requester_config(
            requests_per_minute=20,
            max_retries=4,
            timeout=45,
            use_session=True,
            use_referer=True,
            delay_between_requests=(0.5, 2.0),
            use_basic_headers = True
        )

    def get_requester_config(self) -> Dict:
        """Override to provide Spacenet-specific requester config"""
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
    # DUPLICATION HANDLER
    # =====================================================
    
    def handle_duplicate(self, product_id, source, seen_products):
        """Handle duplicate product detection with dict safety"""
        with self.lock:
            if isinstance(seen_products, set):
                logger.error(f"{self.name} | SEEN_IS_SET | product_id={product_id} | converting set to dict")
                new_dict = {}
                for item in seen_products:
                    if isinstance(item, str):
                        new_dict[item] = [source]
                    elif isinstance(item, dict):
                        new_dict.update(item)
                seen_products.clear()
                for key, value in new_dict.items():
                    seen_products[key] = value
                logger.info(f"{self.name} | SEEN_CONVERTED | product_id={product_id} | now dict with {len(seen_products)} items")
            
            if product_id in seen_products:
                first_sources = seen_products[product_id]
                dup_logger.warning(
                    f"{self.name} | DUPLICATE | id={product_id} | "
                    f"first_seen={first_sources} | duplicate_in={source}"
                )
                seen_products[product_id].append(source)
                return True
            else:
                if product_id != "#":
                    seen_products[product_id] = [source]
                    return False
                else:
                    return False

    # =====================================================
    # SEARCH - Using SmartRequester
    # =====================================================
    
    def search(self, query):
        logger.info(f"{self.name} | SEARCH_START | {query}")
        results = []
        seen_ids = set()
        page = 1

        while True:
            url = self.SEARCH_URL.format(query=query)
            if page > 1:
                url += f"&page={page}"
            
            # Use SmartRequester instead of get()
            html = self.requester.get_html(url)

            if not html:
                break

            products = parse_spacenet_products(html, self.name)
            if not products:
                break

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

    # =====================================================
    # PRODUCT PARSER - Using SmartRequester
    # =====================================================
    
    def parse_product(self, url, product_id=None):
        logger.info(f"{self.name} | PRODUCT_PARSE_START | {url}")

        # Use SmartRequester instead of get()
        html = self.requester.get_html(url)

        if not html:
            logger.warning(f"{self.name} | PRODUCT_PARSE_FAILED | {url}")
            return None

        try:
            product = parse_spacenet_product_page(html, url, self.name, product_id)
            
            if isinstance(product, set):
                logger.error(f"{self.name} | PARSE_RETURNED_SET | {url}")
                try:
                    for item in product:
                        if isinstance(item, dict):
                            logger.info(f"{self.name} | PARSE_SET_CONVERTED | {url}")
                            return item
                except:
                    pass
                return None
            
            return product
        except Exception as e:
            logger.error(f"{self.name} | PRODUCT_PARSE_ERROR | {url} | {e}")
            self._add_error(f"Parse error {product_id}: {str(e)[:50]}")
            return None

    # =====================================================
    # CATEGORY PAGE COUNT
    # =====================================================
    
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

    # =====================================================
    # PRODUCT WORKER
    # =====================================================
    
    def scrape_product_worker(self, product, seen_products, source):
        """Process a single product for Spacenet - returns product dict or None"""
        worker_pid = "UNKNOWN"
        start_time = time.time()
        
        try:
            if product is None:
                logger.debug(f"{self.name} | SKIP_NONE")
                return None
            
            if isinstance(product, set):
                logger.warning(f"{self.name} | PRODUCT_IS_SET | converting...")
                try:
                    for item in product:
                        if isinstance(item, dict):
                            product = item
                            break
                except:
                    pass
                if isinstance(product, set):
                    return None
            
            if not isinstance(product, dict):
                logger.warning(f"{self.name} | SKIP_INVALID_TYPE | {type(product)}")
                return None
            
            pid = product.get("main_product_id")
            url = product.get("product_link")

            if not pid or not url:
                logger.debug(f"{self.name} | SKIP_NO_ID")
                return None

            pid = str(pid)
            worker_pid = pid

            # Use inherited _product_exists from BaseSite
            if self._product_exists(pid):
                with self.lock:
                    self.stats["skipped_duplicates"] += 1
                return None

            if isinstance(seen_products, set):
                logger.warning(f"{self.name} | SEEN_IS_SET_IN_WORKER | {pid} | converting to dict")
                new_dict = {}
                for item in seen_products:
                    if isinstance(item, str):
                        new_dict[item] = []
                    elif isinstance(item, dict):
                        new_dict.update(item)
                seen_products.clear()
                for key, value in new_dict.items():
                    seen_products[key] = value

            if self.handle_duplicate(pid, source, seen_products):
                logger.debug(f"{self.name} | DUPLICATE_SESSION | {pid}")
                return None

            details = self.parse_product(url, pid)

            if details is None:
                logger.debug(f"{self.name} | PARSE_FAILED | {pid}")
                self._add_error(f"Parse returned None: {pid}")
                return None
            
            if isinstance(details, set):
                try:
                    for item in details:
                        if isinstance(item, dict):
                            details = item
                            break
                except:
                    pass
                if isinstance(details, set):
                    return None
            
            if not isinstance(details, dict):
                logger.warning(f"{self.name} | PARSE_INVALID | {pid} | {type(details)}")
                return None

            if not details.get("name"):
                logger.debug(f"{self.name} | NO_NAME | {pid}")
                return None
            
            if not details.get("price") or details.get("price") <= 0:
                logger.debug(f"{self.name} | NO_PRICE | {pid} | {details.get('price')}")
                return None

            details['main_product_id'] = pid
            
            # USE PARENT'S _save_new_product (which uses batching)
            if super()._save_new_product(details, pid):
                with self.lock:
                    self.stats["new_products"] += 1
                write_product_result(details, self.name)
                self.performance.add_product()
                
                elapsed = round(time.time() - start_time, 3)
                logger.info(f"{self.name} | WORKER_SUCCESS | {pid} | {elapsed}s")
                return details
            else:
                self._add_error(f"Save failed: {pid}")

            return None

        except Exception as e:
            elapsed = round(time.time() - start_time, 3)
            logger.error(f"{self.name} | WORKER_ERROR | {worker_pid} | {e} | {elapsed}s")
            with self.lock:
                self.stats["errors"] += 1
            self._add_error(f"Worker error {worker_pid}: {str(e)[:50]}")
            return None

    # =====================================================
    # CATEGORY SCRAPER
    # =====================================================
    
    def scrape_category(self, category_name, category_url, seen_products):
        logger.info(f"{self.name} | CATEGORY_START | {category_name}")
        log_global(f"{self.name} | CATEGORY_START | {category_name}")
        
        if isinstance(seen_products, set):
            logger.error(f"{self.name} | SEEN_IS_SET_AT_ENTRY | {category_name} | converting to dict")
            new_dict = {}
            for item in seen_products:
                if isinstance(item, str):
                    new_dict[item] = []
                elif isinstance(item, dict):
                    new_dict.update(item)
            seen_products.clear()
            for key, value in new_dict.items():
                seen_products[key] = value
        
        results = []
        category_start = time.time()

        # Use SmartRequester instead of get()
        first_page = self.requester.get_html(category_url)
        if not first_page:
            logger.warning(f"{self.name} | CATEGORY_NO_FIRST_PAGE | {category_name}")
            self._add_error(f"Category no first page: {category_name}")
            return results

        total_pages = self.get_category_page_count(first_page)
        logger.info(f"{self.name} | CATEGORY_TOTAL_PAGES | {category_name} | {total_pages}")
        
        self.stats['current_category'] = category_name
        self.stats['total_pages'] = total_pages
        self.stats['current_page'] = 0
        self.update_progress(category_name, 0, total_pages)

        for page in range(1, total_pages + 1):
            page_start = time.time()
            url = f"{category_url}?page={page}"
            
            # Use SmartRequester instead of get()
            html = self.requester.get_html(url)

            if not html:
                logger.warning(f"{self.name} | PAGE_EMPTY | {category_name} | {page}/{total_pages}")
                self._add_error(f"Page {page} empty in {category_name}")
                break

            self.stats['current_page'] = page
            self.update_progress(category_name, page, total_pages)

            try:
                products = parse_spacenet_products(html, self.name)
            except Exception as e:
                logger.error(f"{self.name} | PAGE_PARSE_ERROR | {category_name} | {page} | {e}")
                self._add_error(f"Page parse error {category_name}: {str(e)[:50]}")
                continue

            if not products:
                logger.warning(f"{self.name} | PAGE_NO_PRODUCTS | {category_name} | {page}")
                break

            valid_products = []
            for p in products:
                if p is None:
                    continue
                if isinstance(p, set):
                    continue
                if not isinstance(p, dict):
                    continue
                if not p.get("main_product_id") or not p.get("product_link"):
                    continue
                valid_products.append(p)
            
            if not valid_products:
                logger.debug(f"{self.name} | PAGE_NO_VALID | {category_name} | {page}")
                continue

            self.stats['current_product'] = f"Products: {len(valid_products)}"
            self.update_progress(category_name, page, total_pages)

            with ThreadPoolExecutor(max_workers=40) as executor:
                futures = [
                    executor.submit(
                        self.scrape_product_worker,
                        p,
                        seen_products,
                        category_name
                    )
                    for p in valid_products
                ]

                for future in as_completed(futures):
                    try:
                        result = future.result(timeout=60)
                        if result:
                            results.append(result)
                    except Exception as e:
                        logger.error(f"{self.name} | WORKER_ERROR | {e}")
                        with self.lock:
                            self.stats["errors"] += 1
                        self._add_error(f"Worker error: {str(e)[:50]}")

            page_elapsed = round(time.time() - page_start, 2)
            logger.debug(f"{self.name} | PAGE_COMPLETE | {category_name} | {page}/{total_pages} | {page_elapsed}s")
            
            batch_size = self.get_pending_batch_size()
            if batch_size > 0:
                logger.info(f"{self.name} | PAGE_BATCH | {category_name} | page={page} | batch={batch_size}")

        self.stats['completed_categories'] += 1
        self.update_progress(category_name, total_pages, total_pages)

        category_elapsed = round(time.time() - category_start, 2)
        logger.info(f"{self.name} | CATEGORY_DONE | {category_name} | {len(results)} products | {category_elapsed}s")
        return results

    # =====================================================
    # BRAND SCRAPER
    # =====================================================
    
    def scrape_brands(self, seen_products):
        if isinstance(seen_products, set):
            logger.error(f"{self.name} | SEEN_IS_SET_IN_BRANDS | converting to dict")
            new_dict = {}
            for item in seen_products:
                if isinstance(item, str):
                    new_dict[item] = []
                elif isinstance(item, dict):
                    new_dict.update(item)
            seen_products.clear()
            for key, value in new_dict.items():
                seen_products[key] = value
        
        logger.info(f"{self.name} | BRAND_START")
        log_global(f"{self.name} | BRAND_START")
        results = []
        brand_start = time.time()

        self.stats['current_category'] = "Brands (Fallback)"
        self.update_progress("Brands (Fallback)")

        for brand in SEARCH_BRANDS:
            brand_loop_start = time.time()
            logger.info(f"{self.name} | BRAND_PROCESS | {brand}")
            
            self.stats['current_product'] = f"Brand: {brand}"
            self.update_progress("Brands (Fallback)")
            
            try:
                search_result = self.search(brand)
                products = search_result.get("products", [])
            except Exception as e:
                logger.error(f"{self.name} | BRAND_SEARCH_ERROR | {brand} | {e}")
                self._add_error(f"Brand search {brand}: {str(e)[:50]}")
                continue
                
            if not products:
                logger.debug(f"{self.name} | BRAND_NO_PRODUCTS | {brand}")
                continue

            valid_products = []
            for p in products:
                if p is None:
                    continue
                if isinstance(p, set):
                    continue
                if not isinstance(p, dict):
                    continue
                if not p.get("main_product_id") or not p.get("product_link"):
                    continue
                valid_products.append(p)
            
            if not valid_products:
                continue

            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [
                    executor.submit(self.scrape_product_worker, p, seen_products, brand)
                    for p in valid_products
                ]
                for future in as_completed(futures):
                    try:
                        result = future.result(timeout=60)
                        if result:
                            results.append(result)
                            self.performance.add_product()
                    except Exception as e:
                        logger.error(f"{self.name} | BRAND_WORKER_ERROR | {brand} | {e}")
                        self._add_error(f"Brand worker {brand}: {str(e)[:50]}")

            brand_elapsed = round(time.time() - brand_loop_start, 2)
            logger.info(f"{self.name} | BRAND_DONE | {brand} | {len(results)} collected | {brand_elapsed}s")

        brand_elapsed = round(time.time() - brand_start, 2)
        self.update_progress("Brands (Fallback)")
        logger.info(f"{self.name} | BRAND_COMPLETE | {len(results)} total | {brand_elapsed}s")
        return results

    # =====================================================
    # FULL SITE SCRAPER
    # =====================================================
    
    def scrape_site(self):
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")
        
        self.start_time = time.time()
        results = []
        
        seen_products: Dict[str, List[str]] = {}

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
                return {"site": self.name, "total_products": 0, "products": [], "stats": self.stats}
        except Exception as e:
            logger.error(f"{self.name} | INIT_ERROR | {e}")
            return {"site": self.name, "total_products": 0, "products": [], "stats": self.stats}
        
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
            
            logger.info(f"{self.name} | SCRAPING | {total_categories} categories")
            
            self.update_progress()
            
            for i, (name, url) in enumerate(CATEGORIES.items(), start=1):
                logger.info(f"{self.name} | CATEGORY_PROGRESS | {i}/{total_categories} | {name}")
                
                self.stats['current_category'] = name
                self.update_progress(name)
                
                try:
                    res = self.scrape_category(name, url, seen_products)
                    results.extend(res)
                except Exception as e:
                    error_msg = f"Category '{name}' failed: {str(e)[:50]}"
                    logger.error(f"{self.name} | CATEGORY_ERROR | {name} | {e}")
                    self._add_error(error_msg)
                    continue
                
                self.stats['completed_categories'] = i
                self.update_progress(name)
                
                batch_size = self.get_pending_batch_size()
                logger.info(f"Progress: {i}/{total_categories} categories completed | batch={batch_size}")
            
            logger.info(f"{self.name} | BRAND_FALLBACK_START")
            try:
                brand_results = self.scrape_brands(seen_products)
                results.extend(brand_results)
            except Exception as e:
                error_msg = f"Brands fallback failed: {str(e)[:50]}"
                logger.error(f"{self.name} | BRANDS_ERROR | {e}")
                self._add_error(error_msg)
            
            self.stats['completed_categories'] = total_categories
            self.update_progress()
            
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
        
        total_elapsed = round(time.time() - self.start_time, 2)
        logger.info(f"{self.name} | SCRAPE_DONE | total_products={len(results)} | elapsed={total_elapsed}s")
        log_global(f"{self.name} | SCRAPE_DONE | total_products={len(results)}")
        
        print("\n" + "="*60)
        print(f" {self.name} SCRAPING COMPLETE")
        print("="*60)
        print(f" TOTAL PRODUCTS: {len(results)}")
        print(f" NEW PRODUCTS: {self.stats['new_products']}")
        print(f" SKIPPED (EXISTING): {self.stats['skipped_duplicates']}")
        print(f" REDIS SAVED: {self.stats['redis_saved']}")
        print(f" POSTGRES SAVED: {self.stats['postgres_saved']}")
        print(f" ERRORS: {self.stats['errors']}")
        print(f" ELAPSED: {total_elapsed}s")
        print("="*60 + "\n")
        
        return {
            "site": self.name,
            "total_products": len(results),
            "products": results,
            "stats": self.stats,
            "elapsed": total_elapsed
        }

    def extract_categories(self):
        logger.info(f"{self.name} | CATEGORY_DISCOVERY_START")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_START")

        # Use SmartRequester instead of get()
        html = self.requester.get_html(self.BASE_URL)
        if not html:
            logger.error(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            log_global(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            return

        categories = parse_spacenet_categories(html)
        if not categories:
            logger.warning(f"{self.name} | NO_CATEGORIES_FOUND")
            return

        import os
        os.makedirs("config", exist_ok=True)
        file_path = "config/categoriesSpacenet.py"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("CATEGORIES = {\n")
            for name, url in categories.items():
                f.write(f'    "{name}": "{url}",\n')
            f.write("}\n")

        logger.info(f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}")


def create_spacenet_site() -> SpacenetSite:
    return SpacenetSite()