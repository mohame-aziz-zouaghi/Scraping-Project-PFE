# core/Web/http_client.py
import random
import time
from typing import Optional, Dict
import requests

from core.Web.headers import get_headers
from core.logger import setup_logger

logger = setup_logger("http_client", "monitor")


class HTTPClient:
    """Simple HTTP client with anti-detection features"""
    
    def __init__(self):
        self.session = requests.Session()
        self.request_times = []
        self.consecutive_failures = 0
    
    def _random_delay(self, min_sec: float = 0.5, max_sec: float = 2.0) -> None:
        """Random delay to avoid patterns"""
        time.sleep(random.uniform(min_sec, max_sec))
    
    def _rate_limit(self, max_requests_per_minute: int = 20) -> None:
        """Simple rate limiting"""
        now = time.time()
        
        # Clean old timestamps
        self.request_times = [t for t in self.request_times if now - t < 60]
        
        # Check if we're over limit
        if len(self.request_times) >= max_requests_per_minute:
            oldest = self.request_times[0]
            wait_time = 60 - (now - oldest)
            if wait_time > 0:
                logger.debug(f"Rate limit: waiting {wait_time:.1f}s")
                time.sleep(wait_time)
        
        self.request_times.append(time.time())
    
    def _should_retry(self, status_code: int, attempt: int, max_retries: int) -> bool:
        """Determine if we should retry"""
        if attempt >= max_retries:
            return False
        
        # Retry on these status codes
        retryable = {429, 500, 502, 503, 504, 408}
        
        # Don't retry on 403 (blocked) or 404 (not found)
        if status_code == 403 or status_code == 404:
            return False
        
        return status_code in retryable or status_code >= 500
    
    def get(
        self,
        url: str,
        max_retries: int = 3,
        timeout: int = 30,
        requests_per_minute: int = 20,
        use_referer: bool = True,
        verify_ssl: bool = True,
        delay_between_requests: float = None
    ) -> Optional[str]:
        """
        Make GET request with retries and anti-detection
        
        Args:
            url: Target URL
            max_retries: Number of retry attempts
            timeout: Request timeout in seconds
            requests_per_minute: Max requests per minute
            use_referer: Add referer header
            verify_ssl: Verify SSL certificates
            delay_between_requests: Optional fixed delay (uses random if None)
        
        Returns:
            HTML string or None
        """
        # Apply rate limiting
        self._rate_limit(requests_per_minute)
        
        for attempt in range(1, max_retries + 1):
            try:
                # Get fresh headers for each attempt
                headers = get_headers(include_referer=use_referer)
                
                # Log attempt
                logger.debug(f"Request {attempt}/{max_retries}: {url[:80]}")
                
                # Make request
                start_time = time.time()
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    verify=verify_ssl,
                    allow_redirects=True
                )
                elapsed = time.time() - start_time
                
                # Success
                if response.status_code == 200:
                    self.consecutive_failures = 0
                    html = response.text
                    
                    # Validate we got HTML
                    if html and len(html) > 100:
                        logger.info(f"✅ {elapsed:.2f}s | {len(html)} chars")
                        
                        # Random delay after success
                        if delay_between_requests:
                            time.sleep(delay_between_requests)
                        else:
                            self._random_delay(0.5, 2.0)
                        
                        return html
                    else:
                        logger.warning(f"⚠️ Empty response | attempt {attempt}")
                        
                # Rate limited
                elif response.status_code == 429:
                    wait = min(30 * attempt, 120)
                    logger.warning(f"⚠️ Rate limited (429) | waiting {wait}s")
                    time.sleep(wait)
                    
                # Forbidden - likely blocked
                elif response.status_code == 403:
                    logger.warning(f"🔒 Blocked (403) | {url[:80]}")
                    return None
                    
                # Not found
                elif response.status_code == 404:
                    logger.warning(f"❌ Not found (404)")
                    return None
                    
                # Other errors
                else:
                    logger.warning(f"⚠️ HTTP {response.status_code}")
                    
                    if not self._should_retry(response.status_code, attempt, max_retries):
                        return None
                    
                    wait = min(5 * attempt, 30)
                    time.sleep(wait)
                    
            except requests.exceptions.Timeout:
                logger.warning(f"⏰ Timeout | attempt {attempt}")
                if attempt < max_retries:
                    time.sleep(min(5 * attempt, 30))
                    
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"🔌 Connection error | {str(e)[:50]}")
                if attempt < max_retries:
                    time.sleep(min(5 * attempt, 30))
                    
            except Exception as e:
                logger.error(f"❌ Error: {str(e)[:100]}")
                if attempt < max_retries:
                    time.sleep(min(5 * attempt, 30))
        
        self.consecutive_failures += 1
        logger.error(f"💀 Failed after {max_retries} attempts")
        return None
    
    def close(self):
        """Close the session"""
        self.session.close()


# ---------------------------------
# SINGLETON INSTANCE
# ---------------------------------
_default_client = None


def get_client() -> HTTPClient:
    """Get default HTTP client instance"""
    global _default_client
    if _default_client is None:
        _default_client = HTTPClient()
    return _default_client


# ---------------------------------
# SIMPLE GET FUNCTION (Backward Compatible)
# ---------------------------------
def get(
    url: str,
    max_retries: int = 3,
    timeout: int = 30,
    requests_per_minute: int = 20,
    use_referer: bool = True,
    verify_ssl: bool = True
) -> Optional[str]:
    """
    Simple GET function (backward compatible with old code)
    
    Usage:
        html = get("https://megapc.tn/product/...")
    """
    client = get_client()
    return client.get(
        url=url,
        max_retries=max_retries,
        timeout=timeout,
        requests_per_minute=requests_per_minute,
        use_referer=use_referer,
        verify_ssl=verify_ssl
    )


# ---------------------------------
# CONVENIENCE FUNCTIONS
# ---------------------------------
def get_megapc(url: str) -> Optional[str]:
    """Convenience function for MegaPC requests"""
    return get(
        url=url,
        max_retries=3,
        requests_per_minute=20,
        use_referer=True
    )


def get_tunisianet(url: str) -> Optional[str]:
    """Convenience function for Tunisianet requests (more conservative)"""
    return get(
        url=url,
        max_retries=4,
        requests_per_minute=10,
        use_referer=True
    )