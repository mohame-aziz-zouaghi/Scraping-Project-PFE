# Database/Redis/product_cache.py
"""
Redis Cache Manager - Optimized for Real-time Performance
Only stores essential data needed for monitoring and frontend.
Heavy monitoring statistics stay in PostgreSQL.
"""
from datetime import datetime
import json
import time
from typing import Optional, Dict, Any

from Database.Redis.Configuration.redis_client import RedisClient
from Database.Redis.cache_keys import product_key
from core.utils.cleaning_url_logic import normalize_url
from core.logger import setup_logger

logger = setup_logger("product_cache", "cache")


class ProductCache:
    """Redis cache manager - Stores ONLY essential real-time data"""

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
        """Get product data from Redis (returns None if not found)"""
        key = product_key(site, main_product_id)
        data = self.redis.get(key)
        if not data:
            return None
        
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                logger.warning(f"Invalid data type in Redis for {key}: {type(result)}")
                return {}
            return result
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for {key}: {e}")
            return {}

    def build_product_payload(self, product_data: Dict) -> Dict:
        """
        Build minimal Redis payload - ONLY essential fields
        Monitoring stats are stored in PostgreSQL
        """
        payload = {
            # ===== CORE PRODUCT DATA =====
            "url": normalize_url(product_data.get("url")),
            "price": float(product_data.get("price")) if product_data.get("price") is not None else 0,
            "discount_price": float(product_data.get("discount_price")) if product_data.get("discount_price") is not None else 0,
            "discount_amount": float(product_data.get("discount_amount")) if product_data.get("discount_amount") is not None else 0,
            "availability": product_data.get("availability", "OutOfStock"),
            "special_id": product_data.get("special_id"),
            "price_valid_until": self.normalize_datetime(product_data.get("price_valid_until")),
            "name": product_data.get("name"),
            "brand": product_data.get("brand"),
            "category": product_data.get("category"),
            
            # ===== REAL-TIME TRACKING (Essential for monitoring) =====
            "last_checked": int(time.time()),
            "is_enabled": product_data.get("is_enabled", True),
            
            # ===== MONITORING PRIORITY (For scheduling) =====
            "monitoring_priority": product_data.get("monitoring_priority", 3),
            "monitoring_group": product_data.get("monitoring_group", "regular"),
            "next_check_at": product_data.get("next_check_at"),
            
            # ===== FAILURE TRACKING (Quick access) =====
            "check_failures": product_data.get("check_failures", 0),
            "consecutive_errors": product_data.get("consecutive_errors", 0),
        }
        
        return payload

    def set_product(self, site: str, main_product_id: str, product_data: Dict, ttl: Optional[int] = None, pipe=None):
        """
        Set product in Redis cache (minimal payload)
        
        Args:
            site: Site name
            main_product_id: Product ID from site
            product_data: Product data (only essential fields stored)
            ttl: Time to live in seconds
            pipe: Redis pipeline for batch operations
        """
        key = product_key(site, main_product_id)
        payload = self.build_product_payload(product_data)
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
        """Update entire product in Redis with new data"""
        key = product_key(site, main_product_id)
        existing = self.get_product(site, main_product_id)
        
        if existing:
            existing.update(updated_data)
            existing["last_checked"] = int(time.time())
            payload = existing
        else:
            payload = self.build_product_payload(updated_data)
        
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
    # MONITORING UPDATE METHODS
    # =====================================================
    
    def update_last_checked(self, site: str, main_product_id: str) -> bool:
        """Update only the last_checked timestamp"""
        return self.update_monitoring_fields(site, main_product_id, {
            "last_checked": int(time.time())
        })

    def update_monitoring_fields(self, site: str, main_product_id: str, updates: Dict) -> bool:
        """Update specific fields in Redis"""
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

    def set_enabled(self, site: str, main_product_id: str, enabled: bool) -> bool:
        """Enable or disable a product in Redis"""
        return self.update_monitoring_fields(site, main_product_id, {"is_enabled": enabled})

    def increment_check_failures(self, site: str, main_product_id: str) -> int:
        """Increment check_failures counter"""
        product = self.get_product(site, main_product_id)
        if not product:
            return 0
        
        new_count = product.get("check_failures", 0) + 1
        product["check_failures"] = new_count
        product["last_checked"] = int(time.time())
        
        key = product_key(site, main_product_id)
        self.redis.set(key, json.dumps(product, default=str))
        return new_count

    # =====================================================
    # CHANGE DETECTION
    # =====================================================
    
    def has_changed(self, site: str, main_product_id: str, new_data: Dict) -> tuple:
        """
        Check if product has changed (compares essential fields only)
        Returns: (has_changes, changed_fields_list)
        """
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

    # =====================================================
    # BULK OPERATIONS (For preload)
    # =====================================================
    
    def get_all_for_site(self, site: str) -> Dict[str, Dict]:
        """Get all products for a specific site"""
        from Database.Redis.cache_keys import site_pattern
        pattern = site_pattern(site)
        products = {}
        
        try:
            for key in self.redis.scan_iter(match=pattern):
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                
                product_id = key.split(":")[-1]
                product_data = self.get_product(site, product_id)
                if product_data:
                    products[product_id] = product_data
            
            return products
        except Exception as e:
            logger.error(f"Failed to get products for site {site}: {e}")
            return {}