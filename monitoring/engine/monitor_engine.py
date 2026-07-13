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
    # MONITOR CYCLE (USING WORKER POOL)
    # ---------------------------------
    def monitor_cycle(self, site: str, workers: int = 20, show_progress: bool = True, stop_event: threading.Event = None) -> dict:
        """
        Run one monitoring cycle with progress bar
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
        
        # Create progress bar
        progress = None
        if show_progress:
            progress = ProgressBar(
                total=total,
                width=40,
                prefix=f"🔍 {site.upper()}"
            )
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(worker, product, self.processor): product
                for product in products
            }
            
            for future in as_completed(futures):
                # Check if we need to stop
                if stop_event and stop_event.is_set():
                    stopped = True
                    break
                
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
        # LOG CYCLE RECAP TO RECAP FILE (not debug)
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
            "timestamp": datetime.now().isoformat()
        }
        
        # Log to recap file using log_scrape_stats (which writes to recap)
        log_scrape_stats(
            site=site,
            stats=cycle_stats
        )
        
        # Also log changed products specifically to recap
        if self.changed_products:
            # Create a detailed recap entry for changed products
            changed_summary = {
                "cycle_type": "changed_products_detail",
                "site": site,
                "total_changed": len(self.changed_products),
                "changed_products": self.changed_products[:20],  # First 20
                "timestamp": datetime.now().isoformat()
            }
            log_scrape_stats(
                site=site,
                stats=changed_summary
            )
        
        return {
            "site": site,
            "total": total,
            "changed": changed_count,
            "no_change": no_change_count,
            "errors": error_count,
            "time": elapsed,
            "stopped": stopped,
            "timestamp": datetime.now().isoformat(),
            "changed_products": self.changed_products[:10]
        }

    # ---------------------------------
    # RUN LOOP
    # ---------------------------------
    def run(self, site: str, workers: int = 20, interval: int = 300):
        """
        Run continuous monitoring loop
        
        Args:
            site: Site name (e.g., 'megapc')
            workers: Number of worker threads
            interval: Sleep interval between cycles in seconds
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
            
            try:
                logger.info(f"{site} | CYCLE_{cycle_count} | STARTING")
                
                stats = self.monitor_cycle(
                    site=site,
                    workers=workers
                )
                
                logger.info(
                    f"{site} | CYCLE_{cycle_count} | COMPLETE | "
                    f"changed={stats.get('changed', 0)}/{stats.get('total', 0)} | "
                    f"time={stats.get('time', 0)}s"
                )
                
            except Exception as e:
                logger.error(f"{site} | MONITOR_LOOP_ERROR | {e}")
            
            logger.info(f"{site} | SLEEPING | {interval}s")
            time.sleep(interval)

    # ---------------------------------
    # MONITOR SITE CONTINUOUS
    # ---------------------------------
    def monitor_site_continuous(self, site: str, stop_event: threading.Event = None):
        """
        Continuously monitor a single site with stop event
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
                stats = self.monitor_cycle(
                    site=site, 
                    workers=workers, 
                    show_progress=True,
                    stop_event=stop_event
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