# monitoring/preload_cache.py
"""
Redis Cache Preload - Optimized with DB connection pooling, field validation, and batch loading
Only validates critical fields that would break monitoring.
"""

import json
import time
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional, Tuple
from collections import defaultdict

from Database.Redis.Configuration.redis_client import RedisClient
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from core.logger import setup_logger
from core.utils.progress_bar import ProgressBar

logger = setup_logger("preload_cache", "monitor")


# ─── CONSTANTS ──────────────────────────────────────────────────────
# CRITICAL fields - missing these means monitoring WON'T work
CRITICAL_FIELDS = [
    "main_product_id",   # Required for Redis key
    "site",              # Required for Redis key
    "url",               # Required to fetch product page
]

# IMPORTANT fields - nice to have but not critical
IMPORTANT_FIELDS = [
    "price",             # Will be updated during monitoring
    "availability",      # Will be updated during monitoring
    "special_id",        # SKU reference
    "price_valid_until", # Price expiration
    "discount_price",    # Will be updated during monitoring
    "discount_amount",   # Will be updated during monitoring
]

# FIELDS TO SKIP - not in preload data
SKIP_FIELDS = [
    "name",
    "brand",
    "category",
    "is_enabled",
    "check_failures",
    "monitoring_priority",
    "last_checked",
    "last_updated_at"
]


# ─── FIELD VALIDATION ─────────────────────────────────────────────


def validate_product_fields(products: List[Dict]) -> Tuple[Dict[str, Dict], int, List[str]]:
    """
    Validate products for missing critical fields.
    Returns (field_stats, total_invalid, invalid_ids)
    """
    field_stats = {}
    
    # Initialize counters for critical + important fields
    for field in CRITICAL_FIELDS + IMPORTANT_FIELDS:
        field_stats[field] = {
            "missing": 0,
            "empty": 0,
            "sample_ids": []
        }
    
    invalid_products = []
    invalid_ids = []
    
    for product in products:
        product_id = product.get("main_product_id", "unknown")
        is_invalid = False
        
        for field in CRITICAL_FIELDS:
            value = product.get(field)
            stats = field_stats[field]
            
            # Check missing
            if value is None:
                stats["missing"] += 1
                if len(stats["sample_ids"]) < 5:
                    stats["sample_ids"].append(str(product_id))
                is_invalid = True
                continue
            
            # Check empty string
            if isinstance(value, str) and value.strip() == "":
                stats["empty"] += 1
                if len(stats["sample_ids"]) < 5:
                    stats["sample_ids"].append(str(product_id))
                is_invalid = True
                continue
        
        # Check important fields (warning only, not invalid)
        for field in IMPORTANT_FIELDS:
            value = product.get(field)
            stats = field_stats[field]
            
            if value is None:
                stats["missing"] += 1
                if len(stats["sample_ids"]) < 5:
                    stats["sample_ids"].append(str(product_id))
            elif isinstance(value, str) and value.strip() == "":
                stats["empty"] += 1
                if len(stats["sample_ids"]) < 5:
                    stats["sample_ids"].append(str(product_id))
        
        if is_invalid:
            invalid_products.append(product)
            invalid_ids.append(str(product_id))
    
    return field_stats, len(invalid_products), invalid_ids


