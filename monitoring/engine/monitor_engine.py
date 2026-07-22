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

logger = setup_logger("monitor_engine", "monitor")


class MonitorEngine:

    def __init__(self):
        self.redis = RedisClient().get_client()
        self.processor = ResultProcessor()
        self.worker_pool = None
        self.changed_products = []  # Track changed products for recap

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
    # MONITOR CYCLE (WITH PAUSE/STOP SUPPORT)
    # ---------------------------------
    def monitor_cycle(self, site: str, workers: int = 20, show_progress: bool = True, stop_event: threading.Event = None, pause_event: threading.Event = None) -> dict:
        """
        Run one monitoring cycle with adaptive batch processing, stop and pause event checking
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
            return {"site": site, "total": 0, "changed": 0, "no_change": 0, "errors": 0, "time": 0}
        
        # Get the worker function
        worker = SITE_WORKERS.get(site)
        
        if not worker:
            logger.error(f"{site} | NO_WORKER_FOUND")
            return {"site": site, "total": 0, "changed": 0, "no_change": 0, "errors": 0, "time": 0, "error": "No worker registered"}
        
        # Progress tracking
        changed_count = 0
        no_change_count = 0
        error_count = 0
        completed = 0
        stopped = False
        paused = False
        
        # Create progress bar
        progress = None
        if show_progress:
            progress = ProgressBar(
                total=total,
                width=40,
                prefix=f"🔍 {site.upper()}"
            )
        
        # Process products in smaller batches to check pause/stop more frequently
        BATCH_SIZE = 50  # Process 50 products at a time
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        
        for batch_idx in range(total_batches):
            # =====================================================
            # CHECK STOP AND PAUSE EVENTS BEFORE EACH BATCH
            # =====================================================
            if stop_event and stop_event.is_set():
                stopped = True
                logger.info(f"{site} | STOP_SIGNAL_RECEIVED | stopping cycle")
                break
            
            if pause_event and pause_event.is_set():
                paused = True
                logger.info(f"{site} | PAUSE_SIGNAL_RECEIVED | pausing cycle")
                
                # Update progress bar to show paused state
                if progress:
                    progress.update(completed, "⏸️ PAUSED")
                
                # Wait until pause is cleared or stop is set
                while pause_event.is_set() and not (stop_event and stop_event.is_set()):
                    time.sleep(1)
                
                if stop_event and stop_event.is_set():
                    stopped = True
                    break
                
                # Resume processing
                logger.info(f"{site} | RESUME_SIGNAL_RECEIVED | resuming cycle")
                if progress:
                    progress.update(completed, "▶️ RESUMED")
            
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
                        logger.info(f"{site} | STOP_SIGNAL_RECEIVED | stopping during batch")
                        # Cancel remaining futures
                        for f in futures:
                            f.cancel()
                        break
                    
                    if pause_event and pause_event.is_set():
                        paused = True
                        logger.info(f"{site} | PAUSE_SIGNAL_RECEIVED | pausing during batch")
                        
                        # Cancel remaining futures
                        for f in futures:
                            f.cancel()
                        
                        # Update progress bar
                        if progress:
                            progress.update(completed, "⏸️ PAUSED")
                        
                        # Wait until pause is cleared or stop is set
                        while pause_event.is_set() and not (stop_event and stop_event.is_set()):
                            time.sleep(1)
                        
                        if stop_event and stop_event.is_set():
                            stopped = True
                            break
                        
                        # Resume processing
                        logger.info(f"{site} | RESUME_SIGNAL_RECEIVED | resuming cycle")
                        if progress:
                            progress.update(completed, "▶️ RESUMED")
                        break  # Break out of batch processing to restart with new batch
                    
                    try:
                        result = future.result(timeout=60)
                        if result:
                            changed_count += 1
                            # Track changed product for recap
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
                    
                    # Update progress bar with stats
                    if progress:
                        extra_info = f"✓ {changed_count} | ○ {no_change_count} | ✗ {error_count}"
                        progress.update(completed, extra_info)
                    
                    # If we broke due to pause, break out of the for loop
                    if paused:
                        break
                
                # If we broke due to stop or pause, break out of batch loop
                if stopped:
                    break
                if paused:
                    paused = False  # Reset paused flag after resuming
                    continue  # Continue to next batch
        
        # Close progress bar
        if progress:
            progress.close()
        
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
        
        # Also flush any remaining global batches
        global_flush_results = self.processor.flush_all()
        if global_flush_results:
            logger.info(f"{site} | GLOBAL_FLUSH | {sum(global_flush_results.values())} products updated across {len(global_flush_results)} sites")
        
        # =====================================================
        # LOG CYCLE RECAP TO RECAP FILE
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
        
        # Log to recap file
        log_scrape_stats(site=site, stats=cycle_stats)
        
        # Get batch stats for return
        batch_stats = self.processor.get_stats()
        logger.info(f"{site} | BATCH_STATS | {batch_stats}")
        
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
            "batch_stats": batch_stats
        }

    # ---------------------------------
    # RUN LOOP
    # ---------------------------------
    def run(self, site: str, workers: int = 20, interval: int = 300, pause_event: threading.Event = None):
        """
        Run continuous monitoring loop with pause support
        
        Args:
            site: Site name (e.g., 'megapc')
            workers: Number of worker threads
            interval: Sleep interval between cycles in seconds
            pause_event: Event to pause monitoring
        """
        site = site.lower()
        
        logger.info(
            f"{site} | MONITOR_STARTED | "
            f"workers={workers} | "
            f"interval={interval}s"
        )
        
        cycle_count = 0
        
        while True:
            cycle_count += 1
            
            # Check if paused before starting new cycle
            if pause_event and pause_event.is_set():
                logger.info(f"{site} | PAUSED | waiting for resume...")
                while pause_event.is_set():
                    time.sleep(1)
                logger.info(f"{site} | RESUMED | continuing monitoring...")
            
            try:
                logger.info(f"{site} | CYCLE_{cycle_count} | STARTING")
                self.processor = ResultProcessor()  # Fresh processor per cycle
                
                stats = self.monitor_cycle(
                    site=site,
                    workers=workers,
                    show_progress=True,
                    stop_event=None,
                    pause_event=pause_event
                )
                
                logger.info(
                    f"{site} | CYCLE_{cycle_count} | COMPLETE | "
                    f"changed={stats.get('changed', 0)}/{stats.get('total', 0)} | "
                    f"time={stats.get('time', 0)}s"
                )
                
            except Exception as e:
                logger.error(f"{site} | MONITOR_LOOP_ERROR | {e}")
            
            # Check pause before sleeping
            if pause_event and pause_event.is_set():
                continue
            
            logger.info(f"{site} | SLEEPING | {interval}s")
            time.sleep(interval)

    # ---------------------------------
    # RUN SITE (Single site)
    # ---------------------------------
    def run_site(self, site: str, workers: int = None, interval: int = None, show_progress: bool = True):
        """
        Run monitoring for a specific site
        
        Args:
            site: Site name
            workers: Number of workers (overrides config)
            interval: Check interval in seconds (overrides config)
            show_progress: Show progress bar
        """
        site = site.lower()
        
        if site not in SITE_WORKERS:
            print(f"\n❌ Site '{site}' not registered!")
            print(f"   Available sites: {', '.join(SITE_WORKERS.keys())}")
            return
        
        # Update config if overrides provided
        if workers:
            update_site_config(site, workers=workers)
        if interval:
            update_site_config(site, interval=interval)
        
        # Run monitoring (without stop_event for single site mode)
        config = get_site_config(site)
        interval = config.get("interval", 300)
        workers = config.get("workers", 20)
        
        cycle_count = 0
        
        print(f"\n{'='*60}")
        print(f"🚀 STARTING MONITORING: {site.upper()}")
        print(f"   Workers: {workers} | Interval: {interval}s")
        print(f"   Press Ctrl+C to stop")
        print(f"{'='*60}\n")
        
        try:
            while True:
                cycle_count += 1
                
                print(f"\n{'─'*60}")
                print(f"🔄 {site.upper()} | CYCLE #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'─'*60}")
                
                stats = self.monitor_cycle(site=site, workers=workers, show_progress=show_progress)
                
                print(f"\n   📈 Cycle #{cycle_count} Statistics:")
                print(f"      📦 Products: {stats['total']}")
                print(f"      🔄 Changes: {stats['changed']}")
                print(f"      ❌ Errors: {stats['errors']}")
                print(f"      ⏱️ Duration: {stats['time']}s")
                
                # Show changed products sample
                changed_products = stats.get('changed_products', [])
                if changed_products:
                    print(f"\n   📝 Changed products (sample):")
                    for i, prod in enumerate(changed_products[:5], 1):
                        print(f"      {i}. {prod['product_id']} - Price: {prod['price']} TND")
                    if len(changed_products) > 5:
                        print(f"      ... and {len(changed_products) - 5} more")
                
                print(f"\n   💤 Sleeping for {interval}s...")
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print(f"\n\n🛑 Monitoring stopped for {site.upper()}")

    # ---------------------------------
    # RUN ALL SITES
    # ---------------------------------
    def run_all(self):
        """Run monitoring for all enabled sites"""
        from monitoring.engine.site_registry import get_enabled_sites
        
        enabled_sites = get_enabled_sites()
        
        if not enabled_sites:
            print("\n❌ No enabled sites found!")
            return
        
        print(f"\n{'='*60}")
        print("🚀 RUNNING ALL ENABLED SITES")
        print(f"{'='*60}")
        print(f"📋 Sites: {', '.join([s.upper() for s in enabled_sites])}")
        print(f"{'='*60}\n")
        
        for site in enabled_sites:
            print(f"\n▶️ Starting: {site.upper()}")
            self.run_site(site)

    # ---------------------------------
    # MONITOR SITE CONTINUOUS (For controller)
    # ---------------------------------
    def monitor_site_continuous(self, site: str, stop_event: threading.Event = None):
        """
        Continuously monitor a single site with stop event (for controller)
        """
        site = site.lower()
        config = get_site_config(site)
        interval = config.get("interval", 300)
        workers = config.get("workers", 20)
        
        cycle_count = 0
        
        print(f"\n{'='*50}")
        print(f"🚀 STARTING: {site.upper()}")
        print(f"   Workers: {workers} | Interval: {interval}s")
        print(f"{'='*50}")
        
        logger.info(f"{site} | MONITOR_STARTED | workers={workers} | interval={interval}s")
        
        while not (stop_event and stop_event.is_set()):
            cycle_count += 1
            
            print(f"\n{'─'*50}")
            print(f"🔄 {site.upper()} | Cycle #{cycle_count} | {datetime.now().strftime('%H:%M:%S')}")
            print(f"{'─'*50}")
            
            try:
                # Create a pause event for this site if needed
                # (pause_event is managed by controller)
                pause_event = None
                if hasattr(self, 'pause_events') and site in self.pause_events:
                    pause_event = self.pause_events[site]
                
                stats = self.monitor_cycle(
                    site=site, 
                    workers=workers, 
                    show_progress=True,
                    stop_event=stop_event,
                    pause_event=pause_event
                )
                
                # If cycle was stopped, break out
                if stats.get('stopped', False):
                    print(f"\n⏹️ {site.upper()} stopped by user")
                    break
                
                print(f"\n   📊 Cycle #{cycle_count}: {stats['changed']} changed, {stats['no_change']} unchanged, {stats['errors']} errors")
                
                # Show changed products sample
                changed_products = stats.get('changed_products', [])
                if changed_products:
                    print(f"\n   📝 Changed products (sample):")
                    for i, prod in enumerate(changed_products[:5], 1):
                        print(f"      {i}. {prod['product_id']} - Price: {prod['price']} TND")
                    if len(changed_products) > 5:
                        print(f"      ... and {len(changed_products) - 5} more")
                
            except Exception as e:
                logger.error(f"{site} | LOOP_ERROR | {e}")
                print(f"\n   ❌ Error: {e}")
            
            if stop_event and stop_event.is_set():
                break
            
            # Sleep in small intervals to allow stop checking
            print(f"\n   💤 Sleeping {interval}s...")
            for _ in range(interval):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)
        
        logger.info(f"{site} | MONITOR_STOPPED")
        print(f"\n✅ {site.upper()} monitoring stopped")