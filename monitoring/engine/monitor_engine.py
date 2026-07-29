# monitoring/engine/monitor_engine.py
from datetime import datetime
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from Database.Redis.Configuration.redis_client import RedisClient
from Database.Redis.cache_keys import site_pattern, extract_product_id_from_key
from core.logger import setup_logger
from core.utils.progress_bar import ProgressBar
from core.utils.debug_logger import log_debug, log_scrape_stats, end_session
from monitoring.engine.worker_pool import WorkerPool
from monitoring.engine.result_processor import ResultProcessor
from monitoring.engine.site_registry import SITE_WORKERS, get_site_config, update_site_config
from monitoring.engine.multi_progress import get_multi_progress

logger = setup_logger("monitor_engine", "monitor")


class MonitorEngine:

    def __init__(self):
        self.redis = RedisClient().get_client()
        self.processor = ResultProcessor()
        self.worker_pool = None
        self.changed_products = []  # Track changed products for recap
        self.multi_progress = get_multi_progress()

    # ---------------------------------
    # LOAD PRODUCTS FROM REDIS
    # ---------------------------------
    def load_products(self, site: str) -> list:
        """Load ONLY enabled products from Redis (is_enabled = TRUE)"""
        site = site.lower()
        pattern = site_pattern(site)
        products = []
        now = int(time.time())
        
        for key in self.redis.scan_iter(match=pattern, count=100):
            if isinstance(key, bytes):
                key = key.decode('utf-8')
            
            try:
                raw = self.redis.get(key)
                if not raw:
                    continue
                
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                
                data = json.loads(raw)
                main_product_id = extract_product_id_from_key(key)
                
                if not main_product_id:
                    continue
                
                # Check if product is enabled (from Redis cache)
                is_enabled = data.get("is_enabled", True)
                
                # If disabled, check if it's been more than 3 days
                if not is_enabled:
                    last_updated = data.get("last_updated_at")
                    if last_updated:
                        # Check if more than 3 days have passed
                        from datetime import datetime
                        try:
                            if isinstance(last_updated, (int, float)):
                                last_updated_date = datetime.fromtimestamp(last_updated)
                            else:
                                last_updated_date = datetime.fromisoformat(str(last_updated).replace('Z', '+00:00'))
                            
                            days_since_update = (datetime.now() - last_updated_date).days
                            if days_since_update <= 3:
                                # Skip disabled products that were disabled within last 3 days
                                continue
                        except:
                            pass
                    # If more than 3 days, we can consider re-enabling later, but skip for now
                    continue
                
                product = {
                    "main_product_id": main_product_id,
                    "site": site,
                    "url": data.get("url"),
                    "special_id": data.get("special_id"),
                    "price": data.get("price"),
                    "discount_price": data.get("discount_price"),
                    "discount_amount": data.get("discount_amount"),
                    "availability": data.get("availability"),
                    "price_valid_until": data.get("price_valid_until"),
                    "monitoring_priority": data.get("monitoring_priority", 3),
                    "check_failures": data.get("check_failures", 0),
                    "is_enabled": data.get("is_enabled", True)
                }
                products.append(product)
                
            except Exception as e:
                logger.error(f"{site} | LOAD_ERROR | {key} | {e}")
                return []
        
        logger.info(f"{site} | ENABLED_PRODUCTS_LOADED | count={len(products)}")
        return products

    # ---------------------------------
    # MONITOR CYCLE (WITH MULTI-PROGRESS)
    # ---------------------------------
    def monitor_cycle(self, site: str, workers: int = 20, show_progress: bool = True, stop_event: threading.Event = None, pause_event: threading.Event = None) -> dict:
        """
        Run one monitoring cycle with multi-progress display
        """
        start = time.time()
        site = site.lower()
        
        # Reset changed products list for this cycle
        self.changed_products = []
        
        # Get site configuration
        config = get_site_config(site)
        workers = config.get("workers", workers)
        
        logger.info(f"{site} | MONITOR_CYCLE_START")
        
        # Load products from Redis
        products = self.load_products(site)
        total = len(products)
        
        if not total:
            logger.warning(f"{site} | NO_PRODUCTS_FOUND")
            self.multi_progress.add_site(site, 0)
            self.multi_progress.update_progress(site, 0, 0, 0, 0, "✅ No products")
            return {"site": site, "total": 0, "changed": 0, "no_change": 0, "errors": 0, "time": 0}
        
        # Get the worker function
        worker = SITE_WORKERS.get(site)
        
        if not worker:
            logger.error(f"{site} | NO_WORKER_FOUND")
            self.multi_progress.add_site(site, total)
            self.multi_progress.update_progress(site, 0, 0, 0, 0, "❌ No worker")
            return {"site": site, "total": 0, "changed": 0, "no_change": 0, "errors": 0, "time": 0, "error": "No worker registered"}
        
        # Add site to multi-progress
        self.multi_progress.add_site(site, total)
        self.multi_progress.update_progress(site, 0, 0, 0, 0, "🔄 Starting...")
        
        # Progress tracking
        changed_count = 0
        no_change_count = 0
        error_count = 0
        completed = 0
        stopped = False
        paused = False
        
        # Process products in smaller batches to check pause/stop more frequently
        BATCH_SIZE = 50
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        
        for batch_idx in range(total_batches):
            # =====================================================
            # CHECK STOP AND PAUSE EVENTS BEFORE EACH BATCH
            # =====================================================
            if stop_event and stop_event.is_set():
                stopped = True
                self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "⏹️ Stopped")
                logger.info(f"{site} | STOP_SIGNAL_RECEIVED")
                break
            
            if pause_event and pause_event.is_set():
                paused = True
                self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "⏸️ Paused")
                logger.info(f"{site} | PAUSE_SIGNAL_RECEIVED")
                
                # Wait until pause is cleared or stop is set
                while pause_event.is_set() and not (stop_event and stop_event.is_set()):
                    time.sleep(1)
                
                if stop_event and stop_event.is_set():
                    stopped = True
                    self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "⏹️ Stopped")
                    break
                
                # Resume processing
                self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "▶️ Resuming...")
                logger.info(f"{site} | RESUME_SIGNAL_RECEIVED")
                continue
            
            # Update status for batch
            self.multi_progress.update_progress(
                site, completed, changed_count, no_change_count, error_count,
                f"📦 Batch {batch_idx+1}/{total_batches}"
            )
            
            # Get batch of products
            start_idx = batch_idx * BATCH_SIZE
            end_idx = min(start_idx + BATCH_SIZE, total)
            batch_products = products[start_idx:end_idx]
            
            # Process batch
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(worker, product, self.processor): product
                    for product in batch_products
                }
                
                for future in as_completed(futures):
                    # =====================================================
                    # CHECK STOP/PAUSE EVENTS DURING BATCH PROCESSING
                    # =====================================================
                    if stop_event and stop_event.is_set():
                        stopped = True
                        self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "⏹️ Stopped")
                        logger.info(f"{site} | STOP_SIGNAL_RECEIVED | stopping during batch")
                        for f in futures:
                            f.cancel()
                        break
                    
                    if pause_event and pause_event.is_set():
                        paused = True
                        self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "⏸️ Paused")
                        logger.info(f"{site} | PAUSE_SIGNAL_RECEIVED | pausing during batch")
                        for f in futures:
                            f.cancel()
                        
                        # Wait until pause is cleared or stop is set
                        while pause_event.is_set() and not (stop_event and stop_event.is_set()):
                            time.sleep(1)
                        
                        if stop_event and stop_event.is_set():
                            stopped = True
                            self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "⏹️ Stopped")
                            break
                        
                        # Resume processing
                        self.multi_progress.update_progress(site, completed, changed_count, no_change_count, error_count, "▶️ Resuming...")
                        logger.info(f"{site} | RESUME_SIGNAL_RECEIVED | resuming cycle")
                        break
                    
                    try:
                        result = future.result(timeout=60)
                        if result:
                            changed_count += 1
                            product = futures[future]
                            self.changed_products.append({
                                "product_id": product.get("main_product_id"),
                                "url": product.get("url"),
                                "price": product.get("price")
                            })
                        else:
                            no_change_count += 1
                    except Exception as e:
                        error_count += 1
                        logger.error(f"{site} | WORKER_ERROR | {e}")
                    
                    completed += 1
                    
                    # Update progress
                    self.multi_progress.update_progress(
                        site, completed, changed_count, no_change_count, error_count,
                        f"📦 Batch {batch_idx+1}/{total_batches}"
                    )
                    
                    if paused:
                        break
                
                if stopped:
                    break
                if paused:
                    paused = False
                    continue
        
        # Handle final status
        if stopped:
            status = "⏹️ Stopped"
        elif paused:
            status = "⏸️ Paused"
        else:
            status = "✅ Complete"
        
        self.multi_progress.update_progress(
            site, completed, changed_count, no_change_count, error_count, status
        )
        
        elapsed = round(time.time() - start, 2)
        
        # Print summary
        if show_progress:
            print(f"\n   📊 {site.upper()} Cycle Complete:")
            print(f"      ✅ Total: {total} products")
            print(f"      ✓ Changed: {changed_count}")
            print(f"      ○ No change: {no_change_count}")
            print(f"      ✗ Errors: {error_count}")
            print(f"      ⏱️ Time: {elapsed}s")
            
            # Show changed products sample (first 5)
            if self.changed_products:
                print(f"\n   📝 Changed Products (sample):")
                for i, prod in enumerate(self.changed_products[:5], 1):
                    print(f"      {i}. {prod['product_id']} - Price: {prod['price']} TND")
                if len(self.changed_products) > 5:
                    print(f"      ... and {len(self.changed_products) - 5} more")
        
        logger.info(
            f"{site} | CYCLE_FINISHED | "
            f"total={total} | changed={changed_count} | "
            f"no_change={no_change_count} | errors={error_count} | time={elapsed}s"
        )
        
        # =====================================================
        # FLUSH PENDING BATCHES
        # =====================================================
        site_flush_count = self.processor.flush_site(site)
        if site_flush_count > 0:
            logger.info(f"{site} | SITE_FLUSH | {site_flush_count} products updated")
        
        global_flush_results = self.processor.flush_all()
        if global_flush_results:
            logger.info(f"{site} | GLOBAL_FLUSH | {sum(global_flush_results.values())} products updated across {len(global_flush_results)} sites")
        
        # =====================================================
        # LOG CYCLE RECAP
        # =====================================================
        cycle_stats = {
            "cycle_type": "monitoring",
            "site": site,
            "cycle_time_seconds": elapsed,
            "total_products": total,
            "changed_count": changed_count,
            "no_change_count": no_change_count,
            "error_count": error_count,
            "total_changed_products": len(self.changed_products),
            "changed_products_sample": self.changed_products[:10],
            "batch_stats": self.processor.get_stats(),
            "timestamp": datetime.now().isoformat()
        }
        
        log_scrape_stats(site=site, stats=cycle_stats)
        
        return {
            "site": site,
            "total": total,
            "changed": changed_count,
            "no_change": no_change_count,
            "errors": error_count,
            "time": elapsed,
            "stopped": stopped,
            "paused": paused,
            "timestamp": datetime.now().isoformat(),
            "changed_products": self.changed_products[:10],
            "batch_stats": self.processor.get_stats()
        }

    # ---------------------------------
    # RUN SITE (Single site with multi-progress)
    # ---------------------------------
    def run_site(self, site: str, workers: int = None, interval: int = None, show_progress: bool = True):
        """Run monitoring for a specific site with multi-progress"""
        site = site.lower()
        
        if site not in SITE_WORKERS:
            print(f"\n❌ Site '{site}' not registered!")
            print(f"   Available sites: {', '.join(SITE_WORKERS.keys())}")
            return
        
        if workers:
            update_site_config(site, workers=workers)
        if interval:
            update_site_config(site, interval=interval)
        
        config = get_site_config(site)
        interval = config.get("interval", 300)
        workers = config.get("workers", 20)
        
        # Start multi-progress display
        self.multi_progress.start()
        
        print(f"\n{'='*60}")
        print(f"🚀 STARTING MONITORING: {site.upper()}")
        print(f"   Workers: {workers} | Interval: {interval}s")
        print(f"   Press Ctrl+C to stop")
        print(f"{'='*60}\n")
        
        try:
            while True:
                self.monitor_cycle(site=site, workers=workers, show_progress=show_progress)
                time.sleep(interval)
        except KeyboardInterrupt:
            print(f"\n\n🛑 Monitoring stopped for {site.upper()}")
            self.multi_progress.stop()
            self.multi_progress.close()

    # ---------------------------------
    # RUN ALL SITES (Multi-progress dashboard)
    # ---------------------------------
    def run_all(self):
        """Run monitoring for all enabled sites with multi-progress dashboard"""
        from monitoring.engine.site_registry import get_enabled_sites
        
        enabled_sites = get_enabled_sites()
        
        if not enabled_sites:
            print("\n❌ No enabled sites found!")
            return
        
        print(f"\n{'='*60}")
        print("🚀 RUNNING ALL ENABLED SITES (Multi-Progress Dashboard)")
        print(f"{'='*60}")
        print(f"📋 Sites: {', '.join([s.upper() for s in enabled_sites])}")
        print(f"   Press Ctrl+C to stop all")
        print(f"{'='*60}\n")
        
        # Start multi-progress display
        self.multi_progress.start()
        
        # Start each site in its own thread
        threads = []
        for site in enabled_sites:
            config = get_site_config(site)
            interval = config.get("interval", 300)
            workers = config.get("workers", 20)
            
            thread = threading.Thread(
                target=self._run_site_loop,
                args=(site, interval, workers),
                daemon=True
            )
            thread.start()
            threads.append(thread)
            print(f"   ✅ Started: {site.upper()} (workers={workers}, interval={interval}s)")
        
        print(f"\n{'='*60}")
        print("📊 Dashboard is now live! Press Ctrl+C to stop all.")
        print(f"{'='*60}\n")
        
        try:
            # Keep main thread alive
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n\n🛑 Stopping all sites...")
            self.multi_progress.stop()
            self.multi_progress.close()
            for thread in threads:
                thread.join(timeout=2)
            print("✅ All sites stopped")
    
    def _run_site_loop(self, site: str, interval: int, workers: int):
        """Run a site in a continuous loop"""
        while True:
            try:
                self.monitor_cycle(site=site, workers=workers, show_progress=False)
            except Exception as e:
                logger.error(f"{site} | MONITOR_LOOP_ERROR | {e}")
                self.multi_progress.set_status(site, f"❌ Error")
            time.sleep(interval)

    # ---------------------------------
    # MONITOR SITE CONTINUOUS (For controller compatibility)
    # ---------------------------------
    def monitor_site_continuous(self, site: str, stop_event: threading.Event = None):
        """Continuously monitor a single site with stop event"""
        site = site.lower()
        config = get_site_config(site)
        interval = config.get("interval", 300)
        workers = config.get("workers", 20)
        
        # Start multi-progress
        self.multi_progress.start()
        
        cycle_count = 0
        
        print(f"\n{'='*50}")
        print(f"🚀 STARTING: {site.upper()}")
        print(f"   Workers: {workers} | Interval: {interval}s")
        print(f"{'='*50}")
        
        logger.info(f"{site} | MONITOR_STARTED | workers={workers} | interval={interval}s")
        
        while not (stop_event and stop_event.is_set()):
            cycle_count += 1
            
            try:
                pause_event = None
                if hasattr(self, 'pause_events') and site in self.pause_events:
                    pause_event = self.pause_events[site]
                
                stats = self.monitor_cycle(
                    site=site, 
                    workers=workers, 
                    show_progress=False,
                    stop_event=stop_event,
                    pause_event=pause_event
                )
                
                if stats.get('stopped', False):
                    print(f"\n⏹️ {site.upper()} stopped by user")
                    break
                
                if stats.get('total', 0) > 0:
                    print(f"   📊 Cycle #{cycle_count}: {stats['changed']} changed, {stats['no_change']} unchanged, {stats['errors']} errors")
                
            except Exception as e:
                logger.error(f"{site} | LOOP_ERROR | {e}")
                self.multi_progress.set_status(site, f"❌ Error")
            
            if stop_event and stop_event.is_set():
                break
            
            for _ in range(interval):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)
        
        self.multi_progress.stop()
        self.multi_progress.close()
        logger.info(f"{site} | MONITOR_STOPPED")
        print(f"\n✅ {site.upper()} monitoring stopped")