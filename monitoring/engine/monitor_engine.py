"""
Enhanced Monitoring Engine - Optimized with all fixes applied.
- Single cycle counter (no desync)
- Separate anomaly monitor thread (catches stalls mid-cycle)
- Producer prefetch (Redis fetch overlaps with worker consumption)
- Adaptive worker scaling
- Per-product timeout via concurrent.futures
- Exponential backoff on crashes
- Interruptible wait via stop_event.wait()
- Monitoring batches lifecycle integrated (start/end)
- Graceful stop with longer join
- Producer never dies on queue.Full (safe_put)
- Worker drains queue on shutdown (15s cap)
- Per-site product timeouts
- SIGINT/SIGTERM/atexit graceful shutdown
"""

import json
import time
import threading
import queue
import signal
import atexit
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError

from Database.Redis.Configuration.redis_client import RedisClient
from Database.Redis.cache_keys import site_pattern, extract_product_id_from_key
from core.logger import setup_logger
from monitoring.config import get_worker, get_site_config, get_enabled_sites
from monitoring.engine.multi_progress import get_multi_monitor_progress
from monitoring.engine.result_processor import ResultProcessor
from monitoring.debug_logger import get_debug_logger

logger = setup_logger("monitor_engine", "monitor")
debug = get_debug_logger()


