# Database/Redis/Configuration/redis_client.py
"""
Redis Client - Thread-safe singleton with connection pooling
"""
import redis
import threading
import logging
from typing import Optional, Dict, Any
from redis.connection import ConnectionPool

logger = logging.getLogger("redis_client")


class RedisClient:
    """Thread-safe singleton Redis client with connection pooling"""
    
    _instance = None
    _lock = threading.Lock()
    _pool = None
    _client = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(RedisClient, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        with self._lock:
            if self._initialized:
                return
            self._connect()
            self._initialized = True
    
    def _connect(self):
        """Establish Redis connection with connection pooling"""
        try:
            # Try to get from environment, fallback to defaults
            import os
            from dotenv import load_dotenv
            load_dotenv()
            
            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', 6379))
            redis_db = int(os.getenv('REDIS_DB', 0))
            redis_password = os.getenv('REDIS_PASSWORD', None)
            
            logger.info(f"REDIS | Connecting to {redis_host}:{redis_port}/{redis_db}")
            
            # Create connection pool
            self._pool = ConnectionPool(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password,
                max_connections=50,
                socket_timeout=5,
                socket_connect_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
                decode_responses=True
            )
            
            # Create client with pool
            self._client = redis.Redis(
                connection_pool=self._pool,
                socket_keepalive=True
            )
            
            # Test connection
            if self._client.ping():
                logger.info(f"REDIS | Connected successfully | Pool max={self._pool.max_connections}")
                
                # Clear any old test keys
                self._client.delete("_test_connection")
                
                return True
            else:
                logger.error("REDIS | Ping failed")
                self._client = None
                self._pool = None
                return False
                
        except redis.ConnectionError as e:
            logger.error(f"REDIS | Connection error: {e}")
            self._client = None
            self._pool = None
            return False
        except Exception as e:
            logger.error(f"REDIS | Unexpected error: {e}")
            self._client = None
            self._pool = None
            return False
    
    @property
    def client(self):
        """Get Redis client"""
        if self._client is None:
            self._connect()
        return self._client
    
    @property
    def redis(self):
        """Alias for client"""
        return self.client
    
    def get_client(self):
        """Get Redis client"""
        return self.client
    
    def get_pool(self):
        """Get connection pool"""
        return self._pool
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics"""
        if self._pool:
            return {
                'max_connections': self._pool.max_connections,
                'in_use': self._pool._in_use_connections if hasattr(self._pool, '_in_use_connections') else 0,
                'available': self._pool._available_connections if hasattr(self._pool, '_available_connections') else 0,
            }
        return {'max_connections': 0, 'in_use': 0, 'available': 0}
    
    def ping(self) -> bool:
        """Check if Redis is available"""
        try:
            if self._client:
                return self._client.ping()
            return False
        except Exception:
            return False
    
    def is_connected(self) -> bool:
        """Check if connected"""
        return self._client is not None and self.ping()
    
    # =====================================================
    # REDIS OPERATIONS
    # =====================================================
    
    def get(self, key: str) -> Optional[str]:
        """Get value by key"""
        try:
            if self._client:
                return self._client.get(key)
            return None
        except Exception as e:
            logger.error(f"REDIS | GET error: {key} | {e}")
            return None
    
    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """Set key with value"""
        try:
            if self._client:
                return self._client.set(key, value, ex=ex)
            return False
        except Exception as e:
            logger.error(f"REDIS | SET error: {key} | {e}")
            return False
    
    def setex(self, key: str, time: int, value: Any) -> bool:
        """Set key with expiration"""
        try:
            if self._client:
                return self._client.setex(key, time, value)
            return False
        except Exception as e:
            logger.error(f"REDIS | SETEX error: {key} | {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """Check if key exists"""
        try:
            if self._client:
                return self._client.exists(key) > 0
            return False
        except Exception as e:
            logger.error(f"REDIS | EXISTS error: {key} | {e}")
            return False
    
    def delete(self, *keys) -> int:
        """Delete keys"""
        try:
            if self._client:
                return self._client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"REDIS | DELETE error: {keys} | {e}")
            return 0
    
    def keys(self, pattern: str = "*") -> list:
        """Get keys matching pattern"""
        try:
            if self._client:
                return self._client.keys(pattern)
            return []
        except Exception as e:
            logger.error(f"REDIS | KEYS error: {pattern} | {e}")
            return []
    
    def scan_iter(self, pattern: str = "*", count: int = 100):
        """Iterate over keys matching pattern"""
        try:
            if self._client:
                return self._client.scan_iter(match=pattern, count=count)
            return iter([])
        except Exception as e:
            logger.error(f"REDIS | SCAN_ITER error: {pattern} | {e}")
            return iter([])
    
    def incr(self, key: str) -> int:
        """Increment key"""
        try:
            if self._client:
                return self._client.incr(key)
            return 0
        except Exception as e:
            logger.error(f"REDIS | INCR error: {key} | {e}")
            return 0
    
    def expire(self, key: str, time: int) -> bool:
        """Set expiration on key"""
        try:
            if self._client:
                return self._client.expire(key, time)
            return False
        except Exception as e:
            logger.error(f"REDIS | EXPIRE error: {key} | {e}")
            return False
    
    def ttl(self, key: str) -> int:
        """Get TTL of key"""
        try:
            if self._client:
                return self._client.ttl(key)
            return -1
        except Exception as e:
            logger.error(f"REDIS | TTL error: {key} | {e}")
            return -1
    
    def pipeline(self):
        """Get pipeline for batch operations"""
        if self._client:
            return self._client.pipeline()
        return None
    
    def close(self):
        """Close client (pool stays open)"""
        self._client = None
        logger.info("REDIS | Client closed (pool remains)")
    
    def close_pool(self):
        """Close connection pool (shutdown only)"""
        if self._pool:
            try:
                self._pool.disconnect()
                logger.info("REDIS | Pool closed")
            except Exception as e:
                logger.error(f"REDIS | Pool close error: {e}")
            finally:
                self._pool = None
                self._client = None
    
    def __del__(self):
        """Cleanup on deletion"""
        try:
            if self._client:
                self.close()
        except:
            pass


# =====================================================
# SINGLETON ACCESSORS
# =====================================================

_redis_client_instance = None
_redis_lock = threading.Lock()


def get_redis_client() -> RedisClient:
    """Get the singleton Redis client instance"""
    global _redis_client_instance
    if _redis_client_instance is None:
        with _redis_lock:
            if _redis_client_instance is None:
                _redis_client_instance = RedisClient()
    return _redis_client_instance


def get_redis_connection() -> Optional[redis.Redis]:
    """Get the Redis connection (for backward compatibility)"""
    client = get_redis_client()
    return client.client


def get_redis_pool_stats() -> Dict[str, Any]:
    """Get Redis pool statistics"""
    client = get_redis_client()
    return client.get_pool_stats()


def check_redis_connection() -> bool:
    """Check if Redis is connected"""
    client = get_redis_client()
    return client.is_connected()