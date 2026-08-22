# core/batch_manager.py
"""
Central Batch Manager - Singleton that handles batching for all sites
"""
import json
import time
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging
import os
import sys

from core.logger import setup_logger

logger = setup_logger("batch_manager", "scraper")
logger.setLevel(logging.DEBUG)


class BatchManager:
    """
    Central batch manager that handles batching for all sites.
    Singleton pattern - only one instance exists.
    """
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(BatchManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.batch_size = 10  # Reduced to 10 for testing
        self.flush_interval = 10  # 10 seconds flush interval
        
        # Per-site pending products
        self.pending_products: Dict[str, List[Dict]] = {}
        self._batch_locks: Dict[str, threading.Lock] = {}
        self.stats: Dict[str, Dict] = {}
        
        # Timer thread
        self._timer_thread = None
        self._stop_timer = False
        self._timer_lock = threading.Lock()
        self._is_shutting_down = False
        
        # DB connection
        self.db = None
        
        logger.info("=" * 60)
        logger.info(f"BATCH_MANAGER_INITIALIZED | batch_size={self.batch_size}, flush_interval={self.flush_interval}s | instance_id={id(self)}")
        logger.info("=" * 60)
        
        self._start_timer()
    
    def _get_lock(self, site: str) -> threading.Lock:
        """Get or create a lock for a site."""
        site_key = site.lower()
        if site_key not in self._batch_locks:
            self._batch_locks[site_key] = threading.Lock()
        return self._batch_locks[site_key]
    
    def _get_pending(self, site: str) -> List[Dict]:
        """Get or create pending list for a site."""
        site_key = site.lower()
        if site_key not in self.pending_products:
            self.pending_products[site_key] = []
            self.stats[site_key] = {
                'saved': 0,
                'queued': 0,
                'flushed': 0,
                'errors': 0,
                'total_added': 0,
                'last_flush': None
            }
        return self.pending_products[site_key]
    
    def add_product(self, site: str, product: Dict, product_id: str) -> bool:
        """
        Add a product to the batch queue for a site.
        Returns True if added successfully.
        """
        site_key = site.lower()
        lock = self._get_lock(site_key)
        
        with lock:
            pending = self._get_pending(site_key)
            pending.append(product)
            self.stats[site_key]['queued'] += 1
            self.stats[site_key]['total_added'] += 1
            
            queue_size = len(pending)
            logger.debug(f"BATCH_MANAGER | {site_key} | ADDED | product_id={product_id} | queue={queue_size}")
            
            # Check if we should flush (batch full)
            if queue_size >= self.batch_size:
                logger.info(f"BATCH_MANAGER | {site_key} | BATCH_FULL | {queue_size} products | flushing...")
                # Call flush - it will handle its own locking
                # Release the lock before calling flush to avoid deadlock
                pass
        
        # Call flush outside the lock
        if queue_size >= self.batch_size:
            return self.flush_site(site_key)
        
        return True
    
    def flush_site(self, site: str) -> bool:
        """Flush all pending products for a site."""
        site_key = site.lower()
        lock = self._get_lock(site_key)
        
        logger.info(f"BATCH_MANAGER | {site_key} | FLUSH_SITE_CALLED")
        
        with lock:
            pending = self._get_pending(site_key)
            if not pending:
                logger.debug(f"BATCH_MANAGER | {site_key} | FLUSH_SKIP | no pending products")
                return True
            
            queue_size = len(pending)
            logger.info("=" * 60)
            logger.info(f"BATCH_MANAGER | {site_key} | FLUSH_START | {queue_size} products")
            
            # Copy products and clear queue
            batch_copy = pending.copy()
            pending.clear()
            self.stats[site_key]['flushed'] += 1
            self.stats[site_key]['last_flush'] = datetime.now().isoformat()
            
            # Log product IDs being flushed
            product_ids = []
            for p in batch_copy[:10]:
                product_ids.append(p.get('main_product_id', 'unknown'))
            logger.info(f"BATCH_MANAGER | {site_key} | PRODUCT_IDS | {product_ids}")
        
        # Now save to DB (outside the lock to avoid long hold)
        try:
            # =====================================================
            # ENSURE DB CONNECTION
            # =====================================================
            logger.info(f"BATCH_MANAGER | {site_key} | DB_CONNECTING...")
            
            if self.db is None:
                from Database.Postgresql.db import Database
                self.db = Database()
                logger.info(f"BATCH_MANAGER | {site_key} | DB_CONNECTED | {id(self.db)}")
                
                # Verify connection
                if self.db and hasattr(self.db, 'conn') and self.db.conn:
                    logger.info(f"BATCH_MANAGER | {site_key} | DB_VERIFIED | connection active")
                else:
                    logger.error(f"BATCH_MANAGER | {site_key} | DB_CONNECTION_FAILED")
                    # Restore products
                    lock = self._get_lock(site_key)
                    with lock:
                        pending = self._get_pending(site_key)
                        pending = batch_copy + pending
                        self.pending_products[site_key] = pending
                    return False
            else:
                logger.info(f"BATCH_MANAGER | {site_key} | DB_REUSING | {id(self.db)}")
            
            # =====================================================
            # SERIALIZE PRODUCTS
            # =====================================================
            logger.info(f"BATCH_MANAGER | {site_key} | SERIALIZING | {len(batch_copy)} products")
            
            serialized_batch = []
            failed_products = []
            
            for idx, p in enumerate(batch_copy):
                if p:
                    try:
                        serialized = self._make_json_serializable(p)
                        if serialized:
                            serialized_batch.append(serialized)
                        else:
                            failed_products.append(p)
                            logger.warning(f"BATCH_MANAGER | {site_key} | SERIALIZE_FAIL | index={idx}")
                    except Exception as e:
                        failed_products.append(p)
                        logger.error(f"BATCH_MANAGER | {site_key} | SERIALIZE_ERROR | index={idx} | {e}")
            
            logger.info(f"BATCH_MANAGER | {site_key} | SERIALIZED | {len(serialized_batch)} valid, {len(failed_products)} failed")
            
            if not serialized_batch:
                logger.warning(f"BATCH_MANAGER | {site_key} | NO_VALID_PRODUCTS | skipping save")
                # Restore failed products
                if failed_products:
                    lock = self._get_lock(site_key)
                    with lock:
                        pending = self._get_pending(site_key)
                        pending = failed_products + pending
                        self.pending_products[site_key] = pending
                return True
            
            # =====================================================
            # ACTUAL DB SAVE
            # =====================================================
            logger.info(f"BATCH_MANAGER | {site_key} | DB_SAVE_START | {len(serialized_batch)} products")
            logger.info("-" * 60)
            
            # Log first product for debugging
            if serialized_batch:
                first = serialized_batch[0]
                logger.info(f"BATCH_MANAGER | {site_key} | FIRST_PRODUCT | id={first.get('main_product_id')} | name={first.get('name', '')[:30]}")
            
            try:
                # Force a commit by calling upsert_products
                result = self.db.upsert_products(serialized_batch)
                
                # Update stats
                lock = self._get_lock(site_key)
                with lock:
                    self.stats[site_key]['saved'] += result
                
                logger.info(f"BATCH_MANAGER | {site_key} | DB_SAVE_COMPLETE | saved={result}/{len(serialized_batch)} products")
                logger.info("=" * 60)
                
                # Log any failed products
                if failed_products:
                    logger.warning(f"BATCH_MANAGER | {site_key} | FAILED_PRODUCTS | {len(failed_products)} products not saved")
                    lock = self._get_lock(site_key)
                    with lock:
                        pending = self._get_pending(site_key)
                        pending = failed_products + pending
                        self.pending_products[site_key] = pending
                
                # Force flush the logger
                for handler in logger.handlers:
                    try:
                        handler.flush()
                    except:
                        pass
                
                return True
                
            except Exception as db_error:
                logger.error(f"BATCH_MANAGER | {site_key} | DB_SAVE_ERROR | {db_error}")
                import traceback
                traceback.print_exc()
                
                # Restore all products on error
                lock = self._get_lock(site_key)
                with lock:
                    pending = self._get_pending(site_key)
                    pending = batch_copy + pending
                    self.pending_products[site_key] = pending
                    self.stats[site_key]['errors'] += 1
                
                return False
            
        except Exception as e:
            logger.error(f"BATCH_MANAGER | {site_key} | FLUSH_ERROR | {e}")
            import traceback
            traceback.print_exc()
            
            # Restore products on error
            lock = self._get_lock(site_key)
            with lock:
                pending = self._get_pending(site_key)
                pending = batch_copy + pending
                self.pending_products[site_key] = pending
                self.stats[site_key]['errors'] += 1
            
            return False
    
    def flush_all(self) -> bool:
        """Flush all pending products for all sites."""
        logger.info("=" * 60)
        logger.info("BATCH_MANAGER | FLUSHING_ALL_SITES")
        
        success = True
        for site in list(self.pending_products.keys()):
            logger.info(f"BATCH_MANAGER | FLUSH_SITE | {site}")
            if not self.flush_site(site):
                success = False
        
        logger.info(f"BATCH_MANAGER | FLUSH_ALL_COMPLETE | success={success}")
        logger.info("=" * 60)
        return success
    
    def get_pending_count(self, site: str) -> int:
        """Get number of pending products for a site."""
        site_key = site.lower()
        lock = self._get_lock(site_key)
        with lock:
            return len(self.pending_products.get(site_key, []))
    
    def get_stats(self, site: str = None) -> Dict:
        """Get stats for a site or all sites."""
        if site:
            site_key = site.lower()
            return self.stats.get(site_key, {})
        return self.stats
    
    def _start_timer(self):
        """Start the timer thread."""
        with self._timer_lock:
            if self._timer_thread is not None and self._timer_thread.is_alive():
                logger.info("BATCH_MANAGER | TIMER_ALREADY_RUNNING | skipping")
                return
            
            self._stop_timer = False
            self._timer_thread = threading.Thread(
                target=self._timer_loop,
                name="BatchManagerTimer",
                daemon=True
            )
            self._timer_thread.start()
            logger.info(f"BATCH_MANAGER | TIMER_STARTED | thread={self._timer_thread.name} | instance_id={id(self)}")
    
    def _stop_timer(self):
        """Stop the timer thread."""
        self._stop_timer = True
        if self._timer_thread and self._timer_thread.is_alive():
            self._timer_thread.join(timeout=5)
            logger.info("BATCH_MANAGER | TIMER_STOPPED")
    
    def _timer_loop(self):
        """Timer loop that flushes all sites at regular intervals."""
        logger.info(f"BATCH_MANAGER | TIMER_LOOP_STARTED | interval={self.flush_interval}s")
        loop_count = 0
        
        while not self._stop_timer:
            time.sleep(self.flush_interval)
            loop_count += 1
            
            if self._stop_timer:
                break
            
            # Check all sites for pending products
            sites_to_flush = []
            for site in list(self.pending_products.keys()):
                lock = self._get_lock(site)
                with lock:
                    count = len(self.pending_products.get(site, []))
                    if count > 0:
                        sites_to_flush.append((site, count))
            
            # Flush each site with pending products
            if sites_to_flush:
                for site, count in sites_to_flush:
                    logger.info(f"BATCH_MANAGER | {site} | TIMER_TRIGGERED | loop={loop_count} | {count} products | interval={self.flush_interval}s")
                    self.flush_site(site)
            else:
                # Only log every 5th loop to reduce noise
                if loop_count % 5 == 0:
                    logger.debug(f"BATCH_MANAGER | TIMER_CHECK | loop={loop_count} | no pending products")
        
        logger.info(f"BATCH_MANAGER | TIMER_LOOP_STOPPED | total_loops={loop_count}")
    
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
    
    def shutdown(self):
        """Shutdown the batch manager and flush all pending products."""
        if self._is_shutting_down:
            return
        
        self._is_shutting_down = True
        logger.info("=" * 60)
        logger.info("BATCH_MANAGER | SHUTDOWN_START")
        
        self._stop_timer()
        self.flush_all()
        
        if self.db:
            try:
                self.db.close()
                logger.info("BATCH_MANAGER | DB_CLOSED")
            except Exception as e:
                logger.error(f"BATCH_MANAGER | DB_CLOSE_ERROR | {e}")
        
        # Print final stats
        logger.info("BATCH_MANAGER | FINAL_STATS")
        for site, stats in self.stats.items():
            logger.info(f"  {site}: saved={stats.get('saved', 0)}, queued={stats.get('queued', 0)}, errors={stats.get('errors', 0)}")
        
        logger.info("BATCH_MANAGER | SHUTDOWN_COMPLETE")
        logger.info("=" * 60)


# =====================================================
# SINGLETON ACCESSOR - Thread-safe
# =====================================================

_batch_manager_instance = None
_batch_manager_lock = threading.Lock()


def get_batch_manager() -> BatchManager:
    """Get the singleton BatchManager instance."""
    global _batch_manager_instance
    
    if _batch_manager_instance is None:
        with _batch_manager_lock:
            if _batch_manager_instance is None:
                _batch_manager_instance = BatchManager()
    
    return _batch_manager_instance


def shutdown_batch_manager():
    """Shutdown the batch manager."""
    global _batch_manager_instance
    if _batch_manager_instance:
        _batch_manager_instance.shutdown()
        _batch_manager_instance = None