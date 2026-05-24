# cli/redis_manager.py
import json
import sys
import os
from datetime import datetime
from typing import Dict, List, Optional

from Database.Redis.Configuration.backup_redis import RedisBackup
from Database.Redis.Configuration.redis_client import RedisClient
from menu.redis_diagnostic import RedisDiagnostic

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger

logger = setup_logger("redis_manager", "cli")

class RedisManager:
    def __init__(self):
        """Initialize Redis manager with CLI interface"""
        self.redis_client = RedisClient().get_client()
        self.current_menu = "main"
    
    def print_header(self, title: str):
        """Print formatted header"""
        print("\n" + "=" * 60)
        print(f" {title}")
        print("=" * 60)
    
    def print_success(self, msg: str):
        """Print success message"""
        print(f"✅ {msg}")
    
    def print_error(self, msg: str):
        """Print error message"""
        print(f"❌ {msg}")
    
    def print_info(self, msg: str):
        """Print info message"""
        print(f"📊 {msg}")
    
    def print_warning(self, msg: str):
        """Print warning message"""
        print(f"⚠️ {msg}")
    
    # ---------------------------------
    # CORE FUNCTIONALITY
    # ---------------------------------
    
    def get_all_keys(self) -> List[str]:
        """Get all product keys from Redis"""
        keys = self.redis_client.keys("product:*")
        decoded_keys = []
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode('utf-8')
            decoded_keys.append(key)
        return decoded_keys
    
    def get_product_data(self, key: str) -> Optional[Dict]:
        """Get product data from Redis"""
        value = self.redis_client.get(key)
        if not value:
            return None
        
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        
        try:
            return json.loads(value)
        except:
            return {"raw_value": value}
    
    # ---------------------------------
    # MENU OPTIONS
    # ---------------------------------
    
    def show_menu(self):
        """Main Redis management menu"""
        while True:
            self.print_header("REDIS CACHE MANAGEMENT")
            print("1 - View Cache Statistics")
            print("2 - List All Products")
            print("3 - Search Products")
            print("4 - View Product Details")
            print("5 - Compare Redis vs Database")
            print("6 - Delete Product from Cache")
            print("7 - Clear Entire Cache (DANGER)")
            print("8 - Export Cache to JSON")
            print("9 - Import Cache from JSON")
            print("10 - Validate Cache Health")
            print("11 - 🔧 Redis Diagnostic (Check Persistence)")
            print("12 - 💿 Backup Management")
            print("0 - Back to Main Menu")
            
            choice = input("\n👉 Choice: ").strip()
            
            if choice == "1":
                self.show_statistics()
            elif choice == "2":
                self.list_products()
            elif choice == "3":
                self.search_products()
            elif choice == "4":
                self.view_product_details()
            elif choice == "5":
                self.compare_with_database()
            elif choice == "6":
                self.delete_product()
            elif choice == "7":
                self.clear_all_cache()
            elif choice == "8":
                self.export_cache()
            elif choice == "9":
                self.import_cache()
            elif choice == "10":
                self.validate_cache_health()
            elif choice == "11":
                diagnostic = RedisDiagnostic()
                diagnostic.show_diagnostic()
            elif choice == "12":
                backup = RedisBackup()
                backup.backup_menu()
            elif choice == "0":
                break
            else:
                self.print_error(f"Invalid option: {choice}")
    
    # ---------------------------------
    # 1. STATISTICS
    # ---------------------------------
    
    def show_statistics(self):
        """Display detailed cache statistics"""
        self.print_header("CACHE STATISTICS")
        
        keys = self.get_all_keys()
        total_keys = len(keys)
        
        if total_keys == 0:
            self.print_warning("Cache is empty!")
            return
        
        # Count by site
        sites = {}
        for key in keys:
            parts = key.split(':')
            if len(parts) >= 2:
                site = parts[1]
                sites[site] = sites.get(site, 0) + 1
        
        self.print_info(f"Total Products in Cache: {total_keys}")
        print("\n📈 Breakdown by Site:")
        for site, count in sorted(sites.items()):
            print(f"   • {site}: {count} products")
        
        # Memory usage
        try:
            info = self.redis_client.info('memory')
            memory_used = info.get('used_memory_human', 'N/A')
            print(f"\n💾 Memory Usage: {memory_used}")
        except:
            pass
        
        # Sample data quality
        self.print_info("\n📊 Data Quality Sample (first 50 products):")
        total_price = 0
        with_discount = 0
        in_stock = 0
        valid_until_count = 0
        price_count = 0
        
        for key in keys[:50]:
            data = self.get_product_data(key)
            if data and isinstance(data, dict):
                # Handle price - check for None
                price = data.get('price')
                if price is not None and isinstance(price, (int, float)):
                    total_price += float(price)
                    price_count += 1
                
                # Handle discount_price - FIXED: check for None
                discount_price = data.get('discount_price')
                if discount_price is not None and discount_price > 0:
                    with_discount += 1
                
                # Handle availability
                availability = data.get('availability')
                if availability == 'InStock':
                    in_stock += 1
                
                # Handle price_valid_until
                if data.get('price_valid_until'):
                    valid_until_count += 1
        
        if price_count > 0:
            print(f"   • Average price: {total_price/price_count:.2f} TND (based on {price_count} products)")
        else:
            print(f"   • Average price: N/A (no price data)")
        
        print(f"   • Products with discount: {with_discount}")
        print(f"   • Products in stock: {in_stock}")
        print(f"   • Products with valid until date: {valid_until_count}")
        
        # Last checked times
        recent_checked = 0
        for key in keys[:20]:
            data = self.get_product_data(key)
            if data and isinstance(data, dict):
                last_checked = data.get('last_checked')
                if last_checked and isinstance(last_checked, (int, float)):
                    from datetime import datetime
                    last_checked_date = datetime.fromtimestamp(last_checked)
                    days_ago = (datetime.now() - last_checked_date).days
                    if days_ago <= 1:
                        recent_checked += 1
        
        print(f"\n🕐 Recently checked (last 24h): {recent_checked}/20 sampled")
    
    # ---------------------------------
    # 2. LIST PRODUCTS
    # ---------------------------------
    
    def list_products(self):
        """List all products with pagination"""
        self.print_header("PRODUCTS LIST")
        
        keys = self.get_all_keys()
        total = len(keys)
        
        if total == 0:
            self.print_warning("No products in cache")
            return
        
        self.print_info(f"Total products: {total}")
        
        # Pagination
        page_size = 20
        current_page = 1
        total_pages = (total + page_size - 1) // page_size
        
        while True:
            start_idx = (current_page - 1) * page_size
            end_idx = min(start_idx + page_size, total)
            
            print(f"\n📄 Page {current_page}/{total_pages} (showing {start_idx + 1}-{end_idx}):")
            print("-" * 80)
            
            for i, key in enumerate(keys[start_idx:end_idx], start_idx + 1):
                data = self.get_product_data(key)
                if data and isinstance(data, dict):
                    # Handle price - check for None
                    price = data.get('price')
                    price_str = f"{price} TND" if price is not None else 'N/A'
                    
                    availability = data.get('availability', 'N/A')
                    
                    last_checked = data.get('last_checked')
                    if last_checked and isinstance(last_checked, (int, float)):
                        from datetime import datetime
                        last_checked = datetime.fromtimestamp(last_checked).strftime('%Y-%m-%d %H:%M')
                    else:
                        last_checked = 'N/A'
                    
                    print(f"{i:3}. {key}")
                    print(f"     💰 Price: {price_str} | 📦 Status: {availability} | 🕐 Last: {last_checked}")
                else:
                    print(f"{i:3}. {key} (Invalid data)")
            
            print("\n" + "-" * 80)
            print("Commands: [N]ext | [P]revious | [S]earch | [Q]uit")
            
            cmd = input("\n👉 ").strip().lower()
            
            if cmd == 'n' and current_page < total_pages:
                current_page += 1
            elif cmd == 'p' and current_page > 1:
                current_page -= 1
            elif cmd == 's':
                self.search_products()
                break
            elif cmd == 'q':
                break
            else:
                if current_page >= total_pages:
                    self.print_error("Already on last page")
                elif current_page <= 1:
                    self.print_error("Already on first page")
    
    # ---------------------------------
    # 3. SEARCH PRODUCTS
    # ---------------------------------
    
    def search_products(self):
        """Search products by site, Main Product ID, or URL"""
        self.print_header("SEARCH PRODUCTS")
        
        print("Search by:")
        print("1 - Site name (e.g., megapc)")
        print("2 - Main Product ID")
        print("3 - Partial URL")
        
        search_type = input("\n👉 Choice: ").strip()
        
        if search_type == "1":
            site = input("Enter site name: ").strip().lower()
            pattern = f"product:{site}:*"
            keys = self.redis_client.keys(pattern)
            decoded_keys = [k.decode('utf-8') if isinstance(k, bytes) else k for k in keys]
            
            self.print_info(f"Found {len(decoded_keys)} products for site '{site}'")
            for i, key in enumerate(decoded_keys[:50], 1):
                print(f"   {i}. {key}")
        
        elif search_type == "2":
            product_id = input("Enter product ID (Product_id): ").strip()
            # Search across all sites
            all_keys = self.get_all_keys()
            found = []
            for key in all_keys:
                data = self.get_product_data(key)
                if data and isinstance(data, dict):
                    if data.get('main_product_id') == product_id:
                        found.append(key)
            
            if found:
                self.print_info(f"Found {len(found)} product(s):")
                for key in found:
                    print(f"   • {key}")
            else:
                self.print_warning(f"No product found with ID: {product_id}")
        
        elif search_type == "3":
            url_part = input("Enter part of URL: ").strip().lower()
            all_keys = self.get_all_keys()
            found = []
            for key in all_keys[:100]:  # Limit to 100 for performance
                data = self.get_product_data(key)
                if data and isinstance(data, dict):
                    url = data.get('url', '').lower()
                    if url_part in url:
                        found.append((key, url))
            
            if found:
                self.print_info(f"Found {len(found)} product(s):")
                for key, url in found[:20]:
                    print(f"   • {key}")
                    print(f"     URL: {url[:80]}...")
            else:
                self.print_warning(f"No products found with URL containing: {url_part}")
        
        else:
            self.print_error("Invalid search type")
    
    # ---------------------------------
    # 4. VIEW PRODUCT DETAILS
    # ---------------------------------
    
    def view_product_details(self):
        """View detailed information for a specific product"""
        self.print_header("PRODUCT DETAILS")
        
        key = input("Enter product key (e.g., product:megapc:12345): ").strip()
        
        # Auto-complete if just ID provided
        if not key.startswith("product:"):
            site = input("Enter site name: ").strip().lower()
            key = f"product:{site}:{key}"
        
        data = self.get_product_data(key)
        
        if data:
            print("\n📦 Product Details:")
            print(json.dumps(data, indent=2, default=str))
            
            # Show human-readable last checked
            if 'last_checked' in data:
                from datetime import datetime
                last_checked = datetime.fromtimestamp(data['last_checked'])
                print(f"\n🕐 Last checked: {last_checked.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"   Days ago: {(datetime.now() - last_checked).days}")
        else:
            self.print_error(f"Product not found: {key}")
    
    # ---------------------------------
    # 5. COMPARE WITH DATABASE
    # ---------------------------------
    
    def compare_with_database(self):
        """Compare Redis cache with PostgreSQL database"""
        self.print_header("REDIS VS DATABASE COMPARISON")
        
        from Database.Postgresql.db import Database
        db = Database()
        
        # Get Redis keys
        redis_keys = self.get_all_keys()
        redis_count = len(redis_keys)
        
        # Get DB products
        db_products = db.load_products_for_monitoring()
        db_count = len(db_products)
        
        self.print_info(f"Redis cache: {redis_count} products")
        self.print_info(f"Database: {db_count} products")
        
        if redis_count == 0 and db_count == 0:
            self.print_warning("Both cache and database are empty")
            return
        
        # Find products in DB but not in Redis
        redis_key_set = set(redis_keys)
        missing_in_redis = []
        
        for product in db_products:
            redis_key = f"product:{product['site'].lower()}:{product['main_product_id']}"
            if redis_key not in redis_key_set:
                missing_in_redis.append(redis_key)
        
        if missing_in_redis:
            self.print_warning(f"⚠️ Products missing in Redis: {len(missing_in_redis)}")
            for key in missing_in_redis[:10]:
                print(f"   • {key}")
        else:
            self.print_success("All database products are present in Redis cache!")
        
        # Find products in Redis but not in DB
        db_key_set = set()
        for product in db_products:
            db_key = f"product:{product['site'].lower()}:{product['main_product_id']}"
            db_key_set.add(db_key)
        
        missing_in_db = [key for key in redis_keys if key not in db_key_set]
        
        if missing_in_db:
            self.print_warning(f"⚠️ Products in Redis but not in DB: {len(missing_in_db)}")
            for key in missing_in_db[:10]:
                print(f"   • {key}")
        
        db.close()
    
    # ---------------------------------
    # 6. DELETE PRODUCT
    # ---------------------------------
    
    def delete_product(self):
        """Delete a specific product from cache"""
        self.print_header("DELETE PRODUCT")
        
        key = input("Enter product key to delete: ").strip()
        
        # Confirm
        confirm = input(f"Are you sure you want to delete {key}? (yes/no): ").strip().lower()
        
        if confirm != 'yes':
            self.print_info("Deletion cancelled")
            return
        
        result = self.redis_client.delete(key)
        
        if result:
            self.print_success(f"Deleted {key}")
        else:
            self.print_error(f"Key not found: {key}")
    
    # ---------------------------------
    # 7. CLEAR ALL CACHE
    # ---------------------------------
    
    def clear_all_cache(self):
        """Clear entire Redis cache with confirmation"""
        self.print_header("⚠️ DANGER: CLEAR ALL CACHE ⚠️")
        
        keys = self.get_all_keys()
        count = len(keys)
        
        if count == 0:
            self.print_warning("Cache is already empty")
            return
        
        self.print_warning(f"This will delete ALL {count} products from Redis cache!")
        confirm = input("Type 'DELETE ALL' to confirm: ").strip()
        
        if confirm == 'DELETE ALL':
            # Delete in batches to avoid timeout
            batch_size = 100
            for i in range(0, count, batch_size):
                batch = keys[i:i+batch_size]
                self.redis_client.delete(*batch)
                print(f"   Deleted {min(i+batch_size, count)}/{count}...")
            
            self.print_success(f"Cleared all {count} keys from cache")
            logger.warning(f"CLI | Cleared entire Redis cache ({count} keys)")
        else:
            self.print_info("Operation cancelled")
    
    # ---------------------------------
    # 8. EXPORT CACHE
    # ---------------------------------
    
    def export_cache(self):
        """Export Redis cache to JSON file"""
        self.print_header("EXPORT CACHE")
        
        keys = self.get_all_keys()
        
        if not keys:
            self.print_warning("Cache is empty")
            return
        
        self.print_info(f"Exporting {len(keys)} products...")
        
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "total_products": len(keys),
            "products": {}
        }
        
        for key in keys:
            data = self.get_product_data(key)
            if data:
                export_data["products"][key] = data
        
        filename = f"redis_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, default=str, ensure_ascii=False)
            
            file_size = os.path.getsize(filename) / 1024  # KB
            self.print_success(f"Exported to {filename} ({file_size:.2f} KB)")
        except Exception as e:
            self.print_error(f"Export failed: {e}")
    
    # ---------------------------------
    # 9. IMPORT CACHE
    # ---------------------------------
    
    def import_cache(self):
        """Import cache from JSON file"""
        self.print_header("IMPORT CACHE")
        
        filename = input("Enter filename to import: ").strip()
        
        if not os.path.exists(filename):
            self.print_error(f"File not found: {filename}")
            return
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            products = data.get('products', {})
            
            if not products:
                self.print_warning("No products found in file")
                return
            
            self.print_info(f"Importing {len(products)} products...")
            
            for key, product_data in products.items():
                self.redis_client.set(key, json.dumps(product_data))
            
            self.print_success(f"Successfully imported {len(products)} products")
            
        except Exception as e:
            self.print_error(f"Import failed: {e}")
    
    # ---------------------------------
    # 10. VALIDATE CACHE HEALTH
    # ---------------------------------
    
    def validate_cache_health(self):
        """Validate cache health and data integrity"""
        self.print_header("CACHE HEALTH CHECK")
        
        keys = self.get_all_keys()
        total = len(keys)
        
        if total == 0:
            self.print_warning("Cache is empty")
            return
        
        issues = []
        valid_count = 0
        
        for key in keys:
            data = self.get_product_data(key)
            
            if not data:
                issues.append(f"{key}: No data")
                continue
            
            if not isinstance(data, dict):
                issues.append(f"{key}: Invalid JSON structure")
                continue
            
            # Check required fields
            required_fields = ['url', 'price', 'availability']
            for field in required_fields:
                if field not in data:
                    issues.append(f"{key}: Missing '{field}' field")
            
            # Check URL format
            url = data.get('url', '')
            if url and not url.startswith('http'):
                issues.append(f"{key}: Invalid URL format: {url[:50]}")
            
            # Check price type - FIXED: handle None
            price = data.get('price')
            if price is not None and not isinstance(price, (int, float)):
                issues.append(f"{key}: Price is not a number: {type(price)}")
            
            # Check availability value
            availability = data.get('availability', '')
            if availability and availability.lower() not in ['instock', 'outofstock', 'in_stock', 'backorder', 'en stock', 'sur commande']:
                issues.append(f"{key}: Invalid availability: {availability}")
            
            # Check discount_price - FIXED: handle None
            discount_price = data.get('discount_price')
            if discount_price is not None and not isinstance(discount_price, (int, float)):
                issues.append(f"{key}: Discount price is not a number: {type(discount_price)}")
            
            valid_count += 1
        
        self.print_info(f"Total products: {total}")
        self.print_info(f"Valid products: {valid_count}")
        
        if issues:
            self.print_warning(f"Issues found: {len(issues)}")
            for issue in issues[:20]:
                print(f"   • {issue}")
        else:
            self.print_success("All products are valid!")
        
        # Check Redis connection
        try:
            self.redis_client.ping()
            self.print_success("Redis connection: OK")
        except:
            self.print_error("Redis connection: FAILED")


# ---------------------------------
# STANDALONE RUN
# ---------------------------------
if __name__ == "__main__":
    manager = RedisManager()
    manager.show_menu()