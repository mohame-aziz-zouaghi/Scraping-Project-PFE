# Database/Redis/product_cache.py
from datetime import datetime
import json
import time
from typing import Optional, Dict, Any, List

from Database.Redis.Configuration.redis_client import RedisClient
from Database.Redis.cache_keys import product_key
from core.utils.cleaning_url_logic import normalize_url
from core.logger import setup_logger

logger = setup_logger("product_cache", "cache")


class ProductCache:
    """Redis cache manager with complete product data storage"""

    def __init__(self):
        self.redis = RedisClient().get_client()

    # =====================================================
    # HELPER METHODS
    # =====================================================
    
    def normalize_datetime(self, value):
        """Normalize datetime to ISO date string"""
        if not value:
            return None
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", ""))
                return parsed.date().isoformat()
            except:
                return value
        if isinstance(value, datetime):
            return value.date().isoformat()
        return str(value)

    # =====================================================
    # CORE OPERATIONS
    # =====================================================
    
    def exists(self, site: str, main_product_id: str) -> bool:
        """Check if product exists in cache"""
        key = product_key(site, main_product_id)
        return self.redis.exists(key) == 1

    def get_product(self, site: str, main_product_id: str) -> Optional[Dict]:
        """Get complete product data including all monitoring fields"""
        key = product_key(site, main_product_id)
        data = self.redis.get(key)
        if not data:
            return None
        return json.loads(data)

    def build_product_payload(self, product_data: Dict, include_monitoring: bool = True) -> Dict:
        """Build complete product payload with all fields"""
        date = product_data.get("price_valid_until")
        
        # Core product data
        payload = {
            "url": normalize_url(product_data.get("url")),
            "price": float(product_data.get("price")) if product_data.get("price") is not None else 0,
            "discount_price": float(product_data.get("discount_price")) if product_data.get("discount_price") is not None else 0,
            "discount_amount": float(product_data.get("discount_amount")) if product_data.get("discount_amount") is not None else 0,
            "availability": product_data.get("availability", "OutOfStock"),
            "special_id": product_data.get("special_id"),
            "price_valid_until": self.normalize_datetime(date),
            "name": product_data.get("name"),
            "brand": product_data.get("brand"),
            "category": product_data.get("category"),
            "main_product_id": product_data.get("main_product_id"),
            "site": product_data.get("site", "").lower(),
        }
        
        # Monitoring tracking fields
        if include_monitoring:
            payload.update({
                "last_checked": int(time.time()),
                "check_count": product_data.get("check_count", 0),
                "change_count": product_data.get("change_count", 0),
                "price_change_count": product_data.get("price_change_count", 0),
                "availability_change_count": product_data.get("availability_change_count", 0),
                "monitoring_priority": product_data.get("monitoring_priority", 3),
                "monitoring_group": product_data.get("monitoring_group", "regular"),
                "next_check_at": product_data.get("next_check_at"),
                "last_check_duration": product_data.get("last_check_duration", 0),
                "consecutive_errors": product_data.get("consecutive_errors", 0),
                "price_volatility_score": product_data.get("price_volatility_score", 50),
                "price_trend": product_data.get("price_trend", "stable"),
                "avg_price": product_data.get("avg_price"),
                "min_price": product_data.get("min_price"),
                "max_price": product_data.get("max_price"),
                "alert_on_price_drop_percent": 10,
                "alert_on_price_increase_percent": 20,
                "is_enabled": product_data.get("is_enabled", True),
                "check_failures": product_data.get("check_failures", 0),
            })
        
        return payload

    def set_product(self, site: str, main_product_id: str, product_data: Dict, ttl: Optional[int] = None, pipe=None):
        """Set complete product in Redis cache (includes all monitoring fields)"""
        key = product_key(site, main_product_id)
        payload = self.build_product_payload(product_data, include_monitoring=True)
        serialized = json.dumps(payload, default=str)

        if pipe:
            if ttl:
                pipe.setex(key, ttl, serialized)
            else:
                pipe.set(key, serialized)
        else:
            if ttl:
                self.redis.setex(key, ttl, serialized)
            else:
                self.redis.set(key, serialized)
        
        logger.debug(f"Product cached: {site}/{main_product_id}")

    def update_product(self, site: str, main_product_id: str, updated_data: Dict, ttl: Optional[int] = None) -> bool:
        """Update entire product in Redis with new data (one request)"""
        key = product_key(site, main_product_id)
        existing = self.get_product(site, main_product_id)
        
        if existing:
            existing.update(updated_data)
            existing["last_checked"] = int(time.time())
            payload = existing
        else:
            payload = self.build_product_payload(updated_data, include_monitoring=True)
        
        serialized = json.dumps(payload, default=str)
        
        if ttl:
            self.redis.setex(key, ttl, serialized)
        else:
            self.redis.set(key, serialized)
        
        logger.debug(f"Product updated: {site}/{main_product_id}")
        return True

    def delete_product(self, site: str, main_product_id: str):
        """Delete product from cache"""
        key = product_key(site, main_product_id)
        self.redis.delete(key)
        logger.debug(f"Product deleted: {site}/{main_product_id}")

    # =====================================================
    # MONITORING UPDATE METHODS (ONE REQUEST EACH)
    # =====================================================
    
    def increment_check_count(self, site: str, main_product_id: str, has_change: bool = False,
                              is_price_change: bool = False, is_availability_change: bool = False,
                              duration: float = 0) -> bool:
        """Increment check count and update related fields"""
        product = self.get_product(site, main_product_id)
        if not product:
            return False
        
        product["check_count"] = product.get("check_count", 0) + 1
        product["last_checked"] = int(time.time())
        product["last_check_duration"] = duration
        
        if has_change:
            product["change_count"] = product.get("change_count", 0) + 1
        if is_price_change:
            product["price_change_count"] = product.get("price_change_count", 0) + 1
        if is_availability_change:
            product["availability_change_count"] = product.get("availability_change_count", 0) + 1
        
        product["consecutive_errors"] = 0
        
        key = product_key(site, main_product_id)
        self.redis.set(key, json.dumps(product, default=str))
        return True

    def update_monitoring_fields(self, site: str, main_product_id: str, updates: Dict) -> bool:
        """Update specific monitoring fields in one Redis operation"""
        product = self.get_product(site, main_product_id)
        if not product:
            return False
        
        product.update(updates)
        product["last_checked"] = int(time.time())
        
        key = product_key(site, main_product_id)
        self.redis.set(key, json.dumps(product, default=str))
        return True

    def update_next_check(self, site: str, main_product_id: str, next_check_at: int) -> bool:
        """Update when this product should be checked next"""
        return self.update_monitoring_fields(site, main_product_id, {"next_check_at": next_check_at})

    def update_price_volatility(self, site: str, main_product_id: str, new_price: float) -> bool:
        """Update price volatility score in PostgreSQL based on price change"""
        cursor = self.conn.cursor()
        try:
            # Get current product data - convert Decimal to float
            cursor.execute("""
                SELECT price, price_volatility_score, price_change_count
                FROM products 
                WHERE site = %s AND main_product_id = %s
            """, (site.lower(), main_product_id))
            
            row = cursor.fetchone()
            if not row:
                logger.warning(f"Product not found for volatility update: {site}/{main_product_id}")
                return False
            
            # Convert Decimal to float
            old_price = float(row[0]) if row[0] is not None else 0
            current_volatility = int(row[1]) if row[1] is not None else 50
            
            # Calculate new volatility
            if old_price > 0:
                price_change_percent = abs(new_price - old_price) / old_price * 100
                
                if price_change_percent > 20:
                    new_volatility = min(100, current_volatility + 10)
                elif price_change_percent > 10:
                    new_volatility = min(100, current_volatility + 5)
                elif price_change_percent > 5:
                    new_volatility = current_volatility
                else:
                    new_volatility = max(0, current_volatility - 2)
            else:
                new_volatility = current_volatility
            
            # Determine price trend
            if old_price > 0:
                if new_price > old_price:
                    trend = "upward"
                elif new_price < old_price:
                    trend = "downward"
                else:
                    trend = "stable"
            else:
                trend = "stable"
            
            # Update the product
            cursor.execute("""
                UPDATE products 
                SET price_volatility_score = %s,
                    price_trend = %s,
                    last_updated_at = NOW()
                WHERE site = %s AND main_product_id = %s
            """, (new_volatility, trend, site.lower(), main_product_id))
            
            self.conn.commit()
            logger.debug(f"Updated price volatility for {site}/{main_product_id}: {new_volatility} ({trend})")
            return True
            
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to update price volatility: {e}")
            return False
        finally:
            cursor.close()

    # =====================================================
    # CHANGE DETECTION
    # =====================================================
    
    def has_changed(self, site: str, main_product_id: str, new_data: Dict) -> tuple:
        """Check if product has changed and return changed fields"""
        cached = self.get_product(site, main_product_id)
        if not cached:
            return True, []

        changed_fields = []
        fields_to_compare = ["price", "discount_price", "discount_amount", "availability", "special_id", "price_valid_until"]
        
        for field in fields_to_compare:
            cached_val = cached.get(field)
            new_val = new_data.get(field)
            
            if field in ["price", "discount_price", "discount_amount"]:
                cached_val = float(cached_val) if cached_val else 0
                new_val = float(new_val) if new_val else 0
            elif field == "price_valid_until":
                cached_val = self.normalize_datetime(cached_val)
                new_val = self.normalize_datetime(new_val)
            
            if cached_val != new_val:
                changed_fields.append(field)
        
        return len(changed_fields) > 0, changed_fields