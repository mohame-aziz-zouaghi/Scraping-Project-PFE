"""
Skymil monitoring worker - Optimized with site-wide hash comparison.
1. Fetch ALL products from API (single request)
2. Compute site-wide hash
3. Compare with cached site hash
4. If same → skip ALL products (no changes)
5. If different → check each product's individual hash
6. Only process products whose hash changed
"""

import threading
import time
import hashlib
import json
import requests
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta

from core.logger import setup_logger
from monitoring.config import get_requester_config
from monitoring.debug_logger import get_debug_logger
from Database.Redis.Configuration.redis_client import get_redis_client

logger = setup_logger("skymil_worker", "monitor")
debug = get_debug_logger()

# Supabase API configuration
SUPABASE_URL = "https://twhtfguapjatibrzbmbi.supabase.co/rest/v1"

# Cache keys
SITE_HASH_KEY = "skymil:site_hash"
SITE_HASH_TIMESTAMP_KEY = "skymil:site_hash_timestamp"
PRODUCT_HASH_PREFIX = "skymil:hash:"

# Thread safety
_lock = threading.Lock()
_processed_ids = set()
_processed_lock = threading.Lock()

# Error tracking
_error_stats = {
    'api_fetch_failed': 0,
    'extract_failed': 0,
    'exception': 0,
    'missing_url': 0,
    'success': 0,
    'no_change': 0,
    'site_hash_match': 0,
    'site_hash_changed': 0,
    'products_skipped': 0,
    'products_processed': 0
}
_error_lock = threading.Lock()

# Cache for API data (site-wide)
_site_data_cache = {
    'products': None,
    'product_hashes': None,
    'site_hash': None,
    'timestamp': None,
    'ttl': 300  # Cache for 5 minutes
}
_cache_lock = threading.Lock()


def _get_credentials():
    """Get Supabase credentials from environment."""
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    token = os.getenv("SKYMIL_SUPABASE_TOKEN")
    apikey = os.getenv("SKYMIL_SUPABASE_APIKEY")
    
    if not token or not apikey:
        # Fallback to hardcoded credentials
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InR3aHRmZ3VhcGphdGlicnpibWJpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI5ODExNzEsImV4cCI6MjA4ODU1NzE3MX0.eBn8FuXBt4pZ0ehZFRJzDkVDmRgBbYkTgUfqSOW0Les"
        apikey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InR3aHRmZ3VhcGphdGlicnpibWJpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI5ODExNzEsImV4cCI6MjA4ODU1NzE3MX0.eBn8FuXBt4pZ0ehZFRJzDkVDmRgBbYkTgUfqSOW0Les"
    
    return token, apikey


def compute_product_hash(tracked_fields: Dict) -> str:
    """Compute deterministic hash of tracked fields."""
    sorted_fields = {}
    for key in sorted(tracked_fields.keys()):
        value = tracked_fields.get(key)
        if isinstance(value, float):
            sorted_fields[key] = round(value, 2)
        elif value is None:
            sorted_fields[key] = ""
        else:
            sorted_fields[key] = str(value)
    
    json_str = json.dumps(sorted_fields, sort_keys=True)
    return hashlib.md5(json_str.encode()).hexdigest()


def extract_tracked_fields(product_data: Dict) -> Dict:
    """Extract tracked fields from raw API product data."""
    if not product_data or not isinstance(product_data, dict):
        return None
    
    try:
        # Price logic
        price = float(product_data.get("price", 0) or 0)
        old_price = product_data.get("old_price") or product_data.get("regular_price") or product_data.get("compare_at_price")
        
        if old_price and float(old_price) > price:
            final_price = float(old_price)
            discount_price = price
            discount_amount = round(float(old_price) - price, 2)
        else:
            final_price = price
            discount_price = 0.0
            discount_amount = 0.0
        
        # Availability
        availability = product_data.get("availability") or product_data.get("stock_status") or product_data.get("in_stock")
        if availability is not None:
            if isinstance(availability, bool):
                availability = "InStock" if availability else "OutOfStock"
            elif isinstance(availability, (int, float)):
                availability = "InStock" if availability > 0 else "OutOfStock"
            else:
                avail_str = str(availability).lower()
                if avail_str in ['in_stock', 'instock', 'true', '1']:
                    availability = "InStock"
                elif avail_str in ['on_order', 'preorder']:
                    availability = "OnOrder"
                else:
                    availability = "OutOfStock"
        else:
            availability = "OutOfStock"
        
        tracked_fields = {
            "price": final_price,
            "discount_price": discount_price,
            "discount_amount": discount_amount,
            "availability": availability,
            "special_id": product_data.get("reference") or product_data.get("sku") or product_data.get("special_id"),
            "price_valid_until": product_data.get("price_valid_until") or (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        }
        
        return tracked_fields
        
    except Exception as e:
        logger.error(f"SKYMIL | EXTRACT_ERROR | {e}")
        return None


def fetch_all_products_from_api() -> List[Dict]:
    """Fetch ALL products from Supabase API in a single request."""
    token, apikey = _get_credentials()
    
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": apikey,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    url = f"{SUPABASE_URL}/products"
    params = {"limit": 10000}
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=45)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                logger.info(f"SKYMIL | API_FETCH_SUCCESS | products={len(data)}")
                return data
        else:
            logger.error(f"SKYMIL | API_FETCH_ERROR | status={response.status_code}")
        return []
    except Exception as e:
        logger.error(f"SKYMIL | API_FETCH_EXCEPTION | {e}")
        return []


