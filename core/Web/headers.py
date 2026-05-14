# core/headers.py
import random
from typing import Dict, List, Optional

# ---------------------------------
# USER AGENTS (Realistic browsers)
# ---------------------------------
USER_AGENTS: List[str] = [
    # Windows Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    
    # Windows Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    
    # Mac Chrome
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    
    # Mac Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    
    # Mac Firefox
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
    
    # Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

# ---------------------------------
# ACCEPT LANGUAGES (French first for Tunisia)
# ---------------------------------
ACCEPT_LANGUAGES: List[str] = [
    "fr-FR,fr;q=0.9,en;q=0.8",
    "fr,fr-FR;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9",
    "fr,en;q=0.9",
]

# ---------------------------------
# REFERERS (Where the request appears to come from)
# ---------------------------------
REFERERS: List[str] = [
    "https://megapc.tn/",
    "https://megapc.tn/shop/",
    "https://www.google.com/",
    "https://www.bing.com/",
    "",
]

# ---------------------------------
# ACCEPT TYPES
# ---------------------------------
ACCEPT_TYPES: List[str] = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
]


def get_random_user_agent() -> str:
    """Return random user agent"""
    return random.choice(USER_AGENTS)


def get_random_accept_language() -> str:
    """Return random accept language"""
    return random.choice(ACCEPT_LANGUAGES)


def get_random_referer() -> str:
    """Return random referer"""
    return random.choice(REFERERS)


def get_random_accept() -> str:
    """Return random accept header"""
    return random.choice(ACCEPT_TYPES)


def get_headers(
    include_referer: bool = False,
    custom_user_agent: Optional[str] = None
) -> Dict[str, str]:
    """
    Generate realistic browser headers
    
    Args:
        include_referer: If True, add a random referer header
        custom_user_agent: Optional custom user agent (uses random if not provided)
    
    Returns:
        Dictionary of headers for requests
    """
    headers = {
        "User-Agent": custom_user_agent or get_random_user_agent(),
        "Accept": get_random_accept(),
        "Accept-Language": get_random_accept_language(),
        "Accept-Encoding": "gzip, deflate",  # Let requests handle decompression
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "DNT": "1",  # Do Not Track
    }
    
    # Add referer if requested
    if include_referer:
        headers["Referer"] = get_random_referer()
    
    return headers


def get_headers_stealth() -> Dict[str, str]:
    """
    Extended stealth headers for more aggressive anti-detection
    Use this if you're getting blocked
    """
    headers = get_headers(include_referer=True)
    
    # Add modern browser headers
    headers.update({
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    })
    
    return headers


def get_mobile_headers() -> Dict[str, str]:
    """
    Mobile device headers for mobile-specific pages
    """
    mobile_user_agents = [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ]
    
    headers = get_headers(include_referer=True)
    headers["User-Agent"] = random.choice(mobile_user_agents)
    headers["Sec-Ch-Ua-Mobile"] = "?1"
    
    return headers


# ---------------------------------
# BACKWARD COMPATIBILITY
# ---------------------------------
def get_basic_headers() -> Dict[str, str]:
    """Simple headers for basic requests (backward compatible)"""
    return {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }