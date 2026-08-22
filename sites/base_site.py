# sites/base_site.py
"""
Base Site class with batching, progress tracking, auto-flush on shutdown, 
universal connection management, and SmartRequester integration.
"""
import json
import time
import threading
from typing import List, Dict, Optional, Any
from datetime import datetime
import logging
import os
import sys

from core.logger import setup_logger
from core.shutdown_handler import register_flush_hook
from core.Web.smart_requester import SmartRequester, create_requester
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from Database.Redis.Configuration.redis_client import get_redis_client, get_redis_pool_stats

logger = setup_logger("base_site", "scraper")
logger.setLevel(logging.DEBUG)


class BaseSite:
    """
    Base site class with batching, auto-flush on shutdown, and universal connection management
    """

    name = "BASE"

    def __init__(self):
        logger.info(f"BASE_SITE_INIT | {self.name} | Starting BaseSite initialization... | instance_id={id(self)}")
        
        self.stats = {
            'new_products': 0,
            'skipped_duplicates': 0,
            'errors': 0,
            'redis_saved': 0,
            'postgres_saved': 0,
            'total_products_found': 0,
            'total_categories': 0,
            'completed_categories': 0,
            'current_category': '',
            'current_product': '',
            'current_page': 0,
            'total_pages': 0,
            'category_progress': 0
        }
        
        # =====================================================
        # SMART REQUESTER - Lazy initialized (only when needed)
        # =====================================================
        self._requester = None
        self._requester_initialized = False
        self._requester_config = {
            "requests_per_minute": 15,
            "max_retries": 3,
            "timeout": 30,
            "use_session": True,
            "use_referer": True,
            "verify_ssl": True,
            "delay_between_requests": (0.3, 1.5),
            "use_basic_headers": False,
        }
        
        # =====================================================
        # BATCHING CONFIGURATION
        # =====================================================
        self.pending_products: List[Dict] = []
        self.batch_size = 50
        self.batch_flush_interval = 30
        self._batch_lock = threading.Lock()
        self._product_counter = 0
        self._is_scraping = False
        self._last_batch_flush = time.time()
        self._batch_timer_thread = None
        self._stop_timer = False
        self._flush_count = 0
        
        # Category tracking
        self.current_category_index = 0
        self.total_categories_count = 0
        self.category_page_count = 0
        
        # =====================================================
        # Connection state
        # =====================================================
        self._connections_initialized = False
        self._connection_error = None
        self.cache = None
        self.db = None
        self.redis_client = None
        
        # Register flush hook for shutdown
        try:
            register_flush_hook(self.flush_pending_batch, f"{self.name}_scraper")
            logger.info(f"BASE_SITE_SHUTDOWN_HOOK | {self.name} | Registered flush hook")
        except Exception as e:
            logger.error(f"BASE_SITE_SHUTDOWN_HOOK_ERROR | {self.name} | {e}")
        
        # Start batch timer thread
        self._start_batch_timer()
        logger.info(f"BASE_SITE_INIT_DONE | {self.name} | instance_id={id(self)}")
    
    # =====================================================
    # SMART REQUESTER MANAGEMENT - LAZY INITIALIZATION
    # =====================================================
    
    @property
    def requester(self) -> SmartRequester:
        """
        Get or create the SmartRequester instance.
        Lazy initialization - only created when first accessed.
        """
        if not self._requester_initialized:
            self._init_requester()
        return self._requester
    
    def _init_requester(self):
        """Initialize the SmartRequester with site-specific config"""
        if self._requester_initialized:
            return self._requester
        
        # For SKYMIL, we don't need a requester
        if self.name == "SKYMIL":
            self._requester = None
            self._requester_initialized = True
            return self._requester
        
        site_name = self.name.lower() if self.name else "default"
        
        # Get site-specific config or use defaults
        config = self._requester_config.copy()
        
        # Allow sites to override config via get_requester_config method
        if hasattr(self, 'get_requester_config'):
            site_config = self.get_requester_config()
            # Remove site_name if present (it's passed separately)
            site_config.pop('site_name', None)
            config.update(site_config)
        
        # Remove site_name from the main config (just in case)
        config.pop('site_name', None)
        
        try:
            self._requester = create_requester(site_name=site_name, config=config)
            self._requester_initialized = True
            logger.info(f"{self.name} | REQUESTER_INITIALIZED | site={site_name} | basic_headers={config.get('use_basic_headers', False)}")
        except Exception as e:
            logger.error(f"{self.name} | REQUESTER_INIT_ERROR | {e}")
            import traceback
            traceback.print_exc()
            self._requester = None
            self._requester_initialized = True
        
        return self._requester
    
    def set_requester_config(self, **kwargs):
        """Update requester configuration"""
        self._requester_config.update(kwargs)
        # If already initialized, update the requester
        if self._requester_initialized and self._requester:
            try:
                self._requester.update_config(**kwargs)
                logger.info(f"{self.name} | REQUESTER_CONFIG_UPDATED | {kwargs}")
            except Exception as e:
                logger.error(f"{self.name} | REQUESTER_UPDATE_ERROR | {e}")
        else:
            logger.info(f"{self.name} | REQUESTER_CONFIG_STORED | {kwargs}")
    
    def get_requester_config(self) -> Dict:
        """Override this in child classes for site-specific configs"""
        return {}

    def _add_error(self, error_msg: str):
        """Helper method to add error to dashboard - override in child classes"""
        pass
    
    # =====================================================
    # BATCH TIMER
    # =====================================================
    
    def _start_batch_timer(self):
        """Start a background thread that flushes the batch at regular intervals"""
        if self._batch_timer_thread is None or not self._batch_timer_thread.is_alive():
            self._stop_timer = False
            self._batch_timer_thread = threading.Thread(
                target=self._batch_timer_loop,
                name=f"{self.name}_BatchTimer",
                daemon=True
            )
            self._batch_timer_thread.start()
            logger.info(f"{self.name} | BATCH_TIMER_STARTED | interval={self.batch_flush_interval}s")
    
    def _stop_batch_timer(self):
        """Stop the batch timer thread"""
        self._stop_timer = True
        if self._batch_timer_thread and self._batch_timer_thread.is_alive():
            self._batch_timer_thread.join(timeout=2)
            logger.info(f"{self.name} | BATCH_TIMER_STOPPED")
    
    def _batch_timer_loop(self):
        """Timer loop that flushes batch at regular intervals"""
        logger.info(f"{self.name} | BATCH_TIMER_LOOP_STARTED | interval={self.batch_flush_interval}s")
        loop_count = 0
        
        while not self._stop_timer:
            time.sleep(self.batch_flush_interval)
            loop_count += 1
            
            if not self._stop_timer:
                # Check if we need to flush
                with self._batch_lock:
                    batch_size = len(self.pending_products)
                    if batch_size > 0:
                        logger.info(f"{self.name} | BATCH_TIMER_TRIGGERED | loop={loop_count} | {batch_size} products")
                        self._flush_batch_locked()
                    else:
                        logger.debug(f"{self.name} | BATCH_TIMER_CHECK | loop={loop_count} | no products to flush")
        
        logger.info(f"{self.name} | BATCH_TIMER_LOOP_STOPPED | total_loops={loop_count}")
    
    # =====================================================
    # CONNECTION MANAGEMENT
    # =====================================================
    
    def _init_connections(self) -> bool:
        """Initialize database and cache connections."""
        if self._connections_initialized:
            return True
        
        logger.info(f"{self.name} | INIT_CONNECTIONS | Starting...")
        
        redis_ok = self._init_redis()
        db_ok = self._init_db()
        
        self._connections_initialized = True
        
        status_msg = (
            f"{self.name} | CONNECTION_STATUS | "
            f"Redis={'OK' if redis_ok else 'FAILED'} | "
            f"DB={'OK' if db_ok else 'FAILED'}"
        )
        
        if redis_ok and db_ok:
            logger.info(status_msg)
            return True
        else:
            logger.error(status_msg)
            self._connection_error = f"Redis={redis_ok}, DB={db_ok}"
            return False
    
    def _init_redis(self) -> bool:
        """Initialize Redis connection using shared pool."""
        try:
            logger.info(f"{self.name} | INIT_REDIS | Using shared connection pool...")
            redis_client = get_redis_client()
            
            for attempt in range(3):
                if redis_client.ping():
                    self.cache = ProductCache()
                    self.redis_client = redis_client
                    pool_stats = get_redis_pool_stats()
                    logger.info(f"{self.name} | REDIS_POOL_READY | max={pool_stats.get('max_connections', 0)}")
                    return True
                else:
                    logger.warning(f"{self.name} | REDIS_PING_ATTEMPT {attempt + 1}/3 | failed")
                    time.sleep(1)
            
            logger.error(f"{self.name} | REDIS_PING_FAILED | after 3 attempts")
            self.cache = None
            self.redis_client = None
            return False
                
        except Exception as e:
            logger.error(f"{self.name} | REDIS_ERROR | {e}")
            self.cache = None
            self.redis_client = None
            return False
    
    def _init_db(self) -> bool:
        """Initialize PostgreSQL connection."""
        try:
            logger.info(f"{self.name} | INIT_DB | Starting...")
            self.db = Database()
            
            if self.db and hasattr(self.db, 'conn') and self.db.conn:
                logger.info(f"{self.name} | DB_READY")
                return True
            else:
                logger.error(f"{self.name} | DB_CONNECTION_FAILED")
                self.db = None
                return False
                
        except Exception as e:
            logger.error(f"{self.name} | DB_ERROR | {e}")
            self.db = None
            return False
    
    def _close_connections(self):
        """Close database connections."""
        logger.info(f"{self.name} | CLOSE_CONNECTIONS | Starting...")
        
        try:
            if self.db:
                self.db.close()
                logger.info(f"{self.name} | DB_CLOSED")
        except Exception as e:
            logger.error(f"{self.name} | DB_CLOSE_ERROR | {e}")
        
        self.cache = None
        self.redis_client = None
        logger.info(f"{self.name} | CONNECTIONS_CLOSED")
    
    def _ensure_connections(self) -> bool:
        """Ensure connections are initialized."""
        if not self._connections_initialized:
            return self._init_connections()
        return True
    
    # =====================================================
    # PRODUCT EXISTENCE CHECK
    # =====================================================
    
    def _product_exists(self, product_id: str) -> bool:
        """Check if product already exists in Redis or PostgreSQL."""
        if not product_id:
            return False
        
        if not self._ensure_connections():
            logger.error(f"{self.name} | PRODUCT_EXISTS_ERROR | connections not available")
            return False
        
        # Check Redis first
        if self.cache is not None:
            try:
                cached = self.cache.get_product(self.name.lower(), product_id)
                if cached:
                    return True
            except Exception as e:
                logger.debug(f"{self.name} | CACHE_CHECK_ERROR | {product_id} | {e}")
        
        # Check PostgreSQL
        if self.db is not None:
            try:
                existing = self.db.get_product_by_site_and_main_id(self.name.lower(), product_id)
                if existing:
                    logger.debug(f"{self.name} | EXISTS_IN_DB | {product_id}")
                    return True
            except Exception as e:
                logger.debug(f"{self.name} | DB_CHECK_ERROR | {product_id} | {e}")
        
        return False
    
    # =====================================================
    # SAVE PRODUCT - THE MAIN METHOD ALL SITES CALL
    # =====================================================
    
    def _save_new_product(self, product: Dict, product_id: str) -> bool:
        """
        Save ONLY new product using batching.
        This is the MAIN method that all sites should call.
        """
        logger.info(f"SAVE_NEW_PRODUCT_CALLED | {self.name} | product_id={product_id} | instance_id={id(self)}")
        
        if not product_id or not product:
            logger.error(f"{self.name} | SAVE_INVALID | product_id={product_id}")
            return False
        
        if not self._ensure_connections():
            logger.error(f"{self.name} | SAVE_CONNECTION_ERROR | no connections")
            return False
        
        # Mark as new
        product['is_new'] = True
        product['main_product_id'] = product_id
        
        # STEP 1: SAVE TO REDIS IMMEDIATELY
        redis_saved = self._save_to_redis(product, product_id)
        if redis_saved:
            logger.debug(f"{self.name} | REDIS_SAVED | {product_id}")
        else:
            logger.warning(f"{self.name} | REDIS_SAVE_FAILED | {product_id}")
        
        # STEP 2: ADD TO BATCH FOR POSTGRESQL
        batch_added = self._add_to_batch(product)
        if batch_added:
            with self._batch_lock:
                batch_size = len(self.pending_products)
            logger.debug(f"{self.name} | BATCH_QUEUED | {product_id} | queue={batch_size}")
        else:
            logger.warning(f"{self.name} | BATCH_QUEUE_FAILED | {product_id}")
        
        return redis_saved or batch_added
    
    def _save_to_redis(self, product_data: dict, product_id: str = None) -> bool:
        """Save product to Redis cache."""
        try:
            if not self._ensure_connections():
                return False
            
            if self.cache is None:
                return False
            
            if not product_id:
                product_id = product_data.get('main_product_id')
            
            if not product_id:
                return False
            
            cache_payload = {
                "url": str(product_data.get("url", "")),
                "price": float(product_data.get("price", 0) or 0),
                "discount_price": float(product_data.get("discount_price", 0) or 0),
                "discount_amount": float(product_data.get("discount_amount", 0) or 0),
                "availability": str(product_data.get("availability", "OutOfStock") or "OutOfStock"),
                "special_id": str(product_data.get("special_id")) if product_data.get("special_id") else None,
                "price_valid_until": str(product_data.get("price_valid_until")) if product_data.get("price_valid_until") else None,
                "name": product_data.get("name", ""),
                "brand": product_data.get("brand"),
                "category": product_data.get("category"),
                "last_checked": int(time.time()),
                "is_enabled": True
            }
            
            self.cache.set_product(self.name.lower(), product_id, cache_payload, ttl=None)
            with self._batch_lock:
                self.stats['redis_saved'] += 1
            return True
                
        except Exception as e:
            logger.error(f"{self.name} | REDIS_SAVE_ERROR | {product_id or 'unknown'} | {e}")
            return False
    
    # =====================================================
    # BATCHING METHODS - THE CORE LOGIC
    # =====================================================
    
    def _add_to_batch(self, product_data: dict) -> bool:
        """Add product to pending batch."""
        with self._batch_lock:
            self.pending_products.append(product_data)
            self._product_counter += 1
            self.stats['current_product'] = product_data.get('main_product_id', '')
            
            if len(self.pending_products) >= self.batch_size:
                logger.info(f"{self.name} | BATCH_FULL | {len(self.pending_products)} products | flushing...")
                return self._flush_batch_locked()
            
            return True
    
    def _flush_batch(self) -> bool:
        """
        Public flush method - checks if lock is already held.
        """
        logger.info(f"FLUSH_BATCH_CALLED | {self.name} | thread={threading.current_thread().name} | instance_id={id(self)}")
        
        # Check if we already have the lock (called from flush_pending_batch)
        if self._batch_lock._is_owned():
            logger.info(f"FLUSH_BATCH_LOCK_ALREADY_HELD | {self.name}")
            return self._flush_batch_locked()
        else:
            logger.info(f"FLUSH_BATCH_ACQUIRING_LOCK | {self.name}")
            with self._batch_lock:
                return self._flush_batch_locked()
    
    def _flush_batch_locked(self) -> bool:
        """
        Internal flush method - assumes lock is already acquired.
        """
        logger.info(f"FLUSH_BATCH_LOCKED_ENTER | {self.name} | thread={threading.current_thread().name}")
        
        if not self.pending_products:
            logger.info(f"FLUSH_BATCH_EMPTY | {self.name}")
            return True
        
        batch_size = len(self.pending_products)
        logger.info(f"FLUSH_BATCH_COPY | {self.name} | {batch_size} products")
        
        batch_copy = self.pending_products.copy()
        self.pending_products = []
        self._last_batch_flush = time.time()
        self._flush_count += 1
        
        try:
            # Ensure connections are initialized
            logger.info(f"FLUSH_BATCH_CONNECTION_CHECK | {self.name}")
            
            if not self._ensure_connections():
                logger.error(f"FLUSH_BATCH_CONNECTION_ERROR | {self.name}")
                with self._batch_lock:
                    self.pending_products = batch_copy + self.pending_products
                return False
            
            if self.db is None:
                logger.error(f"FLUSH_BATCH_DB_NONE | {self.name}")
                with self._batch_lock:
                    self.pending_products = batch_copy + self.pending_products
                return False
            
            logger.info(f"FLUSH_BATCH_CONNECTION_OK | {self.name} | db={self.db is not None}")
            
            # Log the first few products for debugging
            first_products = []
            for p in batch_copy[:5]:
                first_products.append(p.get('main_product_id', 'unknown'))
            logger.info(f"FLUSH_BATCH_PRODUCTS | {self.name} | first_5={first_products}")
            
            # Make JSON serializable
            serialized_batch = []
            for idx, p in enumerate(batch_copy):
                if p:
                    try:
                        serialized = self._make_json_serializable(p)
                        if serialized:
                            serialized_batch.append(serialized)
                    except Exception as e:
                        logger.error(f"FLUSH_BATCH_SERIALIZE_ERROR | {self.name} | index {idx} | {e}")
            
            logger.info(f"FLUSH_BATCH_SERIALIZED | {self.name} | {len(serialized_batch)} valid products")

            if not serialized_batch:
                logger.warning(f"FLUSH_BATCH_NO_VALID | {self.name}")
                return True
            
            # =====================================================
            # ACTUAL DB SAVE
            # =====================================================
            logger.info(f"FLUSH_BATCH_SAVING | {self.name} | {len(serialized_batch)} products to DB | flush_count={self._flush_count}")
            logger.info("=" * 60)
            
            try:
                result = self.db.upsert_products(serialized_batch)
                logger.info(f"FLUSH_BATCH_RESULT | {self.name} | upsert_products returned: {result}")
                
                with self._batch_lock:
                    self.stats['postgres_saved'] += result
                    self.stats['new_products'] += result
                
                logger.info(f"FLUSH_BATCH_SUCCESS | {self.name} | {result}/{len(serialized_batch)} products saved to DB")
                logger.info("=" * 60)
                return True
                
            except Exception as db_error:
                logger.error(f"FLUSH_BATCH_DB_ERROR | {self.name} | {db_error}")
                import traceback
                traceback.print_exc()
                with self._batch_lock:
                    self.pending_products = batch_copy + self.pending_products
                return False
            
        except Exception as e:
            logger.error(f"FLUSH_BATCH_ERROR | {self.name} | {e}")
            import traceback
            traceback.print_exc()
            
            with self._batch_lock:
                self.pending_products = batch_copy + self.pending_products
            
            return self._fallback_individual_save(batch_copy)
    
    def _fallback_individual_save(self, products: List[Dict]) -> bool:
        """Fallback: save products individually if batch fails."""
        success = True
        failed_products = []
        
        if not self._ensure_connections():
            logger.error(f"{self.name} | FALLBACK_CONNECTION_ERROR")
            return False
        
        logger.info(f"{self.name} | FALLBACK_SAVE | saving {len(products)} products individually")
        
        for product in products:
            if not product:
                continue
            try:
                if self.db:
                    serialized = self._make_json_serializable(product)
                    result = self.db.upsert_products([serialized])
                    if result > 0:
                        with self._batch_lock:
                            self.stats['postgres_saved'] += 1
                            self.stats['new_products'] += 1
                    else:
                        failed_products.append(product)
                        success = False
                else:
                    failed_products.append(product)
                    success = False
            except Exception as e:
                failed_products.append(product)
                success = False
                logger.error(f"{self.name} | INDIVIDUAL_SAVE_ERROR | {e}")
        
        if failed_products:
            logger.error(f"{self.name} | FAILED_PRODUCTS | {len(failed_products)}")
            self._log_failed_products(failed_products)
        
        return success
    
    def _log_failed_products(self, products: list):
        """Log failed products to file."""
        try:
            os.makedirs("logs", exist_ok=True)
            with open(f"logs/failed_products_{self.name}_{datetime.now().strftime('%Y%m%d')}.log", "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                f.write(f"Site: {self.name}\n")
                f.write(f"Failed Products: {len(products)}\n")
                for p in products[:10]:
                    f.write(f"  - {json.dumps(p, default=str)[:200]}\n")
                if len(products) > 10:
                    f.write(f"  ... and {len(products) - 10} more\n")
                f.write(f"{'='*60}\n")
        except:
            pass
    
    def _make_json_serializable(self, obj: Any) -> Any:
        """Convert non-serializable objects to JSON serializable format."""
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
    
    def flush_pending_batch(self) -> bool:
        """Flush pending batch (called on shutdown)."""
        if not hasattr(self, '_batch_lock'):
            logger.warning(f"{self.name} | NO_BATCH_LOCK | Skipping flush")
            return True
        
        # Check if we already have the lock
        if self._batch_lock._is_owned():
            logger.info(f"{self.name} | FLUSHING_PENDING_LOCKED | {len(self.pending_products)} products")
            return self._flush_batch_locked()
        else:
            with self._batch_lock:
                if self.pending_products:
                    logger.info(f"{self.name} | FLUSHING_PENDING | {len(self.pending_products)} products")
                    return self._flush_batch_locked()
                return True
    
    def get_pending_batch_size(self) -> int:
        """Get the number of products waiting in the batch."""
        with self._batch_lock:
            return len(self.pending_products)
    
    def stop_timer_and_flush(self) -> bool:
        """Stop the timer and flush any remaining products."""
        logger.info(f"{self.name} | STOPPING_TIMER_AND_FLUSHING")
        self._stop_batch_timer()
        return self.flush_pending_batch()
    
    # =====================================================
    # PROGRESS TRACKING METHODS
    # =====================================================
    
    def update_progress(self, category_name: str = None, page: int = None, total_pages: int = None):
        """Update progress stats."""
        if category_name:
            self.stats['current_category'] = category_name
        if page is not None:
            self.stats['current_page'] = page
        if total_pages is not None:
            self.stats['total_pages'] = total_pages
        
        if self.stats['total_pages'] > 0 and self.stats['current_page'] > 0:
            self.stats['category_progress'] = (self.stats['current_page'] / self.stats['total_pages']) * 100
        elif self.stats['completed_categories'] > 0 and self.stats['total_categories'] > 0:
            self.stats['category_progress'] = (self.stats['completed_categories'] / self.stats['total_categories']) * 100
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def start_category(self, category_name: str, total_pages: int = 0):
        """Start processing a new category."""
        self.stats['current_category'] = category_name
        self.stats['total_pages'] = total_pages
        self.stats['current_page'] = 0
        self.stats['category_progress'] = 0
        self.current_category_index += 1
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def update_category_page(self, page: int):
        """Update current page within a category."""
        self.stats['current_page'] = page
        if self.stats['total_pages'] > 0:
            self.stats['category_progress'] = (page / self.stats['total_pages']) * 100
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def complete_category(self):
        """Mark current category as complete."""
        self.stats['completed_categories'] = self.stats.get('completed_categories', 0) + 1
        if self.stats['total_categories'] > 0:
            self.stats['category_progress'] = (self.stats['completed_categories'] / self.stats['total_categories']) * 100
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def complete_all_categories(self):
        """Mark all categories as complete."""
        self.stats['completed_categories'] = self.stats['total_categories']
        self.stats['category_progress'] = 100
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def get_stats(self) -> Dict:
        """Get current stats."""
        with self._batch_lock:
            return self.stats.copy()
    
    def reset_stats(self):
        """Reset all stats for a new cycle."""
        with self._batch_lock:
            self.stats = {
                'new_products': 0,
                'skipped_duplicates': 0,
                'errors': 0,
                'redis_saved': 0,
                'postgres_saved': 0,
                'total_products_found': 0,
                'total_categories': 0,
                'completed_categories': 0,
                'current_category': '',
                'current_product': '',
                'current_page': 0,
                'total_pages': 0,
                'category_progress': 0
            }
            self.pending_products = []
            self._product_counter = 0
            self.current_category_index = 0
            self.total_categories_count = 0
            self.category_page_count = 0
    
    def get_connection_status(self) -> Dict[str, bool]:
        """Get current connection status."""
        return {
            'redis': self.cache is not None,
            'db': self.db is not None,
            'initialized': self._connections_initialized
        }
    
    def log_final_summary(self, results: List[Dict]):
        """Log final summary of what was saved."""
        total_new = self.stats.get('new_products', 0)
        total_skipped = self.stats.get('skipped_duplicates', 0)
        total_errors = self.stats.get('errors', 0)
        redis_saved = self.stats.get('redis_saved', 0)
        postgres_saved = self.stats.get('postgres_saved', 0)
        
        logger.info("=" * 60)
        logger.info(f"FINAL SUMMARY | {self.name}")
        logger.info("=" * 60)
        logger.info(f"  New products found: {len(results)}")
        logger.info(f"  New products saved: {total_new}")
        logger.info(f"  Skipped (existing): {total_skipped}")
        logger.info(f"  Errors: {total_errors}")
        logger.info(f"  Redis saves: {redis_saved}")
        logger.info(f"  PostgreSQL saves: {postgres_saved}")
        
        # Log any pending products that weren't saved
        with self._batch_lock:
            pending = len(self.pending_products)
            if pending > 0:
                logger.warning(f"  PENDING PRODUCTS: {pending} (not saved!)")
                for p in self.pending_products[:5]:
                    logger.warning(f"    - {p.get('main_product_id', 'unknown')}")
        
        logger.info("=" * 60)
    
    # =====================================================
    # SITE METHODS (To be overridden)
    # =====================================================
    
    def scrape_site(self):
        raise NotImplementedError

    def search(self, query):
        raise NotImplementedError

    def parse_product(self, url):
        raise NotImplementedError

    def extract_categories(self):
        raise NotImplementedError