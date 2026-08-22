# core/web/smart_requester.py
"""
Universal Intelligent HTTP Requester with anti-detection features
Can be used for any site - MegaPC, Tunisianet, MyTek, etc.
"""
import time
import random
import json
import requests
from typing import Optional, Dict, Any, Tuple, Union, List
from datetime import datetime, timedelta
from collections import deque
from threading import Lock
from urllib.parse import urlparse

from core.Web.headers import get_random_accept_language, get_random_user_agent
from core.logger import setup_logger

logger = setup_logger("smart_requester", "scraper")


class SmartRequester:
    """
    Universal Intelligent HTTP Requester with:
    - Rotating headers per request
    - Session persistence with cookie management
    - Adaptive rate limiting with jitter
    - Exponential backoff with random jitter
    - Browser-like request patterns
    - Connection pooling
    - Request queuing
    - Site-specific configuration
    - Option to use basic headers (for sites that reject modern headers)
    """
    
    # Site-specific default configurations
    SITE_CONFIGS = {
        "megapc": {
            "requests_per_minute": 15,
            "max_retries": 3,
            "timeout": 30,
            "use_referer": True,
            "use_session": True,
            "verify_ssl": True,
            "delay_between_requests": (0.3, 1.5),
            "respect_robots": True,
            "use_basic_headers": False,
        },
        "tunisianet": {
            "requests_per_minute": 10,
            "max_retries": 4,
            "timeout": 45,
            "use_referer": True,
            "use_session": True,
            "verify_ssl": True,
            "delay_between_requests": (0.5, 2.0),
            "respect_robots": True,
            "use_basic_headers": False,
        },
        "mytek": {
            "requests_per_minute": 12,
            "max_retries": 3,
            "timeout": 30,
            "use_referer": True,
            "use_session": True,
            "verify_ssl": True,
            "delay_between_requests": (0.3, 1.5),
            "respect_robots": True,
            "use_basic_headers": True,  # Mytek works better with basic headers
        },
        "spacenet": {
            "requests_per_minute": 20,
            "max_retries": 4,
            "timeout": 45,
            "use_referer": True,
            "use_session": True,
            "verify_ssl": True,
            "delay_between_requests": (0.5, 2.0),
            "respect_robots": True,
            "use_basic_headers": True,  # Spacenet works better with basic headers
        },
        "default": {
            "requests_per_minute": 15,
            "max_retries": 3,
            "timeout": 30,
            "use_referer": True,
            "use_session": True,
            "verify_ssl": True,
            "delay_between_requests": (0.3, 1.5),
            "respect_robots": True,
            "use_basic_headers": False,
        }
    }
    
    def __init__(
        self,
        site_name: str = "default",
        requests_per_minute: Optional[int] = None,
        max_retries: Optional[int] = None,
        timeout: Optional[int] = None,
        use_session: Optional[bool] = None,
        use_referer: Optional[bool] = None,
        verify_ssl: Optional[bool] = None,
        delay_between_requests: Optional[Tuple[float, float]] = None,
        respect_robots: bool = True,
        custom_headers: Optional[Dict[str, str]] = None,
        proxy_config: Optional[Dict[str, str]] = None,
        use_basic_headers: Optional[bool] = None,
    ):
        """
        Initialize the SmartRequester with site-specific or custom configuration
        
        Args:
            site_name: Name of the site (uses predefined configs)
            requests_per_minute: Max requests per minute
            max_retries: Max retry attempts
            timeout: Request timeout in seconds
            use_session: Use session persistence
            use_referer: Add referer header
            verify_ssl: Verify SSL certificates
            delay_between_requests: Min/max delay between requests (tuple)
            respect_robots: Respect robots.txt
            custom_headers: Custom headers to add
            proxy_config: Proxy configuration
            use_basic_headers: If True, send only essential headers (for sites that reject modern headers)
        """
        self.site_name = site_name.lower()
        
        # Load site-specific config
        site_config = self.SITE_CONFIGS.get(self.site_name, self.SITE_CONFIGS["default"])
        
        # Apply config with overrides
        self.requests_per_minute = requests_per_minute if requests_per_minute is not None else site_config.get("requests_per_minute", 15)
        self.max_retries = max_retries if max_retries is not None else site_config.get("max_retries", 3)
        self.timeout = timeout if timeout is not None else site_config.get("timeout", 30)
        self.use_session = use_session if use_session is not None else site_config.get("use_session", True)
        self.use_referer = use_referer if use_referer is not None else site_config.get("use_referer", True)
        self.verify_ssl = verify_ssl if verify_ssl is not None else site_config.get("verify_ssl", True)
        self.delay_between_requests = delay_between_requests or site_config.get("delay_between_requests", (0.3, 1.5))
        self.respect_robots = respect_robots
        self.custom_headers = custom_headers or {}
        self.proxy_config = proxy_config
        self.use_basic_headers = use_basic_headers if use_basic_headers is not None else site_config.get("use_basic_headers", False)
        
        # Session management
        self.session = None
        self.session_lock = Lock()
        if self.use_session:
            self._init_session()
        
        # Rate limiting
        self.request_timestamps = deque(maxlen=self.requests_per_minute * 2)
        self.rate_limit_lock = Lock()
        
        # Request tracking
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.consecutive_failures = 0
        
        # Domain-specific configs
        self.domain_configs = {}
        
        # Stats
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "by_status": {},
            "by_domain": {},
        }
        
        # Initialize with a random browser fingerprint
        self._browser_fingerprint = self._generate_fingerprint()
        
        logger.info(f"SmartRequester initialized for: {self.site_name} (basic_headers={self.use_basic_headers})")
        logger.debug(f"Config: RPM={self.requests_per_minute}, Retries={self.max_retries}, Timeout={self.timeout}s")
    
    def _init_session(self):
        """Initialize session with base headers and settings"""
        self.session = requests.Session()
        
        base_headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': get_random_accept_language(),
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'DNT': '1',
        }
        
        # Apply custom headers
        base_headers.update(self.custom_headers)
        
        self.session.headers.update(base_headers)
        self.session.headers['User-Agent'] = get_random_user_agent()
        
        # Set proxy if configured
        if self.proxy_config:
            self.session.proxies.update(self.proxy_config)
    
    def _generate_fingerprint(self) -> Dict[str, str]:
        """Generate a browser fingerprint for consistency"""
        return {
            'user_agent': get_random_user_agent(),
            'accept_language': get_random_accept_language(),
            'sec_ch_ua': f'"Not_A Brand";v="8", "Chromium";v="{random.randint(119, 122)}", "Google Chrome";v="{random.randint(119, 122)}"',
            'sec_ch_ua_mobile': '?0',
            'sec_ch_ua_platform': random.choice(['"Windows"', '"macOS"', '"Linux"']),
        }
    
    def _get_headers(
        self, 
        use_referer: bool = True,
        content_type: str = None,
        extra_headers: Dict[str, str] = None
    ) -> Dict[str, str]:
        """
        Generate request-specific headers with rotation.
        If use_basic_headers is True, sends only essential headers.
        """
        headers = {}
        
        if self.use_basic_headers:
            # =====================================================
            # BASIC HEADERS - For sites that reject modern headers
            # =====================================================
            headers = {
                'User-Agent': get_random_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': get_random_accept_language(),
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            if use_referer:
                headers['Referer'] = random.choice(self._get_referers())
            if content_type:
                headers['Content-Type'] = content_type
            if extra_headers:
                headers.update(extra_headers)
            return headers
        
        # =====================================================
        # ADVANCED HEADERS - For modern sites with anti-bot protection
        # =====================================================
        
        # Start with session headers if available
        if self.session:
            headers = dict(self.session.headers)
        
        # Rotate user agent if not using session
        if not self.session:
            headers['User-Agent'] = get_random_user_agent()
        
        # Add browser fingerprint headers
        headers.update({
            'Sec-Ch-Ua': self._browser_fingerprint['sec_ch_ua'],
            'Sec-Ch-Ua-Mobile': self._browser_fingerprint['sec_ch_ua_mobile'],
            'Sec-Ch-Ua-Platform': self._browser_fingerprint['sec_ch_ua_platform'],
            'Accept-Language': get_random_accept_language(),
        })
        
        # Add content type if specified
        if content_type:
            headers['Content-Type'] = content_type
        
        # Add referer with realistic source
        if use_referer:
            referers = self._get_referers()
            headers['Referer'] = random.choice(referers)
        
        # Add cache control with rotation
        cache_controls = ['max-age=0', 'no-cache', 'no-store', '']
        headers['Cache-Control'] = random.choice(cache_controls)
        
        # Sometimes add X-Requested-With (makes it look like AJAX)
        if random.random() > 0.7:
            headers['X-Requested-With'] = 'XMLHttpRequest'
        
        # Add extra headers
        if extra_headers:
            headers.update(extra_headers)
        
        return headers
    
    def _get_referers(self) -> List[str]:
        """Get site-specific referers"""
        base_referers = [
            f"https://{self.site_name}.tn/",
            f"https://{self.site_name}.tn/shop/",
            f"https://www.google.com/",
            f"https://www.bing.com/",
            f"https://www.facebook.com/",
            f"https://www.instagram.com/",
            "",
        ]
        
        # Add site-specific referers
        if self.site_name == "megapc":
            base_referers.extend([
                "https://megapc.tn/categories/",
                "https://megapc.tn/shop/category/",
            ])
        elif self.site_name == "tunisianet":
            base_referers.extend([
                "https://www.tunisianet.com.tn/",
                "https://www.tunisianet.com.tn/category/",
            ])
        elif self.site_name == "mytek":
            base_referers.extend([
                "https://www.mytek.tn/",
                "https://www.mytek.tn/category/",
            ])
        
        return base_referers
    
    def _rate_limit(self) -> None:
        """Intelligent rate limiting with jitter"""
        with self.rate_limit_lock:
            now = time.time()
            
            # Clean old timestamps
            while self.request_timestamps and now - self.request_timestamps[0] > 60:
                self.request_timestamps.popleft()
            
            current_rate = len(self.request_timestamps)
            
            if current_rate >= self.requests_per_minute:
                oldest = self.request_timestamps[0]
                wait_time = 60 - (now - oldest)
                
                # Add jitter (10-30% variation)
                jitter = random.uniform(0.1, 0.3)
                wait_time = wait_time * (1 + jitter)
                
                logger.debug(f"Rate limit reached, waiting {wait_time:.2f}s")
                time.sleep(wait_time)
            
            self.request_timestamps.append(time.time())
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate backoff delay with jitter for retries"""
        base_delay = 2 ** attempt  # Exponential: 1s, 2s, 4s, 8s
        jitter = random.uniform(0.5, 1.5)
        delay = base_delay * jitter + random.uniform(0.1, 0.5)
        return min(delay, 30)  # Cap at 30 seconds
    
    def _should_retry(self, status_code: int, attempt: int, error_type: str = "") -> Tuple[bool, str]:
        """Determine if request should be retried"""
        if attempt >= self.max_retries:
            return False, "max_retries_exceeded"
        
        retryable_statuses = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
        
        if status_code in retryable_statuses:
            return True, f"status_{status_code}"
        
        # Don't retry on 403 (blocked) or 404 (not found)
        if status_code in {403, 404}:
            return False, "blocked_or_not_found"
        
        # Retry on client errors (except 400)
        if 400 <= status_code < 500 and status_code != 400:
            return True, f"client_error_{status_code}"
        
        # Retry on connection errors
        if error_type in {"timeout", "connection_error", "ssl_error"}:
            return True, error_type
        
        return False, "unknown"
    
    def _process_response(self, response: requests.Response, url: str) -> Tuple[Optional[str], Optional[Dict], Optional[Dict]]:
        """
        Process response and handle different content types including compressed data.
        Returns: (html_content, json_data, error_info)
        """
        try:
            content_type = response.headers.get('Content-Type', '').lower()
            content_encoding = response.headers.get('Content-Encoding', '').lower()
            error_info = None
            
            # Check for error responses
            if response.status_code >= 400:
                error_info = {
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "content_encoding": content_encoding,
                    "message": response.text[:200] if response.text else "No content"
                }
            
            # =====================================================
            # Handle compressed content
            # =====================================================
            
            # Try to get the content as text (requests handles decompression automatically)
            try:
                # First try to get the text (requests usually handles gzip/deflate)
                text_content = response.text
                
                # Check if the text is still garbled (contains binary data)
                if text_content and (len(text_content) > 0 and ord(text_content[0]) > 127):
                    # Text is probably still compressed, try to decode manually
                    try:
                        import zlib
                        import gzip
                        import io
                        
                        # If content is gzipped
                        if 'gzip' in content_encoding or response.raw.headers.get('Content-Encoding') == 'gzip':
                            gzip_file = gzip.GzipFile(fileobj=io.BytesIO(response.content))
                            text_content = gzip_file.read().decode('utf-8', errors='ignore')
                        # Try deflate
                        elif 'deflate' in content_encoding:
                            text_content = zlib.decompress(response.content, -zlib.MAX_WBITS).decode('utf-8', errors='ignore')
                        # Try raw zlib
                        else:
                            try:
                                text_content = zlib.decompress(response.content, 16 + zlib.MAX_WBITS).decode('utf-8', errors='ignore')
                            except:
                                text_content = response.content.decode('utf-8', errors='ignore')
                    except Exception as e:
                        logger.debug(f"Decompression attempt failed: {e}")
                        # Last resort: decode with errors ignored
                        try:
                            text_content = response.content.decode('utf-8', errors='ignore')
                        except:
                            text_content = ""
                
                # If text is still empty or invalid, use content decoding
                if not text_content or len(text_content.strip()) < 10:
                    try:
                        text_content = response.content.decode('utf-8', errors='ignore')
                    except:
                        text_content = ""
                        
            except Exception as e:
                logger.warning(f"Failed to decode response: {e}")
                text_content = ""
            
            # Check if we got valid HTML
            if text_content and (text_content.strip().startswith('<!DOCTYPE') or text_content.strip().startswith('<html')):
                logger.debug(f"Valid HTML received ({len(text_content)} chars)")
            elif text_content and len(text_content) > 10:
                logger.debug(f"Received content ({len(text_content)} chars), first 100 chars: {text_content[:100]}")
            else:
                logger.warning(f"Empty or invalid content from {url}")
            
            # JSON response
            if 'application/json' in content_type or url.endswith('.json'):
                try:
                    if text_content:
                        return None, json.loads(text_content), error_info
                    else:
                        return None, response.json(), error_info
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON response from {url}")
                    return None, None, {"error": "invalid_json", "content": text_content[:100] if text_content else ""}
            
            # HTML response
            if 'text/html' in content_type or (text_content and (text_content.strip().startswith('<!DOCTYPE') or text_content.strip().startswith('<html'))):
                return text_content, None, error_info
            
            # Raw text
            return text_content, None, error_info
            
        except Exception as e:
            logger.error(f"Error processing response: {e}")
            return None, None, {"error": str(e)}
    
    def _make_request(self, method: str, **kwargs) -> requests.Response:
        """Make the actual request using session or direct"""
        # Add Accept-Encoding header to handle compression if not already present
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        if 'Accept-Encoding' not in kwargs['headers']:
            kwargs['headers']['Accept-Encoding'] = 'gzip, deflate'
        if self.session and 'Accept-Encoding' not in self.session.headers:
            self.session.headers['Accept-Encoding'] = 'gzip, deflate'
        
        if self.use_session and self.session:
            with self.session_lock:
                return self.session.request(method, **kwargs)
        else:
            return requests.request(method, **kwargs)
    
    def request(
        self,
        url: str,
        method: str = 'GET',
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
        use_referer: Optional[bool] = None,
        json_response: bool = False,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        extra_delay: Optional[float] = None,
        return_metadata: bool = False
    ) -> Union[Optional[str], Optional[Dict], Tuple[Optional[str], Optional[Dict], Dict]]:
        """
        Universal request method with full anti-detection
        
        Args:
            url: Request URL
            method: HTTP method (GET, POST, etc.)
            data: Request data (for POST)
            params: URL parameters
            headers: Additional headers
            use_referer: Add referer header
            json_response: Expect JSON response
            timeout: Custom timeout
            max_retries: Custom max retries
            extra_delay: Additional delay before request
            return_metadata: Return metadata with response
        
        Returns:
            - HTML: html_string
            - JSON: json_data
            - With metadata: (content, metadata)
            - On failure: None
        """
        # Set defaults
        timeout = timeout or self.timeout
        max_retries = max_retries or self.max_retries
        use_referer = use_referer if use_referer is not None else self.use_referer
        
        # Track request
        self.total_requests += 1
        self.stats["total"] += 1
        domain = urlparse(url).netloc
        if domain not in self.stats["by_domain"]:
            self.stats["by_domain"][domain] = {"total": 0, "success": 0, "failed": 0}
        self.stats["by_domain"][domain]["total"] += 1
        
        # Apply rate limiting
        self._rate_limit()
        
        # Add pre-request delay (mimics human behavior)
        if extra_delay:
            time.sleep(extra_delay)
        else:
            min_delay, max_delay = self.delay_between_requests
            pre_delay = random.uniform(min_delay, max_delay)
            time.sleep(pre_delay)
        
        last_error = None
        response_metadata = {
            "url": url,
            "method": method,
            "attempts": 0,
            "status_code": None,
            "response_time": None,
            "error": None
        }
        
        for attempt in range(max_retries + 1):
            response_metadata["attempts"] = attempt + 1
            start_time = time.time()
            
            try:
                # Generate request headers
                request_headers = self._get_headers(
                    use_referer=use_referer,
                    extra_headers=headers
                )
                
                # Prepare request arguments
                request_kwargs = {
                    'url': url,
                    'headers': request_headers,
                    'timeout': timeout,
                    'allow_redirects': True,
                    'verify': self.verify_ssl,
                }
                
                # Add method-specific args
                if method.upper() == 'GET':
                    if params:
                        request_kwargs['params'] = params
                elif method.upper() == 'POST':
                    if data:
                        if headers and headers.get('Content-Type') == 'application/json':
                            request_kwargs['json'] = data
                        else:
                            request_kwargs['data'] = data
                else:
                    logger.error(f"Unsupported method: {method}")
                    return None
                
                # Make request
                response = self._make_request(method, **request_kwargs)
                response_time = time.time() - start_time
                response_metadata["response_time"] = response_time
                response_metadata["status_code"] = response.status_code
                
                # Track status code
                status_key = str(response.status_code)
                if status_key not in self.stats["by_status"]:
                    self.stats["by_status"][status_key] = 0
                self.stats["by_status"][status_key] += 1
                
                # Process response
                html_content, json_data, error_info = self._process_response(response, url)
                
                # Check for errors
                if error_info:
                    last_error = error_info
                    should_retry, retry_reason = self._should_retry(
                        response.status_code, attempt, error_info.get('type', '')
                    )
                    
                    if should_retry:
                        delay = self._calculate_delay(attempt)
                        logger.warning(f"Retry {attempt+1}/{max_retries} for {url[:80]} (status={response.status_code}, reason={retry_reason})")
                        time.sleep(delay)
                        continue
                    else:
                        logger.warning(f"Request failed: {url[:80]} (status={response.status_code})")
                        self.failed_requests += 1
                        self.stats["failed"] += 1
                        self.stats["by_domain"][domain]["failed"] += 1
                        response_metadata["error"] = error_info
                        return None if not return_metadata else (None, response_metadata)
                
                # Success
                self.successful_requests += 1
                self.consecutive_failures = 0
                self.stats["success"] += 1
                self.stats["by_domain"][domain]["success"] += 1
                
                # Update session cookies
                if self.session and response.cookies:
                    with self.session_lock:
                        self.session.cookies.update(response.cookies)
                
                logger.debug(f"Request successful: {url[:80]} (status={response.status_code}, time={response_time:.2f}s)")
                
                # Return based on expected response type
                if return_metadata:
                    if json_response:
                        return json_data, response_metadata
                    else:
                        return html_content, response_metadata
                else:
                    if json_response:
                        return json_data
                    else:
                        return html_content
                
            except requests.exceptions.Timeout as e:
                last_error = {"error": "timeout", "message": str(e)}
                if attempt < max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"Timeout for {url[:80]}, retry {attempt+1}/{max_retries}")
                    time.sleep(delay)
                else:
                    logger.error(f"Timeout after {max_retries} retries: {url[:80]}")
                    
            except requests.exceptions.ConnectionError as e:
                last_error = {"error": "connection_error", "message": str(e)}
                if attempt < max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"Connection error for {url[:80]}, retry {attempt+1}/{max_retries}")
                    time.sleep(delay)
                else:
                    logger.error(f"Connection error after {max_retries} retries: {url[:80]}")
                    
            except requests.exceptions.SSLError as e:
                last_error = {"error": "ssl_error", "message": str(e)}
                if attempt < max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"SSL error for {url[:80]}, retry {attempt+1}/{max_retries}")
                    time.sleep(delay)
                else:
                    logger.error(f"SSL error after {max_retries} retries: {url[:80]}")
                    
            except Exception as e:
                last_error = {"error": "unknown", "message": str(e)}
                if attempt < max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"Request error for {url[:80]}, retry {attempt+1}/{max_retries}")
                    time.sleep(delay)
                else:
                    logger.error(f"Request error after {max_retries} retries: {url[:80]} | {e}")
        
        # All retries exhausted
        self.failed_requests += 1
        self.stats["failed"] += 1
        self.stats["by_domain"][domain]["failed"] += 1
        self.consecutive_failures += 1
        response_metadata["error"] = last_error
        
        return None if not return_metadata else (None, response_metadata)
    
    # =====================================================
    # CONVENIENCE METHODS
    # =====================================================
    
    def get_html(self, url: str, **kwargs) -> Optional[str]:
        """Get HTML content"""
        return self.request(url, method='GET', json_response=False, **kwargs)
    
    def get_json(self, url: str, **kwargs) -> Optional[Dict]:
        """Get JSON response"""
        return self.request(url, method='GET', json_response=True, **kwargs)
    
    def post_html(self, url: str, data: Dict = None, **kwargs) -> Optional[str]:
        """POST and get HTML"""
        return self.request(url, method='POST', data=data, json_response=False, **kwargs)
    
    def post_json(self, url: str, data: Dict = None, **kwargs) -> Optional[Dict]:
        """POST and get JSON"""
        return self.request(url, method='POST', data=data, json_response=True, **kwargs)
    
    # =====================================================
    # STATS AND MANAGEMENT
    # =====================================================
    
    def get_stats(self) -> Dict:
        """Get request statistics"""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": self.successful_requests / self.total_requests * 100 if self.total_requests > 0 else 0,
            "consecutive_failures": self.consecutive_failures,
            "by_status": self.stats["by_status"],
            "by_domain": self.stats["by_domain"],
        }
    
    def reset_stats(self):
        """Reset statistics"""
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.consecutive_failures = 0
        self.stats = {"total": 0, "success": 0, "failed": 0, "by_status": {}, "by_domain": {}}
    
    def update_config(self, **kwargs):
        """Update configuration dynamically"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                logger.info(f"Updated {key} to {value}")
    
    def close(self):
        """Close session"""
        if self.session:
            self.session.close()
            self.session = None


# =====================================================
# FACTORY FUNCTION FOR EASY CREATION
# =====================================================

def create_requester(
    site_name: str = "default",
    config: Optional[Dict] = None
) -> SmartRequester:
    """
    Factory function to create a SmartRequester instance
    
    Args:
        site_name: Name of the site
        config: Custom configuration override
    
    Returns:
        Configured SmartRequester instance
    """
    if config:
        return SmartRequester(site_name=site_name, **config)
    return SmartRequester(site_name=site_name)


# =====================================================
# PRE-CONFIGURED INSTANCES FOR COMMON SITES
# =====================================================

def get_megapc_requester() -> SmartRequester:
    """Get a pre-configured requester for MegaPC"""
    return SmartRequester(
        site_name="megapc",
        requests_per_minute=15,
        max_retries=3,
        timeout=30,
        use_session=True,
        use_referer=True,
        delay_between_requests=(0.3, 1.5),
        use_basic_headers=False,
    )

def get_tunisianet_requester() -> SmartRequester:
    """Get a pre-configured requester for Tunisianet"""
    return SmartRequester(
        site_name="tunisianet",
        requests_per_minute=10,
        max_retries=4,
        timeout=45,
        use_session=True,
        use_referer=True,
        delay_between_requests=(0.5, 2.0),
        use_basic_headers=False,
    )

def get_mytek_requester() -> SmartRequester:
    """Get a pre-configured requester for MyTek"""
    return SmartRequester(
        site_name="mytek",
        requests_per_minute=12,
        max_retries=3,
        timeout=30,
        use_session=True,
        use_referer=True,
        delay_between_requests=(0.3, 1.5),
        use_basic_headers=True,  # Mytek works better with basic headers
    )

def get_spacenet_requester() -> SmartRequester:
    """Get a pre-configured requester for Spacenet"""
    return SmartRequester(
        site_name="spacenet",
        requests_per_minute=20,
        max_retries=4,
        timeout=45,
        use_session=True,
        use_referer=True,
        delay_between_requests=(0.5, 2.0),
        use_basic_headers=True,  # Spacenet works better with basic headers
    )