def print_validation_summary(field_stats: Dict, total_invalid: int, invalid_ids: List[str]):
    """Print validation summary."""
    
    print("\n📊 FIELD VALIDATION SUMMARY")
    print("=" * 60)
    
    has_critical_issues = False
    
    # Critical fields
    print("\n   🔴 Critical Fields (must have):")
    for field in CRITICAL_FIELDS:
        stats = field_stats[field]
        missing = stats["missing"]
        empty = stats["empty"]
        total_issues = missing + empty
        
        if total_issues > 0:
            has_critical_issues = True
            sample_ids = ", ".join(stats["sample_ids"][:3]) if stats["sample_ids"] else ""
            print(f"      {field:20} | ❌ {total_issues:,} missing/empty")
            if missing > 0:
                print(f"         ├─ Missing: {missing:,}")
            if empty > 0:
                print(f"         └─ Empty: {empty:,}")
            if sample_ids:
                print(f"         └─ Samples: {sample_ids}")
        else:
            print(f"      {field:20} | ✅ OK")
    
    # Important fields (warnings only)
    print("\n   🟡 Important Fields (warning only):")
    for field in IMPORTANT_FIELDS:
        stats = field_stats[field]
        missing = stats["missing"]
        empty = stats["empty"]
        total_issues = missing + empty
        
        if total_issues > 0:
            sample_ids = ", ".join(stats["sample_ids"][:3]) if stats["sample_ids"] else ""
            print(f"      {field:20} | ⚠️ {total_issues:,} missing/empty")
            if missing > 0:
                print(f"         ├─ Missing: {missing:,}")
            if empty > 0:
                print(f"         └─ Empty: {empty:,}")
            if sample_ids:
                print(f"         └─ Samples: {sample_ids}")
        else:
            print(f"      {field:20} | ✅ OK")
    
    print("=" * 60)
    
    # Summary
    if total_invalid > 0:
        print(f"\n   ❌ {total_invalid} products with missing CRITICAL fields")
        print(f"      IDs: {', '.join(invalid_ids[:10])}" + ("..." if len(invalid_ids) > 10 else ""))
        print("      These products will be SKIPPED during preload.")
    else:
        print("\n   ✅ All products have required critical fields!")
    
    return has_critical_issues


# ─── CACHE STATS ──────────────────────────────────────────────────


def show_cache_stats() -> Dict[str, int]:
    """Display current cache statistics."""
    print("\n" + "=" * 60)
    print("📊 CURRENT CACHE STATISTICS")
    print("=" * 60)
    
    try:
        redis_client = RedisClient().get_client()
        keys = redis_client.keys("product:*")
        
        if not keys:
            print("❌ Cache is empty!")
            return {"total": 0, "per_site": {}}
        
        total = len(keys)
        print(f"📦 Total products in cache: {total:,}")
        
        # Breakdown by site
        sites = {}
        for key in keys:
            key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
            parts = key_str.split(':')
            if len(parts) >= 2:
                site = parts[1]
                sites[site] = sites.get(site, 0) + 1
        
        if sites:
            print("\n📈 Breakdown by site:")
            for site, count in sorted(sites.items(), key=lambda x: x[1], reverse=True):
                bar_len = int(count / total * 20) if total > 0 else 0
                bar = '█' * bar_len + '░' * (20 - bar_len)
                print(f"   {site.upper():15} |{bar}| {count:6,}")
        
        print("=" * 60)
        return {"total": total, "per_site": sites}
        
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        return {"total": 0, "per_site": {}}


# ─── MAIN PRELOAD ─────────────────────────────────────────────────


