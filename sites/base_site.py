# sites/base_site.py
"""
Base Site class with batching, progress tracking, and auto-flush on shutdown
"""
import json
import time
import threading
from typing import List, Dict, Optional, Any
from datetime import datetime

from core.logger import setup_logger
from core.shutdown_handler import register_flush_hook
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database

logger = setup_logger("base_site", "scraper")


class BaseSite:
    """
    Base site class with batching and auto-flush on shutdown
    """

    name = "BASE"

    def __init__(self):
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
        
        # Batch collection
        self.pending_products: List[Dict] = []
        self.batch_size = 50
        self.product_counter = 0
        self._batch_lock = threading.Lock()
        self._is_scraping = False
        
        # Category tracking
        self.current_category_index = 0
        self.total_categories_count = 0
        self.category_page_count = 0
        
        # Register flush hook for shutdown
        try:
            register_flush_hook(self.flush_pending_batch, f"{self.name}_scraper")
        except:
            pass
    
    def update_progress(self, category_name: str = None, page: int = None, total_pages: int = None):
        """Update progress stats - common method for all sites"""
        if category_name:
            self.stats['current_category'] = category_name
        if page is not None:
            self.stats['current_page'] = page
        if total_pages is not None:
            self.stats['total_pages'] = total_pages
        
        # Calculate category progress
        if self.stats['total_pages'] > 0 and self.stats['current_page'] > 0:
            self.stats['category_progress'] = (self.stats['current_page'] / self.stats['total_pages']) * 100
        elif self.stats['completed_categories'] > 0 and self.stats['total_categories'] > 0:
            self.stats['category_progress'] = (self.stats['completed_categories'] / self.stats['total_categories']) * 100
        
        # Call update stats hook
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def start_category(self, category_name: str, total_pages: int = 0):
        """Start processing a new category"""
        self.stats['current_category'] = category_name
        self.stats['total_pages'] = total_pages
        self.stats['current_page'] = 0
        self.stats['category_progress'] = 0
        self.current_category_index += 1
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def update_category_page(self, page: int):
        """Update current page within a category"""
        self.stats['current_page'] = page
        if self.stats['total_pages'] > 0:
            self.stats['category_progress'] = (page / self.stats['total_pages']) * 100
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def complete_category(self):
        """Mark current category as complete"""
        self.stats['completed_categories'] = self.stats.get('completed_categories', 0) + 1
        if self.stats['total_categories'] > 0:
            self.stats['category_progress'] = (self.stats['completed_categories'] / self.stats['total_categories']) * 100
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def complete_all_categories(self):
        """Mark all categories as complete"""
        self.stats['completed_categories'] = self.stats['total_categories']
        self.stats['category_progress'] = 100
        
        if hasattr(self, '_update_stats'):
            self._update_stats(self.stats)
    
    def scrape_site(self):
        raise NotImplementedError

    def search(self, query):
        raise NotImplementedError

    def parse_product(self, url):
        raise NotImplementedError

    def extract_categories(self):
        raise NotImplementedError
    
    # =====================================================
    # BATCHING METHODS
    # =====================================================
    
    def _add_to_batch(self, product_data: dict) -> bool:
        with self._batch_lock:
            self.pending_products.append(product_data)
            self.product_counter += 1
            
            self.stats['current_product'] = product_data.get('main_product_id', '')
            
            if len(self.pending_products) >= self.batch_size:
                return self._flush_batch()
            
            self._save_to_redis(product_data)
            return True
    
    def _flush_batch(self) -> bool:
        with self._batch_lock:
            if not self.pending_products:
                return True
            
            batch_copy = self.pending_products.copy()
            self.pending_products = []
        
        try:
            db = Database()
            result = db.upsert_products(batch_copy)
            
            with self._batch_lock:
                self.stats['postgres_saved'] += result
                self.stats['new_products'] += result
            
            logger.info(f"{self.name} | BATCH_FLUSH | {result} products saved")
            return True
            
        except Exception as e:
            logger.error(f"{self.name} | BATCH_FLUSH_ERROR | {e}")
            return self._fallback_individual_save(batch_copy)
    
    def _fallback_individual_save(self, products: List[Dict]) -> bool:
        success = True
        failed_products = []
        
        for product in products:
            try:
                db = Database()
                result = db.upsert_products([product])
                if result > 0:
                    with self._batch_lock:
                        self.stats['postgres_saved'] += 1
                        self.stats['new_products'] += 1
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
        try:
            import os
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
    
    def _save_to_redis(self, product_data: dict):
        try:
            cache = ProductCache()
            main_id = product_data.get('main_product_id')
            if main_id:
                cache.set_product(self.name.lower(), main_id, product_data)
                with self._batch_lock:
                    self.stats['redis_saved'] += 1
        except Exception as e:
            logger.error(f"{self.name} | REDIS_SAVE_ERROR | {e}")
    
    def flush_pending_batch(self) -> bool:
        if not hasattr(self, '_batch_lock'):
            logger.warning(f"{self.name} | NO_BATCH_LOCK | Skipping flush")
            return True
        
        with self._batch_lock:
            if self.pending_products:
                logger.info(f"{self.name} | FLUSHING_PENDING | {len(self.pending_products)} products")
                return self._flush_batch()
            return True
    
    def _product_exists(self, product_id: str) -> bool:
        try:
            cache = ProductCache()
            if cache.exists(self.name.lower(), product_id):
                return True
        except:
            pass
        
        try:
            db = Database()
            return db.get_product_by_site_and_main_id(self.name.lower(), product_id) is not None
        except:
            return False
    
    def get_stats(self) -> Dict:
        if hasattr(self, '_batch_lock'):
            with self._batch_lock:
                return self.stats.copy()
        return self.stats.copy()
    
    def reset_stats(self):
        """Reset all stats for a new cycle"""
        if hasattr(self, '_batch_lock'):
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
                self.product_counter = 0
                self.current_category_index = 0
                self.total_categories_count = 0
                self.category_page_count = 0
        else:
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
            self.product_counter = 0