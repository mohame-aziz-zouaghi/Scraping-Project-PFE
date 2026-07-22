# monitoring/engine/adaptive_batch_processor.py
"""
Intelligent Adaptive Batch Processor with Smart Batching

NEW APPROACH:
- Site flush: 50 products OR 60 seconds (whichever comes first)
- Global flush: ONLY when multiple sites have pending updates AND no site is flushing
- Smart: If a site flushes, reset ALL site timers (to avoid staggered flushes)
"""
import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from Database.Postgresql.db import Database
from core.logger import setup_logger

logger = setup_logger("batch_processor", "monitor")


class AdaptiveBatchProcessor:
    """
    Smart batch processor with intelligent flushing
    
    Core Principle: Flush as soon as possible WITHOUT wasting database trips
    
    Flush Conditions (Independent):
    1. Site reaches 50 products → Flush that site immediately
    2. Site has been waiting 60 seconds → Flush that site immediately
    3. ANY site flushes → Reset ALL site timers (prevent staggered flushes)
    4. Global 5-min safety net (rarely used)
    """
    
    def __init__(self, db_instance: Database = None):
        self.db = db_instance or Database()
        self.site_batches: Dict[str, List] = defaultdict(list)
        self.site_last_flush: Dict[str, float] = {}
        self.site_stats: Dict[str, Dict] = defaultdict(lambda: {
            "batch_count": 0,
            "total_updated": 0,
            "total_failed": 0,
            "avg_batch_size": 0,
            "pending": 0,
            "last_flush": None
        })
        
        # Core thresholds
        self.SITE_BATCH_SIZE = 50       # Flush when site reaches 50
        self.SITE_FLUSH_INTERVAL = 60   # Flush site after 60 seconds
        self.GLOBAL_FLUSH_INTERVAL = 120  # 2 minutes global safety net (reduced from 5min)
        
        self.lock = threading.Lock()
        self.is_flushing = False
        self.last_global_flush = time.time()
        self.last_any_flush = time.time()  # Track when ANY site flushed
        self.total_updates = 0
        self.total_batches = 0
        self.running = True
        
        # Start background timer
        self._start_timer()
        logger.info(f"✅ SmartBatchProcessor started (site_batch=50, site_interval=60s, global_interval=120s)")
    
    # =====================================================
    # CORE METHODS
    # =====================================================
    
    def add_update(self, site: str, main_product_id: str, old_data: Dict, new_data: Dict, source: str):
        """Add an update to the appropriate site batch"""
        with self.lock:
            self.site_batches[site].append((site, main_product_id, old_data, new_data, source))
            self.total_updates += 1
            
            current_site_count = len(self.site_batches[site])
            global_count = sum(len(batch) for batch in self.site_batches.values())
            
            # === FLUSH CONDITIONS ===
            
            # 1. Site reached batch size → FLUSH SITE
            if current_site_count >= self.SITE_BATCH_SIZE:
                logger.info(f"⚡ {site} reached {self.SITE_BATCH_SIZE} - flushing")
                self._flush_site(site)
                return
            
            # 2. Site timed out (60 seconds) → FLUSH SITE
            if site in self.site_last_flush:
                elapsed = time.time() - self.site_last_flush[site]
                if current_site_count > 0 and elapsed >= self.SITE_FLUSH_INTERVAL:
                    logger.info(f"⏰ {site} timed out ({elapsed:.0f}s) - flushing {current_site_count} updates")
                    self._flush_site(site)
                    return
            
            # 3. Global safety net (120 seconds) - only if NO site has flushed recently
            if global_count > 0:
                elapsed_global = time.time() - self.last_any_flush
                if elapsed_global >= self.GLOBAL_FLUSH_INTERVAL:
                    logger.info(f"🌍 Global safety net ({elapsed_global:.0f}s) - flushing {global_count} updates")
                    self._flush_all()
                    return
    
    def flush_site(self, site: str) -> int:
        """Force flush a specific site's batch"""
        with self.lock:
            return self._flush_site(site)
    
    def flush_all(self) -> Dict[str, int]:
        """Force flush all sites' batches"""
        with self.lock:
            return self._flush_all()
    
    def get_stats(self) -> Dict:
        """Get comprehensive statistics"""
        with self.lock:
            return {
                "sites": {
                    site: {
                        "pending": len(batch),
                        "batch_count": self.site_stats[site]["batch_count"],
                        "total_updated": self.site_stats[site]["total_updated"],
                        "total_failed": self.site_stats[site]["total_failed"],
                        "avg_batch_size": round(self.site_stats[site]["avg_batch_size"], 1),
                        "last_flush": self.site_stats[site]["last_flush"]
                    }
                    for site, batch in self.site_batches.items()
                },
                "global": {
                    "total_updates": self.total_updates,
                    "total_batches": self.total_batches,
                    "pending_global": sum(len(batch) for batch in self.site_batches.values()),
                    "active_sites": len([s for s in self.site_batches if self.site_batches[s]]),
                    "last_flush_any": self.last_any_flush,
                    "global_interval": self.GLOBAL_FLUSH_INTERVAL,
                    "site_interval": self.SITE_FLUSH_INTERVAL
                }
            }
    
    def close(self):
        """Clean up resources"""
        self.running = False
        if self.site_batches:
            total_pending = sum(len(batch) for batch in self.site_batches.values())
            if total_pending > 0:
                logger.info(f"🔄 Closing - flushing {total_pending} remaining updates")
                self.flush_all()
        if self.db:
            self.db.close()
        logger.info("✅ AdaptiveBatchProcessor closed")
    
    # =====================================================
    # INTERNAL METHODS
    # =====================================================
    
    def _flush_site(self, site: str) -> int:
        """Internal: Flush a specific site's batch"""
        if not self.site_batches[site]:
            return 0
        
        updates = self.site_batches[site].copy()
        self.site_batches[site] = []
        self.site_last_flush[site] = time.time()
        self.last_any_flush = time.time()  # Reset global timer
        
        logger.info(f"🔄 Flushing {len(updates)} updates for {site}")
        
        success_count, failed = self._execute_batch(updates)
        
        # Update stats
        self.site_stats[site]["batch_count"] += 1
        self.site_stats[site]["total_updated"] += success_count
        self.site_stats[site]["total_failed"] += len(failed)
        self.site_stats[site]["avg_batch_size"] = (
            self.site_stats[site]["total_updated"] / 
            self.site_stats[site]["batch_count"]
            if self.site_stats[site]["batch_count"] > 0 else 0
        )
        self.site_stats[site]["last_flush"] = datetime.now().isoformat()
        
        return success_count
    
    def _flush_all(self) -> Dict[str, int]:
        """Internal: Flush ALL sites"""
        sites_with_data = [site for site, updates in self.site_batches.items() if updates]
        
        if not sites_with_data:
            return {}
        
        results = {}
        for site in sites_with_data:
            results[site] = self._flush_site(site)
        
        self.last_global_flush = time.time()
        self.last_any_flush = time.time()
        return results
    
    def _execute_batch(self, updates: List) -> Tuple[int, List]:
        """Execute the actual database batch update"""
        try:
            success_count, failed = self.db.batch_update_products_with_monitoring_data(updates)
            self.total_batches += 1
            
            if failed:
                logger.warning(f"⚠️ Batch: {success_count}/{len(updates)} succeeded, {len(failed)} failed")
                for fail in failed[:3]:
                    logger.warning(f"  ❌ {fail['site']}/{fail['main_product_id']} - {fail['reason']}")
            else:
                logger.info(f"✅ Batch: {success_count} products updated")
            
            return success_count, failed
            
        except Exception as e:
            logger.error(f"❌ Batch execution error: {e}")
            return 0, updates
    
    def _start_timer(self):
        """Start the background timer"""
        timer = threading.Timer(10, self._timer_check)
        timer.daemon = True
        timer.start()
    
    def _timer_check(self):
        """Periodic check for pending updates"""
        if not self.running:
            return
        
        with self.lock:
            if self.is_flushing:
                self._start_timer()
                return
            
            global_count = sum(len(batch) for batch in self.site_batches.values())
            
            if global_count > 0:
                # Check individual site timeouts
                for site, updates in list(self.site_batches.items()):
                    if updates and site in self.site_last_flush:
                        elapsed = time.time() - self.site_last_flush[site]
                        if elapsed >= self.SITE_FLUSH_INTERVAL:
                            logger.info(f"⏰ {site} timer flush ({elapsed:.0f}s) - {len(updates)} updates")
                            self._flush_site(site)
                            break  # Exit after one flush, timer will check again
                
                # Global safety net (if no site flushed above)
                if global_count > 0:
                    elapsed_global = time.time() - self.last_any_flush
                    if elapsed_global >= self.GLOBAL_FLUSH_INTERVAL:
                        logger.info(f"🌍 Global safety net ({elapsed_global:.0f}s) - flushing {global_count} updates")
                        self._flush_all()
        
        self._start_timer()