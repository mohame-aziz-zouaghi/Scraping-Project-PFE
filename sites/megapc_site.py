# sites/megapc.py
import time
import threading
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from urllib.parse import quote
from contextlib import contextmanager

import requests

from Database.Redis.product_cache import ProductCache
from core.utils.Dclean_html import clean_html
from core.utils.clean_parse_specs import parse_specs
from core.utils.progress_bar import ProgressBar
from core.logger import setup_logger, write_product_result, log_global
from core.Web.product_schema import create_product_schema
from core.performance import PerformanceTracker
from Database.Postgresql.db import Database

from parsers.megapc.megapc_products_parser import parse_megapc_products
from parsers.megapc.megapc_product_details_parser import parse_megapc_product_details

from config.categoriesMegaPC import CATEGORIES
from sites.base_site import BaseSite


logger = setup_logger("megapc", "scraper")
dup_logger = setup_logger("megapc", "duplication")


class MegaPCSite(BaseSite):
    """MegaPC scraper with Redis cache and PostgreSQL integration"""
    
    def __init__(self):
        self.name = "MEGAPC"
        self.performance = PerformanceTracker()
        
        # API endpoints
        self.api_products = "https://apiclt.gi-ga.tech/produit/byPaginationNew"
        self.base_next = "https://megapc.tn/_next/data/build-1757602359699"
        
        # Stats tracking
        self.stats = {
            "new_products": 0,
            "updated_products": 0,
            "skipped_duplicates": 0,
            "errors": 0,
            "redis_saved": 0,
            "postgres_saved": 0,
            "no_changes": 0
        }
        
        # Thread safety
        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        
        # Connections (initialized in scrape_site)
        self.cache = None
        self.db = None
        self.redis_client = None
        
        # Test mode for debugging
        self.test_mode = False
        self.test_limit = 5
    
    # ---------------------------------
    # CONNECTION MANAGEMENT
    # ---------------------------------
    def _init_connections(self):
        """Initialize database and cache connections"""
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
        """Close database and cache connections"""
        try:
            if self.db:
                self.db.close()
                logger.info(f"{self.name} | POSTGRES_CLOSED")
        except Exception as e:
            logger.error(f"{self.name} | POSTGRES_CLOSE_ERROR | {e}")
        
        try:
            if self.cache and self.redis_client:
                # Redis client doesn't need explicit close, but we can clean up
                pass
                logger.info(f"{self.name} | REDIS_CLOSED")
        except Exception as e:
            logger.error(f"{self.name} | REDIS_CLOSE_ERROR | {e}")
    
    @contextmanager
    def _connections(self):
        """Context manager for database connections"""
        self._init_connections()
        try:
            yield
        finally:
            self._close_connections()
    
    # ---------------------------------
    # JSON SERIALIZATION FIX
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
        if hasattr(obj, 'isoformat'):  # datetime
            return obj.isoformat()
        # Handle UUID or other objects
        try:
            return str(obj)
        except:
            return None
    
    # ---------------------------------
    # API PRODUCTS FETCH
    # ---------------------------------
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
                return []
            
            try:
                data = response.json()
            except Exception as e:
                logger.error(f"{self.name} | INVALID_JSON | {category} > {subcategory} | {e}")
                return []
            
            if not isinstance(data, dict):
                logger.error(f"{self.name} | UNEXPECTED_RESPONSE | type={type(data)}")
                return []
            
            products = parse_megapc_products(data)
            
            # Limit for test mode
            if self.test_mode and len(products) > self.test_limit:
                products = products[:self.test_limit]
                logger.info(f"{self.name} | TEST_MODE | limiting to {self.test_limit} products")
            
            logger.info(f"{self.name} | FOUND_PRODUCTS | {category} > {subcategory} | count={len(products)}")
            return products
            
        except requests.exceptions.Timeout:
            logger.error(f"{self.name} | API_TIMEOUT | {category} > {subcategory}")
            return []
        except Exception as e:
            logger.error(f"{self.name} | API_ERROR | {category} > {subcategory} | {e}")
            return []
    
    # ---------------------------------
    # PRODUCT DETAILS FETCH
    # ---------------------------------
    def get_product_details(self, category: str, subcategory: str, slug: str) -> Dict:
        """Fetch product details from MegaPC Next.js data endpoint"""
        
        if not slug:
            return {}
        
        cat_param = quote(category.replace(" ", "+"))
        sub_param = quote(subcategory.replace(" ", "+"))
        cat_path = quote(category.replace(" ", "%20"))
        sub_path = quote(subcategory.replace(" ", "%20"))
        
        url = f"{self.base_next}/shop/product/{cat_path}/{sub_path}/{slug}.json?lien={cat_param}&lien={sub_param}&lien={slug}"
        
        try:
            response = requests.get(url, timeout=15)
            
            if response.status_code != 200:
                logger.warning(f"{self.name} | DETAILS_FAIL | {slug} | status={response.status_code}")
                return {}
            
            return parse_megapc_product_details(response.json())
            
        except requests.exceptions.Timeout:
            logger.error(f"{self.name} | DETAILS_TIMEOUT | {slug}")
            return {}
        except Exception as e:
            logger.error(f"{self.name} | DETAILS_ERROR | {slug} | {e}")
            return {}
    
    # ---------------------------------
    # PRODUCT BUILDING
    # ---------------------------------
    def build_product(self, base: Dict, details: Dict, url: str) -> Optional[Dict]:
        """Build complete product object from base and details"""
        
        product_id = base.get("id")
        if not product_id:
            return None
        
        product = create_product_schema(self.name, url, product_id)
        
        # Basic info
        product["name"] = base.get("name")
        product["brand"] = base.get("brand")
        product["category"] = base.get("category")
        
        # Price calculation
        price = base.get("price") or 0
        promo = base.get("promo_price") or 0
        
        product["price"] = float(price)
        
        if promo and promo < price:
            product["discount_price"] = float(promo)
            product["discount_amount"] = round(float(price - promo), 2)
        else:
            product["discount_price"] = 0
            product["discount_amount"] = 0
        
        # Availability
        stock = base.get("stock", 0)
        product["availability"] = "InStock" if stock > 0 else "OutOfStock"
        
        # Special ID (SKU)
        product["special_id"] = details.get("sku") or base.get("id")
        
        # Details
        if details:
            product["description"] = clean_html(details.get("description", ""))
            product["short_description"] = parse_specs(details.get("short_description", []))
            
            images = details.get("images", [])
            product["images"] = [img for img in images if isinstance(img, str)]
            if product["images"]:
                product["main_image"] = product["images"][0]
        
        product["price_valid_until"] = details.get("price_valid_until")
        
        # Make sure everything is JSON serializable
        product = self._make_json_serializable(product)
        
        return product
    
    # ---------------------------------
    # CHANGE DETECTION
    # ---------------------------------
    def _has_changes(self, cached: Dict, new: Dict) -> Tuple[bool, List[str]]:
        """Check if product has meaningful changes and return changed fields"""
        fields_to_check = ["price", "discount_price", "discount_amount", "availability", "special_id", "price_valid_until"]
        changed_fields = []
        
        for field in fields_to_check:
            old_val = cached.get(field)
            new_val = new.get(field)
            
            if field in ["price", "discount_price", "discount_amount"]:
                old_val = float(old_val) if old_val else 0
                new_val = float(new_val) if new_val else 0
            elif field == "price_valid_until":
                old_val = str(old_val).split("T")[0] if old_val else None
                new_val = str(new_val).split("T")[0] if new_val else None
            
            if old_val != new_val:
                changed_fields.append(field)
        
        return len(changed_fields) > 0, changed_fields
    
    # ---------------------------------
    # SAVE TO CACHE AND DB
    # ---------------------------------
    def _save_to_cache_and_db(self, product: Dict, product_id: str) -> bool:
        """Save product to Redis cache and PostgreSQL"""
        try:
            # Prepare cache payload (ensure JSON serializable)
            cache_payload = {
                "url": str(product.get("url", "")),
                "price": float(product.get("price", 0)),
                "discount_price": float(product.get("discount_price", 0)),
                "discount_amount": float(product.get("discount_amount", 0)),
                "availability": str(product.get("availability", "OutOfStock")),
                "special_id": str(product.get("special_id")) if product.get("special_id") else None,
                "price_valid_until": str(product.get("price_valid_until")) if product.get("price_valid_until") else None,
                "last_checked": int(time.time())
            }
            
            # Save to Redis
            if self.cache:
                self.cache.set_product(self.name.lower(), product_id, cache_payload, ttl=None)
                self.stats["redis_saved"] += 1
            
            # Save to PostgreSQL
            if self.db:
                # Create a clean copy for PostgreSQL
                db_product = {
                    "site": self.name.lower(),
                    "special_id": product.get("special_id"),
                    "main_product_id": product.get("id"),
                    "url": product.get("url"),
                    "name": product.get("name"),
                    "price": float(product.get("price", 0)),
                    "discount_price": float(product.get("discount_price", 0)),
                    "discount_amount": float(product.get("discount_amount", 0)),
                    "availability": product.get("availability", "OutOfStock"),
                    "price_valid_until": product.get("price_valid_until"),
                    "brand": product.get("brand"),
                    "category": product.get("category"),
                    "description": product.get("description", ""),
                    "short_description": product.get("short_description", []),
                    "images": product.get("images", []),
                    "main_image": product.get("main_image")
                }
                
                # Convert any non-serializable items
                db_product = self._make_json_serializable(db_product)
                
                self.db.upsert_products([db_product])
                self.stats["postgres_saved"] += 1
            
            # Write to result file
            write_product_result(product, self.name)
            
            return True
            
        except Exception as e:
            logger.error(f"{self.name} | SAVE_ERROR | {product_id} | {e}")
            return False
    
    # ---------------------------------
    # PROCESS SINGLE PRODUCT
    # ---------------------------------
    def process_product(self, product_data: Dict, category: str, subcategory: str) -> Optional[Dict]:
        """Process single product: check cache, fetch details, save to Redis and PostgreSQL"""
        try:
            slug = product_data.get("slug")
            product_id = product_data.get("id")
            
            if not slug or not product_id:
                logger.warning(f"{self.name} | SKIP_NO_ID | slug={slug}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            # Check for duplicates within this scrape session
            with self.lock:
                if slug in self.seen_products:
                    dup_logger.warning(f"{self.name} | DUPLICATE | {slug}")
                    self.stats["skipped_duplicates"] += 1
                    return None
                self.seen_products.add(slug)
            
            # Build URL
            url = f"https://megapc.tn/shop/product/{category}/{subcategory}/{slug}"
            
            # Fetch details
            details = self.get_product_details(category, subcategory, slug)
            final_product = self.build_product(product_data, details, url)
            
            if not final_product:
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            # Check if product exists in Redis cache
            cached_product = None
            if self.cache:
                cached_product = self.cache.get_product(self.name.lower(), product_id)
            
            if cached_product:
                has_changes, changed_fields = self._has_changes(cached_product, final_product)
                
                if has_changes:
                    logger.info(f"{self.name} | PRODUCT_UPDATED | {product_id} | Changed: {changed_fields}")
                    final_product['is_new'] = False
                    final_product['is_updated'] = True
                    
                    if self._save_to_cache_and_db(final_product, product_id):
                        with self.lock:
                            self.stats["updated_products"] += 1
                    else:
                        with self.lock:
                            self.stats["errors"] += 1
                else:
                    logger.debug(f"{self.name} | NO_CHANGE | {product_id}")
                    final_product['is_new'] = False
                    final_product['is_updated'] = False
                    with self.lock:
                        self.stats["no_changes"] += 1
            else:
                logger.info(f"{self.name} | NEW_PRODUCT | {product_id} | {slug}")
                final_product['is_new'] = True
                final_product['is_updated'] = False
                
                if self._save_to_cache_and_db(final_product, product_id):
                    with self.lock:
                        self.stats["new_products"] += 1
                else:
                    with self.lock:
                        self.stats["errors"] += 1
            
            return final_product
            
        except Exception as e:
            logger.error(f"{self.name} | PRODUCT_ERROR | {product_data.get('id')} | {e}")
            with self.lock:
                self.stats["errors"] += 1
            return None
    
    # ---------------------------------
    # ASK FOR CONTINUATION ON ERROR
    # ---------------------------------
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
    
    # ---------------------------------
    # PRINT FINAL STATS
    # ---------------------------------
    def _print_final_stats(self, results: List[Dict]):
        """Print final statistics with formatting"""
        print(f"\n{'='*60}")
        print("📊 SCRAPE COMPLETE")
        print(f"{'='*60}")
        print(f"✅ New products: {self.stats['new_products']:,}")
        print(f"🔄 Updated products: {self.stats['updated_products']:,}")
        print(f"📝 No changes: {self.stats['no_changes']:,}")
        print(f"⚠️ Duplicates skipped: {self.stats['skipped_duplicates']:,}")
        print(f"💾 Redis saves: {self.stats['redis_saved']:,}")
        print(f"🗄️ PostgreSQL saves: {self.stats['postgres_saved']:,}")
        print(f"❌ Errors: {self.stats['errors']:,}")
        print(f"📦 Total products scraped: {len(results):,}")
        
        total_processed = self.stats['new_products'] + self.stats['updated_products'] + self.stats['no_changes']
        if total_processed > 0:
            success_rate = (total_processed / (total_processed + self.stats['errors'])) * 100
            print(f"\n📈 Success rate: {success_rate:.1f}%")
        
        print(f"{'='*60}\n")
    
    # ---------------------------------
    # TEST WITH 5 PRODUCTS
    # ---------------------------------
    def test_scrape(self, limit: int = 5) -> List[Dict]:
        """Test scrape with limited products to verify functionality"""
        print(f"\n🧪 TEST MODE: Scraping first {limit} products only")
        self.test_mode = True
        self.test_limit = limit
        
        results = self.scrape_site()
        
        print(f"\n✅ Test completed: {len(results)} products scraped")
        print(f"   Errors: {self.stats['errors']}")
        
        if self.stats['errors'] > 0:
            print("\n⚠️ Some errors occurred during test.")
            response = input("Continue with full scrape? (yes/no): ").strip().lower()
            if response == 'yes':
                self.test_mode = False
                return self.scrape_site()
        
        return results
    
    # ---------------------------------
    # MAIN SCRAPE METHOD
    # ---------------------------------
    def scrape_site(self) -> List[Dict]:
        """Main scrape method with connection management and error handling"""
        
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")
        
        results = []
        total_categories = len(CATEGORIES)
        error_count = 0
        
        # Initialize connections
        try:
            self._init_connections()
        except Exception as e:
            logger.error(f"{self.name} | CONNECTION_FAILED | {e}")
            print(f"\n❌ Failed to connect to database: {e}")
            return []
        
        try:
            # Reset stats
            self.stats = {k: 0 for k in self.stats}
            self.seen_products.clear()
            
            # Process each category
            for i, (cat, subs) in enumerate(CATEGORIES.items(), start=1):
                if self.test_mode and i > 1:
                    print(f"\n⏹️ Test mode: stopping after first category")
                    break
                
                print(f"\n{'='*60}")
                print(f"📦 CATEGORY {i}/{total_categories}: {cat}")
                print(f"{'='*60}")
                
                # Create progress bar for subcategories
                sub_progress = ProgressBar(
                    total=len(subs),
                    width=30,
                    prefix=f"   Subcategories",
                    show_eta=False
                )
                
                for sub_index, sub in enumerate(subs, 1):
                    # Check if we should stop due to errors
                    if error_count > 10:
                        print(f"\n⚠️ Too many errors ({error_count}). Stopping...")
                        if not self._ask_continuation(error_count, len(results)):
                            break
                    
                    sub_progress.update(sub_index, f"Processing: {sub}")
                    
                    print(f"\n   📁 Subcategory: {sub}")
                    
                    # Fetch products from API
                    products = self.get_products(cat, sub)
                    
                    if not products:
                        print(f"      ⚠️ No products found")
                        continue
                    
                    print(f"      📊 Found {len(products)} products")
                    
                    # Create product progress bar
                    product_progress = ProgressBar(
                        total=len(products),
                        width=35,
                        prefix=f"      Products",
                        show_eta=True
                    )
                    
                    # Process products in parallel
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
                            except Exception as e:
                                logger.error(f"{self.name} | FUTURE_ERROR | {e}")
                                sub_error_count += 1
                                error_count += 1
                            
                            completed += 1
                            
                            # Update progress bar with stats
                            extra_info = f"N:{self.stats['new_products']} U:{self.stats['updated_products']} E:{self.stats['errors']}"
                            product_progress.update(completed, extra_info)
                    
                    product_progress.close()
                    
                    if sub_error_count > 0:
                        print(f"      ⚠️ Subcategory had {sub_error_count} errors")
                    
                    print(f"      ✅ Subcategory complete: {len(products)} products processed")
                
                sub_progress.close()
            
            # Print final statistics
            self._print_final_stats(results)
            
        except KeyboardInterrupt:
            print("\n\n⚠️ Scrape interrupted by user!")
            logger.warning(f"{self.name} | INTERRUPTED")
        except Exception as e:
            logger.error(f"{self.name} | SCRAPE_ERROR | {e}")
            print(f"\n❌ Scrape failed: {e}")
        finally:
            # Always close connections
            self._close_connections()
        
        # Performance summary
        self.performance.print_summary()
        
        logger.info(f"{self.name} | SCRAPE_DONE | total={len(results)} | stats={self.stats}")
        log_global(f"{self.name} | SCRAPE_DONE | total={len(results)}")
        
        return results
    
    # ---------------------------------
    # SEARCH PRODUCTS
    # ---------------------------------
    def search(self, query: str) -> Optional[Dict]:
        """Search for products on MegaPC"""
        logger.warning(f"{self.name} | SEARCH_NOT_IMPLEMENTED | query={query}")
        return {"products": [], "total_results": 0}
    
    # ---------------------------------
    # PARSE SINGLE PRODUCT
    # ---------------------------------
    def parse_product(self, url: str, html: Optional[str] = None) -> Optional[Dict]:
        """Parse a single product from URL"""
        logger.warning(f"{self.name} | PARSE_PRODUCT_NOT_IMPLEMENTED | url={url}")
        return None
    
    # ---------------------------------
    # EXTRACT CATEGORIES
    # ---------------------------------
    def extract_categories(self) -> Dict[str, List[str]]:
        """Extract categories from MegaPC"""
        logger.info(f"{self.name} | EXTRACT_CATEGORIES_START")
        logger.info(f"{self.name} | EXTRACT_CATEGORIES_DONE | count={len(CATEGORIES)}")
        return CATEGORIES


# ---------------------------------
# FACTORY FUNCTION
# ---------------------------------
def create_megapc_site() -> MegaPCSite:
    """Factory function to create MegaPC site instance"""
    return MegaPCSite()