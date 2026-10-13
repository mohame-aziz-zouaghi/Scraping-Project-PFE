# core/web/smart_requester.py
"""
Universal Intelligent HTTP Requester with anti-detection features.

Changes vs the previous version:
  - Fixed gzip sniff: only decompress when the payload actually starts
    with the gzip magic bytes (\\x1f\\x8b). Stops the
    "BadGzipFile: Not a gzipped file (b'\\xef\\xbb')" flood caused by
    servers that set Content-Encoding: gzip but send raw HTML.
  - Removed the per-response debug logs. They were the dominant source
    of log volume and pushed the log file past the rotation cap every
    few seconds under concurrency, which in turn triggered the
    WinError 32 races in RotatingFileHandler.
  - _make_request no longer mutates the caller's headers dict or the
    shared session headers. It builds a fresh dict per request.
  - _rate_limit now uses a soft cap that backs off proportionally
    instead of a hard 1-second ceiling, so per-site limits are
    actually respected under load.
"""
import time
import random
import json
import gzip
import requests
from typing import Optional, Dict, Any, Tuple, Union, List
from datetime import datetime, timedelta
from collections import deque
from threading import Lock
from urllib.parse import urlparse

from core.Web.headers import get_random_accept_language, get_random_user_agent
from core.logger import setup_logger

logger = setup_logger("smart_requester", "scraper")


# ═══════════════════════════════════════════════════════════
# Response decoding helpers
# ═══════════════════════════════════════════════════════════
_GZIP_MAGIC = b"\x1f\x8b"
_ZLIB_MAGIC = b"\x78"


