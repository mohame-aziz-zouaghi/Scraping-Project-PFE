# sites/tunisianet.py
import time
import threading
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from urllib.parse import quote
from contextlib import contextmanager

import requests
from bs4 import BeautifulSoup

from Database.Redis.product_cache import ProductCache
from core.Web.smart_requester import SmartRequester
from core.utils.Dclean_html import clean_html
from core.utils.clean_parse_specs import parse_specs
from core.utils.progress_bar import ProgressBar
from core.logger import setup_logger, write_product_result, log_global
from core.Web.product_schema import create_product_schema
from core.performance import PerformanceTracker
from core.utils.debug_logger import log_debug, log_product_check, log_scrape_stats, end_session
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
        self.name = "tunisianet"
        self.performance = PerformanceTracker()
        
        # Base URL
        self.base_url = "https://www.tunisianet.com.tn"
        self.search_url = "https://www.tunisianet.com.tn/recherche"
        
        # Smart Requester for anti-detection
        self.requester = SmartRequester(
            site_name="tunisianet",
            requests_per_minute=100,
            max_retries=4,
            timeout=45,
            use_session=True,
            use_referer=True,
            delay_between_requests=(0.5, 2.0)
        )
        
        # Stats tracking
        self.stats = {
            "new_products": 0,
            "skipped_duplicates": 0,
            "errors": 0,
            "redis_saved": 0,
            "postgres_saved": 0,
            "total_products_found": 0
        }
        
        # Thread safety
        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        
        # Connections (initialized in scrape_site)
        self.cache = None
        self.db = None
        self.redis_client = None
        
        # Test mode
        self.test_mode = False
        self.test_limit = 5
    
    # =====================================================
    # CONNECTION MANAGEMENT
    # =====================================================
    
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
    
    # =====================================================
    # JSON SERIALIZATION
    # =====================================================
    
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
        except:
            return None
    
    # =====================================================
    # SEARCH
    # =====================================================
    
    def search(self, query: str) -> Dict:
        """
        Search for products on Tunisianet
        """
        logger.info(f"{self.name} | SEARCH_START | {query}")
        
        page = 1
        results = []
        seen_first_ids = set()
        
        while True:
            url = f"{self.search_url}?s={query}&submit_search=&page={page}&order=product.price.asc"
            
            html = self.requester.get_html(url)
            
            if not html:
                logger.warning(f"{self.name} | SEARCH_FAILED | page={page} | query={query}")
                break
            
            products = parse_tunisianet_products(html, self.name.lower())
            
            if not products:
                break
            
            # Check for loop detection
            first_pid = products[0].get("main_product_id")
            if first_pid in seen_first_ids:
                logger.info(f"{self.name} | SEARCH_LOOP_DETECTED | page={page} | query={query}")
                break
            
            seen_first_ids.add(first_pid)
            results.extend(products)
            page += 1
        
        logger.info(f"{self.name} | SEARCH_FINISHED | {query} | total_products={len(results)}")
        
        return {
            "site": self.name.lower(),
            "query": query,
            "total_results": len(results),
            "products": results
        }
    
    # =====================================================
    # GET CATEGORY PAGE COUNT
    # =====================================================
    
    def get_category_page_count(self, html: str) -> int:
        """Get total number of pages for a category"""
        soup = BeautifulSoup(html, "lxml")
        page_numbers = []
        
        # Try classic pagination
        pages = soup.select(".page-list li a")
        
        for p in pages:
            text = p.text.strip()
            if text.isdigit():
                page_numbers.append(int(text))
        
        # Fallback
        if not page_numbers:
            pages = soup.select(".pagination a")
            for p in pages:
                text = p.text.strip()
                if text.isdigit():
                    page_numbers.append(int(text))
        
        return max(page_numbers) if page_numbers else 1
    
    # =====================================================
    # GET CATEGORY PRODUCTS
    # =====================================================
    
    def get_category_products(self, category_url: str, page: int) -> List[Dict]:
        """Fetch products from a category page"""
        url = f"{category_url}?page={page}"
        
        html = self.requester.get_html(url)
        
        if not html:
            logger.warning(f"{self.name} | CATEGORY_PAGE_FAILED | {url}")
            return []
        
        return parse_tunisianet_products(html, self.name.lower())
    
    # =====================================================
    # PARSE SINGLE PRODUCT
    # =====================================================
    
    def parse_product(self, url: str, product_id: str = None) -> Optional[Dict]:
        """Parse a single product from URL"""
        html = self.requester.get_html(url)
        
        if not html:
            return None
        
        return parse_tunisianet_product_page(html, url, self.name.lower(), product_id)
    
    # =====================================================
    # CHECK IF PRODUCT EXISTS
    # =====================================================
    
    def _product_exists(self, product_id: str) -> bool:
        """Check if product already exists in Redis or PostgreSQL"""
        # Check Redis first (fast)
        if self.cache:
            cached = self.cache.get_product(self.name.lower(), product_id)
            if cached:
                return True
        
        # Check PostgreSQL
        if self.db:
            existing = self.db.get_product_by_site_and_main_id(self.name.lower(), product_id)
            if existing:
                return True
        
        return False
    
    # =====================================================
    # SAVE NEW PRODUCT
    # =====================================================
    
    def _save_new_product(self, product: Dict, product_id: str) -> bool:
        """Save ONLY new product to Redis cache and PostgreSQL"""
        try:
            # Prepare cache payload
            cache_payload = {
                "url": str(product.get("url", "")),
                "price": float(product.get("price", 0)),
                "discount_price": float(product.get("discount_price", 0)),
                "discount_amount": float(product.get("discount_amount", 0)),
                "availability": str(product.get("availability", "OutOfStock")),
                "special_id": str(product.get("special_id")) if product.get("special_id") else None,
                "price_valid_until": str(product.get("price_valid_until")) if product.get("price_valid_until") else None,
                "last_checked": int(time.time()),
                "is_enabled": True
            }
            
            # Save to Redis
            if self.cache:
                self.cache.set_product(self.name.lower(), product_id, cache_payload, ttl=None)
                self.stats["redis_saved"] += 1
            
            # Save to PostgreSQL
            if self.db:
                db_product = {
                    "site": self.name.lower(),
                    "main_product_id": product_id,
                    "special_id": product.get("special_id"),
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
                    "main_image": product.get("main_image"),
                    "is_enabled": True
                }
                
                db_product = self._make_json_serializable(db_product)
                self.db.upsert_products([db_product])
                self.stats["postgres_saved"] += 1
            
            write_product_result(product, self.name)
            
            return True
            
        except Exception as e:
            logger.error(f"{self.name} | SAVE_ERROR | {product_id} | {e}")
            return False
    
    # =====================================================
    # PROCESS SINGLE PRODUCT
    # =====================================================
    
    def process_product(self, product_data: Dict) -> Optional[Dict]:
        """Process single product - ONLY add if NEW, skip if exists"""
        try:
            product_id = product_data.get("main_product_id")
            url = product_data.get("product_link")
            
            if not product_id or not url:
                logger.warning(f"{self.name} | SKIP_NO_ID | product_id={product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            # Check for duplicates within this scrape session
            with self.lock:
                if product_id in self.seen_products:
                    dup_logger.warning(f"{self.name} | DUPLICATE | {product_id}")
                    self.stats["skipped_duplicates"] += 1
                    return None
                self.seen_products.add(product_id)
            
            # =====================================================
            # SKIP IF PRODUCT ALREADY EXISTS
            # =====================================================
            if self._product_exists(product_id):
                with self.lock:
                    self.stats["skipped_duplicates"] += 1
                logger.debug(f"{self.name} | SKIP_EXISTING | {product_id}")
                return None
            
            # Parse product details
            details = self.parse_product(url, product_id)
            
            if not details:
                logger.warning(f"{self.name} | PARSE_FAILED | {product_id}")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            # =====================================================
            # NEW PRODUCT - SAVE IT
            # =====================================================
            logger.info(f"{self.name} | NEW_PRODUCT | {product_id}")
            details['is_new'] = True
            
            if self._save_new_product(details, product_id):
                with self.lock:
                    self.stats["new_products"] += 1
            else:
                with self.lock:
                    self.stats["errors"] += 1
            
            return details
            
        except Exception as e:
            logger.error(f"{self.name} | PRODUCT_ERROR | {product_data.get('main_product_id')} | {e}")
            with self.lock:
                self.stats["errors"] += 1
            return None
    
    # =====================================================
    # PROCESS BATCH
    # =====================================================
    
    def process_batch(self, products: List[Dict], batch_name: str = "Batch") -> List[Dict]:
        """Process a batch of products in parallel"""
        results = []
        
        if not products:
            return results
        
        logger.info(f"{self.name} | {batch_name} | Processing {len(products)} products")
        
        # Create progress bar
        progress = ProgressBar(
            total=len(products),
            width=35,
            prefix=f"      {batch_name}",
            show_eta=True
        )
        
        completed = 0
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {
                executor.submit(self.process_product, product): product
                for product in products
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
                
                extra_info = f"N:{self.stats['new_products']} S:{self.stats['skipped_duplicates']} E:{self.stats['errors']}"
                progress.update(completed, extra_info)
        
        progress.close()
        
        return results
    
    # =====================================================
    # SCRAPE CATEGORY
    # =====================================================
    
    def scrape_category(self, category_name: str, category_url: str) -> List[Dict]:
        """Scrape a single category - only new products"""
        logger.info(f"{self.name} | CATEGORY_START | {category_name}")
        
        results = []
        
        # Get first page to determine total pages
        first_page_html = self.requester.get_html(f"{category_url}?page=1")
        
        if not first_page_html:
            logger.warning(f"{self.name} | CATEGORY_NO_PAGE | {category_name}")
            return results
        
        total_pages = self.get_category_page_count(first_page_html)
        logger.info(f"{self.name} | CATEGORY_PAGES | {category_name} | pages={total_pages}")
        
        # Process each page
        for page in range(1, total_pages + 1):
            products = self.get_category_products(category_url, page)
            
            if not products:
                continue
            
            self.stats["total_products_found"] += len(products)
            
            # Process products in this page
            page_results = self.process_batch(products, f"{category_name} P{page}")
            results.extend(page_results)
        
        logger.info(f"{self.name} | CATEGORY_FINISHED | {category_name} | new_products={len(results)}")
        return results
    
    # =====================================================
    # SCRAPE BRANDS (FALLBACK)
    # =====================================================
    
    def scrape_brands(self) -> List[Dict]:
        """Scrape products by brand as fallback"""
        logger.info(f"{self.name} | BRAND_SCRAPE_START")
        
        results = []
        
        for brand in SEARCH_BRANDS:
            logger.info(f"{self.name} | BRAND_START | {brand}")
            
            search_results = self.search(brand)
            products = search_results.get("products", [])
            
            if not products:
                logger.info(f"{self.name} | BRAND_EMPTY | {brand}")
                continue
            
            self.stats["total_products_found"] += len(products)
            
            brand_results = self.process_batch(products, f"Brand: {brand}")
            results.extend(brand_results)
            
            logger.info(f"{self.name} | BRAND_DONE | {brand} | new_products={len(brand_results)}")
        
        logger.info(f"{self.name} | BRAND_SCRAPE_FINISHED | total_new={len(results)}")
        return results
    
    # =====================================================
    # PRINT FINAL STATS
    # =====================================================
    
    def _print_final_stats(self, results: List[Dict]):
        """Print final statistics"""
        print(f"\n{'='*60}")
        print("📊 SCRAPE COMPLETE (NEW PRODUCTS ONLY)")
        print(f"{'='*60}")
        print(f"✅ New products: {self.stats['new_products']:,}")
        print(f"⏭️  Existing products (skipped): {self.stats['skipped_duplicates']:,}")
        print(f"💾 Redis saves: {self.stats['redis_saved']:,}")
        print(f"🗄️ PostgreSQL saves: {self.stats['postgres_saved']:,}")
        print(f"❌ Errors: {self.stats['errors']:,}")
        print(f"📦 Total products found: {self.stats['total_products_found']:,}")
        
        total_processed = self.stats['new_products'] + self.stats['skipped_duplicates']
        if total_processed > 0:
            new_rate = (self.stats['new_products'] / total_processed) * 100
            print(f"\n📈 New product rate: {new_rate:.1f}%")
        
        print(f"{'='*60}\n")
    
    # =====================================================
    # MAIN SCRAPE METHOD
    # =====================================================
    
    def scrape_site(self) -> List[Dict]:
        """
        Main scrape method - ONLY finds and adds NEW products
        Existing products are skipped
        """
        logger.info(f"{self.name} | SCRAPE_START (NEW PRODUCTS ONLY)")
        log_global(f"{self.name} | SCRAPE_START (NEW PRODUCTS ONLY)")
        
        # Reset performance tracker
        self.performance = PerformanceTracker()
        
        # =====================================================
        # STEP 1: INITIALIZE CONNECTIONS
        # =====================================================
        results = []
        
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
            
            # =====================================================
            # STEP 2: SCRAPE CATEGORIES
            # =====================================================
            total_categories = len(CATEGORIES)
            
            print(f"\n{'='*60}")
            print(f"📦 SCRAPING {total_categories} CATEGORIES")
            print(f"{'='*60}")
            
            for i, (category_name, category_url) in enumerate(CATEGORIES.items(), start=1):
                if self.test_mode and i > 1:
                    print(f"\n⏹️ Test mode: stopping after first category")
                    break
                
                print(f"\n📁 Category {i}/{total_categories}: {category_name}")
                
                category_results = self.scrape_category(category_name, category_url)
                results.extend(category_results)
            
            # =====================================================
            # STEP 3: SCRAPE BRANDS (FALLBACK)
            # =====================================================
            print(f"\n{'='*60}")
            print(f"📦 SCRAPING BRANDS (FALLBACK)")
            print(f"{'='*60}")
            
            brand_results = self.scrape_brands()
            results.extend(brand_results)
            
            # =====================================================
            # STEP 4: PRINT FINAL STATISTICS
            # =====================================================
            self._print_final_stats(results)
            
        except KeyboardInterrupt:
            print("\n\n⚠️ Scrape interrupted by user!")
            logger.warning(f"{self.name} | INTERRUPTED")
        except Exception as e:
            logger.error(f"{self.name} | SCRAPE_ERROR | {e}")
            print(f"\n❌ Scrape failed: {e}")
        finally:
            self._close_connections()
        
        # Performance summary
        self.performance.print_summary(self.name)
        
        logger.info(f"{self.name} | SCRAPE_DONE | total_new={len(results)} | stats={self.stats}")
        log_global(f"{self.name} | SCRAPE_DONE | total_new={len(results)}")
        
        return results
    
    # =====================================================
    # TEST SCRAPE
    # =====================================================
    
    def test_scrape(self, limit: int = 5) -> List[Dict]:
        """Test scrape with limited products"""
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
    # EXTRACT CATEGORIES
    # =====================================================
    
    def extract_categories(self):
        """Extract categories from Tunisianet"""
        from parsers.tunisianet.tunisianet_category_parser import parse_tunisianet_categories
        
        logger.info(f"{self.name} | CATEGORY_EXTRACTION_START")
        
        html = self.requester.get_html(self.base_url)
        
        if not html:
            logger.error(f"{self.name} | CATEGORY_EXTRACTION_FAILED")
            return
        
        categories = parse_tunisianet_categories(html)
        
        print(f"\n✅ Found {len(categories)} categories")
        
        return categories


# =====================================================
# FACTORY FUNCTION
# =====================================================

def create_tunisianet_site() -> TunisiaNetSite:
    """Factory function to create Tunisianet site instance"""
    return TunisiaNetSite()