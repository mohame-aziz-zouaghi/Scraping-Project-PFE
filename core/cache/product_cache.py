import json
import time

from core.cache.redis_client import RedisClient
from core.cache.cache_keys import product_key


class ProductCache:

    def __init__(self):

        self.redis = RedisClient().get_client()


    def normalize_datetime(self, value):

        if not value:
            return None

        return value.isoformat()
    
    # ---------------------------------
    # CACHE EXISTS
    # ---------------------------------
    def exists(self, site, product_id):

        key = product_key(site, product_id)

        return self.redis.exists(key) == 1

    # ---------------------------------
    # GET PRODUCT CACHE
    # ---------------------------------
    def get_product(self, site, product_id):

        key = product_key(site, product_id)

        data = self.redis.get(key)

        if not data:
            return None

        return json.loads(data)

    # ---------------------------------
    # SET PRODUCT CACHE
    # ---------------------------------
    def set_product(
        self,
        site,
        product_id,
        payload,
        ttl=3600,
        pipe=None
    ):

        key = product_key(site, product_id)

        date = payload.get("price_valid_until")
        cache_payload = {
            "price": payload.get("price"),
            "discount_price": payload.get("discount_price"),
            "discount_amount": payload.get("discount_amount"),
            "availability": payload.get("availability"),
            "special_id": payload.get("special_id"),
            "price_valid_until": self.normalize_datetime(date),
            "last_checked": int(time.time())
        }

        serialized = json.dumps(
            cache_payload,
            default=str
        )

        # ---------------------------------
        # PIPELINE MODE
        # ---------------------------------
        if pipe:

            pipe.set(
                key,
                serialized,
                ex=ttl
            )

        # ---------------------------------
        # NORMAL MODE
        # ---------------------------------
        else:

            self.redis.set(
                key,
                serialized,
                ex=ttl
            )

    # ---------------------------------
    # DELETE CACHE
    # ---------------------------------
    def delete_product(self, site, product_id):

        key = product_key(site, product_id)

        self.redis.delete(key)

    # ---------------------------------
    # CHANGE DETECTION
    # ---------------------------------
    def has_changed(self, site, product_id, new_data):

        cached = self.get_product(site, product_id)

        if not cached:
            return True

        return (
            cached.get("price") != new_data.get("price") or
            cached.get("discount_price") != new_data.get("discount_price") or
            cached.get("discount_amount") != new_data.get("discount_amount") or
            cached.get("availability") != new_data.get("availability") or
            cached.get("special_id") != new_data.get("special_id") or
            cached.get("price_valid_until") != new_data.get("price_valid_until")
        )