def compute_site_hash_from_products(products: List[Dict]) -> tuple:
    """Compute site-wide hash and individual product hashes."""
    product_hashes = {}
    
    for product in products:
        product_id = product.get("id")
        if not product_id:
            continue
        
        product_id = str(product_id)
        
        tracked_fields = extract_tracked_fields(product)
        if not tracked_fields:
            continue
        
        product_hash = compute_product_hash(tracked_fields)
        product_hashes[product_id] = {
            'hash': product_hash,
            'tracked_fields': tracked_fields
        }
    
    if not product_hashes:
        return hashlib.md5(b"empty").hexdigest(), {}
    
    # Sort for deterministic combined hash
    sorted_hashes = [f"{pid}:{data['hash']}" for pid, data in sorted(product_hashes.items())]
    combined = "|".join(sorted_hashes)
    site_hash = hashlib.md5(combined.encode()).hexdigest()
    
    return site_hash, product_hashes


def get_cached_site_hash() -> Optional[str]:
    """Get cached site hash from Redis."""
    try:
        redis = get_redis_client()
        value = redis.get(SITE_HASH_KEY)
        if value:
            return value.decode() if isinstance(value, bytes) else value
        return None
    except Exception as e:
        logger.debug(f"SKYMIL | CACHE_READ_ERROR | {e}")
        return None


def update_site_hash(site_hash: str, product_hashes: Dict) -> bool:
    """Update site hash and individual product hashes in Redis."""
    try:
        redis = get_redis_client()
        
        # Update site hash
        redis.set(SITE_HASH_KEY, site_hash)
        redis.set(SITE_HASH_TIMESTAMP_KEY, datetime.now().isoformat())
        
        # Update individual product hashes
        pipeline = redis.pipeline()
        for product_id, data in product_hashes.items():
            hash_key = f"{PRODUCT_HASH_PREFIX}{product_id}"
            pipeline.set(hash_key, data['hash'])
        pipeline.execute()
        
        logger.info(f"SKYMIL | HASHES_UPDATED | site={site_hash[:8]} | products={len(product_hashes)}")
        return True
        
    except Exception as e:
        logger.error(f"SKYMIL | HASH_UPDATE_ERROR | {e}")
        return False


def get_cached_product_hash(product_id: str) -> Optional[str]:
    """Get cached hash for a specific product."""
    try:
        redis = get_redis_client()
        hash_key = f"{PRODUCT_HASH_PREFIX}{product_id}"
        value = redis.get(hash_key)
        if value:
            return value.decode() if isinstance(value, bytes) else value
        return None
    except Exception as e:
        logger.debug(f"SKYMIL | PRODUCT_HASH_READ_ERROR | {product_id} | {e}")
        return None


def get_site_data() -> tuple:
    """Get site-wide data with caching."""
    with _cache_lock:
        current_time = time.time()
        
        # Check if cache is valid
        if (_site_data_cache['products'] is not None and 
            _site_data_cache['timestamp'] is not None and
            current_time - _site_data_cache['timestamp'] < _site_data_cache['ttl']):
            
            logger.debug("SKYMIL | USING_CACHED_DATA")
            return (_site_data_cache['products'], 
                    _site_data_cache['product_hashes'], 
                    _site_data_cache['site_hash'])
    
    # Fetch fresh data
    products = fetch_all_products_from_api()
    
    if not products:
        return [], {}, None
    
    site_hash, product_hashes = compute_site_hash_from_products(products)
    
    # Cache the data
    with _cache_lock:
        _site_data_cache['products'] = products
        _site_data_cache['product_hashes'] = product_hashes
        _site_data_cache['site_hash'] = site_hash
        _site_data_cache['timestamp'] = time.time()
    
    return products, product_hashes, site_hash


