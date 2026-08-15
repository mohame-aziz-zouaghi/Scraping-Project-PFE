# Database/Redis/Configuration/redis_client.py
"""
Redis Client - Singleton pattern with proper connection handling
Uses configuration from config.py (loaded from .env)
"""
import redis
import threading
from typing import Optional

from core.logger import setup_logger
from Database.Redis.Configuration.config import REDIS_CONFIG, validate_redis_config, get_redis_connection_url

logger = setup_logger("redis_client", "cache")


class RedisClient:
    """Redis client singleton with proper connection management"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(RedisClient, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._client = None
        self._connect()
    
    def _connect(self):
        """Establish Redis connection using config from .env"""
        try:
            # Get connection parameters from config
            host = REDIS_CONFIG.get('host', 'localhost')
            port = REDIS_CONFIG.get('port', 6379)
            db = REDIS_CONFIG.get('db', 0)
            password = REDIS_CONFIG.get('password', None)
            socket_timeout = REDIS_CONFIG.get('socket_timeout', 5)
            socket_connect_timeout = REDIS_CONFIG.get('socket_connect_timeout', 5)
            max_connections = REDIS_CONFIG.get('max_connections', 50)
            retry_on_timeout = REDIS_CONFIG.get('retry_on_timeout', True)
            decode_responses = REDIS_CONFIG.get('decode_responses', True)
            
            # Log connection attempt (without password)
            logger.info(f"Connecting to Redis at {host}:{port}/{db}")
            
            # Create connection pool
            connection_pool = redis.ConnectionPool(
                host=host,
                port=port,
                db=db,
                password=password,
                max_connections=max_connections,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                retry_on_timeout=retry_on_timeout,
                decode_responses=decode_responses,
            )
            
            # Create Redis client with connection pool
            self._client = redis.Redis(connection_pool=connection_pool)
            
            # Test connection
            self._client.ping()
            logger.info("✅ Redis connected successfully")
            
        except redis.ConnectionError as e:
            logger.error(f"❌ Redis connection failed: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Redis connection error: {e}")
            raise
    
    # =====================================================
    # PROPERTIES FOR BACKWARD COMPATIBILITY
    # =====================================================
    
    @property
    def client(self):
        """Backward compatibility - returns the redis client"""
        return self._client
    
    @property
    def redis(self):
        """Backward compatibility - returns the redis client"""
        return self._client
    
    # =====================================================
    # CONNECTION MANAGEMENT
    # =====================================================
    
    def get_client(self):
        """Get the underlying Redis client"""
        return self._client
    
    def ping(self) -> bool:
        """Ping Redis server"""
        if self._client:
            try:
                return self._client.ping()
            except:
                return False
        return False
    
    def is_connected(self) -> bool:
        """Check if Redis is connected"""
        try:
            return self.ping()
        except:
            return False
    
    # =====================================================
    # BASIC REDIS OPERATIONS
    # =====================================================
    
    def get(self, key: str):
        """Get value by key"""
        if self._client:
            try:
                return self._client.get(key)
            except:
                return None
        return None
    
    def set(self, key: str, value: str, ex: Optional[int] = None):
        """Set key with optional expiry"""
        if self._client:
            try:
                return self._client.set(key, value, ex=ex)
            except:
                return None
        return None
    
    def setex(self, key: str, ttl: int, value: str):
        """Set key with TTL"""
        if self._client:
            try:
                return self._client.setex(key, ttl, value)
            except:
                return None
        return None
    
    def exists(self, key: str) -> int:
        """Check if key exists"""
        if self._client:
            try:
                return self._client.exists(key)
            except:
                return 0
        return 0
    
    def delete(self, key: str) -> int:
        """Delete key"""
        if self._client:
            try:
                return self._client.delete(key)
            except:
                return 0
        return 0
    
    def keys(self, pattern: str) -> list:
        """Get keys matching pattern"""
        if self._client:
            try:
                return self._client.keys(pattern)
            except:
                return []
        return []
    
    def scan_iter(self, match: str = None, count: int = 100):
        """Scan keys matching pattern"""
        if self._client:
            try:
                return self._client.scan_iter(match=match, count=count)
            except:
                return []
        return []
    
    def incr(self, key: str) -> int:
        """Increment a key"""
        if self._client:
            try:
                return self._client.incr(key)
            except:
                return 0
        return 0
    
    def expire(self, key: str, ttl: int) -> bool:
        """Set expiration on a key"""
        if self._client:
            try:
                return self._client.expire(key, ttl)
            except:
                return False
        return False
    
    def ttl(self, key: str) -> int:
        """Get TTL of a key"""
        if self._client:
            try:
                return self._client.ttl(key)
            except:
                return -2
        return -2
    
    # =====================================================
    # BULK OPERATIONS
    # =====================================================
    
    def pipeline(self):
        """Get a Redis pipeline for batch operations"""
        if self._client:
            return self._client.pipeline()
        return None
    
    def mget(self, keys: list) -> list:
        """Get multiple keys"""
        if self._client and keys:
            try:
                return self._client.mget(keys)
            except:
                return []
        return []
    
    def mset(self, mapping: dict) -> bool:
        """Set multiple keys"""
        if self._client and mapping:
            try:
                return self._client.mset(mapping)
            except:
                return False
        return False
    
    # =====================================================
    # CLOSE CONNECTION
    # =====================================================
    
    def close(self):
        """Close Redis connection"""
        if self._client:
            try:
                self._client.close()
            except:
                pass
            self._client = None
            logger.info("Redis connection closed")
    
    def __del__(self):
        """Cleanup on deletion"""
        self.close()


# =====================================================
# SINGLETON INSTANCE
# =====================================================

_redis_client = None


def get_redis_client() -> RedisClient:
    """Get or create the global Redis client"""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client


def get_redis_connection() -> redis.Redis:
    """Get the raw Redis connection (for direct use)"""
    return get_redis_client().get_client()