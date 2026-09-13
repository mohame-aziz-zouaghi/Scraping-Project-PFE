"""
Intelligent Adaptive Batch Processor - Optimized to reduce DB contention.
Flushes only when significant updates accumulate or after longer intervals.
"""

import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from Database.Postgresql.db import Database
from core.logger import setup_logger
from core.shutdown_handler import register_flush_hook

logger = setup_logger("batch_processor", "monitor")


class AdaptiveBatchProcessor:
    """
    Smart batch processor - reduces DB contention by batching larger.
    - Flush when site has 100 UPDATED products (increased from 50)
    - Flush after 120 seconds (increased from 60)
    - Global flush every 300 seconds (increased from 120)
    """
    
    def __init__(self, db_instance: Database = None):
        self.db = db_instance or Database()
        self.site_batches: Dict[str, List] = defaultdict(list)
        self.site_last_flush: Dict[str, float] = {}
        self.site_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.site_stats: Dict[str, Dict] = defaultdict(lambda: {
            "batch_count": 0,
            "total_updated": 0,
            "total_failed": 0,
            "pending": 0,
            "last_flush": None,
            "avg_batch_size": 0
        })
        
        # ─── INCREASED THRESHOLDS ──────────────────────────────
        self.SITE_BATCH_SIZE = 100       # Flush when 100 UPDATED (was 50)
        self.SITE_FLUSH_INTERVAL = 120   # Flush after 120s (was 60)
        self.GLOBAL_FLUSH_INTERVAL = 300 # Global after 300s (was 120)
        
        self._global_lock = threading.Lock()
        self.is_flushing = False
        self.last_global_flush = time.time()
        self.last_any_flush = time.time()
        self.total_updates = 0
        self.total_batches = 0
        self.running = True
        
        self._start_timer()
        logger.info(f"✅ BatchProcessor initialized | batch_size={self.SITE_BATCH_SIZE} | site_interval={self.SITE_FLUSH_INTERVAL}s | global={self.GLOBAL_FLUSH_INTERVAL}s")
        register_flush_hook(self.flush_all, "BatchProcessor")
    
    def add_update(self, site: str, main_product_id: str, old_data: Dict, new_data: Dict, source: str):
        """Add an update - ONLY counts if product ACTUALLY changed."""
        site = site.lower()
        
        # Check if product actually changed
        tracked_fields = ["price", "discount_price", "discount_amount", 
                         "availability", "special_id", "price_valid_until"]
        has_change = False
        
        if old_data and new_data:
            for field in tracked_fields:
                if old_data.get(field) != new_data.get(field):
                    has_change = True
                    break
        
        if not has_change:
            return
        
        # Add to batch
        should_flush = False
        with self.site_locks[site]:
            self.site_batches[site].append((site, main_product_id, old_data, new_data, source))
            self.total_updates += 1
            self.site_stats[site]["pending"] = len(self.site_batches[site])
            
            # Only flush when batch is large enough
            should_flush = len(self.site_batches[site]) >= self.SITE_BATCH_SIZE
        
        if should_flush:
            logger.info(f"⚡ {site.upper()} reached {self.SITE_BATCH_SIZE} UPDATED - async flush")
            threading.Thread(target=self._flush_site_async, args=(site,), daemon=True).start()
    
    def _flush_site_async(self, site: str):
        """Flush a site's batch asynchronously."""
        with self.site_locks[site]:
            if not self.site_batches[site]:
                return
            updates = self.site_batches[site].copy()
            self.site_batches[site] = []
            self.site_last_flush[site] = time.time()
            self.site_stats[site]["pending"] = 0
        
        self.last_any_flush = time.time()
        
        try:
            success_count, failed = self._execute_batch(updates)
            
            with self.site_locks[site]:
                self.site_stats[site]["batch_count"] += 1
                self.site_stats[site]["total_updated"] += success_count
                self.site_stats[site]["total_failed"] += len(failed)
                if self.site_stats[site]["batch_count"] > 0:
                    self.site_stats[site]["avg_batch_size"] = (
                        self.site_stats[site]["total_updated"] / 
                        self.site_stats[site]["batch_count"]
                    )
                self.site_stats[site]["last_flush"] = datetime.now().isoformat()
            
            logger.info(f"✅ {site.upper()} flush | updated={success_count} | failed={len(failed)}")
            
        except Exception as e:
            logger.error(f"❌ Batch flush error for {site}: {e}")
    
    def flush_site(self, site: str) -> int:
        """Force flush pending updates for a specific site."""
        site = site.lower()
        with self.site_locks[site]:
            pending = len(self.site_batches[site])
        if pending > 0:
            self._flush_site_async(site)
        return pending
    
    def flush_all(self) -> Dict[str, int]:
        """Force flush ALL pending updates."""
        result = {}
        for site in list(self.site_batches.keys()):
            with self.site_locks[site]:
                pending = len(self.site_batches[site])
            if pending > 0:
                self._flush_site_async(site)
                result[site] = pending
        return result
    
    def get_stats(self) -> Dict:
        """Get processor statistics."""
        with self._global_lock:
            sites_stats = {}
            for site, batch in self.site_batches.items():
                sites_stats[site] = {
                    "pending": len(batch),
                    "batch_count": self.site_stats[site]["batch_count"],
                    "total_updated": self.site_stats[site]["total_updated"],
                    "total_failed": self.site_stats[site]["total_failed"],
                    "avg_batch_size": round(self.site_stats[site].get("avg_batch_size", 0), 1),
                    "last_flush": self.site_stats[site]["last_flush"]
                }
            
            return {
                "sites": sites_stats,
                "global": {
                    "total_updates": self.total_updates,
                    "total_batches": self.total_batches,
                    "pending_global": sum(len(batch) for batch in self.site_batches.values()),
                    "last_flush_any": self.last_any_flush,
                }
            }
    
    def close(self):
        """Clean up resources."""
        self.running = False
        total_pending = sum(len(batch) for batch in self.site_batches.values())
        if total_pending > 0:
            logger.info(f"🔄 Closing - flushing {total_pending} remaining updates")
            self.flush_all()
        if self.db:
            self.db.close()
        logger.info("✅ AdaptiveBatchProcessor closed")
    
    def _execute_batch(self, updates: List) -> Tuple[int, List]:
        """Execute batch update with retry logic."""
        try:
            success_count, failed = self.db.batch_update_products_with_monitoring_data(updates)
            self.total_batches += 1
            
            if failed:
                logger.warning(f"⚠️ Batch: {success_count}/{len(updates)} succeeded, {len(failed)} failed")
            else:
                logger.debug(f"✅ Batch: {success_count} products updated")
            
            return success_count, failed
            
        except Exception as e:
            logger.error(f"❌ Batch execution error: {e}")
            # Retry once
            try:
                time.sleep(2)
                success_count, failed = self.db.batch_update_products_with_monitoring_data(updates)
                self.total_batches += 1
                return success_count, failed
            except Exception as retry_e:
                logger.error(f"❌ Batch retry failed: {retry_e}")
                return 0, updates
    
    def _start_timer(self):
        """Start background timer - checks less frequently."""
        timer = threading.Timer(30, self._timer_check)  # Check every 30s instead of 10s
        timer.daemon = True
        timer.start()
    
    def _timer_check(self):
        """Periodic check - flushes less aggressively."""
        if not self.running:
            return
        
        try:
            current_time = time.time()
            sites_to_flush = []
            
            # Check site timeouts - only flush if enough time passed
            with self._global_lock:
                for site, updates in list(self.site_batches.items()):
                    if updates and site in self.site_last_flush:
                        elapsed = current_time - self.site_last_flush[site]
                        if elapsed >= self.SITE_FLUSH_INTERVAL and len(updates) >= 10:
                            sites_to_flush.append(site)
            
            # Flush timed-out sites
            for site in sites_to_flush:
                threading.Thread(target=self._flush_site_async, args=(site,), daemon=True).start()
            
            # Global check - less frequent
            with self._global_lock:
                global_count = sum(len(batch) for batch in self.site_batches.values())
                if global_count > 0:
                    elapsed_global = current_time - self.last_any_flush
                    if elapsed_global >= self.GLOBAL_FLUSH_INTERVAL and global_count >= 20:
                        logger.info(f"🌍 Global flush ({elapsed_global:.0f}s) - {global_count} updates")
                        for site in list(self.site_batches.keys()):
                            threading.Thread(target=self._flush_site_async, args=(site,), daemon=True).start()
        
        except Exception as e:
            logger.error(f"Timer check error: {e}")
        
        # Restart timer
        self._start_timer()