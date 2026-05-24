# monitoring/preload_cache.py
import json
import time
import os
from datetime import datetime, timedelta

from Database.Redis.Configuration.redis_client import RedisClient
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from core.logger import setup_logger
from core.utils.progress_bar import ProgressBar

logger = setup_logger("preload_cache", "monitor")


def show_cache_stats():
    """Display current cache statistics"""
    print("\n" + "="*60)
    print("📊 CURRENT CACHE STATISTICS")
    print("="*60)
    
    try:
        redis_client = RedisClient().get_client()
        keys = redis_client.keys("product:*")
        
        if not keys:
            print("❌ Cache is empty!")
            return 0
        
        print(f"📦 Total products in cache: {len(keys):,}")
        
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
                bar_len = int(count / len(keys) * 20)
                bar = '█' * bar_len + '░' * (20 - bar_len)
                print(f"   {site:15} |{bar}| {count:6,}")
        
        return len(keys)
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        return 0


def preload_cache():
    """Preload Redis cache with complete products from database"""
    print("\n" + "="*60)
    print("🚀 REDIS CACHE PRELOAD")
    print("="*60)
    logger.info("Starting Redis cache preload...")
    
    db = Database()
    cache = ProductCache()
    
    try:
        print("\n📊 Current cache status:")
        current_keys = show_cache_stats()
        
        print("\n📊 Fetching complete products from database...")
        products = db.load_complete_products_for_preload()
        print("1")
        
        if not products:
            logger.warning("No products found!")
            print("\n❌ No products found!")
            return False
        
        total = len(products)
        print(f"✅ Found {total:,} products")
        
        print("\n" + "="*60)
        print("PRELOAD MODES")
        print("="*60)
        print("1 - Fresh load (clear existing)")
        print("2 - Incremental (add missing only)")
        print("3 - Overwrite all")
        
        mode = input("\n👉 Choose mode (1-3): ").strip()
        redis_client = RedisClient().get_client()
        
        if mode == "1":
            print("\n⚠️ Clearing existing cache...")
            if current_keys > 0:
                keys = redis_client.keys("product:*")
                if keys:
                    redis_client.delete(*keys)
                    print(f"✅ Cleared {current_keys:,} keys")
        elif mode == "2":
            print("\n🔍 Finding missing products...")
            existing = set()
            for key in redis_client.keys("product:*"):
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                existing.add(key_str)
            
            to_load = []
            for p in products:
                site = p.get('site', '').lower()
                pid = p.get('main_product_id')
                if site and pid and f"product:{site}:{pid}" not in existing:
                    to_load.append(p)
            
            if not to_load:
                print("\n✅ No missing products!")
                return True
            
            print(f"📝 Found {len(to_load):,} missing products")
            products = to_load
            total = len(products)
        elif mode == "3":
            print("\n📝 Will overwrite all products")
        else:
            print("\n❌ Invalid mode! Using incremental...")
            mode = "2"
        
        if total > 1000:
            confirm = input(f"\n⚠️ Load {total:,} products? (yes/no): ").strip().lower()
            if confirm != 'yes':
                print("❌ Cancelled")
                return False
        
        print("\n" + "="*60)
        print("LOADING PRODUCTS TO REDIS")
        print("="*60)
        
        progress = ProgressBar(total, width=40, prefix="Loading")
        loaded = 0
        errors = 0
        start = time.time()
        batch_size = 100
        last_batch = start
        
        for i, prod in enumerate(products, 1):
            try:
                site = prod.get('site', '').lower()
                pid = prod.get('main_product_id')
                
                if not site or not pid:
                    errors += 1
                    progress.update(i, f"⚠️ Skipped (missing id)")
                    continue
                
                cache.set_product(site, pid, prod, ttl=None)
                loaded += 1
                
                if i % batch_size == 0:
                    now = time.time()
                    speed = batch_size / (now - last_batch) if (now - last_batch) > 0 else 0
                    last_batch = now
                    progress.update(i, f"Speed: {speed:.1f}/s | Loaded: {loaded:,}")
                else:
                    progress.update(i, f"Loaded: {loaded:,}")
            except Exception as e:
                errors += 1
                logger.error(f"Error loading {prod.get('main_product_id')}: {e}")
                progress.update(i, f"❌ Errors: {errors}")
        
        progress.close()
        
        print("\n" + "="*60)
        print("📊 PRELOAD SUMMARY")
        print("="*60)
        print(f"✅ Loaded: {loaded:,} products")
        print(f"❌ Errors: {errors:,}")
        print(f"⏱️ Time: {str(timedelta(seconds=int(time.time()-start)))}")
        
        return True
    except Exception as e:
        logger.error(f"Preload error: {e}")
        return False
    finally:
        db.close()


def preload_cache_quick():
    """Quick preload without prompts"""
    os.environ['QUICK_MODE'] = 'true'
    original_input = __builtins__.input
    def quick_input(p):
        if "Choose mode" in p:
            return "1"
        elif "Load" in p and "products" in p:
            return "yes"
        return original_input(p)
    
    __builtins__.input = quick_input
    try:
        return preload_cache()
    finally:
        __builtins__.input = original_input
        del os.environ['QUICK_MODE']


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "--quick":
            preload_cache_quick()
        elif sys.argv[1] == "--stats":
            show_cache_stats()
        else:
            preload_cache()
    else:
        preload_cache()