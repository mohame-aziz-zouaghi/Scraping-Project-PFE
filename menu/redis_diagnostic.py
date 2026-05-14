# cli/redis_diagnostic.py
import sys
import os
import subprocess
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Database.Redis.Configuration.redis_client import RedisClient
from core.logger import setup_logger

logger = setup_logger("redis_diagnostic", "cli")

class RedisDiagnostic:
    def __init__(self):
        self.redis_client = RedisClient().get_client()
        self.container_name = "scraping_redis"  # Docker container name
    
    def check_persistence(self):
        """Check Redis persistence configuration and status"""
        print("\n" + "="*60)
        print("REDIS PERSISTENCE DIAGNOSTIC")
        print("="*60)
        
        # Get persistence info from Redis
        info = self.redis_client.info('persistence')
        
        print("\n📁 PERSISTENCE STATUS:")
        print(f"   • RDB enabled: {info.get('rdb_enabled', 0)}")
        print(f"   • RDB last save time: {info.get('rdb_last_save_time', 'N/A')}")
        print(f"   • RDB last background save status: {info.get('rdb_last_bgsave_status', 'N/A')}")
        print(f"   • RDB current background save time: {info.get('rdb_current_bgsave_time_sec', 'N/A')}")
        print(f"   • AOF enabled: {info.get('aof_enabled', 0)}")
        print(f"   • AOF rewrite in progress: {info.get('aof_rewrite_in_progress', 'N/A')}")
        
        # Get save configuration
        save_config = self.redis_client.config_get('save')
        print(f"\n💾 Save Configuration:")
        print(f"   • {save_config}")
        
        # Check RDB file INSIDE Docker container (not on host)
        print(f"\n📂 Checking RDB file inside Docker container...")
        try:
            # Use docker exec to check inside container
            result = subprocess.run(
                ["docker", "exec", self.container_name, "ls", "-lh", "/data/"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print(f"   📂 /data directory contents:")
                for line in result.stdout.split('\n'):
                    if 'rdb' in line.lower() or 'aof' in line.lower():
                        print(f"   ✅ {line}")
                    elif 'total' in line.lower():
                        print(f"   {line}")
                
                # Specifically check for dump.rdb
                rdb_result = subprocess.run(
                    ["docker", "exec", self.container_name, "ls", "-lh", "/data/dump.rdb"],
                    capture_output=True,
                    text=True
                )
                
                if rdb_result.returncode == 0:
                    print(f"\n   ✅ RDB file exists in container!")
                    # Parse file size
                    parts = rdb_result.stdout.strip().split()
                    if len(parts) >= 5:
                        size = parts[4]
                        print(f"   📦 Size: {size}")
                else:
                    print(f"\n   ⚠️ No RDB file found in container")
            else:
                print(f"   ❌ Could not access container: {result.stderr}")
                
        except Exception as e:
            print(f"   ❌ Error checking container: {e}")
    
    def check_keys(self):
        """Check current keys in Redis"""
        print("\n" + "="*60)
        print("CURRENT REDIS KEYS")
        print("="*60)
        
        keys = self.redis_client.keys("*")
        print(f"\n🔑 Total keys in Redis: {len(keys)}")
        
        if keys:
            # Handle both bytes and string keys safely
            product_keys = []
            other_keys = []
            
            for k in keys:
                if isinstance(k, bytes):
                    key_str = k.decode('utf-8')
                else:
                    key_str = str(k)
                
                if 'product:' in key_str:
                    product_keys.append(key_str)
                else:
                    other_keys.append(key_str)
            
            print(f"   • Product keys: {len(product_keys)}")
            print(f"   • Other keys: {len(other_keys)}")
            
            # Show sample product keys
            if product_keys:
                print("\n📋 Sample product keys:")
                for i, key in enumerate(product_keys[:10], 1):
                    # Get value for this key to show info
                    value = self.redis_client.get(key)
                    if value:
                        try:
                            import json
                            if isinstance(value, bytes):
                                value = value.decode('utf-8')
                            data = json.loads(value)
                            price = data.get('price', 'N/A')
                            print(f"   {i}. {key} - Price: {price} TND")
                        except:
                            print(f"   {i}. {key}")
                    else:
                        print(f"   {i}. {key}")
            
            # Show other keys
            if other_keys:
                print(f"\n📋 Other keys (first 5):")
                for i, key in enumerate(other_keys[:5], 1):
                    print(f"   {i}. {key}")
        else:
            print("\n⚠️ NO KEYS FOUND IN REDIS!")
            print("   Redis is running but has no data loaded.")
            print("\n   Solution: Run 'python monitoring/preload_cache.py'")
    
    def check_memory(self):
        """Check Redis memory usage"""
        print("\n" + "="*60)
        print("REDIS MEMORY STATUS")
        print("="*60)
        
        info = self.redis_client.info('memory')
        
        print(f"\n💾 Memory usage:")
        print(f"   • Used memory: {info.get('used_memory_human', 'N/A')}")
        print(f"   • Peak memory: {info.get('used_memory_peak_human', 'N/A')}")
        print(f"   • Memory fragmentation ratio: {info.get('mem_fragmentation_ratio', 'N/A')}")
        print(f"   • Max memory: {info.get('maxmemory_human', 'N/A')}")
        print(f"   • Eviction policy: {info.get('maxmemory_policy', 'N/A')}")
    
    def force_load_rdb(self):
        """Force Redis to save and verify RDB file"""
        print("\n" + "="*60)
        print("FORCE REDIS SAVE & VERIFY")
        print("="*60)
        
        try:
            # Get current keys count
            before_keys = len(self.redis_client.keys("*"))
            print(f"\n📊 Before save: {before_keys} keys in Redis")
            
            # Force Redis to save
            print("\n💾 Forcing Redis to save to disk...")
            result = self.redis_client.save()
            
            if result:
                print("   ✅ Save command executed successfully!")
                
                # Get save info
                info = self.redis_client.info('persistence')
                last_save = info.get('rdb_last_save_time', 0)
                if last_save:
                    last_save_time = datetime.fromtimestamp(last_save)
                    print(f"   📅 Last save completed at: {last_save_time.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # Verify RDB file inside container
                print("\n🔍 Verifying RDB file in Docker container...")
                result = subprocess.run(
                    ["docker", "exec", self.container_name, "ls", "-lh", "/data/dump.rdb"],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode == 0:
                    print(f"   ✅ RDB file verified in container!")
                    print(f"   📦 {result.stdout.strip()}")
                    
                    # Check file size
                    parts = result.stdout.strip().split()
                    if len(parts) >= 5:
                        size = parts[4]
                        print(f"   💾 File size: {size}")
                else:
                    print(f"   ⚠️ RDB file not found in container")
                    
                    # Check what's in /data
                    print("\n   📂 Contents of /data:")
                    ls_result = subprocess.run(
                        ["docker", "exec", self.container_name, "ls", "-la", "/data/"],
                        capture_output=True,
                        text=True
                    )
                    print(f"   {ls_result.stdout}")
                
                # Show save configuration
                save_config = self.redis_client.config_get('save')
                print(f"\n⚙️ Current save configuration: {save_config}")
                
                if not save_config.get('save'):
                    print("\n   ⚠️ WARNING: No save configuration set!")
                    print("   Run: docker exec scraping_redis redis-cli CONFIG SET save '3600 1 300 100 60 1000'")
                
            else:
                print("   ❌ Save command failed!")
                
        except Exception as e:
            print(f"❌ Error: {e}")
    
    def enable_persistence(self):
        """Enable persistence on a running Redis container"""
        print("\n" + "="*60)
        print("ENABLE PERSISTENCE")
        print("="*60)
        
        try:
            # Enable RDB saves
            print("\n🔧 Enabling RDB persistence...")
            self.redis_client.config_set('save', '3600 1 300 100 60 1000')
            print("   ✅ Save intervals set: 3600 1, 300 100, 60 1000")
            
            # Enable AOF
            print("\n🔧 Enabling AOF persistence...")
            self.redis_client.config_set('appendonly', 'yes')
            self.redis_client.config_set('appendfsync', 'everysec')
            print("   ✅ AOF enabled with everysec sync")
            
            # Enable compression
            print("\n🔧 Enabling RDB compression...")
            self.redis_client.config_set('rdbcompression', 'yes')
            print("   ✅ RDB compression enabled")
            
            # Force a save
            print("\n💾 Saving to disk...")
            self.redis_client.save()
            print("   ✅ Save completed")
            
            # Verify
            print("\n📊 Verifying configuration...")
            save_config = self.redis_client.config_get('save')
            appendonly = self.redis_client.config_get('appendonly')
            
            print(f"   Save config: {save_config}")
            print(f"   Appendonly: {appendonly}")
            
            print("\n✅ Persistence enabled successfully!")
            print("\n⚠️ Note: These settings will be lost if container restarts.")
            print("   To make permanent, update docker-compose.yml with:")
            print("   command: redis-server --save 3600 1 --save 300 100 --save 60 1000 --appendonly yes")
            
        except Exception as e:
            print(f"❌ Error enabling persistence: {e}")
    
    def run_full_diagnostic(self):
        """Run all diagnostics"""
        self.check_persistence()
        self.check_keys()
        self.check_memory()
        self.force_load_rdb()
        
        print("\n" + "="*60)
        print("RECOMMENDATIONS")
        print("="*60)
        
        keys = self.redis_client.keys("*")
        info = self.redis_client.info('persistence')
        rdb_enabled = info.get('rdb_enabled', 0)
        
        if len(keys) == 0:
            print("\n❌ REDIS IS EMPTY!")
            print("\nSolutions:")
            print("1. Run 'python monitoring/preload_cache.py' to reload data")
            print("2. Check Redis connection")
            print("3. Verify database has products")
        elif rdb_enabled == 0:
            print("\n⚠️ REDIS HAS DATA BUT PERSISTENCE IS DISABLED!")
            print("\nYour data will be lost on container restart!")
            print("\nSolutions:")
            print("1. Run option '6' to enable persistence now")
            print("2. Update docker-compose.yml to make permanent")
        else:
            print("\n✅ Redis has data and persistence is enabled!")
            print(f"   {len(keys)} keys in cache")
            print("   Your cache should survive container restarts")
            
            # Test persistence
            print("\n🧪 To test persistence, run:")
            print("   docker-compose restart")
            print("   docker exec scraping_redis redis-cli DBSIZE")


    def show_diagnostic(self):
        # Show menu
        print("\n" + "="*60)
        print("REDIS DIAGNOSTIC TOOL")
        print("="*60)
        print("1 - Run full diagnostic")
        print("2 - Check persistence only")
        print("3 - Check keys only")
        print("4 - Check memory only")
        print("5 - Force save & verify RDB")
        print("6 - Enable persistence (fix missing saves)")
        print("0 - Exit")
        
        choice = input("\n👉 Choose option: ").strip()
        
        if choice == "1":
            self.run_full_diagnostic()
        elif choice == "2":
            self.check_persistence()
        elif choice == "3":
            self.check_keys()
        elif choice == "4":
            self.check_memory()
        elif choice == "5":
            self.force_load_rdb()
        elif choice == "6":
            self.enable_persistence()
        else:
            print("Exiting...")

if __name__ == "__main__":
    diagnostic = RedisDiagnostic()
    