def _safe_decode_content(
    raw: bytes,
    declared_encoding: str,
) -> str:
    """
    Decode a response body to text, decompressing only when the
    bytes actually look compressed.

    Precedence:
      1. gzip magic bytes present            -> gzip.decompress
      2. zlib magic byte + declared deflate  -> zlib.decompress
      3. anything else                       -> use the bytes as-is

    A server that sets Content-Encoding: gzip but sends raw HTML
    lands in case 3 and is decoded without error. This is the case
    that produced every "Not a gzipped file (b'\\xef\\xbb')" line
    in the logs.
    """
    if not raw:
        return ""

    # 1. gzip
    if raw[:2] == _GZIP_MAGIC:
        try:
            return gzip.decompress(raw).decode("utf-8", errors="ignore")
        except Exception as exc:
            # Corrupt gzip; fall through to raw decode.
            logger.debug(f"gzip decompress failed despite magic bytes: {exc}")

    # 2. zlib-wrapped deflate
    elif declared_encoding == "deflate" and raw[:1] == _ZLIB_MAGIC:
        try:
            import zlib
            return zlib.decompress(raw).decode("utf-8", errors="ignore")
        except Exception as exc:
            logger.debug(f"zlib decompress failed: {exc}")

    # 3. raw
    return raw.decode("utf-8", errors="ignore")


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
            "use_basic_headers": True,
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
            "use_basic_headers": True,
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
        },
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
        self.site_name = site_name.lower()

        site_config = self.SITE_CONFIGS.get(
            self.site_name, self.SITE_CONFIGS["default"]
        )

        self.requests_per_minute = (
            requests_per_minute
            if requests_per_minute is not None
            else site_config.get("requests_per_minute", 15)
        )
        self.max_retries = (
            max_retries if max_retries is not None
            else site_config.get("max_retries", 3)
        )
        self.timeout = (
            timeout if timeout is not None
            else site_config.get("timeout", 30)
        )
        self.use_session = (
            use_session if use_session is not None
            else site_config.get("use_session", True)
        )
        self.use_referer = (
            use_referer if use_referer is not None
            else site_config.get("use_referer", True)
        )
        self.verify_ssl = (
            verify_ssl if verify_ssl is not None
            else site_config.get("verify_ssl", True)
        )
        self.delay_between_requests = (
            delay_between_requests
            or site_config.get("delay_between_requests", (0.3, 1.5))
        )
        self.respect_robots = respect_robots
        self.custom_headers = custom_headers or {}
        self.proxy_config = proxy_config
        self.use_basic_headers = (
            use_basic_headers
            if use_basic_headers is not None
            else site_config.get("use_basic_headers", False)
        )

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

        # Browser fingerprint
        self._browser_fingerprint = self._generate_fingerprint()

        logger.info(
            f"SmartRequester initialized for: {self.site_name} "
            f"(basic_headers={self.use_basic_headers})"
        )
        logger.debug(
            f"Config: RPM={self.requests_per_minute}, "
            f"Retries={self.max_retries}, Timeout={self.timeout}s"
        )

    def _init_session(self):
        """Initialize session with base headers and settings."""
        self.session = requests.Session()

        base_headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": get_random_accept_language(),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "DNT": "1",
        }
        base_headers.update(self.custom_headers)

        self.session.headers.update(base_headers)
        self.session.headers["User-Agent"] = get_random_user_agent()

        if self.proxy_config:
            self.session.proxies.update(self.proxy_config)

    def _generate_fingerprint(self) -> Dict[str, str]:
        return {
            "user_agent": get_random_user_agent(),
            "accept_language": get_random_accept_language(),
            "sec_ch_ua": (
                f'"Not_A Brand";v="8", "Chromium";v="{random.randint(119, 122)}", '
                f'"Google Chrome";v="{random.randint(119, 122)}"'
            ),
            "sec_ch_ua_mobile": "?0",
            "sec_ch_ua_platform": random.choice(
                ['"Windows"', '"macOS"', '"Linux"']
            ),
        }

    def _get_headers(
        self,
        use_referer: bool = True,
        content_type: str = None,
        extra_headers: Dict[str, str] = None,
    ) -> Dict[str, str]:
        """
        Build a fresh headers dict for one request. Never mutates
        the session or the caller's dict.
        """
        headers: Dict[str, str] = {}

        if self.use_basic_headers:
            headers = {
                "User-Agent": get_random_user_agent(),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/webp,image/apng,*/*;q=0.8"
                ),
                "Accept-Language": get_random_accept_language(),
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            if use_referer:
                headers["Referer"] = random.choice(self._get_referers())
            if content_type:
                headers["Content-Type"] = content_type
            if extra_headers:
                headers.update(extra_headers)
            return headers

        # Advanced headers, starting from a copy of the session headers.
        if self.session:
            headers = dict(self.session.headers)

        if not self.session:
            headers["User-Agent"] = get_random_user_agent()

        headers.update(
            {
                "Sec-Ch-Ua": self._browser_fingerprint["sec_ch_ua"],
                "Sec-Ch-Ua-Mobile": self._browser_fingerprint["sec_ch_ua_mobile"],
                "Sec-Ch-Ua-Platform": self._browser_fingerprint["sec_ch_ua_platform"],
                "Accept-Language": get_random_accept_language(),
            }
        )

        if content_type:
            headers["Content-Type"] = content_type

        if use_referer:
            headers["Referer"] = random.choice(self._get_referers())

        headers["Cache-Control"] = random.choice(
            ["max-age=0", "no-cache", "no-store", ""]
        )

        if random.random() > 0.7:
            headers["X-Requested-With"] = "XMLHttpRequest"

        if extra_headers:
            headers.update(extra_headers)

        return headers

    def _get_referers(self) -> List[str]:
        base_referers = [
            f"https://{self.site_name}.tn/",
            f"https://{self.site_name}.tn/shop/",
            "https://www.google.com/",
            "https://www.bing.com/",
            "https://www.facebook.com/",
            "https://www.instagram.com/",
            "",
        ]
        if self.site_name == "megapc":
            base_referers.extend(
                ["https://megapc.tn/categories/", "https://megapc.tn/shop/category/"]
            )
        elif self.site_name == "tunisianet":
            base_referers.extend(
                [
                    "https://www.tunisianet.com.tn/",
                    "https://www.tunisianet.com.tn/category/",
                ]
            )
        elif self.site_name == "mytek":
            base_referers.extend(
                ["https://www.mytek.tn/", "https://www.mytek.tn/category/"]
            )
        return base_referers

    # ═══════════════════════════════════════════════════════
    # RATE LIMITING — soft cap that scales with overflow
    # ═══════════════════════════════════════════════════════
    def _rate_limit(self) -> None:
        """
        Enforce this instance's per-minute request budget.

        Soft cap: if we are over the budget, we sleep for
        (overflow_ratio x base_window) with jitter, rather than a
        fixed 1-second ceiling. Under heavy load this actually
        throttles to the configured rate instead of letting the
        queue drain past it.
        """
        with self.rate_limit_lock:
            now = time.time()

            while (
                self.request_timestamps
                and now - self.request_timestamps[0] > 60
            ):
                self.request_timestamps.popleft()

            current_rate = len(self.request_timestamps)

            if current_rate >= self.requests_per_minute:
                oldest = self.request_timestamps[0]
                # How much of the 60-second window is left for the
                # oldest in-flight request.
                remaining = 60.0 - (now - oldest)
                overflow = current_rate / self.requests_per_minute

                # Scale the wait by how far over budget we are.
                # 1x over -> wait ~= remaining.
                # 2x over -> wait ~= 2 * remaining / 2 = remaining.
                # 3x over -> wait ~= remaining / 3 ... down to a floor.
                wait_time = max(0.05, remaining / max(overflow, 1.0))

                # Cap the wait so a single burst can't stall a worker
                # for more than a few seconds; the next call will
                # throttle again if we are still over.
                wait_time = min(wait_time, 5.0)

                jitter = random.uniform(0.02, 0.05)
                wait_time *= 1 + jitter

                if wait_time > 0.25:
                    logger.debug(
                        f"Rate limit ({self.site_name}): waiting "
                        f"{wait_time:.2f}s (rate="
                        f"{current_rate}/{self.requests_per_minute})"
                    )

                time.sleep(wait_time)

            self.request_timestamps.append(time.time())

    def _calculate_delay(self, attempt: int) -> float:
        base_delay = 2 ** attempt
        jitter = random.uniform(0.5, 1.5)
        delay = base_delay * jitter + random.uniform(0.1, 0.5)
        return min(delay, 30)

    def _should_retry(
        self, status_code: int, attempt: int, error_type: str = ""
    ) -> Tuple[bool, str]:
        if attempt >= self.max_retries:
            return False, "max_retries_exceeded"

        retryable_statuses = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
        if status_code in retryable_statuses:
            return True, f"status_{status_code}"

        if status_code in {403, 404}:
            return False, "blocked_or_not_found"

        if 400 <= status_code < 500 and status_code != 400:
            return True, f"client_error_{status_code}"

        if error_type in {"timeout", "connection_error", "ssl_error"}:
            return True, error_type

        return False, "unknown"

    # ═══════════════════════════════════════════════════════
    # RESPONSE PROCESSING — no more BadGzipFile, no more spam
    # ═══════════════════════════════════════════════════════
    def _process_response(
        self, response: requests.Response, url: str
    ) -> Tuple[Optional[str], Optional[Dict], Optional[Dict]]:
        """
        Decode the response body. Decompression is delegated to
        `_safe_decode_content`, which sniffs magic bytes before
        attempting gzip. This eliminates the BadGzipFile storm on
        sites that send raw HTML with a gzip Content-Encoding
        header.
        """
        try:
            content_type = response.headers.get("Content-Type", "").lower()
            content_encoding = response.headers.get("Content-Encoding", "").lower()
            error_info = None

            if response.status_code >= 400:
                error_info = {
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "content_encoding": content_encoding,
                    "message": response.text[:200] if response.text else "No content",
                }

            # requests/urllib3 normally decompress transparently. We
            # only fall back to manual decoding when the payload
            # actually looks compressed.
            try:
                text_content = response.text
            except Exception:
                text_content = ""

            # If the body is still binary-ish (first char is not
            # printable ASCII or a BOM), decode the raw bytes with
            # the magic-byte sniffer.
            if text_content and ord(text_content[0]) > 127:
                text_content = _safe_decode_content(
                    response.content, content_encoding
                )

            # Empty or suspiciously short → decode raw bytes.
            if not text_content or len(text_content.strip()) < 10:
                text_content = _safe_decode_content(
                    response.content, content_encoding
                )

            # ── The per-response debug log is intentionally gone.
            #    Under a 20-worker crawl it produced hundreds of
            #    megabytes per run and forced the log rotation to
            #    fire dozens of times per second.
            #    If you need to inspect a specific URL, use
            #    scripts/check_mytek_parser.py or add a temporary
            #    log line locally.

            if "application/json" in content_type or url.endswith(".json"):
                try:
                    if text_content:
                        return None, json.loads(text_content), error_info
                    return None, response.json(), error_info
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON response from {url}")
                    return None, None, {
                        "error": "invalid_json",
                        "content": text_content[:100] if text_content else "",
                    }

            if "text/html" in content_type or (
                text_content
                and (
                    text_content.strip().startswith("<!DOCTYPE")
                    or text_content.strip().startswith("<html")
                )
            ):
                return text_content, None, error_info

            return text_content, None, error_info

        except Exception as e:
            logger.error(f"Error processing response: {e}")
            return None, None, {"error": str(e)}

    def _make_request(self, method: str, **kwargs) -> requests.Response:
        """
        Build a request without mutating caller or session headers.
        """
        # Copy the caller's headers so we don't clobber their dict.
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("Accept-Encoding", "gzip, deflate")
        kwargs["headers"] = headers

        if self.use_session and self.session:
            with self.session_lock:
                return self.session.request(method, **kwargs)
        return requests.request(method, **kwargs)

    def request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
        use_referer: Optional[bool] = None,
        json_response: bool = False,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        extra_delay: Optional[float] = None,
        return_metadata: bool = False,
    ) -> Union[
        Optional[str], Optional[Dict], Tuple[Optional[str], Optional[Dict], Dict]
    ]:
        timeout = timeout or self.timeout
        max_retries = max_retries or self.max_retries
        use_referer = use_referer if use_referer is not None else self.use_referer

        self.total_requests += 1
        self.stats["total"] += 1
        domain = urlparse(url).netloc
        if domain not in self.stats["by_domain"]:
            self.stats["by_domain"][domain] = {"total": 0, "success": 0, "failed": 0}
        self.stats["by_domain"][domain]["total"] += 1

        self._rate_limit()

        if extra_delay:
            time.sleep(extra_delay)
        else:
            min_delay, max_delay = self.delay_between_requests
            time.sleep(random.uniform(min_delay, max_delay))

        last_error = None
        response_metadata = {
            "url": url,
            "method": method,
            "attempts": 0,
            "status_code": None,
            "response_time": None,
            "error": None,
        }

        for attempt in range(max_retries + 1):
            response_metadata["attempts"] = attempt + 1
            start_time = time.time()

            try:
                request_headers = self._get_headers(
                    use_referer=use_referer, extra_headers=headers
                )

                request_kwargs = {
                    "url": url,
                    "headers": request_headers,
                    "timeout": timeout,
                    "allow_redirects": True,
                    "verify": self.verify_ssl,
                }

                if method.upper() == "GET":
                    if params:
                        request_kwargs["params"] = params
                elif method.upper() == "POST":
                    if data:
                        if headers and headers.get("Content-Type") == "application/json":
                            request_kwargs["json"] = data
                        else:
                            request_kwargs["data"] = data
                else:
                    logger.error(f"Unsupported method: {method}")
                    return None

                response = self._make_request(method, **request_kwargs)
                response_time = time.time() - start_time
                response_metadata["response_time"] = response_time
                response_metadata["status_code"] = response.status_code

                status_key = str(response.status_code)
                self.stats["by_status"][status_key] = (
                    self.stats["by_status"].get(status_key, 0) + 1
                )

                html_content, json_data, error_info = self._process_response(
                    response, url
                )

                if error_info:
                    last_error = error_info
                    should_retry, retry_reason = self._should_retry(
                        response.status_code, attempt, error_info.get("type", "")
                    )

                    if should_retry:
                        delay = self._calculate_delay(attempt)
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} for {url[:80]} "
                            f"(status={response.status_code}, reason={retry_reason})"
                        )
                        time.sleep(delay)
                        continue

                    logger.warning(
                        f"Request failed: {url[:80]} (status={response.status_code})"
                    )
                    self.failed_requests += 1
                    self.stats["failed"] += 1
                    self.stats["by_domain"][domain]["failed"] += 1
                    response_metadata["error"] = error_info
                    return None if not return_metadata else (None, response_metadata)

                self.successful_requests += 1
                self.consecutive_failures = 0
                self.stats["success"] += 1
                self.stats["by_domain"][domain]["success"] += 1

                if self.session and response.cookies:
                    with self.session_lock:
                        self.session.cookies.update(response.cookies)

                if return_metadata:
                    if json_response:
                        return json_data, response_metadata
                    return html_content, response_metadata
                if json_response:
                    return json_data
                return html_content

            except requests.exceptions.Timeout as e:
                last_error = {"error": "timeout", "message": str(e)}
                if attempt < max_retries:
                    time.sleep(self._calculate_delay(attempt))
                    logger.warning(
                        f"Timeout for {url[:80]}, retry "
                        f"{attempt + 1}/{max_retries}"
                    )
                else:
                    logger.error(
                        f"Timeout after {max_retries} retries: {url[:80]}"
                    )

            except requests.exceptions.ConnectionError as e:
                last_error = {"error": "connection_error", "message": str(e)}
                if attempt < max_retries:
                    time.sleep(self._calculate_delay(attempt))
                    logger.warning(
                        f"Connection error for {url[:80]}, retry "
                        f"{attempt + 1}/{max_retries}"
                    )
                else:
                    logger.error(
                        f"Connection error after {max_retries} retries: "
                        f"{url[:80]}"
                    )

            except requests.exceptions.SSLError as e:
                last_error = {"error": "ssl_error", "message": str(e)}
                if attempt < max_retries:
                    time.sleep(self._calculate_delay(attempt))
                    logger.warning(
                        f"SSL error for {url[:80]}, retry "
                        f"{attempt + 1}/{max_retries}"
                    )
                else:
                    logger.error(
                        f"SSL error after {max_retries} retries: {url[:80]}"
                    )

            except Exception as e:
                last_error = {"error": "unknown", "message": str(e)}
                if attempt < max_retries:
                    time.sleep(self._calculate_delay(attempt))
                    logger.warning(
                        f"Request error for {url[:80]}, retry "
                        f"{attempt + 1}/{max_retries}"
                    )
                else:
                    logger.error(
                        f"Request error after {max_retries} retries: "
                        f"{url[:80]} | {e}"
                    )

        self.failed_requests += 1
        self.stats["failed"] += 1
        self.stats["by_domain"][domain]["failed"] += 1
        self.consecutive_failures += 1
        response_metadata["error"] = last_error

        return None if not return_metadata else (None, response_metadata)

    # ═══════════════════════════════════════════════════════
    # CONVENIENCE METHODS
    # ═══════════════════════════════════════════════════════
    def get_html(self, url: str, **kwargs) -> Optional[str]:
        return self.request(url, method="GET", json_response=False, **kwargs)

    def get_json(self, url: str, **kwargs) -> Optional[Dict]:
        return self.request(url, method="GET", json_response=True, **kwargs)

    def post_html(self, url: str, data: Dict = None, **kwargs) -> Optional[str]:
        return self.request(
            url, method="POST", data=data, json_response=False, **kwargs
        )

    def post_json(self, url: str, data: Dict = None, **kwargs) -> Optional[Dict]:
        return self.request(
            url, method="POST", data=data, json_response=True, **kwargs
        )

    # ═══════════════════════════════════════════════════════
    # STATS AND MANAGEMENT
    # ═══════════════════════════════════════════════════════
    def get_stats(self) -> Dict:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (
                self.successful_requests / self.total_requests * 100
                if self.total_requests > 0 else 0
            ),
            "consecutive_failures": self.consecutive_failures,
            "by_status": self.stats["by_status"],
            "by_domain": self.stats["by_domain"],
        }

    def reset_stats(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.consecutive_failures = 0
        self.stats = {
            "total": 0, "success": 0, "failed": 0,
            "by_status": {}, "by_domain": {},
        }

    def update_config(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                logger.info(f"Updated {key} to {value}")

    def close(self):
        if self.session:
            self.session.close()
            self.session = None


# ═══════════════════════════════════════════════════════
# FACTORY FUNCTION
# ═══════════════════════════════════════════════════════
def create_requester(
    site_name: str = "default",
    config: Optional[Dict] = None,
) -> SmartRequester:
    if config:
        return SmartRequester(site_name=site_name, **config)
    return SmartRequester(site_name=site_name)


# ═══════════════════════════════════════════════════════
# PRE-CONFIGURED INSTANCES
# ═══════════════════════════════════════════════════════
def get_megapc_requester() -> SmartRequester:
    return SmartRequester(
        site_name="megapc",
        requests_per_minute=15,
        max_retries=3,
        timeout=30,
        use_session=True,
        use_referer=True,
        delay_between_requests=(0.3, 1.5),
        use_basic_headers=True,
    )


def get_tunisianet_requester() -> SmartRequester:
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
    return SmartRequester(
        site_name="mytek",
        requests_per_minute=12,
        max_retries=3,
        timeout=30,
        use_session=True,
        use_referer=True,
        delay_between_requests=(0.3, 1.5),
        use_basic_headers=True,
    )


def get_spacenet_requester() -> SmartRequester:
    return SmartRequester(
        site_name="spacenet",
        requests_per_minute=20,
        max_retries=4,
        timeout=45,
        use_session=True,
        use_referer=True,
        delay_between_requests=(0.5, 2.0),
        use_basic_headers=True,
    )