def skymil_worker(product: Dict, processor) -> bool:
    """
    Skymil worker with site-wide hash optimization.
    
    Flow:
    1. First worker in cycle fetches ALL products and computes site hash
    2. Compare with cached site hash
    3. If same → skip ALL products
    4. If different → check individual product hash
    5. Only process changed products
    """
    product_id = product.get("main_product_id")
    url = product.get("url")
    
    if not product_id:
        return False
    
    if not url:
        with _error_lock:
            _error_stats['missing_url'] += 1
        logger.error(f"SKYMIL | MISSING_URL | {product_id}")
        return False
    
    # Prevent duplicate processing
    with _processed_lock:
        if product_id in _processed_ids:
            return False
        _processed_ids.add(product_id)
    
    start_time = time.time()
    debug.log_worker_start("skymil", product_id, url)
    
    try:
        # ─── STEP 1: Get site-wide data (cached or fresh) ──────
        products, product_hashes, new_site_hash = get_site_data()
        
        if not products or not product_hashes:
            with _error_lock:
                _error_stats['api_fetch_failed'] += 1
            logger.error(f"SKYMIL | API_FETCH_FAILED | {product_id}")
            processor.handle_failed_product("skymil", product_id)
            return False
        
        # ─── STEP 2: Compare site hash ──────────────────────────
        cached_site_hash = get_cached_site_hash()
        
        if cached_site_hash and cached_site_hash == new_site_hash:
            # ─── SITE HASH MATCH - NO CHANGES ──────────────────
            with _error_lock:
                _error_stats['site_hash_match'] += 1
                _error_stats['products_skipped'] += 1
            
            debug.log_worker_skip("skymil", product_id, "site_hash_match_no_changes")
            logger.debug(f"SKYMIL | SITE_HASH_MATCH | {product_id} | no changes")
            
            # Update last_checked timestamp without processing
            debug.log_worker_end("skymil", product_id, False, time.time() - start_time)
            return False
        
        # ─── STEP 3: Site hash changed - check individual product ──
        with _error_lock:
            _error_stats['site_hash_changed'] += 1
        
        product_hash_data = product_hashes.get(str(product_id))
        
        if not product_hash_data:
            # Product not in API anymore
            logger.warning(f"SKYMIL | PRODUCT_NOT_IN_API | {product_id}")
            processor.handle_failed_product("skymil", product_id)
            debug.log_worker_end("skymil", product_id, False, time.time() - start_time)
            return False
        
        new_product_hash = product_hash_data['hash']
        cached_product_hash = get_cached_product_hash(product_id)
        
        if cached_product_hash and cached_product_hash == new_product_hash:
            # ─── PRODUCT HASH MATCH - NO CHANGE ────────────────
            with _error_lock:
                _error_stats['no_change'] += 1
                _error_stats['products_skipped'] += 1
            
            logger.debug(f"SKYMIL | PRODUCT_HASH_MATCH | {product_id} | no change")
            debug.log_worker_end("skymil", product_id, False, time.time() - start_time)
            return False
        
        # ─── STEP 4: Product changed - process it ──────────────
        tracked_fields = product_hash_data['tracked_fields']
        tracked_fields["_hash"] = new_product_hash
        
        debug.log_extract_end("skymil", product_id, True, tracked_fields)
        
        result = processor.process("skymil", product_id, tracked_fields)
        
        with _error_lock:
            if result:
                _error_stats['success'] += 1
                _error_stats['products_processed'] += 1
            else:
                _error_stats['no_change'] += 1
        
        # ─── STEP 5: Update product hash ────────────────────────
        if result:
            try:
                redis = get_redis_client()
                hash_key = f"{PRODUCT_HASH_PREFIX}{product_id}"
                redis.set(hash_key, new_product_hash)
                logger.debug(f"SKYMIL | HASH_UPDATED | {product_id}")
            except Exception as e:
                logger.warning(f"SKYMIL | HASH_UPDATE_ERROR | {product_id} | {e}")
        
        elapsed = time.time() - start_time
        debug.log_worker_end("skymil", product_id, result, elapsed)
        return result
        
    except Exception as e:
        elapsed = time.time() - start_time
        with _error_lock:
            _error_stats['exception'] += 1
        
        logger.error(f"SKYMIL | WORKER_EXCEPTION | {product_id} | error={e}")
        debug.log_worker_error("skymil", product_id, str(e))
        processor.handle_failed_product("skymil", product_id)
        return False
    finally:
        with _processed_lock:
            _processed_ids.discard(product_id)


def create_skymil_worker():
    """Create a Skymil worker function."""
    return skymil_worker