def preload_cache():
    """
    Preload Redis cache with complete products from database.
    Uses connection pooling for efficient DB access.
    Only validates CRITICAL fields (main_product_id, site, url).
    """
    print("\n" + "=" * 60)
    print("🚀 REDIS CACHE PRELOAD")
    print("=" * 60)
    logger.info("Starting Redis cache preload...")
    
    db = None
    cache = ProductCache()
    redis_client = None
    
    try:
        # ─── STEP 1: Get DB connection from pool ───────────────────
        print("\n📡 Connecting to database...")
        db = Database()
        redis_client = RedisClient().get_client()
        
        # ─── STEP 2: Check current Redis state ─────────────────────
        print("\n📊 Current cache status:")
        current_stats = show_cache_stats()
        current_keys = current_stats.get("total", 0)
        
        # ─── STEP 3: Load products from database ──────────────────
        print("\n📊 Loading products from database...")
        
        # Use optimized query - only fields needed for monitoring
        products = db.load_products_for_monitoring()
        
        if not products:
            logger.warning("No products found!")
            print("\n❌ No products found in database!")
            return False
        
        total = len(products)
        print(f"✅ Found {total:,} products in database")
        
        # ─── STEP 4: Validate critical fields ──────────────────────
        print("\n🔍 Validating critical fields...")
        field_stats, total_invalid, invalid_ids = validate_product_fields(products)
        
        # Print validation summary
        has_critical_issues = print_validation_summary(field_stats, total_invalid, invalid_ids)
        
        # ─── STEP 5: Filter invalid products ──────────────────────
        if total_invalid > 0:
            # Filter out products with missing critical fields
            invalid_set = set(invalid_ids)
            valid_products = []
            for p in products:
                pid = str(p.get("main_product_id", ""))
                if pid not in invalid_set:
                    valid_products.append(p)
            
            print(f"\n   Filtering: {total_invalid} invalid products removed")
            print(f"   Valid products: {len(valid_products):,}")
            
            if has_critical_issues:
                confirm = input("\n⚠️ Some products have missing CRITICAL fields and will be skipped. Continue? (yes/no): ").strip().lower()
                if confirm != 'yes':
                    print("❌ Preload cancelled")
                    return False
            
            products = valid_products
            total = len(products)
        else:
            print("\n   ✅ All products have required critical fields!")
        
        if not products:
            print("\n❌ No valid products to load!")
            return False
        
        # ─── STEP 6: Choose preload mode ──────────────────────────
        print("\n" + "=" * 60)
        print("PRELOAD MODES")
        print("=" * 60)
        print("  1 - Fresh load (clear existing cache)")
        print("  2 - Incremental (add missing only)")
        print("  3 - Overwrite all (keep existing, overwrite duplicates)")
        
        mode = input("\n👉 Choose mode (1-3): ").strip()
        
        if mode not in ["1", "2", "3"]:
            print("\n❌ Invalid mode! Using incremental...")
            mode = "2"
        
        # ─── STEP 7: Handle mode-specific logic ────────────────────
        to_load = products
        existing_keys = set()
        
        if mode == "1":
            print("\n⚠️ Clearing existing cache...")
            if current_keys > 0:
                keys = redis_client.keys("product:*")
                if keys:
                    batch_size = 100
                    deleted = 0
                    for i in range(0, len(keys), batch_size):
                        batch = keys[i:i+batch_size]
                        redis_client.delete(*batch)
                        deleted += len(batch)
                        print(f"   Cleared {deleted}/{len(keys)}...")
                    print(f"✅ Cleared {current_keys:,} keys")
        
        elif mode == "2":
            print("\n🔍 Finding missing products...")
            # Get all existing keys
            for key in redis_client.keys("product:*"):
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                existing_keys.add(key)
            
            # Filter products to only missing ones
            to_load = []
            for p in products:
                site = p.get('site', '').lower()
                pid = p.get('main_product_id')
                key = f"product:{site}:{pid}"
                if site and pid and key not in existing_keys:
                    to_load.append(p)
            
            if not to_load:
                print("\n✅ No missing products! Cache is up to date.")
                return True
            
            print(f"📝 Found {len(to_load):,} missing products out of {total:,}")
        
        elif mode == "3":
            print("\n📝 Will overwrite all products (existing + new)")
        
        # ─── STEP 8: Confirm if large load ──────────────────────────
        total_to_load = len(to_load)
        if total_to_load > 1000:
            confirm = input(f"\n⚠️ Load {total_to_load:,} valid products? (yes/no): ").strip().lower()
            if confirm != 'yes':
                print("❌ Cancelled")
                return False
        
        # ─── STEP 9: Load products in batches ──────────────────────
        print("\n" + "=" * 60)
        print("LOADING PRODUCTS TO REDIS")
        print("=" * 60)
        
        loaded = 0
        errors = 0
        start = time.time()
        
        print(f"📦 Loading {total_to_load:,} products...")
        
        progress = ProgressBar(total_to_load, width=40, prefix="Loading")
        
        for i, prod in enumerate(to_load, 1):
            try:
                site = prod.get('site', '').lower()
                pid = prod.get('main_product_id')
                
                if not site or not pid:
                    errors += 1
                    progress.update(i, f"⚠️ Skipped (missing id)")
                    continue
                
                # Build cache payload (minimal fields)
                payload = {
                    "url": prod.get("url", ""),
                    "price": float(prod.get("price", 0) or 0),
                    "discount_price": float(prod.get("discount_price", 0) or 0),
                    "discount_amount": float(prod.get("discount_amount", 0) or 0),
                    "availability": prod.get("availability", "OutOfStock"),
                    "special_id": prod.get("special_id"),
                    "price_valid_until": prod.get("price_valid_until"),
                    # Include these for completeness but they may be None
                    "name": prod.get("name", ""),
                    "brand": prod.get("brand"),
                    "category": prod.get("category"),
                    "last_checked": int(time.time()),
                    "is_enabled": prod.get("is_enabled", True),
                    "check_failures": prod.get("check_failures", 0),
                    "monitoring_priority": prod.get("monitoring_priority", 3),
                }
                
                cache.set_product(site, pid, payload, ttl=None)
                loaded += 1
                
                # Update progress
                if i % 10 == 0 or i == total_to_load:
                    elapsed = time.time() - start
                    speed = i / elapsed if elapsed > 0 else 0
                    progress.update(i, f"Loaded: {loaded:,} | Speed: {speed:.1f}/s")
                else:
                    progress.update(i, f"Loaded: {loaded:,}")
                
            except Exception as e:
                errors += 1
                logger.error(f"Error loading {prod.get('main_product_id')}: {e}")
                progress.update(i, f"❌ Errors: {errors}")
        
        progress.close()
        
        # ─── STEP 10: Summary ──────────────────────────────────────
        elapsed = time.time() - start
        
        print("\n" + "=" * 60)
        print("📊 PRELOAD SUMMARY")
        print("=" * 60)
        print(f"   Total products in DB: {total + total_invalid:,}")
        print(f"   Invalid (skipped): {total_invalid:,}")
        print(f"   Valid products: {total:,}")
        print(f"   Products loaded: {loaded:,}")
        print(f"   Errors: {errors:,}")
        print(f"   Time: {str(timedelta(seconds=int(elapsed)))}")
        if elapsed > 0:
            print(f"   Speed: {loaded/elapsed:.1f} products/s")
        print("=" * 60)
        
        logger.info(f"PRELOAD_COMPLETE | loaded={loaded} | errors={errors} | time={elapsed:.2f}s")
        
        # ─── STEP 11: Verify load ──────────────────────────────────
        print("\n📊 Verifying Redis cache...")
        verify_stats = show_cache_stats()
        
        if verify_stats.get("total", 0) >= loaded:
            print("✅ Cache verification passed!")
        else:
            print(f"⚠️ Cache verification: Expected {loaded}, found {verify_stats.get('total', 0)}")
        
        return True
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Preload interrupted by user")
        return False
    except Exception as e:
        logger.error(f"Preload error: {e}")
        print(f"\n❌ Preload failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # ─── Release DB connection back to pool ──────────────────
        if db:
            try:
                db.close()
                logger.debug("DB connection released back to pool")
            except Exception as e:
                logger.debug(f"Error releasing DB connection: {e}")


# ─── QUICK PRELOAD ────────────────────────────────────────────────


def preload_cache_quick():
    """Quick preload without prompts (for automation)."""
    print("\n🚀 Quick preload mode (no prompts)...")
    
    # Save original input
    original_input = __builtins__.input if hasattr(__builtins__, 'input') else input
    
    def quick_input(prompt):
        if "Choose mode" in prompt:
            return "2"  # Use incremental mode by default
        elif "Continue" in prompt:
            return "yes"
        elif "Load" in prompt and "products" in prompt:
            return "yes"
        return original_input(prompt)
    
    try:
        __builtins__.input = quick_input
        return preload_cache()
    finally:
        __builtins__.input = original_input


# ─── MAIN ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--quick":
            preload_cache_quick()
        elif sys.argv[1] == "--stats":
            show_cache_stats()
        else:
            preload_cache()
    else:
        preload_cache()