# Database/Redis/cache_keys.py

def product_key(site: str, identifier: str) -> str:
    """
    Generate Redis key for product using site and main_product_id
    
    Args:
        site: Site name (e.g., 'megapc')
        identifier: Main product ID from the site (not PostgreSQL UUID)
    
    Returns:
        Redis key in format: product:{site}:{main_product_id}
    """
    return f"product:{site.lower()}:{identifier}"


def site_pattern(site: str) -> str:
    """Generate pattern to get all keys for a specific site"""
    return f"product:{site.lower()}:*"


def extract_product_id_from_key(key: str) -> str:
    """Extract main_product_id from Redis key"""
    parts = key.split(":")
    if len(parts) >= 3:
        return parts[2]
    return None