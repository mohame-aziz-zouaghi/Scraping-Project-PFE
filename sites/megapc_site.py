# sites/megapc.py
import random
import time
import threading
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any, Set
from urllib.parse import quote
from contextlib import contextmanager

import requests

from core.utils.debug_logger import log_scrape_stats, end_session

from core.Web.smart_requester import SmartRequester
from core.utils.Dclean_html import clean_html
from core.utils.build_id_manager import BuildIDManager, get_build_id_manager
from core.utils.clean_parse_specs import parse_specs
from core.logger import setup_logger, write_product_result, log_global
from core.Web.product_schema import create_product_schema
from core.performance import PerformanceTracker

from parsers.megapc.megapc_products_parser import parse_megapc_products
from parsers.megapc.megapc_product_details_parser import parse_megapc_product_details

from config.categoriesMegaPC import CATEGORIES
from sites.base_site import BaseSite


logger = setup_logger("megapc", "scraper")
dup_logger = setup_logger("megapc", "duplication")


class MegaPCSite(BaseSite):
    """MegaPC scraper - ONLY adds NEW products, skips existing ones"""
    
    def __init__(self):
        super().__init__()
        
        self.name = "MEGAPC"
        self.scrape_interval = 1800
        self.performance = PerformanceTracker()
        
        # API endpoints
        self.api_products = "https://apiclt.gi-ga.tech/produit/byPaginationNew"
        self.base_next_url = "https://megapc.tn/_next/data"
        
        # Build ID Manager
        self.build_manager = get_build_id_manager()
        self.current_build_id = None
        self.base_next = None
        self._update_base_next_url()
        
        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        
        # Batch size for this site
        self.batch_size = 50
        
        # Test mode
        self.test_mode = False
        self.test_limit = 5


    def get_requester_config(self) -> Dict:
        return {
            "requests_per_minute": 100,
            "max_retries": 3,
            "timeout": 30,
            "use_session": True,
            "use_referer": True,
            "delay_between_requests": (0.5, 1.5),
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
    # BUILD ID MANAGEMENT
    # =====================================================
    
    def _update_base_next_url(self) -> bool:
        """Update the base Next.js URL with the current build ID"""
        build_id = self.build_manager.get_build_id(force_refresh=False)
        
        if build_id:
            self.current_build_id = build_id
            self.base_next = f"{self.base_next_url}/{build_id}"
            logger.debug(f"Base Next URL: {self.base_next}")
            return True
        else:
            logger.error("Failed to get build ID")
            return False

    def _validate_build_id(self, build_id: str) -> bool:
        """Validate a build ID by testing with a known endpoint"""
        try:
            test_url = f"{self.base_next_url}/{build_id}/shop/product/"
            response = requests.head(test_url, timeout=10, allow_redirects=True)
            
            if response.status_code in [200, 404]:
                logger.debug(f"Build ID {build_id} validated (status={response.status_code})")
                return True
            
            logger.warning(f"Build ID {build_id} validation failed (status={response.status_code})")
            return False
            
        except requests.RequestException as e:
            logger.warning(f"Build ID validation error: {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected validation error: {e}")
            return False

    # =====================================================
    # API PRODUCTS FETCH
    # =====================================================
    
    def get_products(self, category: str, subcategory: str) -> List[Dict]:
        """Fetch products from MegaPC API for given category"""
        
        payload = {
            "recordByPage": 1000,
            "pageNumber": "1",
            "categorie": {"titre": category},
            "filscateg": {"titre": subcategory},
            "page": 1,
            "price": {"$gte": 0, "$lte": 999999},
            "query": None,
            "brand": [],
            "valeurAttribute1": []
        }
        
        try:
            response = requests.post(self.api_products, json=payload, timeout=30)
            
            if response.status_code != 200:
                logger.warning(f"{self.name} | API_FAIL | {category} > {subcategory} | status={response.status_code}")
                self._add_error(f"API failed {category}: {response.status_code}")
                return []
            
            try:
                data = response.json()
            except Exception as e:
                logger.error(f"{self.name} | INVALID_JSON | {category} > {subcategory} | {e}")
                self._add_error(f"Invalid JSON: {str(e)[:50]}")
                return []
            
            if not isinstance(data, dict):
                logger.error(f"{self.name} | UNEXPECTED_RESPONSE | type={type(data)}")
                return []
            
            products = parse_megapc_products(data)
            
            if self.test_mode and len(products) > self.test_limit:
                products = products[:self.test_limit]
                logger.info(f"{self.name} | TEST_MODE | limiting to {self.test_limit} products")
            
            logger.info(f"{self.name} | FOUND_PRODUCTS | {category} > {subcategory} | count={len(products)}")
            return products
            
        except requests.exceptions.Timeout:
            logger.error(f"{self.name} | API_TIMEOUT | {category} > {subcategory}")
            self._add_error(f"API timeout: {category}")
            return []
        except Exception as e:
            logger.error(f"{self.name} | API_ERROR | {category} > {subcategory} | {e}")
            self._add_error(f"API error: {str(e)[:50]}")
            return []
    
    # =====================================================
    # PRODUCT DETAILS FETCH
    # =====================================================
    
    def get_product_details(self, category: str, subcategory: str, slug: str) -> Dict:
        """Fetch product details from MegaPC Next.js data endpoint"""
        
        if not slug:
            return {}
        
        if not self.base_next or not self.current_build_id:
            if not self._update_base_next_url():
                logger.error(f"Failed to get build ID for product details: {slug}")
                return {}
        
        cat_param = quote(category.replace(" ", "+"))
        sub_param = quote(subcategory.replace(" ", "+"))
        cat_path = quote(category.replace(" ", "%20"))
        sub_path = quote(subcategory.replace(" ", "%20"))
        
        url = f"{self.base_next}/shop/product/{cat_path}/{sub_path}/{slug}.json?lien={cat_param}&lien={sub_param}&lien={slug}"
        
        max_retries = 2
        max_server_retries = 3
        
        for attempt in range(max_retries):
            try:
                json_data, metadata = self.requester.get_json(
                    url=url,
                    use_referer=True,
                    return_metadata=True
                )
                
                if json_data is not None:
                    self.build_manager.record_success()
                    logger.debug(f"Successfully fetched details for {slug}")
                    return parse_megapc_product_details(json_data)
                
                status_code = metadata.get('status_code', 0)
                
                if status_code == 404:
                    failure_count = self.build_manager.record_failure()
                    logger.warning(f"Details 404 for {slug} (product removed) - attempt {attempt + 1}/{max_retries}")
                    
                    if attempt < max_retries - 1:
                        wait_time = random.uniform(1, 2)
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"Product {slug} confirmed removed (404 after {max_retries} attempts)")
                        return {}
                
                elif status_code >= 500:
                    self.build_manager.record_failure()
                    logger.warning(f"Details {status_code} for {slug} (attempt {attempt + 1}/{max_server_retries})")
                    
                    if attempt < max_server_retries - 1:
                        wait_time = (2 ** attempt) + random.uniform(0, 1)
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"Server error {status_code} for {slug} after {max_server_retries} attempts")
                        self._add_error(f"Server error {status_code}: {slug}")
                        return {}
                
                elif status_code == 403:
                    self.build_manager.record_failure()
                    logger.warning(f"Details 403 (blocked) for {slug} (attempt {attempt + 1}/{max_retries})")
                    
                    if attempt < max_retries - 1:
                        wait_time = 5 * (attempt + 1) + random.uniform(0, 2)
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"Product {slug} blocked (403) after {max_retries} attempts")
                        self._add_error(f"Blocked (403): {slug}")
                        return {}
                
                else:
                    self.build_manager.record_failure()
                    logger.warning(f"Details failed for {slug}: status={status_code} (attempt {attempt + 1}/{max_retries})")
                    
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"Product {slug} failed after {max_retries} attempts (status={status_code})")
                        return {}
                        
            except requests.exceptions.Timeout:
                self.build_manager.record_failure()
                logger.warning(f"Details timeout for {slug} (attempt {attempt + 1}/{max_retries})")
                
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"Product {slug} timeout after {max_retries} attempts")
                    return {}
                    
            except Exception as e:
                self.build_manager.record_failure()
                logger.error(f"Details error for {slug} (attempt {attempt + 1}/{max_retries}): {e}")
                self._add_error(f"Details error {slug}: {str(e)[:50]}")
                
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    continue
                else:
                    return {}
        
        return {}
    
    # =====================================================
    # PRODUCT BUILDING
    # =====================================================
    
    def build_product(self, base: Dict, details: Dict, url: str) -> Optional[Dict]:
        """Build complete product object from base and details"""
        
        product_id = base.get("id")
        if not product_id:
            return None
        
        if not details or not details.get("sku"):
            logger.warning(f"Missing details for product {product_id}, skipping...")
            return None
        
        product = create_product_schema(self.name, url, product_id)
        
        product["name"] = base.get("name")
        product["brand"] = base.get("brand")
        product["category"] = base.get("category")
        
        price = base.get("price") or 0
        promo = base.get("promo_price") or 0
        
        product["price"] = float(price)
        
        if promo and promo < price:
            product["discount_price"] = float(promo)
            product["discount_amount"] = round(float(price - promo), 2)
        else:
            product["discount_price"] = 0
            product["discount_amount"] = 0
        
        stock = base.get("stock", 0)
        product["availability"] = "InStock" if stock > 0 else "OutOfStock"
        
        product["special_id"] = details.get("sku")
        product["description"] = clean_html(details.get("description", ""))
        product["short_description"] = parse_specs(details.get("short_description", []))
        
        images = details.get("images", [])
        product["images"] = [img for img in images if isinstance(img, str)]
        if product["images"]:
            product["main_image"] = product["images"][0]
        
        product["price_valid_until"] = details.get("price_valid_until")
        
        return self._make_json_serializable(product)
    
    # =====================================================
    # PROCESS SINGLE PRODUCT
    # =====================================================
    
    def process_product(self, product_data: Dict, category: str, subcategory: str) -> Optional[Dict]:
        """Process single product - ONLY add if NEW, skip if exists"""
        try:
            slug = product_data.get("slug")
            product_id = product_data.get("id")
            
            if not slug or not product_id:
                logger.warning(f"{self.name} | SKIP_NO_ID | slug={slug}")
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"Product missing ID or slug")
                return None
            
            with self.lock:
                if slug in self.seen_products:
                    dup_logger.warning(f"{self.name} | DUPLICATE | {slug}")
                    self.stats["skipped_duplicates"] += 1
                    return None
                self.seen_products.add(slug)
            
            # Use inherited _product_exists from BaseSite
            if self._product_exists(product_id):
                with self.lock:
                    self.stats["skipped_duplicates"] += 1
                logger.debug(f"{self.name} | SKIP_EXISTING | {product_id} | {slug}")
                return None
            
            url = f"https://megapc.tn/shop/product/{category}/{subcategory}/{slug}"
            
            details = self.get_product_details(category, subcategory, slug)
            
            if not details or not details.get("sku"):
                logger.warning(f"{self.name} | SKIP_NO_DETAILS | {product_id} | {slug} | Details fetch failed")
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"No details for {product_id}")
                return None

            final_product = self.build_product(product_data, details, url)
            
            if not final_product:
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"Build failed for {product_id}")
                return None
            
            logger.info(f"{self.name} | NEW_PRODUCT | {product_id} | {slug}")
            final_product['is_new'] = True
            
            # USE PARENT'S _save_new_product (which uses batching)
            if super()._save_new_product(final_product, product_id):
                with self.lock:
                    self.stats["new_products"] += 1
            else:
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error(f"Save failed for {product_id}")
            
            return final_product
            
        except Exception as e:
            logger.error(f"{self.name} | PRODUCT_ERROR | {product_data.get('id')} | {e}")
            with self.lock:
                self.stats["errors"] += 1
            self._add_error(f"Product error: {str(e)[:50]}")
            return None

    # =====================================================
    # ASK FOR CONTINUATION ON ERROR
    # =====================================================
    
    def _ask_continuation(self, error_count: int, total_processed: int) -> bool:
        """Ask user if they want to continue after errors"""
        print(f"\n⚠️ Encountered {error_count} errors so far")
        print(f"   Processed {total_processed} products")
        
        response = input("\nContinue scraping? (yes/no/retry): ").strip().lower()
        
        if response == 'yes' or response == 'y':
            return True
        elif response == 'retry' or response == 'r':
            print("🔄 Retrying...")
            return True
        else:
            print("❌ Stopping scrape...")
            return False

    # =====================================================
    # PRINT FINAL STATS
    # =====================================================
    
    def _print_final_stats(self, results: List[Dict]):
        """Print final statistics with formatting"""
        print(f"\n{'='*60}")
        print(f"📊 SCRAPE COMPLETE: {self.name}")
        print(f"{'='*60}")
        print(f"✅ New products: {self.stats['new_products']:,}")
        print(f"⏭️  Existing products (skipped): {self.stats['skipped_duplicates']:,}")
        print(f"💾 Redis saves: {self.stats['redis_saved']:,}")
        print(f"🗄️ PostgreSQL saves: {self.stats['postgres_saved']:,}")
        print(f"❌ Errors: {self.stats['errors']:,}")
        print(f"📦 Total products found: {self.stats['total_products_found']:,}")
        
        total_processed = self.stats['new_products'] + self.stats['skipped_duplicates']
        if total_processed > 0:
            success_rate = (self.stats['new_products'] / total_processed) * 100
            print(f"\n📈 New product rate: {success_rate:.1f}%")
        
        print(f"{'='*60}\n")

    # =====================================================
    # TEST SCRAPE
    # =====================================================
    
    def test_scrape(self, limit: int = 5) -> List[Dict]:
        """Test scrape with limited products to verify functionality"""
        print(f"\n🧪 TEST MODE: Scraping first {limit} products only")
        self.test_mode = True
        self.test_limit = limit
        
        results = self.scrape_site()
        
        print(f"\n✅ Test completed: {len(results)} new products found")
        print(f"   Errors: {self.stats['errors']}")
        
        if self.stats['errors'] > 0:
            print("\n⚠️ Some errors occurred during test.")
            response = input("Continue with full scrape? (yes/no): ").strip().lower()
            if response == 'yes':
                self.test_mode = False
                return self.scrape_site()
        
        return results

    # =====================================================
    # MAIN SCRAPE METHOD
    # =====================================================

    def scrape_site(self, force_build_refresh: bool = False) -> List[Dict]:
        """Main scrape method - ONLY finds and adds NEW products"""
        logger.info(f"{self.name} | SCRAPE_START (NEW PRODUCTS ONLY)")
        log_global(f"{self.name} | SCRAPE_START (NEW PRODUCTS ONLY)")
        
        self.performance = PerformanceTracker()

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
        
        # Validate build ID
        logger.info("Validating MegaPC build ID...")
        
        if force_build_refresh:
            logger.info("Forcing build ID refresh...")
            self.build_manager.invalidate_cache()
        
        build_id = self.build_manager.get_build_id(force_refresh=force_build_refresh)
        
        if not build_id:
            logger.error("Failed to get build ID. Aborting scrape.")
            self._add_error("Failed to get build ID")
            return []
        
        if not self._validate_build_id(build_id):
            logger.warning(f"Build ID {build_id} appears invalid. Discovering new one...")
            self.build_manager.invalidate_cache()
            new_build_id = self.build_manager.get_build_id(force_refresh=True)
            
            if not new_build_id:
                logger.error("Failed to discover valid build ID. Aborting scrape.")
                self._add_error("Failed to discover build ID")
                return []
            
            if not self._validate_build_id(new_build_id):
                logger.error(f"New build ID {new_build_id} also appears invalid. Aborting scrape.")
                self._add_error(f"Invalid build ID: {new_build_id}")
                return []
            
            build_id = new_build_id
            logger.info(f"Valid build ID discovered: {build_id}")
        else:
            logger.info(f"Build ID validated: {build_id}")
        
        self.current_build_id = build_id
        self.base_next = f"{self.base_next_url}/{build_id}"
        logger.info(f"Base Next URL: {self.base_next}")
        
        # Initialize connections
        results = []
        total_categories = len(CATEGORIES)
        error_count = 0
        
        try:
            if not self._init_connections():
                logger.error(f"{self.name} | CONNECTION_FAILED")
                self._add_error("Connection failed")
                return []
        except Exception as e:
            logger.error(f"{self.name} | CONNECTION_FAILED | {e}")
            self._add_error(f"Connection error: {str(e)[:50]}")
            return []
        
        try:
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
            
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
            
            for i, (cat, subs) in enumerate(CATEGORIES.items(), start=1):
                if self.test_mode and i > 1:
                    break
                
                self.performance.add_category()
                
                self.stats['current_category'] = cat
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                for sub_index, sub in enumerate(subs, 1):
                    self.performance.add_subcategory()
                    
                    if error_count > 10:
                        if not self._ask_continuation(error_count, len(results)):
                            break
                    
                    self.stats['current_category'] = f"{cat} > {sub}"
                    if hasattr(self, '_update_stats'):
                        self._update_stats(self.stats)
                    
                    try:
                        products = self.get_products(cat, sub)
                    except Exception as e:
                        error_msg = f"Get products failed {cat}: {str(e)[:50]}"
                        logger.error(f"{self.name} | GET_PRODUCTS_ERROR | {cat} | {e}")
                        self._add_error(error_msg)
                        continue
                    
                    if not products:
                        continue
                    
                    self.stats["total_products_found"] += len(products)
                    
                    completed = 0
                    sub_error_count = 0
                    
                    with ThreadPoolExecutor(max_workers=5) as executor:
                        futures = {
                            executor.submit(self.process_product, p, cat, sub): p
                            for p in products
                        }
                        
                        for future in as_completed(futures):
                            try:
                                result = future.result(timeout=60)
                                if result:
                                    results.append(result)
                                    self.performance.add_success()
                                else:
                                    self.performance.add_error()
                            except Exception as e:
                                logger.error(f"{self.name} | FUTURE_ERROR | {e}")
                                sub_error_count += 1
                                error_count += 1
                                self.performance.add_error()
                                self._add_error(f"Future error: {str(e)[:50]}")
                            
                            self.performance.add_product()
                            completed += 1
                            
                            self.stats['current_product'] = f"{completed}/{len(products)}"
                            if hasattr(self, '_update_stats'):
                                self._update_stats(self.stats)
                    
                    if sub_error_count > 0:
                        logger.warning(f"Subcategory had {sub_error_count} errors")
                
                self.stats['completed_categories'] = i
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                batch_size = self.get_pending_batch_size()
                logger.info(f"Progress: {i}/{total_categories} categories completed | batch={batch_size}")
            
            log_scrape_stats(self.name, self.stats)
            recap_path = end_session()
            
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
        self._print_final_stats(results)
        
        logger.info(f"{self.name} | SCRAPE_DONE | total_new={len(results)} | stats={self.stats}")
        log_global(f"{self.name} | SCRAPE_DONE | total_new={len(results)}")
        
        return results

    def search(self, query: str) -> Optional[Dict]:
        """Search for products on MegaPC"""
        logger.warning(f"{self.name} | SEARCH_NOT_IMPLEMENTED | query={query}")
        return {"products": [], "total_results": 0}

    def parse_product(self, url: str, html: Optional[str] = None) -> Optional[Dict]:
        """Parse a single product from URL"""
        logger.warning(f"{self.name} | PARSE_PRODUCT_NOT_IMPLEMENTED | url={url}")
        return None

    def extract_categories(self) -> Dict[str, List[str]]:
        """Extract categories from MegaPC"""
        logger.info(f"{self.name} | EXTRACT_CATEGORIES_START")
        logger.info(f"{self.name} | EXTRACT_CATEGORIES_DONE | count={len(CATEGORIES)}")
        return CATEGORIES


def create_megapc_site() -> MegaPCSite:
    return MegaPCSite()