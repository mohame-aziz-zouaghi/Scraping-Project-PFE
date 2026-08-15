# Database/Redis/Configuration/config.py
"""
Redis Configuration - Loads from .env file
"""
import os
from typing import Dict, Any
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =====================================================
# REDIS CONFIGURATION (from .env)
# =====================================================

REDIS_CONFIG: Dict[str, Any] = {
    # Connection settings from .env
    'host': os.getenv('REDIS_HOST', 'localhost'),
    'port': int(os.getenv('REDIS_PORT', 6379)),
    'db': int(os.getenv('REDIS_DB', 0)),
    'password': os.getenv('REDIS_PASSWORD', None),
    
    # Connection pool settings
    'max_connections': int(os.getenv('REDIS_MAX_CONNECTIONS', 50)),
    'socket_timeout': int(os.getenv('REDIS_SOCKET_TIMEOUT', 5)),
    'socket_connect_timeout': int(os.getenv('REDIS_CONNECT_TIMEOUT', 5)),
    'retry_on_timeout': True,
    'decode_responses': True,
    
    # Cache TTL settings (in seconds)
    'ttl': {
        'product': 86400,           # 24 hours
        'monitoring': 3600,         # 1 hour
        'session': 1800,            # 30 minutes
        'preload': 86400 * 7,       # 7 days
        'default': 3600,            # 1 hour
    },
    
    # Cache key prefixes
    'key_prefixes': {
        'product': 'product',
        'monitoring': 'monitor',
        'session': 'session',
        'cache': 'cache',
        'temp': 'temp',
    },
}

# =====================================================
# VALIDATION
# =====================================================

def validate_redis_config() -> bool:
    """Validate Redis configuration"""
    required_keys = ['host', 'port', 'db']
    
    for key in required_keys:
        if key not in REDIS_CONFIG:
            print(f"❌ Missing required Redis config key: {key}")
            return False
    
    if not isinstance(REDIS_CONFIG['port'], int) or REDIS_CONFIG['port'] < 1:
        print(f"❌ Invalid Redis port: {REDIS_CONFIG['port']}")
        return False
    
    return True

def get_redis_connection_url() -> str:
    """Get Redis connection URL"""
    host = REDIS_CONFIG['host']
    port = REDIS_CONFIG['port']
    db = REDIS_CONFIG['db']
    password = REDIS_CONFIG.get('password')
    
    if password:
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"

def get_ttl(key_type: str) -> int:
    """Get TTL for a specific key type"""
    ttl_config = REDIS_CONFIG.get('ttl', {})
    return ttl_config.get(key_type, ttl_config.get('default', 3600))

# =====================================================
# EXPORTS
# =====================================================

__all__ = [
    'REDIS_CONFIG',
    'validate_redis_config',
    'get_redis_connection_url',
    'get_ttl',
]