class MonitorEngine:
    """Manages monitoring with streaming, adaptive workers, and cycle management."""
    
    # Optimal worker counts (fallback when config missing)
    OPTIMAL_WORKERS = {
        "megapc": 40,
        "tunisianet": 50,
        "sbs": 30,
        "mytek": 60,
        "spacenet": 60,
        "skymil": 5,
    }
    
    # Streaming
    STREAM_CHUNK_SIZE = 500
    QUEUE_MAX_SIZE = 1000
    PRODUCER_PREFETCH = True
    
    # Anomaly detection thresholds
    SLOW_RATE_THRESHOLD = 0.5
    HIGH_ERROR_THRESHOLD = 0.3
    STALL_TIMEOUT = 120
    ANOMALY_CHECK_INTERVAL = 10
    
    # Worker behavior
    PRODUCT_TIMEOUT = 60
    PROGRESS_UPDATE_INTERVAL = 10
    ADAPTIVE_MIN_WORKERS = 5
    ADAPTIVE_MAX_MULTIPLIER = 1.5
    
    # Per-site product timeouts (seconds)
    PRODUCT_TIMEOUT_BY_SITE = {
        "megapc": 30,
        "tunisianet": 30,
        "sbs": 30,
        "mytek": 30,
        "spacenet": 20,
        "skymil": 90,
    }
    
    # Shutdown drain
    SHUTDOWN_DRAIN_TIMEOUT = 15      # Max seconds to drain queue on shutdown
    DB_CHECKPOINT_INTERVAL = 30      # Seconds between DB checkpoints
    PRODUCER_PUT_TIMEOUT = 10        # Seconds to wait per queue.put attempt
    PRODUCER_MAX_ATTEMPTS = 5        # Max retries before dropping a product
    
    # Crash retry
    BACKOFF_START = 10
    BACKOFF_MAX = 300
    BACKOFF_MULTIPLIER = 2
    
    def __init__(self):
        self.redis = RedisClient().get_client()
        self.processor = ResultProcessor()
        self.progress = get_multi_monitor_progress()
        self.running_sites = {}
        self._lock = threading.Lock()
        self._stop_events = {}
        
        # SINGLE cycle counter per site (source of truth)
        self._site_cycles = {}
        self._cycle_lock = threading.Lock()
        
        # Anomaly tracking
        self._site_anomalies = {}
        self._anomaly_lock = threading.Lock()
        self._last_progress_time = {}
        self._last_progress_count = {}
        self._anomaly_monitor_thread = None
        self._anomaly_monitor_running = False
        
        # Track active cycle state per site (for anomaly monitor)
        self._active_cycle_stats = {}
        
        # Shutdown state (prevents double-fire)
        self._shutdown_called = False
        self._shutdown_lock = threading.Lock()
    
    # =====================================================
    # REDIS
    # =====================================================
    
    def _get_redis_keys(self, site: str) -> List[str]:
        site_lower = site.lower()
        pattern = site_pattern(site_lower)
        keys = []
        try:
            for key in self.redis.scan_iter(match=pattern, count=5000):
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                keys.append(key)
            logger.info(f"{site.upper()} | REDIS_SCAN | found={len(keys)} keys")
            return keys
        except Exception as e:
            logger.error(f"{site.upper()} | REDIS_SCAN_ERROR | {e}")
            return []
    
    def _parse_product(self, key: str, raw: any, site: str) -> Optional[Dict]:
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        try:
            data = json.loads(raw)
            product_id = extract_product_id_from_key(key)
            if not product_id or not data.get("is_enabled", True):
                return None
            url = data.get("url")
            if not url:
                return None
            return {
                "main_product_id": product_id,
                "site": site,
                "url": url,
                "price": data.get("price"),
                "discount_price": data.get("discount_price"),
                "availability": data.get("availability"),
                "special_id": data.get("special_id"),
                "price_valid_until": data.get("price_valid_until"),
                "check_failures": data.get("check_failures", 0),
            }
        except Exception:
            return None
    
    # =====================================================
    # CYCLE NUMBER — single source of truth
    # =====================================================
    
    def _increment_cycle(self, site: str) -> int:
        with self._cycle_lock:
            self._site_cycles[site] = self._site_cycles.get(site, 0) + 1
            return self._site_cycles[site]
    
    def _get_current_cycle(self, site: str) -> int:
        with self._cycle_lock:
            return self._site_cycles.get(site, 0)
    
    # =====================================================
    # WORKER COUNT — adaptive
    # =====================================================
    
    def _get_optimal_workers(self, site: str, total_products: int, 
                             last_rate: float = None) -> int:
        config_workers = get_site_config(site).get("workers", 20)
        optimal = self.OPTIMAL_WORKERS.get(site, 30)
        base = min(optimal, config_workers)
        
        if last_rate is not None and last_rate > 0:
            if last_rate > 3.0:
                base = int(base * 1.2)
            elif last_rate < 1.0:
                base = max(self.ADAPTIVE_MIN_WORKERS, int(base * 0.8))
        
        max_workers = int(config_workers * self.ADAPTIVE_MAX_MULTIPLIER)
        workers = min(base, max_workers, total_products)
        return max(self.ADAPTIVE_MIN_WORKERS, workers)
    
    def _get_product_timeout(self, site: str) -> int:
        """Per-site product fetch timeout."""
        return self.PRODUCT_TIMEOUT_BY_SITE.get(site, self.PRODUCT_TIMEOUT)
    
    # =====================================================
    # ANOMALY MONITOR — runs independently
    # =====================================================
    
    def _start_anomaly_monitor(self):
        if self._anomaly_monitor_running:
            return
        self._anomaly_monitor_running = True
        
        def monitor_loop():
            while self._anomaly_monitor_running:
                try:
                    self._check_all_anomalies()
                except Exception as e:
                    logger.error(f"ANOMALY_MONITOR | Error: {e}")
                time.sleep(self.ANOMALY_CHECK_INTERVAL)
        
        self._anomaly_monitor_thread = threading.Thread(
            target=monitor_loop, name="anomaly_monitor", daemon=True
        )
        self._anomaly_monitor_thread.start()
        logger.info("ANOMALY_MONITOR | Started")
    
    def _stop_anomaly_monitor(self):
        self._anomaly_monitor_running = False
        if self._anomaly_monitor_thread:
            self._anomaly_monitor_thread.join(timeout=2)
    
    def _check_all_anomalies(self):
        now = time.time()
        with self._anomaly_lock:
            for site, stats in list(self._active_cycle_stats.items()):
                completed = stats.get('completed', 0)
                errors = stats.get('errors', 0)
                start_time = stats.get('start_time', now)
                last_progress = stats.get('last_progress_time', start_time)
                last_count = stats.get('last_progress_count', 0)
                
                elapsed = now - start_time
                time_since_progress = now - last_progress
                
                if elapsed < 30:
                    continue
                
                if time_since_progress > self.STALL_TIMEOUT and completed == last_count:
                    if self._site_anomalies.get(site) != "STALLED":
                        logger.error(
                            f"🔴 {site.upper()} | STALLED | "
                            f"no progress for {time_since_progress:.0f}s | "
                            f"completed={completed}"
                        )
                        self._site_anomalies[site] = "STALLED"
                else:
                    if self._site_anomalies.get(site) == "STALLED":
                        self._site_anomalies.pop(site, None)
                
                if completed > 50:
                    rate = completed / elapsed
                    if rate < self.SLOW_RATE_THRESHOLD:
                        logger.warning(f"🟡 {site.upper()} | SLOW_RATE | {rate:.2f}/s")
                
                if completed > 20:
                    error_rate = errors / completed
                    if error_rate > self.HIGH_ERROR_THRESHOLD:
                        logger.error(
                            f"🔴 {site.upper()} | HIGH_ERROR_RATE | "
                            f"{error_rate:.1%} ({errors}/{completed})"
                        )
    
    def _register_active_cycle(self, site: str, stats: Dict):
        with self._anomaly_lock:
            self._active_cycle_stats[site] = stats
    
    def _unregister_active_cycle(self, site: str):
        with self._anomaly_lock:
            self._active_cycle_stats.pop(site, None)
    
    # =====================================================
    # SINGLE CYCLE
    # =====================================================
    
    def _run_single_cycle(self, site_name: str, stop_event: threading.Event = None) -> Dict:
        site = site_name.lower()
        if stop_event is None:
            stop_event = threading.Event()

        # ─── START BATCH TRACKING (DB) ───
        batch_id = None
        try:
            batch_id = self.processor.db.start_monitoring_batch(
                site=site, batch_type="scheduled", total_products=0
            )
        except Exception as e:
            logger.warning(f"{site.upper()} | BATCH_START_FAILED | {e}")

        cycle_start = time.time()
        interval = get_site_config(site).get("interval", 300)

        try:
            self.progress.add_site(site)

            keys = self._get_redis_keys(site)
            total_keys = len(keys)
            if batch_id:
                self.processor.db.update_monitoring_batch_total(batch_id, total_keys)

            if total_keys == 0:
                logger.warning(f"{site.upper()} | NO_KEYS_FOUND")
                self.progress.mark_error(site, "No products in Redis")
                if batch_id:
                    try:
                        self.processor.db.update_monitoring_batch(
                            batch_id, status="completed", processed=0, changed=0, errors=0
                        )
                    except Exception:
                        pass
                return {'site': site, 'total': 0, 'changed': 0}

            # ─── START CYCLE IN DASHBOARD ───
            self.progress.start_cycle(site, total=total_keys, interval=interval)

            # ─── PATCH BATCH TOTAL ───
            if batch_id:
                try:
                    self.processor.db.update_monitoring_batch_total(batch_id, total_keys)
                except Exception as e:
                    logger.warning(f"{site.upper()} | BATCH_TOTAL_UPDATE_FAILED | {e}")

            cycle_num = None
            with self._cycle_lock:
                cycle_num = self._site_cycles.get(site, 0)

            logger.info(f"{site.upper()} | CYCLE_{cycle_num}_START | keys={total_keys}")

            with self._lock:
                self.running_sites[site] = True

            result = self._process_streaming(site, keys, stop_event, cycle_num, batch_id=batch_id)

            elapsed = time.time() - cycle_start

            # ─── END BATCH TRACKING (DB) ───
            if batch_id:
                try:
                    status = "completed"
                    if stop_event.is_set():
                        status = "partial"
                    elif result.get('errors', 0) > 0 and result.get('completed', 0) == 0:
                        status = "failed"
                    self.processor.db.update_monitoring_batch(
                        batch_id, status=status,
                        processed=result.get('completed', 0),
                        changed=result.get('changed', 0),
                        errors=result.get('errors', 0),
                    )
                except Exception as e:
                    logger.warning(f"{site.upper()} | BATCH_UPDATE_FAILED | {e}")

            if result:
                changed = result.get('changed', 0)
                completed = result.get('completed', 0)
                errors = result.get('errors', 0)
                rate = completed / elapsed if elapsed > 0 else 0

                logger.info(
                    f"{site.upper()} | CYCLE_{cycle_num}_COMPLETE | "
                    f"processed={completed}/{total_keys} | "
                    f"changed={changed} | errors={errors} | "
                    f"rate={rate:.2f}/s | time={elapsed:.1f}s"
                )

                self.progress.update(
                    site,
                    completed=completed,
                    changed=changed,
                    no_change=result.get('no_change', 0),
                    errors=errors,
                    rate=rate,
                )
                self.progress.mark_complete(site)
                self.progress.set_next_monitor(site, interval)

            return result

        except Exception as e:
            elapsed = time.time() - cycle_start
            logger.error(f"🔴 {site.upper()} | CYCLE_CRASH | error={e} | time={elapsed:.1f}s")
            import traceback
            traceback.print_exc()

            if batch_id:
                try:
                    self.processor.db.update_monitoring_batch(
                        batch_id,
                        status="failed",
                        processed=0, changed=0, errors=1,
                        total=locals().get('total_keys', 0) or 0,
                    )
                except Exception:
                    pass

            self.progress.mark_error(site, message=str(e)[:80])
            return {'site': site, 'error': str(e)}
        finally:
            with self._lock:
                self.running_sites[site] = False
            self._unregister_active_cycle(site)
    
    # =====================================================
    # STREAMING PROCESSOR
    # =====================================================
    
    def _process_streaming(self, site: str, keys: List[str],
                           stop_event: threading.Event, cycle_num: int,
                           batch_id: Optional[str] = None) -> Dict:
        worker_func = get_worker(site)
        if not worker_func:
            logger.error(f"{site.upper()} | NO_WORKER_FOUND")
            return {'completed': 0, 'changed': 0, 'errors': 0}

        work_queue = queue.Queue(maxsize=self.QUEUE_MAX_SIZE)
        streaming_done = threading.Event()
        producer_done = threading.Event()

        stats = {
            'completed': 0,
            'changed': 0,
            'no_change': 0,
            'errors': 0,
            'start_time': time.time(),
            'last_progress_time': time.time(),
            'last_progress_count': 0,
            'last_db_checkpoint': time.time(),
        }
        stats_lock = threading.Lock()
        total_keys = len(keys)

        self._register_active_cycle(site, stats)

        # ═══════════════════════════════════════════════════
        # WORKER FUNCTION (with per-product timeout + drain)
        # ═══════════════════════════════════════════════════
        def process_worker():
            drain_start = None
            while True:
                # Exit when: (stopped OR streaming_done) AND queue empty
                if (stop_event.is_set() or streaming_done.is_set()) and work_queue.empty():
                    break

                # Track drain start for hard cap
                if stop_event.is_set() and drain_start is None:
                    drain_start = time.time()

                # Hard cap: stop draining after SHUTDOWN_DRAIN_TIMEOUT
                if drain_start is not None and time.time() - drain_start > self.SHUTDOWN_DRAIN_TIMEOUT:
                    logger.info(
                        f"{site.upper()} | DRAIN_TIMEOUT | "
                        f"{work_queue.qsize()} items left in queue"
                    )
                    break

                try:
                    product = work_queue.get(timeout=2)
                except queue.Empty:
                    if streaming_done.is_set():
                        break
                    continue

                product_id = product.get("main_product_id")
                try:
                    with ThreadPoolExecutor(max_workers=1) as single_pool:
                        future = single_pool.submit(worker_func, product, self.processor)
                        try:
                            result = future.result(timeout=self._get_product_timeout(site))
                        except FutureTimeoutError:
                            logger.warning(
                                f"{site.upper()} | WORKER_TIMEOUT | {product_id} | "
                                f">{self._get_product_timeout(site)}s"
                            )
                            try:
                                self.processor.handle_failed_product(site, product_id)
                            except Exception:
                                pass
                            result = False

                    with stats_lock:
                        stats['completed'] += 1
                        if result:
                            stats['changed'] += 1
                        else:
                            stats['no_change'] += 1
                        stats['last_progress_time'] = time.time()
                        stats['last_progress_count'] = stats['completed']

                        if stats['completed'] % self.PROGRESS_UPDATE_INTERVAL == 0:
                            elapsed = time.time() - stats['start_time']
                            rate = stats['completed'] / elapsed if elapsed > 0 else 0
                            self.progress.update(
                                site,
                                completed=stats['completed'],
                                changed=stats['changed'],
                                no_change=stats['no_change'],
                                errors=stats['errors'],
                                rate=rate,
                            )

                            now = time.time()
                            if now - stats.get('last_db_checkpoint', 0) > self.DB_CHECKPOINT_INTERVAL:
                                stats['last_db_checkpoint'] = now
                                if batch_id is not None:
                                    try:
                                        self.processor.db.update_batch_progress(
                                            batch_id,
                                            processed=stats['completed'],
                                            changed=stats['changed'],
                                            errors=stats['errors'],
                                        )
                                    except Exception:
                                        pass

                except Exception as e:
                    with stats_lock:
                        stats['errors'] += 1
                        stats['completed'] += 1
                        stats['last_progress_time'] = time.time()
                        stats['last_progress_count'] = stats['completed']

                    logger.warning(
                        f"{site.upper()} | WORKER_ERROR | {product_id} | "
                        f"{type(e).__name__}: {str(e)[:80]}"
                    )
                finally:
                    work_queue.task_done()

        # ═══════════════════════════════════════════════════
        # PRODUCER (with prefetch + safe_put)
        # ═══════════════════════════════════════════════════
        def producer():
            chunk_size = self.STREAM_CHUNK_SIZE
            loaded = 0
            dropped = 0
            next_fetch_future = None
            producer_pool = ThreadPoolExecutor(max_workers=1)

            def fetch_chunk(chunk_keys):
                try:
                    pipeline = self.redis.pipeline()
                    for k in chunk_keys:
                        pipeline.get(k)
                    return chunk_keys, pipeline.execute()
                except Exception as e:
                    logger.error(f"{site.upper()} | REDIS_FETCH_ERROR | {e}")
                    return chunk_keys, []

            def safe_put(product) -> bool:
                """Put with backoff; never let the producer thread die."""
                nonlocal dropped
                attempts = 0
                while attempts < self.PRODUCER_MAX_ATTEMPTS:
                    if stop_event.is_set():
                        # Shutdown — one fast attempt then give up
                        try:
                            work_queue.put(product, timeout=1)
                            return True
                        except queue.Full:
                            dropped += 1
                            return False
                    try:
                        work_queue.put(product, timeout=self.PRODUCER_PUT_TIMEOUT)
                        return True
                    except queue.Full:
                        attempts += 1
                        logger.debug(
                            f"{site.upper()} | QUEUE_FULL | attempt={attempts} | "
                            f"loaded={loaded} dropped={dropped}"
                        )
                dropped += 1
                return False

            try:
                for i in range(0, total_keys, chunk_size):
                    if stop_event.is_set():
                        break

                    chunk_keys = keys[i:i + chunk_size]

                    if next_fetch_future is not None:
                        chunk_keys_prev, results = next_fetch_future.result()
                        chunk_keys = chunk_keys_prev
                    else:
                        chunk_keys, results = fetch_chunk(chunk_keys)

                    next_i = i + chunk_size
                    if next_i < total_keys:
                        next_chunk_keys = keys[next_i:next_i + chunk_size]
                        next_fetch_future = producer_pool.submit(fetch_chunk, next_chunk_keys)
                    else:
                        next_fetch_future = None

                    for j, raw in enumerate(results):
                        if stop_event.is_set():
                            break
                        product = self._parse_product(chunk_keys[j], raw, site)
                        if product:
                            if safe_put(product):
                                loaded += 1
            finally:
                producer_pool.shutdown(wait=False)
                producer_done.set()
                logger.info(
                    f"{site.upper()} | C{cycle_num} PRODUCER_DONE | "
                    f"loaded={loaded} dropped={dropped}"
                )

        # ═══════════════════════════════════════════════════
        # START PRODUCER FIRST
        # ═══════════════════════════════════════════════════
        producer_thread = threading.Thread(
            target=producer, name=f"{site}_producer", daemon=True
        )
        producer_thread.start()

        # ═══════════════════════════════════════════════════
        # READ PRIOR RATE
        # ═══════════════════════════════════════════════════
        site_data = self.progress.sites.get(site)
        last_rate = site_data.rate if site_data is not None else None

        # ═══════════════════════════════════════════════════
        # COMPUTE WORKERS
        # ═══════════════════════════════════════════════════
        workers = self._get_optimal_workers(site, total_keys, last_rate)

        logger.info(
            f"{site.upper()} | C{cycle_num} STARTING_{workers}_WORKERS | rate_prev={last_rate}"
        )

        # ═══════════════════════════════════════════════════
        # START WORKERS
        # ═══════════════════════════════════════════════════
        threads = []
        for i in range(workers):
            thread = threading.Thread(
                target=process_worker, name=f"{site}_worker_{i}", daemon=True
            )
            thread.start()
            threads.append(thread)

        # ═══════════════════════════════════════════════════
        # WAIT FOR PRODUCER
        # ═══════════════════════════════════════════════════
        producer_thread.join()

        # ═══════════════════════════════════════════════════
        # SIGNAL WORKERS NO MORE ITEMS
        # ═══════════════════════════════════════════════════
        streaming_done.set()

        # Wait for workers to drain the queue
        work_queue.join()

        # Wait for workers to exit
        for t in threads:
            t.join(timeout=30)

        elapsed = time.time() - stats['start_time']
        rate = stats['completed'] / elapsed if elapsed > 0 else 0

        logger.info(
            f"{site.upper()} | C{cycle_num} COMPLETE | "
            f"processed={stats['completed']} | changed={stats['changed']} | "
            f"errors={stats['errors']} | rate={rate:.2f}/s | time={elapsed:.1f}s"
        )

        return {
            'total': total_keys,
            'completed': stats['completed'],
            'changed': stats['changed'],
            'no_change': stats['no_change'],
            'errors': stats['errors'],
            'time': elapsed,
        }
    
    # =====================================================
    # MONITOR LOOP
    # =====================================================
    
    def monitor_site(self, site_name: str, stop_event: threading.Event = None) -> Dict:
        site = site_name.lower()
        if stop_event is None:
            stop_event = threading.Event()

        interval = get_site_config(site).get("interval", 300)
        logger.info(f"{site.upper()} | MONITOR_STARTED | interval={interval}s")

        backoff = self.BACKOFF_START
        cycles_completed = 0

        while not stop_event.is_set():
            try:
                self._run_single_cycle(site, stop_event)
                cycles_completed += 1
                backoff = self.BACKOFF_START

                if stop_event.is_set():
                    break

                self.progress.mark_waiting(site, interval)
                logger.info(f"{site.upper()} | WAITING_{interval}s_FOR_NEXT_CYCLE")
                stop_event.wait(timeout=interval)

            except Exception as e:
                logger.error(f"🔴 {site.upper()} | CYCLE_CRASH | {e}")
                import traceback
                traceback.print_exc()
                self.progress.mark_error(site, message=str(e)[:80])
                logger.info(f"{site.upper()} | BACKOFF_{backoff}s")
                stop_event.wait(timeout=backoff)
                backoff = min(self.BACKOFF_MAX, backoff * self.BACKOFF_MULTIPLIER)

        self.progress.mark_stopped(site)
        logger.info(f"{site.upper()} | MONITOR_STOPPED | cycles={cycles_completed}")
        return {"site": site, "stopped": True, "cycles_completed": cycles_completed}
    
    # =====================================================
    # GRACEFUL SHUTDOWN
    # =====================================================
    
    def _register_shutdown_handlers(self):
        """Register SIGINT, SIGTERM, and atexit handlers."""
        def _handler(signum=None, frame=None):
            with self._shutdown_lock:
                if self._shutdown_called:
                    return
                self._shutdown_called = True
            logger.info(f"SHUTDOWN_SIGNAL_RECEIVED | sig={signum}")
            try:
                self.stop_all()
                self._close_active_batches()
                self.processor.flush_all()
                # Sweep any leftover orphans
                try:
                    self.processor.db.sweep_orphan_batches(older_than_seconds=0)
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"SHUTDOWN_ERROR | {e}")
        
        # Register atexit (always works)
        atexit.register(_handler)
        
        # Try to register signal handlers (Windows may refuse SIGTERM)
        try:
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError, AttributeError):
            pass
        try:
            signal.signal(signal.SIGTERM, _handler)
        except (ValueError, OSError, AttributeError):
            pass
    
    def _close_active_batches(self):
        """Called on shutdown — mark any running batches as interrupted."""
        try:
            closed = self.processor.db.sweep_orphan_batches(older_than_seconds=0)
            if closed > 0:
                logger.info(f"SHUTDOWN_SWEEP | closed {closed} active batches")
        except Exception as e:
            logger.warning(f"SHUTDOWN_SWEEP_FAILED | {e}")
    
    # =====================================================
    # RUN ALL
    # =====================================================
    
    def run_all(self) -> None:
        sites = get_enabled_sites()
        if not sites:
            print("\n❌ No enabled sites found!")
            return

        # ─── REGISTER SHUTDOWN HANDLERS ───
        self._register_shutdown_handlers()

        # ─── SWEEP ORPHAN BATCHES FROM PREVIOUS RUNS ───
        try:
            closed = self.processor.db.sweep_orphan_batches(older_than_seconds=0)
            if closed > 0:
                logger.warning(f"STARTUP_SWEEP | closed {closed} orphan batches from prior run")
        except Exception as e:
            logger.warning(f"STARTUP_SWEEP_FAILED | {e}")
        
        # Normalize site names to lowercase (consistent keys)
        sites = [s.lower() for s in sites]
        
        print(f"\n{'='*60}")
        print("🚀 MONITORING SYSTEM - OPTIMIZED STREAMING MODE")
        print(f"{'='*60}")
        print(f"Sites: {', '.join([s.upper() for s in sites])}")
        print("   Adaptive workers | Prefetch | Anomaly monitor | Batch tracking")
        print("   Press Ctrl+C to stop all")
        print(f"{'='*60}\n")
        
        self._start_anomaly_monitor()
        self.progress.start()
        
        for site in sites:
            if site not in self._stop_events:
                self._stop_events[site] = threading.Event()
            self.progress.add_site(site)
        
        threads = {}
        for site in sites:
            thread = threading.Thread(
                target=self.monitor_site,
                args=(site, self._stop_events[site]),
                name=f"monitor_{site}",
                daemon=True,
            )
            thread.start()
            threads[site] = thread
            logger.info(f"🚀 Started: {site.upper()}")
        
        try:
            while True:
                time.sleep(5)
                
                for site, thread in list(threads.items()):
                    if not thread.is_alive():
                        logger.error(f"🔴 MONITOR_THREAD_DIED | {site}")
                        if site in self._stop_events and not self._stop_events[site].is_set():
                            logger.info(f"🔄 Restarting monitor thread: {site}")
                            new_thread = threading.Thread(
                                target=self.monitor_site,
                                args=(site, self._stop_events[site]),
                                name=f"monitor_{site}",
                                daemon=True,
                            )
                            new_thread.start()
                            threads[site] = new_thread
        except KeyboardInterrupt:
            print("\n\n🛑 Stopping all sites...")
            self.stop_all()
            for site, thread in threads.items():
                thread.join(timeout=30)
            self._stop_anomaly_monitor()
            self._close_active_batches()
            self.progress.stop()
            self.processor.flush_all()
            print("✅ All sites stopped")
    
    # =====================================================
    # CONTROL
    # =====================================================
    
    def stop_site(self, site: str) -> bool:
        site = site.lower()
        if site in self._stop_events:
            self._stop_events[site].set()
            logger.info(f"{site.upper()} | STOP_SIGNALED")
            return True
        return False
    
    def stop_all(self) -> None:
        logger.info("STOPPING_ALL_SITES")
        for event in self._stop_events.values():
            event.set()