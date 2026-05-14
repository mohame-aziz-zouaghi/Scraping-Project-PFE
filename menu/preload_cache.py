# monitoring/preload_cache.py
import json
import time
import os
from datetime import datetime, timedelta
from Database.Redis.Configuration.redis_client import RedisClient
from Database.Redis.product_cache import ProductCache
from core.logger import setup_logger
from Database.Postgresql.db import Database

logger = setup_logger("preload_cache", "monitor")

class ProgressBar:
    """Custom progress bar with ETA and visual indicators"""
    
    def __init__(self, total, width=50, prefix="Progress"):
        self.total = total
        self.width = width
        self.prefix = prefix
        self.start_time = time.time()
        self.current = 0
        
    def update(self, current, extra_info=""):
        """Update progress bar"""
        self.current = current
        percent = current / self.total
        filled = int(self.width * percent)
        bar = '█' * filled + '░' * (self.width - filled)
        
        # Calculate ETA
        elapsed = time.time() - self.start_time
        if current > 0:
            eta_seconds = (elapsed / current) * (self.total - current)
            eta = str(timedelta(seconds=int(eta_seconds)))
        else:
            eta = "calculating..."
        
        # Format numbers
        percentage = percent * 100
        
        # Create progress line
        line = f"\r{self.prefix}: |{bar}| {percentage:.1f}% ({current:,}/{self.total:,}) | ETA: {eta} | {extra_info}"
        
        # Print with proper spacing
        print(f"{line:<120}", end="", flush=True)
        
        if current == self.total:
            print()  # New line when complete
    
    def close(self):
        """Close the progress bar"""
        elapsed = time.time() - self.start_time
        print(f"\n✅ Completed in {str(timedelta(seconds=int(elapsed)))}")

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
        
        # Group by site
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
                bar_length = int(count / len(keys) * 20)
                bar = '█' * bar_length + '░' * (20 - bar_length)
                print(f"   {site:15} |{bar}| {count:6,}")
        
        return len(keys)
        
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        return 0

