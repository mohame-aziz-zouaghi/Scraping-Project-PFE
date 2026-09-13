"""
MegaPC monitoring worker - Fetches product page and extracts tracked fields.
Optimized: Detailed error logging for debugging failures.
"""

import threading
import time
from typing import Dict
from datetime import datetime, timedelta

from core.logger import setup_logger
from core.Web.smart_requester import create_requester
from monitoring.config import get_requester_config
from monitoring.extractors.megapc_fast import extract_megapc_fast
from monitoring.debug_logger import get_debug_logger

logger = setup_logger("megapc_worker", "monitor")
debug = get_debug_logger()

_requester = None
_lock = threading.Lock()
_processed_ids = set()
_processed_lock = threading.Lock()

# Error tracking
_error_stats = {
    'fetch_failed': 0,
    'extract_failed': 0,
    'timeout': 0,
    'exception': 0,
    'missing_url': 0
}
_error_lock = threading.Lock()


def _get_requester():
    """Get or create a shared SmartRequester instance."""
    global _requester
    if _requester is None:
        with _lock:
            if _requester is None:
                config = get_requester_config("megapc")
                config["max_retries"] = 2
                config["timeout"] = 20
                config["delay_between_requests"] = (0.3, 1.0)
                _requester = create_requester(site_name="megapc_monitor", config=config)
                logger.info(f"MEGAPC_REQUESTER_CREATED | rpm={config.get('requests_per_minute')}")
    return _requester


def _log_error_stats():
    """Log error statistics periodically."""
    with _error_lock:
        if sum(_error_stats.values()) > 0:
            logger.warning(
                f"MEGAPC | ERROR_STATS | fetch_failed={_error_stats['fetch_failed']} | "
                f"extract_failed={_error_stats['extract_failed']} | "
                f"timeout={_error_stats['timeout']} | "
                f"exception={_error_stats['exception']} | "
                f"missing_url={_error_stats['missing_url']}"
            )


def megapc_worker(product: Dict, processor) -> bool:
    """MegaPC worker with detailed error logging."""
    product_id = product.get("main_product_id")
    url = product.get("url")
    
    if not product_id:
        debug.log_worker_skip("megapc", "unknown", "missing_id")
        return False
    
    if not url:
        with _error_lock:
            _error_stats['missing_url'] += 1
        
        logger.error(f"MEGAPC | MISSING_URL | {product_id} | URL is None in Redis cache")
        debug.log_worker_skip("megapc", product_id, "missing_url_from_redis")
        
        # Don't call handle_failed_product for missing URL - this is a data issue
        return False
    
    # Prevent duplicate processing
    with _processed_lock:
        if product_id in _processed_ids:
            return False
        _processed_ids.add(product_id)
    
    start_time = time.time()
    debug.log_worker_start("megapc", product_id, url)
    
    try:
        requester = _get_requester()
        
        # Fetch with retry logic
        html = None
        fetch_error = None
        
        for attempt in range(2):
            try:
                html = requester.get_html(url, timeout=20)
                if html:
                    break
                fetch_error = "empty_html"
            except Exception as e:
                fetch_error = str(e)
                if attempt == 1:
                    logger.error(f"MEGAPC | FETCH_FAILED | {product_id} | url={url[:60]} | attempt={attempt+1} | error={fetch_error}")
                    with _error_lock:
                        _error_stats['fetch_failed'] += 1
                else:
                    time.sleep(0.5)
        
        if not html:
            # Log detailed fetch error
            logger.error(f"MEGAPC | FETCH_FAILED_FINAL | {product_id} | error={fetch_error}")
            debug.log_fetch_end("megapc", product_id, f"FAILED: {fetch_error}", time.time() - start_time)
            
            processor.handle_failed_product("megapc", product_id)
            debug.log_worker_end("megapc", product_id, False, time.time() - start_time)
            return False
        
        debug.log_fetch_end("megapc", product_id, "SUCCESS", time.time() - start_time)
        
        # Extract fields
        tracked_fields = extract_megapc_fast(html)
        
        if not tracked_fields:
            with _error_lock:
                _error_stats['extract_failed'] += 1
            
            logger.error(f"MEGAPC | EXTRACT_FAILED | {product_id} | no fields extracted from HTML")
            debug.log_extract_end("megapc", product_id, False)
            
            # Don't call handle_failed_product for extraction errors - HTML was fetched
            debug.log_worker_end("megapc", product_id, False, time.time() - start_time)
            return False
        
        debug.log_extract_end("megapc", product_id, True, tracked_fields)
        
        # Ensure defaults
        if tracked_fields.get("price_valid_until") is None:
            tracked_fields["price_valid_until"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        
        if tracked_fields.get("availability") is None:
            tracked_fields["availability"] = "OutOfStock"
        
        # Process result
        result = processor.process("megapc", product_id, tracked_fields)
        
        elapsed = time.time() - start_time
        if elapsed > 30:
            logger.warning(f"MEGAPC | SLOW_WORKER | {product_id} | {elapsed:.1f}s")
        
        debug.log_worker_end("megapc", product_id, result, elapsed)
        return result
        
    except Exception as e:
        elapsed = time.time() - start_time
        with _error_lock:
            _error_stats['exception'] += 1
        
        logger.error(f"MEGAPC | WORKER_EXCEPTION | {product_id} | url={url[:60]} | error={e} | time={elapsed:.2f}s")
        import traceback
        logger.error(f"MEGAPC | TRACEBACK | {product_id} | {traceback.format_exc()[:500]}")
        
        debug.log_worker_error("megapc", product_id, str(e))
        processor.handle_failed_product("megapc", product_id)
        
        # Log error stats every 50 errors
        with _error_lock:
            if sum(_error_stats.values()) % 50 == 0:
                _log_error_stats()
        
        return False
    finally:
        with _processed_lock:
            _processed_ids.discard(product_id)


def create_megapc_worker():
    """Create a MegaPC worker function."""
    return megapc_worker