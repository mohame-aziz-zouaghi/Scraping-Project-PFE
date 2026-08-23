# sites/base_site.py
"""
Base Site class with batching (via BatchManager), progress tracking, auto-flush on shutdown, 
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
from core.batch_manager import get_batch_manager, shutdown_batch_manager
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from Database.Redis.Configuration.redis_client import get_redis_client, get_redis_pool_stats

logger = setup_logger("base_site", "scraper")
logger.setLevel(logging.DEBUG)


class BaseSite:
    """
    Base site class with batching (via BatchManager), auto-flush on shutdown, 
    and universal connection management.
    """

    name = "BASE"

    # =====================================================
    # DEFAULT REQUESTER CONFIGURATION - Used by all sites
    # =====================================================
    DEFAULT_REQUESTER_CONFIG = {
        "requests_per_minute": 15,
        "max_retries": 3,
        "timeout": 30,
        "use_session": True,
        "use_referer": True,
        "verify_ssl": True,
        "delay_between_requests": (0.3, 1.5),
        "use_basic_headers": False,
    }

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
        self._requester_config = self.DEFAULT_REQUESTER_CONFIG.copy()
        
        # =====================================================
        # BATCH MANAGER - Centralized batching for all sites
        # =====================================================
        self._batch_manager = get_batch_manager()
        logger.info(f"{self.name} | BATCH_MANAGER_ACQUIRED | instance_id={id(self._batch_manager)}")
        
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
        
        # =====================================================
        # NO BATCH TIMER - BatchManager handles timing centrally
        # =====================================================
        
        # =====================================================
        # INITIALIZE REQUESTER WITH SITE-SPECIFIC CONFIG
        # =====================================================
        self._init_requester()
        
        logger.info(f"BASE_SITE_INIT_DONE | {self.name} | instance_id={id(self)}")
    
    # =====================================================
    # SMART REQUESTER MANAGEMENT - Single initialization
    # =====================================================
    
    @property
    def requester(self) -> SmartRequester:
        """Get the SmartRequester instance (already initialized)."""
        return self._requester
    
    def _init_requester(self):
        """Initialize the SmartRequester with site-specific config - called ONCE in __init__"""
        if self._requester_initialized:
            return self._requester
        
        # For SKYMIL, we don't need a requester (loads from JSON)
        if self.name == "SKYMIL":
            self._requester = None
            self._requester_initialized = True
            return self._requester
        
        site_name = self.name.lower() if self.name else "default"
        
        # Start with default config
        config = self.DEFAULT_REQUESTER_CONFIG.copy()
        
        # ONLY CALL get_requester_config() ONCE - single source of truth
        if hasattr(self, 'get_requester_config'):
            site_config = self.get_requester_config()
            if site_config:
                # Remove site_name if present (it's passed separately)
                site_config.pop('site_name', None)
                # Update config with site-specific values
                config.update(site_config)
                logger.info(f"{self.name} | REQUESTER_CONFIG_LOADED | {site_config}")
        
        # Store the final config
        self._requester_config = config.copy()
        
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
    
    def update_requester_config(self, **kwargs):
        """
        Dynamically update requester configuration at runtime.
        Only use this for temporary changes or debugging.
        """
        if not self._requester_initialized:
            self._init_requester()
            return
        
        # Update stored config
        self._requester_config.update(kwargs)
        
        # Update the requester if it exists
        if self._requester:
            try:
                self._requester.update_config(**kwargs)
                logger.info(f"{self.name} | REQUESTER_CONFIG_UPDATED | {kwargs}")
            except Exception as e:
                logger.error(f"{self.name} | REQUESTER_UPDATE_ERROR | {e}")
        else:
            logger.warning(f"{self.name} | REQUESTER_NOT_INITIALIZED | cannot update config")
    
    def get_requester_config(self) -> Dict:
        """
        Override this in child classes for site-specific configs.
        This is called ONCE during initialization.
        """
        return {}

    def _add_error(self, error_msg: str):
        """Helper method to add error to dashboard - override in child classes"""
        pass
    
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
    # SAVE PRODUCT - USING BATCHMANAGER
    # =====================================================
    
    def _save_new_product(self, product: Dict, product_id: str) -> bool:
        """
        Save ONLY new product using BatchManager.
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
        
        # =====================================================
        # STEP 2: ADD TO BATCH MANAGER (Centralized batching)
        # =====================================================
        site_key = self.name.lower()
        batch_added = self._batch_manager.add_product(site_key, product, product_id)
        
        if batch_added:
            pending = self._batch_manager.get_pending_count(site_key)
            logger.debug(f"{self.name} | BATCH_QUEUED | {product_id} | queue={pending}")
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
            
            # Update stats
            self.stats['redis_saved'] += 1
            
            return True
                
        except Exception as e:
            logger.error(f"{self.name} | REDIS_SAVE_ERROR | {product_id or 'unknown'} | {e}")
            return False
    
    # =====================================================
    # BATCHING METHODS - Delegated to BatchManager
    # =====================================================
    
    def get_pending_batch_size(self) -> int:
        """Get the number of pending products for this site."""
        return self._batch_manager.get_pending_count(self.name.lower())
    
    def flush_pending_batch(self) -> bool:
        """Flush pending batch for this site."""
        return self._batch_manager.flush_site(self.name.lower())
    
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
        return self.stats.copy()
    
    def reset_stats(self):
        """Reset all stats for a new cycle."""
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
        # Get stats from BatchManager
        site_stats = self._batch_manager.get_stats(self.name.lower())
        
        total_new = self.stats.get('new_products', 0)
        total_skipped = self.stats.get('skipped_duplicates', 0)
        total_errors = self.stats.get('errors', 0)
        redis_saved = self.stats.get('redis_saved', 0)
        postgres_saved = site_stats.get('saved', 0) if site_stats else 0
        pending = self.get_pending_batch_size()
        
        logger.info("=" * 60)
        logger.info(f"FINAL SUMMARY | {self.name}")
        logger.info("=" * 60)
        logger.info(f"  New products found: {len(results)}")
        logger.info(f"  New products saved: {total_new}")
        logger.info(f"  Skipped (existing): {total_skipped}")
        logger.info(f"  Errors: {total_errors}")
        logger.info(f"  Redis saves: {redis_saved}")
        logger.info(f"  PostgreSQL saves: {postgres_saved}")
        
        if pending > 0:
            logger.warning(f"  ⚠️ PENDING PRODUCTS: {pending} (not saved!)")
        else:
            logger.info(f"  ✅ No pending products")
        
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


# =====================================================
# SHUTDOWN HOOK
# =====================================================

def shutdown_batch_manager_hook():
    """Shutdown hook for BatchManager."""
    try:
        shutdown_batch_manager()
        logger.info("BASE_SITE | BATCH_MANAGER_SHUTDOWN | complete")
    except Exception as e:
        logger.error(f"BASE_SITE | BATCH_MANAGER_SHUTDOWN_ERROR | {e}")


# Register shutdown hook
try:
    from core.shutdown_handler import register_shutdown_hook
    register_shutdown_hook(shutdown_batch_manager_hook, "batch_manager_shutdown")
    logger.info("BASE_SITE | BATCH_MANAGER_SHUTDOWN_HOOK | registered")
except Exception as e:
    logger.warning(f"BASE_SITE | BATCH_MANAGER_SHUTDOWN_HOOK_ERROR | {e}")