def preload_cache():
    """Preload Redis cache with all products from database"""
    
    print("\n" + "="*60)
    print("🚀 REDIS CACHE PRELOAD")
    print("="*60)
    
    logger.info("Starting Redis cache preload...")
    
    db = Database()
    cache = ProductCache()
    
    try:
        # Show current cache stats
        print("\n📊 Current cache status:")
        current_keys = show_cache_stats()
        
        # Get all products from database
        print("\n📊 Fetching products from database...")
        products = db.load_products_for_monitoring()
        
        if not products:
            logger.warning("No products found in database!")
            print("\n❌ No products found in database!")
            return False
        
        total_products = len(products)
        print(f"✅ Found {total_products:,} products in database")
        
        # Ask for preload mode
        print("\n" + "="*60)
        print("PRELOAD MODES")
        print("="*60)
        print("1 - Fresh load (clear existing cache first)")
        print("2 - Incremental (add missing products only)")
        print("3 - Overwrite all (update all products)")
        
        mode = input("\n👉 Choose mode (1-3): ").strip()
        
        redis_client = RedisClient().get_client()
        
        if mode == "1":
            # Fresh load - clear existing cache
            print("\n⚠️ Clearing existing cache...")
            if current_keys > 0:
                keys = redis_client.keys("product:*")
                if keys:
                    redis_client.delete(*keys)
                    print(f"✅ Cleared {current_keys:,} existing keys")
        
        elif mode == "2":
            # Incremental - find missing products
            print("\n🔍 Finding missing products...")
            existing_keys = redis_client.keys("product:*")
            existing_set = set()
            for key in existing_keys:
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                existing_set.add(key_str)
            
            # Filter products not in cache
            products_to_load = []
            for product in products:
                site = product.get('site', '').lower()
                product_id = product.get('id')
                if site and product_id:
                    redis_key = f"product:{site}:{product_id}"
                    if redis_key not in existing_set:
                        products_to_load.append(product)
            
            if not products_to_load:
                print("\n✅ No missing products found! Cache is already complete.")
                return True
            
            print(f"📝 Found {len(products_to_load):,} missing products to load")
            products = products_to_load
            total_products = len(products)
        
        elif mode == "3":
            # Overwrite all - keep existing but will overwrite
            print("\n📝 Will overwrite all existing products")
        
        else:
            print("\n❌ Invalid mode! Using incremental mode...")
            mode = "2"
        
        # Confirm before loading
        if total_products > 1000:
            confirm = input(f"\n⚠️ Load {total_products:,} products? (yes/no): ").strip().lower()
            if confirm != 'yes':
                print("❌ Operation cancelled")
                return False
        
        # Initialize progress bar
        print("\n" + "="*60)
        print("LOADING PRODUCTS TO REDIS")
        print("="*60)
        
        progress = ProgressBar(total_products, width=40, prefix="Loading")
        
        # Stats tracking
        loaded_count = 0
        error_count = 0
        start_time = time.time()
        batch_size = 100
        last_batch_time = start_time
        
        # Load products
        for i, product in enumerate(products, 1):
            try:
                site = product.get('site', '').lower()
                special_id = product.get('special_id')
                product_id = product.get('id')
                
                if not site or not product_id:
                    error_count += 1
                    progress.update(i, f"⚠️ Skipped (missing site/id)")
                    continue
                
                # Prepare Redis payload
                payload = {
                    "url": product.get('url'),
                    "price": float(product.get('price')) if product.get('price') else 0,
                    "discount_price": float(product.get('discount_price')) if product.get('discount_price') else 0,
                    "discount_amount": float(product.get('discount_amount')) if product.get('discount_amount') else 0,
                    "availability": product.get('availability', 'OutOfStock'),
                    "special_id": special_id,
                    "price_valid_until": str(product.get('price_valid_until')) if product.get('price_valid_until') else None,
                    "last_checked": None
                }
                
                # Set in Redis
                cache.set_product(site, product_id , payload)
                loaded_count += 1
                
                # Update progress with speed
                if i % batch_size == 0:
                    current_time = time.time()
                    batch_duration = current_time - last_batch_time
                    speed = batch_size / batch_duration if batch_duration > 0 else 0
                    last_batch_time = current_time
                    extra = f"Speed: {speed:.1f}/s | Loaded: {loaded_count:,}"
                else:
                    extra = f"Loaded: {loaded_count:,}"
                
                progress.update(i, extra)
                
            except Exception as e:
                error_count += 1
                logger.error(f"Error loading product {product.get('id')}: {e}")
                progress.update(i, f"❌ Errors: {error_count}")
        
        # Close progress bar
        progress.close()
        
        # Print summary
        print("\n" + "="*60)
        print("📊 PRELOAD SUMMARY")
        print("="*60)
        print(f"✅ Successfully loaded: {loaded_count:,} products")
        print(f"❌ Errors: {error_count:,} products")
        print(f"📦 Total processed: {total_products:,} products")
        
        elapsed = time.time() - start_time
        avg_speed = loaded_count / elapsed if elapsed > 0 else 0
        print(f"\n⏱️ Time taken: {str(timedelta(seconds=int(elapsed)))}")
        print(f"📈 Average speed: {avg_speed:.1f} products/second")
        
        # Verification
        print("\n" + "="*60)
        print("🔍 VERIFYING REDIS CACHE")
        print("="*60)
        
        final_keys = redis_client.keys("product:*")
        
        if final_keys:
            print(f"✅ Redis cache now contains {len(final_keys):,} products")
            
            # Show sample
            print("\n📋 Sample products in cache:")
            for i, key in enumerate(list(final_keys)[:3], 1):
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                data = redis_client.get(key)
                if data:
                    try:
                        if isinstance(data, bytes):
                            data = data.decode('utf-8')
                        product_data = json.loads(data)
                        price = product_data.get('price', 'N/A')
                        print(f"   {i}. {key_str} - Price: {price} TND")
                    except:
                        print(f"   {i}. {key_str}")
            
            # Calculate success rate
            expected_count = total_products - error_count
            success_rate = (len(final_keys) / expected_count * 100) if expected_count > 0 else 0
            print(f"\n📊 Success rate: {success_rate:.1f}%")
            
            return True
        else:
            logger.error("❌ FAILED: Redis cache is empty after preload!")
            return False
        
    except Exception as e:
        logger.error(f"Error preloading cache: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

def preload_cache_quick():
    """Quick preload without any prompts (fresh load)"""
    print("\n" + "="*60)
    print("🚀 QUICK PRELOAD (No Prompts)")
    print("="*60)
    
    # Set quick mode environment variable
    os.environ['QUICK_MODE'] = 'true'
    
    # Override input to simulate '1' for fresh load
    original_input = __builtins__.input
    def quick_input(prompt):
        if "Choose mode" in prompt:
            return "1"
        elif "Load" in prompt and "products" in prompt:
            return "yes"
        return original_input(prompt)
    
    __builtins__.input = quick_input
    
    try:
        result = preload_cache()
        return result
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