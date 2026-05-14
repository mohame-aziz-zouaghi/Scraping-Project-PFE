# SCRAPINGPROJECT/backup_redis.py
import os
import subprocess
import json
import shutil
from datetime import datetime
from Database.Redis.Configuration.redis_client import RedisClient
from core.logger import setup_logger

logger = setup_logger("redis_backup", "backup")

class RedisBackup:
    def __init__(self):
        self.backup_dir = "redis_backups"
        os.makedirs(self.backup_dir, exist_ok=True)
    
    def backup_with_rdb(self):
        """Force Redis to save RDB and backup the file"""
        print("\n" + "="*60)
        print("BACKUP REDIS DATA (RDB)")
        print("="*60)
        
        try:
            # Force Redis to save snapshot
            print("Forcing Redis to save snapshot...")
            result = subprocess.run(
                ["docker", "exec", "scraping_redis", "redis-cli", "SAVE"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print("✅ Redis snapshot saved successfully")
                
                # Copy RDB file from container
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_filename = f"dump_{timestamp}.rdb"
                backup_path = os.path.join(self.backup_dir, backup_filename)
                
                print(f"Copying RDB file to {backup_path}...")
                subprocess.run(
                    ["docker", "cp", "scraping_redis:/data/dump.rdb", backup_path],
                    capture_output=True
                )
                
                if os.path.exists(backup_path):
                    size_mb = os.path.getsize(backup_path) / (1024 * 1024)
                    print(f"✅ Backup successful! File size: {size_mb:.2f} MB")
                    
                    # Also copy AOF if exists
                    try:
                        subprocess.run(
                            ["docker", "cp", "scraping_redis:/data/appendonly.aof", 
                             os.path.join(self.backup_dir, f"appendonly_{timestamp}.aof")],
                            capture_output=True
                        )
                        print(f"✅ AOF backup also saved")
                    except:
                        pass
                    
                    return backup_path
                else:
                    print("❌ Failed to copy RDB file")
                    return None
            else:
                print(f"❌ Redis SAVE failed: {result.stderr}")
                return None
                
        except Exception as e:
            print(f"❌ Backup error: {e}")
            return None
    
    def restore_from_backup(self, backup_file):
        """Restore Redis from backup RDB file"""
        print("\n" + "="*60)
        print("RESTORE REDIS FROM BACKUP")
        print("="*60)
        
        if not os.path.exists(backup_file):
            print(f"❌ Backup file not found: {backup_file}")
            return False
        
        try:
            # Stop Redis container
            print("Stopping Redis container...")
            subprocess.run(["docker", "stop", "scraping_redis"], capture_output=True)
            
            # Copy backup file to container
            print(f"Copying {backup_file} to container...")
            subprocess.run(
                ["docker", "cp", backup_file, "scraping_redis:/data/dump.rdb"],
                capture_output=True
            )
            
            # Start Redis container
            print("Starting Redis container...")
            subprocess.run(["docker", "start", "scraping_redis"], capture_output=True)
            
            print("✅ Restore completed! Redis should load the backup on startup")
            return True
            
        except Exception as e:
            print(f"❌ Restore error: {e}")
            return False
    
    def list_backups(self):
        """List all available backups"""
        backups = []
        for file in os.listdir(self.backup_dir):
            if file.endswith('.rdb'):
                filepath = os.path.join(self.backup_dir, file)
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                backups.append({
                    'file': file,
                    'size': f"{size_mb:.2f} MB",
                    'date': mtime.strftime("%Y-%m-%d %H:%M:%S"),
                    'path': filepath
                })
        
        return sorted(backups, key=lambda x: x['date'], reverse=True)
    
    def backup_menu(self):
        """Show backup management menu"""
        while True:
            print("\n" + "="*60)
            print("REDIS BACKUP MANAGEMENT")
            print("="*60)
            print("1 - 💾 Create Backup Now")
            print("2 - 📋 List All Backups")
            print("3 - 🔄 Restore from Backup")
            print("4 - 📤 Export to JSON")
            print("0 - Back to Main Menu")
            
            choice = input("\nChoice: ").strip()
            
            if choice == "1":
                self.backup_with_rdb()
            elif choice == "2":
                backups = self.list_backups()
                if backups:
                    print("\n📋 Available backups:")
                    for i, backup in enumerate(backups, 1):
                        print(f"   {i}. {backup['file']} ({backup['size']}) - {backup['date']}")
                else:
                    print("\nNo backups found")
            elif choice == "3":
                backups = self.list_backups()
                if backups:
                    print("\nSelect backup to restore:")
                    for i, backup in enumerate(backups, 1):
                        print(f"   {i}. {backup['file']}")
                    idx = int(input("\nEnter number: ")) - 1
                    if 0 <= idx < len(backups):
                        self.restore_from_backup(backups[idx]['path'])
                else:
                    print("No backups available")
            elif choice == "4":
                self.export_to_json()
            elif choice == "0":
                break
    
    def export_to_json(self):
        """Export Redis data to JSON file"""

        redis_client = RedisClient.get_client()
        keys = redis_client.keys("product:*")
        
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "total_products": len(keys),
            "products": {}
        }
        
        print(f"\nExporting {len(keys)} products to JSON...")
        
        for key in keys:
            key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
            value = redis_client.get(key)
            if value:
                try:
                    if isinstance(value, bytes):
                        value = value.decode('utf-8')
                    export_data["products"][key_str] = json.loads(value)
                except:
                    export_data["products"][key_str] = str(value)
        
        filename = f"redis_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, default=str)
        
        file_size = os.path.getsize(filename) / (1024 * 1024)
        print(f"✅ Exported to {filename} ({file_size:.2f} MB)")

if __name__ == "__main__":
    backup = RedisBackup()
    backup.show_menu()