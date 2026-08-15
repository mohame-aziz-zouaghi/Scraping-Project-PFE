# sites/spacenet.py
"""
Spacenet Site Scraper - Optimized with Redis caching, batching, and comprehensive logging
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import os
import threading
import time
from typing import Dict, List, Optional, Any, Set, Union
from datetime import datetime

from config.brands import SEARCH_BRANDS
from config.categoriesSpacenet import CATEGORIES
from core.Web.http_client import get
from core.logger import setup_logger, log_global, write_product_result
from parsers.spacenet.spacenet_category_parser import parse_spacenet_categories
from parsers.spacenet.spacenet_product_parser import parse_spacenet_product_page
from parsers.spacenet.spacenet_search_parser import parse_spacenet_products
from core.performance import PerformanceTracker
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from sites.base_site import BaseSite

# ---------------------------------
# LOGGERS
# ---------------------------------
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
        
        # Connections
        self.cache = None
        self.db = None
        self.redis_client = None
        
        self.stats = {
            "new_products": 0,
            "skipped_duplicates": 0,
            "errors": 0,
            "redis_saved": 0,
            "postgres_saved": 0,
            "total_products_found": 0,
            "completed_categories": 0,
            "total_categories": 0,
            "current_category": "",
            "current_product": "",
            "current_page": 0,
            "total_pages": 0,
            "category_progress": 0
        }
        
        # Performance tracking
        self.start_time = None
        self.products_processed = 0
        self.batch_count = 0

    # =====================================================
    # CONNECTION MANAGEMENT
    # =====================================================
    
    def _init_connections(self):
        """Initialize database and cache connections with proper error handling"""
        # =====================================================
        # REDIS CONNECTION
        # =====================================================
        try:
            logger.info(f"{self.name} | INIT_REDIS | Starting Redis connection...")
            self.cache = ProductCache()
            self.redis_client = self.cache.redis
            
            if self.redis_client:
                try:
                    self.redis_client.ping()
                    logger.info(f"{self.name} | REDIS_CONNECTED | ping successful")
                except Exception as ping_error:
                    logger.error(f"{self.name} | REDIS_PING_FAILED | {ping_error}")
                    self.cache = None
                    self.redis_client = None
            else:
                logger.error(f"{self.name} | REDIS_CLIENT_NONE")
                self.cache = None
                self.redis_client = None
                
        except Exception as e:
            logger.error(f"{self.name} | REDIS_CONNECTION_ERROR | {e}")
            self.cache = None
            self.redis_client = None
        
        # =====================================================
        # POSTGRESQL CONNECTION
        # =====================================================
        try:
            logger.info(f"{self.name} | INIT_DB | Starting PostgreSQL connection...")
            self.db = Database()
            
            if self.db and hasattr(self.db, 'conn') and self.db.conn:
                logger.info(f"{self.name} | POSTGRES_CONNECTED")
            else:
                logger.error(f"{self.name} | DB_CONNECTION_FAILED")
                self.db = None
                
        except Exception as e:
            logger.error(f"{self.name} | POSTGRES_CONNECTION_ERROR | {e}")
            self.db = None
        
        logger.info(f"{self.name} | CONNECTION_STATUS | Redis={'OK' if self.cache else 'FAILED'} | DB={'OK' if self.db else 'FAILED'}")

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
                self.redis_client.close()
                logger.info(f"{self.name} | REDIS_CLOSED")
        except Exception as e:
            logger.error(f"{self.name} | REDIS_CLOSE_ERROR | {e}")

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
    # CHECK IF PRODUCT EXISTS
    # =====================================================
    
    def _product_exists(self, product_id: str) -> bool:
        """Check if product already exists in Redis or PostgreSQL"""
        if self.cache is not None:
            try:
                cached = self.cache.get_product(self.name.lower(), product_id)
                if cached:
                    return True
            except Exception as e:
                logger.debug(f"{self.name} | CACHE_CHECK_ERROR | {product_id} | {e}")
        
        if self.db is not None:
            try:
                existing = self.db.get_product_by_site_and_main_id(self.name.lower(), product_id)
                if existing:
                    return True
            except Exception as e:
                logger.debug(f"{self.name} | DB_CHECK_ERROR | {product_id} | {e}")
        
        return False

    # =====================================================
    # SAVE NEW PRODUCT - FIXED REDIS SAVING
    # =====================================================
    
    def _save_new_product(self, product: Union[Dict, Set, Any], product_id: str) -> bool:
        """Save ONLY new product to Redis cache and PostgreSQL"""
        try:
            # =====================================================
            # Validate product type
            # =====================================================
            if isinstance(product, set):
                logger.error(f"{self.name} | SAVE_SET_ERROR | {product_id} | product is a set")
                try:
                    for item in product:
                        if isinstance(item, dict):
                            product = item
                            logger.info(f"{self.name} | SAVE_SET_CONVERTED | {product_id}")
                            break
                except:
                    pass
                if isinstance(product, set):
                    return False
            
            if product is None:
                logger.warning(f"{self.name} | SAVE_EMPTY_PRODUCT | {product_id}")
                return False
            
            if not isinstance(product, dict):
                logger.warning(f"{self.name} | SAVE_INVALID_PRODUCT | {product_id} | type={type(product)}")
                return False
            
            # =====================================================
            # Extract required fields
            # =====================================================
            url = product.get("url", "")
            if not url:
                logger.warning(f"{self.name} | SAVE_NO_URL | {product_id}")
                return False
            
            price = product.get("price", 0)
            if price is None or price <= 0:
                logger.warning(f"{self.name} | SAVE_NO_PRICE | {product_id} | price={price}")
                return False
            
            # =====================================================
            # Prepare cache payload
            # =====================================================
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
            
            # =====================================================
            # FIX: Save to Redis (if available)
            # =====================================================
            if self.cache is not None:
                try:
                    logger.info(f"{self.name} | SAVING_TO_REDIS | {product_id}")
                    self.cache.set_product(self.name.lower(), product_id, cache_payload, ttl=None)
                    saved_to_redis = True
                    with self.lock:
                        self.stats["redis_saved"] += 1
                    logger.info(f"{self.name} | REDIS_SAVED | {product_id}")
                except Exception as redis_error:
                    logger.error(f"{self.name} | REDIS_SAVE_ERROR | {product_id} | {redis_error}")
            else:
                logger.warning(f"{self.name} | REDIS_NOT_AVAILABLE | {product_id} | cannot save to Redis")
            
            # =====================================================
            # Save to PostgreSQL (if available)
            # =====================================================
            if self.db is not None:
                try:
                    logger.info(f"{self.name} | SAVING_TO_PG | {product_id}")
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
                    logger.info(f"{self.name} | PG_SAVED | {product_id}")
                except Exception as pg_error:
                    logger.error(f"{self.name} | PG_SAVE_ERROR | {product_id} | {pg_error}")
            else:
                logger.warning(f"{self.name} | DB_NOT_AVAILABLE | {product_id} | cannot save to PostgreSQL")
            
            # =====================================================
            # Track new product if saved to at least one storage
            # =====================================================
            if saved_to_redis or saved_to_pg:
                with self.lock:
                    self.stats["new_products"] += 1
                write_product_result(product, self.name)
                logger.info(f"{self.name} | NEW_PRODUCT_SAVED | {product_id} | Redis={saved_to_redis}, PG={saved_to_pg}")
                return True
            else:
                logger.error(f"{self.name} | SAVE_FAILED | {product_id} | Redis={saved_to_redis}, PG={saved_to_pg}")
                return False
                
        except Exception as e:
            logger.error(f"{self.name} | SAVE_ERROR | {product_id} | {e}")
            import traceback
            traceback.print_exc()
            return False

    # =====================================================
    # UPDATE PROGRESS
    # =====================================================
    
    def _update_progress(self):
        """Update the dashboard with current progress"""
        if hasattr(self, '_update_stats'):
            try:
                if isinstance(self.stats, dict):
                    self._update_stats(self.stats)
            except Exception as e:
                pass

    # =====================================================
    # CATEGORY DISCOVERY
    # =====================================================
    
    def extract_categories(self):
        """Extract categories from Spacenet"""
        logger.info(f"{self.name} | CATEGORY_DISCOVERY_START")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_START")

        html = get(self.BASE_URL)
        if not html:
            logger.error(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            log_global(f"{self.name} | CATEGORY_DISCOVERY_FAILED")
            return

        categories = parse_spacenet_categories(html)
        if not categories:
            logger.warning(f"{self.name} | NO_CATEGORIES_FOUND")
            return

        os.makedirs("config", exist_ok=True)
        file_path = "config/categoriesSpacenet.py"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("CATEGORIES = {\n")
            for name, url in categories.items():
                f.write(f'    "{name}": "{url}",\n')
            f.write("}\n")

        logger.info(f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}")
        log_global(f"{self.name} | CATEGORY_DISCOVERY_FINISHED | count={len(categories)}")

    # =====================================================
    # DUPLICATION HANDLER - FIXED
    # =====================================================
    
    def handle_duplicate(self, product_id, source, seen_products):
        """Handle duplicate product detection with dict safety"""
        with self.lock:
            # =====================================================
            # CRITICAL FIX: Convert set to dict if needed
            # =====================================================
            if isinstance(seen_products, set):
                logger.error(f"{self.name} | SEEN_IS_SET | product_id={product_id} | converting set to dict")
                # Create new dict from set
                new_dict = {}
                for item in seen_products:
                    if isinstance(item, str):
                        new_dict[item] = [source]
                    elif isinstance(item, dict):
                        new_dict.update(item)
                # Clear the set and update with dict values
                seen_products.clear()
                for key, value in new_dict.items():
                    seen_products[key] = value
                logger.info(f"{self.name} | SEEN_CONVERTED | product_id={product_id} | now dict with {len(seen_products)} items")
            
            # Now safe to use as dict
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
    # SEARCH - OPTIMIZED
    # =====================================================
    
    def search(self, query):
        """Search for products on Spacenet"""
        logger.info(f"{self.name} | SEARCH_START | {query}")
        results = []
        seen_ids = set()
        page = 1

        while True:
            url = self.SEARCH_URL.format(query=query)
            if page > 1:
                url += f"&page={page}"
            html = get(url)
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
    # PRODUCT PARSER - SAFE
    # =====================================================
    
    def parse_product(self, url, product_id=None):
        """Parse a single product page"""
        logger.info(f"{self.name} | PRODUCT_PARSE_START | {url}")

        html = get(url)

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
            return None

    # =====================================================
    # CATEGORY PAGE COUNT
    # =====================================================
    
    def get_category_page_count(self, html):
        """Get total number of pages in a category"""
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
    # PRODUCT WORKER - FULLY OPTIMIZED
    # =====================================================
    
    def scrape_product_worker(self, product, seen_products, source):
        """
        Process a single product for Spacenet - returns product dict or None
        """
        worker_pid = "UNKNOWN"
        start_time = time.time()
        
        try:
            # =====================================================
            # Validate product
            # =====================================================
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
            
            # =====================================================
            # Extract product data
            # =====================================================
            pid = product.get("main_product_id")
            url = product.get("product_link")

            if not pid or not url:
                logger.debug(f"{self.name} | SKIP_NO_ID")
                return None

            pid = str(pid)
            worker_pid = pid

            # =====================================================
            # Check Redis cache (if available)
            # =====================================================
            if self.cache is not None:
                try:
                    cached = self.cache.get_product(self.name.lower(), pid)
                    if cached:
                        with self.lock:
                            self.stats["skipped_duplicates"] += 1
                        logger.debug(f"{self.name} | SKIP_REDIS | {pid}")
                        return None
                except Exception as e:
                    logger.debug(f"{self.name} | REDIS_CHECK_ERROR | {pid} | {e}")

            # =====================================================
            # Check PostgreSQL (if available)
            # =====================================================
            if self.db is not None:
                try:
                    existing = self.db.get_product_by_site_and_main_id(self.name.lower(), pid)
                    if existing:
                        with self.lock:
                            self.stats["skipped_duplicates"] += 1
                        logger.debug(f"{self.name} | SKIP_DB | {pid}")
                        return None
                except Exception as e:
                    logger.debug(f"{self.name} | DB_CHECK_ERROR | {pid} | {e}")

            # =====================================================
            # CRITICAL FIX: Ensure seen_products is a dict before use
            # =====================================================
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
                logger.info(f"{self.name} | SEEN_CONVERTED_IN_WORKER | {pid} | now dict with {len(seen_products)} items")

            # =====================================================
            # Check duplicate in session
            # =====================================================
            if self.handle_duplicate(pid, source, seen_products):
                logger.debug(f"{self.name} | DUPLICATE_SESSION | {pid}")
                return None

            # =====================================================
            # Parse product
            # =====================================================
            details = self.parse_product(url, pid)

            if details is None:
                logger.debug(f"{self.name} | PARSE_FAILED | {pid}")
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

            # =====================================================
            # Validate parsed product
            # =====================================================
            if not details.get("name"):
                logger.debug(f"{self.name} | NO_NAME | {pid}")
                return None
            
            if not details.get("price") or details.get("price") <= 0:
                logger.debug(f"{self.name} | NO_PRICE | {pid} | {details.get('price')}")
                return None

            # =====================================================
            # Save product
            # =====================================================
            details['main_product_id'] = pid
            
            if self._save_new_product(details, pid):
                with self.lock:
                    self.stats["new_products"] += 1
                write_product_result(details, self.name)
                self.performance.add_product()
                
                elapsed = round(time.time() - start_time, 3)
                logger.info(f"{self.name} | WORKER_SUCCESS | {pid} | {elapsed}s")
                return details

            return None

        except Exception as e:
            elapsed = round(time.time() - start_time, 3)
            logger.error(f"{self.name} | WORKER_ERROR | {worker_pid} | {e} | {elapsed}s")
            with self.lock:
                self.stats["errors"] += 1
            return None

    # =====================================================
    # CATEGORY SCRAPER - OPTIMIZED
    # =====================================================
    
    def scrape_category(self, category_name, category_url, seen_products):
        """Scrape a single category with progress tracking"""
        logger.info(f"{self.name} | CATEGORY_START | {category_name}")
        log_global(f"{self.name} | CATEGORY_START | {category_name}")
        
        # =====================================================
        # CRITICAL FIX: Ensure seen_products is a dict at entry
        # =====================================================
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
            logger.info(f"{self.name} | SEEN_CONVERTED_AT_ENTRY | {category_name} | now dict with {len(seen_products)} items")
        
        results = []
        category_start = time.time()

        first_page = get(category_url)
        if not first_page:
            logger.warning(f"{self.name} | CATEGORY_NO_FIRST_PAGE | {category_name}")
            return results

        total_pages = self.get_category_page_count(first_page)
        logger.info(f"{self.name} | CATEGORY_TOTAL_PAGES | {category_name} | {total_pages}")
        
        self.stats['current_category'] = category_name
        self.stats['total_pages'] = total_pages
        self.stats['current_page'] = 0
        self._update_progress()

        for page in range(1, total_pages + 1):
            page_start = time.time()
            url = f"{category_url}?page={page}"
            html = get(url)

            if not html:
                logger.warning(f"{self.name} | PAGE_EMPTY | {category_name} | {page}/{total_pages}")
                break

            self.stats['current_page'] = page
            self._update_progress()

            try:
                products = parse_spacenet_products(html, self.name)
            except Exception as e:
                logger.error(f"{self.name} | PAGE_PARSE_ERROR | {category_name} | {page} | {e}")
                continue

            if not products:
                logger.warning(f"{self.name} | PAGE_NO_PRODUCTS | {category_name} | {page}")
                break

            # =====================================================
            # Filter valid products
            # =====================================================
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
            self._update_progress()

            # =====================================================
            # Process products in parallel
            # =====================================================
            with ThreadPoolExecutor(max_workers=20) as executor:
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

            page_elapsed = round(time.time() - page_start, 2)
            logger.debug(f"{self.name} | PAGE_COMPLETE | {category_name} | {page}/{total_pages} | {page_elapsed}s")

        self.stats['completed_categories'] += 1
        self._update_progress()

        category_elapsed = round(time.time() - category_start, 2)
        logger.info(f"{self.name} | CATEGORY_DONE | {category_name} | {len(results)} products | {category_elapsed}s")
        return results

    # =====================================================
    # BRAND SCRAPER - OPTIMIZED
    # =====================================================
    
    def scrape_brands(self, seen_products):
        """Scrape products by brand as fallback"""
        # =====================================================
        # CRITICAL FIX: Ensure seen_products is a dict at entry
        # =====================================================
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
            logger.info(f"{self.name} | SEEN_CONVERTED_IN_BRANDS | now dict with {len(seen_products)} items")
        
        logger.info(f"{self.name} | BRAND_START")
        log_global(f"{self.name} | BRAND_START")
        results = []
        brand_start = time.time()

        self.stats['current_category'] = "Brands (Fallback)"
        self._update_progress()

        for brand in SEARCH_BRANDS:
            brand_loop_start = time.time()
            logger.info(f"{self.name} | BRAND_PROCESS | {brand}")
            
            self.stats['current_product'] = f"Brand: {brand}"
            self._update_progress()
            
            try:
                search_result = self.search(brand)
                products = search_result.get("products", [])
            except Exception as e:
                logger.error(f"{self.name} | BRAND_SEARCH_ERROR | {brand} | {e}")
                continue
                
            if not products:
                logger.debug(f"{self.name} | BRAND_NO_PRODUCTS | {brand}")
                continue

            # Filter valid products
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

            # Process products
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

            brand_elapsed = round(time.time() - brand_loop_start, 2)
            logger.info(f"{self.name} | BRAND_DONE | {brand} | {len(results)} collected | {brand_elapsed}s")

        brand_elapsed = round(time.time() - brand_start, 2)
        self._update_progress()
        logger.info(f"{self.name} | BRAND_COMPLETE | {len(results)} total | {brand_elapsed}s")
        return results

    # =====================================================
    # FULL SITE SCRAPER - MAIN ENTRY
    # =====================================================
    
    def scrape_site(self):
        """Main entry point for scraping Spacenet"""
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")
        
        self.start_time = time.time()
        results = []
        
        # =====================================================
        # CRITICAL FIX: Initialize seen_products as a DICT
        # =====================================================
        seen_products: Dict[str, List[str]] = {}
        
        # =====================================================
        # Initialize connections
        # =====================================================
        try:
            self._init_connections()
            if self.cache is None:
                logger.warning(f"{self.name} | REDIS_UNAVAILABLE | continuing without cache")
            if self.db is None:
                logger.warning(f"{self.name} | DB_UNAVAILABLE | continuing without database")
        except Exception as e:
            logger.error(f"{self.name} | INIT_ERROR | {e}")
        
        try:
            total_categories = len(CATEGORIES)
            
            # Reset stats
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
            
            self._update_progress()
            
            # =====================================================
            # Process categories
            # =====================================================
            for i, (name, url) in enumerate(CATEGORIES.items(), start=1):
                logger.info(f"{self.name} | CATEGORY_PROGRESS | {i}/{total_categories} | {name}")
                
                self.stats['current_category'] = name
                self._update_progress()
                
                res = self.scrape_category(name, url, seen_products)
                results.extend(res)
                
                self.stats['completed_categories'] = i
                self._update_progress()
            
            # =====================================================
            # Process brand fallback
            # =====================================================
            logger.info(f"{self.name} | BRAND_FALLBACK_START")
            brand_results = self.scrape_brands(seen_products)
            results.extend(brand_results)
            
            self.stats['completed_categories'] = total_categories
            self._update_progress()
            
        except KeyboardInterrupt:
            logger.warning(f"{self.name} | INTERRUPTED")
        except Exception as e:
            logger.error(f"{self.name} | SCRAPE_ERROR | {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._close_connections()
        
        # =====================================================
        # Final summary
        # =====================================================
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


# =====================================================
# FACTORY FUNCTION
# =====================================================

def create_spacenet_site() -> SpacenetSite:
    """Factory function to create Spacenet site instance"""
    return SpacenetSite()