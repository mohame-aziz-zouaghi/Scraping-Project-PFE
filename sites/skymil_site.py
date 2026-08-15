# sites/skymil.py
import json
import time
import threading
from typing import Dict, List, Optional, Any, Set
from tqdm import tqdm
from core.load_ids import load_existing_product_ids
from core.logger import setup_logger, log_global, write_product_result
from core.performance import PerformanceTracker
from core.Web.product_schema import create_product_schema
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from sites.base_site import BaseSite

logger = setup_logger("skymil", "scraper")
dup_logger = setup_logger("skymil", "duplication")


class SkymilSite(BaseSite):
    """Skymil scraper - ONLY adds NEW products, skips existing ones"""

    def __init__(self):
        super().__init__()
        
        self.name = "SKYMIL"
        self.scrape_interval = 86400  # 24 hours between scrapes
        self.performance = PerformanceTracker()

        # FILE PATHS
        self.products_file = "config/SkymilProducts.json"
        self.brands_file = "config/SkymilBrands.json"
        self.categories_file = "config/SkymilCategories.json"
        
        # Thread safety
        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        
        # Connections
        self.cache = None
        self.db = None
        self.redis_client = None

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
    # LOAD DATA
    # =====================================================
    
    def load_data(self):
        logger.info(f"{self.name} | LOAD_DATA")

        with open(self.products_file, "r", encoding="utf-8") as f:
            products = json.load(f)

        with open(self.brands_file, "r", encoding="utf-8") as f:
            brands = json.load(f)

        with open(self.categories_file, "r", encoding="utf-8") as f:
            categories = json.load(f)

        # MAPS
        brand_map = {b["id"]: b["name"] for b in brands}
        category_map = {c["id"]: c["name"] for c in categories}

        logger.info(
            f"{self.name} | DATA_LOADED | products={len(products)} | brands={len(brand_map)} | categories={len(category_map)}"
        )

        return products, brand_map, category_map

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
            
            saved_to_redis = False
            saved_to_pg = False
            
            # Save to Redis
            if self.cache:
                self.cache.set_product(self.name.lower(), product_id, cache_payload, ttl=None)
                saved_to_redis = True
                with self.lock:
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
                saved_to_pg = True
                with self.lock:
                    self.stats["postgres_saved"] += 1
            
            if saved_to_redis and saved_to_pg:
                with self.lock:
                    self.stats["new_products"] += 1
                write_product_result(product, self.name)
                return True
            else:
                logger.warning(f"{self.name} | PARTIAL_SAVE | {product_id}")
                return False
                
        except Exception as e:
            logger.error(f"{self.name} | SAVE_ERROR | {product_id} | {e}")
            return False

    # =====================================================
    # TRANSFORM PRODUCT
    # =====================================================
    
    def transform_product(self, p, brand_map, category_map):
        try:
            product_id = p.get("id")
            name = p.get("name")

            if not product_id or not name:
                return None

            # URL
            slug = p.get("slug")
            url = f"https://skymil-shop.com/produit/{slug}"

            # ---------------------------------
            # INIT PRODUCT SCHEMA
            # ---------------------------------
            product = create_product_schema(
                self.name,
                url,
                product_id
            )

            # ---------------------------------
            # BASIC FIELDS
            # ---------------------------------
            product["name"] = name

            # ---------------------------------
            # PRICE LOGIC
            # ---------------------------------
            price = p.get("price", 0)
            old_price = p.get("old_price")

            if old_price and old_price > price:
                product["price"] = old_price
                product["discount_price"] = price
                product["discount_amount"] = round(old_price - price, 2)
            else:
                product["price"] = price
                product["discount_price"] = 0
                product["discount_amount"] = 0

            # ---------------------------------
            # BRAND / CATEGORY
            # ---------------------------------
            product["brand"] = brand_map.get(p.get("brand_id"), "Unknown")
            product["category"] = category_map.get(p.get("category_id"), "Unknown")

            # ---------------------------------
            # IMAGES
            # ---------------------------------
            main_image = p.get("image_url")
            images = p.get("images", [])

            all_images = []

            if main_image:
                all_images.append(main_image)

            if images:
                all_images.extend(images)

            product["images"] = all_images
            product["main_image"] = main_image

            # ---------------------------------
            # DESCRIPTION
            # ---------------------------------
            product["description"] = p.get("description", "")

            # ---------------------------------
            # SHORT DESCRIPTION (DICT FORMAT)
            # ---------------------------------
            specs = p.get("specs", {})

            clean_specs = {}
            seen_keys = set()

            for k, v in specs.items():
                if not k or not v:
                    continue

                key = str(k).strip().lower()
                value = str(v).strip()

                if key in seen_keys:
                    continue

                seen_keys.add(key)

                clean_specs[key] = value

            product["short_description"] = clean_specs

            # ---------------------------------
            # AVAILABILITY
            # ---------------------------------
            product["availability"] = (
                "InStock" if p.get("in_stock") else "OutOfStock"
            )
            
            # ---------------------------------
            # SKU | Special ID
            # ---------------------------------
            product["special_id"] = p.get("reference")

            return product

        except Exception as e:
            logger.error(f"{self.name} | TRANSFORM_ERROR | {e}")
            return None

    # =====================================================
    # PROCESS SINGLE PRODUCT
    # =====================================================
    
    def process_product(self, product_data: Dict) -> Optional[Dict]:
        """Process single product - ONLY add if NEW, skip if exists"""
        try:
            if product_data is None:
                logger.warning(f"{self.name} | SKIP_NONE")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            if not isinstance(product_data, dict):
                logger.warning(f"{self.name} | SKIP_INVALID")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            product_id = product_data.get("main_product_id")
            
            if not product_id:
                logger.warning(f"{self.name} | SKIP_NO_ID")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            product_id = str(product_id)
            
            # Check duplicates within session
            with self.lock:
                if product_id in self.seen_products:
                    self.stats["skipped_duplicates"] += 1
                    return None
                self.seen_products.add(product_id)
            
            # =====================================================
            # CHECK REDIS FIRST (FAST CACHE)
            # =====================================================
            if self.cache:
                cached = self.cache.get_product(self.name.lower(), product_id)
                if cached:
                    with self.lock:
                        self.stats["skipped_duplicates"] += 1
                    return None
            
            # =====================================================
            # CHECK POSTGRESQL (FALLBACK)
            # =====================================================
            if self.db:
                existing = self.db.get_product_by_site_and_main_id(self.name.lower(), product_id)
                if existing:
                    with self.lock:
                        self.stats["skipped_duplicates"] += 1
                    return None
            
            # Save new product
            if self._save_new_product(product_data, product_id):
                return product_data
            else:
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
        except Exception as e:
            logger.error(f"{self.name} | PRODUCT_ERROR | {e}")
            with self.lock:
                self.stats["errors"] += 1
            return None

    # =====================================================
    # MAIN SCRAPER (TRANSFORMER)
    # =====================================================


    def scrape_site(self):
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")
        
        results = []
        
        try:
            self._init_connections()
        except Exception as e:
            logger.error(f"{self.name} | CONNECTION_FAILED | {e}")
            return {"site": self.name, "total_products": 0, "products": [], "stats": self.stats}
        
        try:
            products, brand_map, category_map = self.load_data()
            
            total_products = len(products)
            self.stats = {
                "new_products": 0,
                "skipped_duplicates": 0,
                "errors": 0,
                "redis_saved": 0,
                "postgres_saved": 0,
                "total_products_found": total_products,
                "completed_categories": 1,
                "total_categories": 1,
                "current_category": "Processing Skymil products",
                "current_product": "",
                "current_page": 0,
                "total_pages": 1,
                "category_progress": 0
            }
            self.seen_products.clear()
            
            logger.info(f"{self.name} | STARTING | total_products={total_products}")
            
            # Initial stats update
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
            
            for i, p in enumerate(products):
                product_id = str(p.get("id"))
                
                # Update product progress
                self.stats['current_product'] = f"{i+1}/{total_products}"
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                # =====================================================
                # CHECK REDIS FIRST (FAST CACHE)
                # =====================================================
                if self.cache:
                    cached = self.cache.get_product(self.name.lower(), product_id)
                    if cached:
                        # Product exists in Redis, skip it
                        with self.lock:
                            self.stats["skipped_duplicates"] += 1
                        continue
                
                # =====================================================
                # CHECK POSTGRESQL (FALLBACK)
                # =====================================================
                if self.db:
                    existing = self.db.get_product_by_site_and_main_id(self.name.lower(), product_id)
                    if existing:
                        # Product exists in PostgreSQL, skip it
                        with self.lock:
                            self.stats["skipped_duplicates"] += 1
                        continue
                
                # Validate product
                if not p.get("name"):
                    continue
                
                if p.get("price", 0) <= 0:
                    continue
                
                # Transform product
                product = self.transform_product(p, brand_map, category_map)
                
                if not product:
                    continue
                
                # Set main_product_id for saving
                product['main_product_id'] = product_id
                
                # Process and save the product
                saved_product = self.process_product(product)
                if saved_product:
                    results.append(saved_product)
                    self.performance.add_product()
                
                # Update new products count
                self.stats['new_products'] = len(results)
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                if self.performance.total_products % 50 == 0:
                    speed = self.performance.get_speed()
                    logger.info(f"{self.name} | PROGRESS | {self.performance.total_products}/{total_products} | speed={speed:.2f} prod/s")
            
            # Complete all categories
            self.stats['completed_categories'] = 1
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
            
        except Exception as e:
            logger.error(f"{self.name} | SCRAPE_ERROR | {e}")
            import traceback
            traceback.print_exc()
        finally:
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
    
    def search(self, query):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return
    
    def parse_product(self, url, product_id):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return
    
    def extract_categories(self):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return


def create_skymil_site() -> SkymilSite:
    return SkymilSite()