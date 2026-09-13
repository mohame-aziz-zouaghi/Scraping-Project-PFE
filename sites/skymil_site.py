# sites/skymil_site.py
"""
Skymil scraper - Fetches products from Supabase API with token caching and auto-refresh.
Properly inherits from BaseSite for batching, connection management, and hash storage.
Supports hash-based change detection for efficient scraping and monitoring.
"""

import json
import time
import threading
import requests
from typing import Dict, List, Optional, Any, Set, Tuple
from datetime import datetime, timedelta
import re
import os
from dotenv import load_dotenv
import hashlib

from core.logger import setup_logger, log_global, write_product_result
from core.performance import PerformanceTracker
from core.Web.product_schema import create_product_schema
from sites.base_site import BaseSite
from Database.Redis.Configuration.redis_client import get_redis_client

logger = setup_logger("skymil", "scraper")
dup_logger = setup_logger("skymil", "duplication")

load_dotenv()


class SkymilSite(BaseSite):
    """
    Skymil scraper - Fetches products from Supabase API with token caching and auto-refresh.
    Inherits from BaseSite for batching, connection management, and progress tracking.
    Supports hash-based change detection for both scraping and monitoring.
    """

    def __init__(self):
        # Call parent first - this initializes stats, batch_manager, requester, etc.
        super().__init__()
        
        self.name = "SKYMIL"
        self.scrape_interval = 86400  # 24 hours between scrapes
        self.performance = PerformanceTracker()

        # FILE PATHS (fallback)
        self.products_file = "config/SkymilProducts.json"
        self.brands_file = "config/SkymilBrands.json"
        self.categories_file = "config/SkymilCategories.json"
        
        # Thread safety
        self.lock = threading.Lock()
        self.seen_products: Set[str] = set()
        
        # =====================================================
        # SUPABASE API CONFIGURATION
        # =====================================================
        self.supabase_url = "https://twhtfguapjatibrzbmbi.supabase.co/rest/v1"
        self.token_env_key = "SKYMIL_SUPABASE_TOKEN"
        self.token_expiry_env_key = "SKYMIL_TOKEN_EXPIRY"
        self.apikey_env_key = "SKYMIL_SUPABASE_APIKEY"
        
        # Load or discover token and apikey
        self.bearer_token = None
        self.apikey = None
        self._load_or_discover_credentials()
        
        # Data maps (loaded from API)
        self.brand_map = {}
        self.category_map = {}
        
        # Stats
        self.stats = {
            'new_products': 0,
            'skipped_duplicates': 0,
            'errors': 0,
            'redis_saved': 0,
            'postgres_saved': 0,
            'total_products_found': 0,
            'total_categories': 0,
            'completed_categories': 0,
            'current_category': '',
            'current_product': '',
            'current_page': 0,
            'total_pages': 0,
            'category_progress': 0
        }
        
        logger.info(f"{self.name} | INIT_DONE")

    def get_requester_config(self) -> Dict:
        """Override to provide Skymil-specific requester config."""
        return {
            "requests_per_minute": 30,
            "max_retries": 3,
            "timeout": 45,
            "use_session": True,
            "use_referer": True,
            "delay_between_requests": (0.5, 2.0),
            "use_basic_headers": False,
        }

    # =====================================================
    # CREDENTIAL MANAGEMENT - ENV + DISCOVERY
    # =====================================================

    def _load_or_discover_credentials(self) -> bool:
        """
        Load token and apikey from env file or discover them from the website.
        Returns True if credentials are available.
        """
        # Try to load from env first
        token = os.getenv(self.token_env_key)
        apikey = os.getenv(self.apikey_env_key)
        expiry = os.getenv(self.token_expiry_env_key)
        
        # Check if token exists and is not expired
        if token and apikey:
            if expiry:
                try:
                    expiry_date = datetime.fromisoformat(expiry)
                    if expiry_date > datetime.now():
                        self.bearer_token = token
                        self.apikey = apikey
                        logger.info(f"{self.name} | CREDENTIALS_LOADED | valid until {expiry}")
                        return True
                    else:
                        logger.info(f"{self.name} | TOKEN_EXPIRED | discovering new credentials...")
                except ValueError:
                    self.bearer_token = token
                    self.apikey = apikey
                    logger.info(f"{self.name} | CREDENTIALS_LOADED | no expiry check")
                    return True
            else:
                self.bearer_token = token
                self.apikey = apikey
                logger.info(f"{self.name} | CREDENTIALS_LOADED")
                return True
        
        # Credentials not found or expired, discover them
        logger.info(f"{self.name} | CREDENTIALS_NOT_FOUND | discovering from website...")
        if self._discover_and_save_credentials():
            return True
        
        # Fallback to hardcoded credentials if discovery fails
        logger.warning(f"{self.name} | USING_HARDCODED_CREDENTIALS_FALLBACK")
        self.bearer_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InR3aHRmZ3VhcGphdGlicnpibWJpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI5ODExNzEsImV4cCI6MjA4ODU1NzE3MX0.eBn8FuXBt4pZ0ehZFRJzDkVDmRgBbYkTgUfqSOW0Les"
        self.apikey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InR3aHRmZ3VhcGphdGlicnpibWJpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI5ODExNzEsImV4cCI6MjA4ODU1NzE3MX0.eBn8FuXBt4pZ0ehZFRJzDkVDmRgBbYkTgUfqSOW0Les"
        self._save_credentials_to_env(self.bearer_token, self.apikey)
        return True

    def _discover_and_save_credentials(self) -> bool:
        """Discover the bearer token and apikey from the website."""
        try:
            logger.info(f"{self.name} | DISCOVERING_CREDENTIALS | Starting...")
            
            html = self.requester.get_html("https://www.skymil-shop.com/")
            if not html:
                logger.warning(f"{self.name} | PAGE_FETCH_FAILED")
                return False
            
            supabase_patterns = [
                r'supabase\.createClient\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
                r'SUPABASE_KEY\s*[:=]\s*["\']([^"\']+)["\']',
                r'ANON_KEY\s*[:=]\s*["\']([^"\']+)["\']',
                r'"supabaseKey"\s*:\s*"([^"]+)"',
                r'"anonKey"\s*:\s*"([^"]+)"',
            ]
            
            found_token = None
            found_apikey = None
            
            for pattern in supabase_patterns:
                matches = re.findall(pattern, html)
                if matches:
                    for match in matches:
                        if isinstance(match, tuple) and len(match) >= 2:
                            if self._is_jwt_token(match[1]) and len(match[1]) > 20:
                                found_token = match[1]
                                found_apikey = match[1]
                                break
                        else:
                            if self._is_jwt_token(match) and len(match) > 20:
                                found_token = match
                                found_apikey = match
                                break
                    if found_token:
                        break
            
            if not found_token:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                for script in soup.find_all("script"):
                    if not script.string:
                        continue
                    for pattern in supabase_patterns:
                        matches = re.findall(pattern, script.string)
                        if matches:
                            for match in matches:
                                if isinstance(match, tuple) and len(match) >= 2:
                                    if self._is_jwt_token(match[1]) and len(match[1]) > 20:
                                        found_token = match[1]
                                        found_apikey = match[1]
                                        break
                                else:
                                    if self._is_jwt_token(match) and len(match) > 20:
                                        found_token = match
                                        found_apikey = match
                                        break
                            if found_token:
                                break
                    if found_token:
                        break
            
            if not found_token:
                jwt_pattern = r'(eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[a-zA-Z0-9\-_=]+\.[a-zA-Z0-9\-_=]+)'
                matches = re.findall(jwt_pattern, html)
                if matches:
                    found_token = matches[0]
                    found_apikey = matches[0]
            
            if found_token and found_apikey:
                self.bearer_token = found_token
                self.apikey = found_apikey
                self._save_credentials_to_env(found_token, found_apikey)
                logger.info(f"{self.name} | CREDENTIALS_DISCOVERED")
                return True
            
            logger.warning(f"{self.name} | CREDENTIALS_NOT_FOUND")
            return False
            
        except Exception as e:
            logger.error(f"{self.name} | CREDENTIALS_DISCOVERY_ERROR | {e}")
            return False

    def _is_jwt_token(self, token: str) -> bool:
        """Check if a string looks like a JWT token."""
        if not token or len(token) < 20:
            return False
        parts = token.split('.')
        if len(parts) >= 3:
            try:
                import base64
                header = parts[0]
                padding = '=' * (4 - len(header) % 4)
                if padding != '===':
                    decoded = base64.urlsafe_b64decode(header + padding)
                    if b'{"alg"' in decoded or b'{"typ"' in decoded:
                        return True
            except:
                pass
        return False

    def _save_credentials_to_env(self, token: str, apikey: str):
        """Save the token and apikey to the .env file with expiry."""
        try:
            env_path = os.path.join(os.getcwd(), '.env')
            expiry_date = datetime.now() + timedelta(days=365)
            
            env_vars = {}
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            env_vars[key] = value
            
            env_vars[self.token_env_key] = token
            env_vars[self.apikey_env_key] = apikey
            env_vars[self.token_expiry_env_key] = expiry_date.isoformat()
            
            with open(env_path, 'w', encoding='utf-8') as f:
                for key, value in env_vars.items():
                    f.write(f"{key}={value}\n")
            
            logger.info(f"{self.name} | CREDENTIALS_SAVED")
            
        except Exception as e:
            logger.error(f"{self.name} | SAVE_CREDENTIALS_ERROR | {e}")

    def _refresh_credentials(self) -> bool:
        """Refresh credentials by re-discovering them."""
        logger.info(f"{self.name} | REFRESHING_CREDENTIALS")
        return self._discover_and_save_credentials()

    # =====================================================
    # API FETCHING WITH CREDENTIALS
    # =====================================================

    def _make_api_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make a Supabase API request with automatic token handling."""
        if not self.bearer_token or not self.apikey:
            if not self._load_or_discover_credentials():
                logger.error(f"{self.name} | NO_CREDENTIALS")
                return None
        
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "apikey": self.apikey,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        
        url = f"{self.supabase_url}/{endpoint}"
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=45)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                logger.warning(f"{self.name} | CREDENTIALS_EXPIRED | refreshing...")
                if self._refresh_credentials():
                    headers["Authorization"] = f"Bearer {self.bearer_token}"
                    headers["apikey"] = self.apikey
                    response = requests.get(url, params=params, headers=headers, timeout=45)
                    if response.status_code == 200:
                        return response.json()
                return None
            elif response.status_code == 429:
                logger.warning(f"{self.name} | RATE_LIMITED | {endpoint}")
                time.sleep(5)
                return self._make_api_request(endpoint, params)
            else:
                logger.error(f"{self.name} | API_ERROR | {endpoint} | status={response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"{self.name} | API_EXCEPTION | {endpoint} | {e}")
            return None

    def fetch_products(self) -> List[Dict]:
        """Fetch all products from Supabase API."""
        result = self._make_api_request("products", {"limit": 10000})
        return result if isinstance(result, list) else []

    def fetch_brands(self) -> Dict:
        """Fetch all brands from Supabase API."""
        result = self._make_api_request("brands")
        if result and isinstance(result, list):
            return {str(b.get("id")): b.get("name", "Unknown") for b in result if b.get("id")}
        return {}

    def fetch_categories(self) -> Dict:
        """Fetch all categories from Supabase API with specific fields."""
        params = {
            "select": "id,name,name_en,slug,icon,parent_id,position",
            "order": "position.asc"
        }
        result = self._make_api_request("categories", params)
        if result and isinstance(result, list):
            return {str(c.get("id")): c.get("name", "Unknown") for c in result if c.get("id")}
        return {}

    # =====================================================
    # HASH COMPUTATION AND COMPARISON
    # =====================================================

    def compute_product_hash(self, product_data: Dict) -> str:
        """
        Compute a deterministic hash of the six tracked fields for fast monitoring.
        """
        tracked_fields = {
            "price": float(product_data.get("price", 0) or 0),
            "discount_price": float(product_data.get("discount_price", 0) or 0),
            "discount_amount": float(product_data.get("discount_amount", 0) or 0),
            "availability": product_data.get("availability", "OutOfStock"),
            "special_id": product_data.get("special_id"),
            "price_valid_until": product_data.get("price_valid_until"),
        }
        
        # Normalize values for consistent hashing
        sorted_fields = {}
        for key in sorted(tracked_fields.keys()):
            value = tracked_fields.get(key)
            if isinstance(value, float):
                sorted_fields[key] = round(value, 2)
            elif value is None:
                sorted_fields[key] = ""
            else:
                sorted_fields[key] = str(value)
        
        json_str = json.dumps(sorted_fields, sort_keys=True)
        return hashlib.md5(json_str.encode()).hexdigest()

    def compute_site_hash_from_products(self, products: List[Dict]) -> Tuple[str, Dict[str, str]]:
        """
        Compute a combined hash for ALL products in the site.
        Returns (site_hash, product_hashes_dict)
        """
        product_hashes = {}
        
        for product in products:
            product_id = product.get("id")
            if not product_id:
                continue
            
            product_id = str(product_id)
            
            # Extract tracked fields from raw product data
            tracked_fields = {
                "price": float(product.get("price", 0) or 0),
                "discount_price": float(product.get("old_price", 0) or product.get("regular_price", 0) or 0),
                "discount_amount": 0.0,
                "availability": product.get("availability", "OutOfStock") or "OutOfStock",
                "special_id": product.get("reference") or product.get("sku") or product.get("special_id"),
                "price_valid_until": product.get("price_valid_until") or (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
            }
            
            # Fix discount logic
            old_price = product.get("old_price") or product.get("regular_price")
            price = float(product.get("price", 0) or 0)
            if old_price and float(old_price) > price:
                tracked_fields["discount_price"] = float(old_price)
                tracked_fields["discount_amount"] = round(float(old_price) - price, 2)
            else:
                tracked_fields["discount_price"] = 0.0
                tracked_fields["discount_amount"] = 0.0
            
            product_hash = self.compute_product_hash(tracked_fields)
            product_hashes[product_id] = product_hash
        
        if not product_hashes:
            return hashlib.md5(b"empty").hexdigest(), {}
        
        # Sort by product ID for deterministic combined hash
        sorted_hashes = [f"{pid}:{h}" for pid, h in sorted(product_hashes.items())]
        combined = "|".join(sorted_hashes)
        site_hash = hashlib.md5(combined.encode()).hexdigest()
        
        return site_hash, product_hashes

    def _get_cached_hash(self, product_id: str) -> Optional[str]:
        """
        Get the cached hash for a product from Redis.
        """
        try:
            # First try to get from the product's _hash field
            product = self.cache.get_product(self.name.lower(), product_id)
            if product and "_hash" in product:
                return product["_hash"]
            
            # Fallback: check the separate hash key
            redis = get_redis_client()
            hash_key = f"skymil:hash:{product_id}"
            value = redis.get(hash_key)
            if value:
                return value.decode() if isinstance(value, bytes) else value
            
            return None
        except Exception as e:
            logger.debug(f"{self.name} | CACHE_HASH_READ_ERROR | {product_id} | {e}")
            return None

    def _store_product_hash(self, product_id: str, product_hash: str) -> bool:
        """
        Store the product hash in Redis.
        """
        try:
            # Update the product in Redis with the hash field
            self.cache.update_product(
                self.name.lower(),
                product_id,
                {"_hash": product_hash}
            )
            
            # Also store in separate hash key for bulk operations
            redis = get_redis_client()
            hash_key = f"skymil:hash:{product_id}"
            redis.set(hash_key, product_hash)
            
            logger.debug(f"{self.name} | HASH_STORED | {product_id} | {product_hash[:8]}")
            return True
            
        except Exception as e:
            logger.warning(f"{self.name} | HASH_STORE_ERROR | {product_id} | {e}")
            return False

    def _get_cached_site_hash(self) -> Optional[str]:
        """
        Get the site-wide hash from Redis.
        """
        try:
            redis = get_redis_client()
            value = redis.get("skymil:site_hash")
            if value:
                return value.decode() if isinstance(value, bytes) else value
            return None
        except Exception as e:
            logger.debug(f"{self.name} | SITE_HASH_READ_ERROR | {e}")
            return None

    def _update_site_hash(self, product_hashes: Dict[str, str]) -> bool:
        """
        Update the site-wide hash in Redis using the provided product hashes.
        """
        try:
            if not product_hashes:
                logger.warning(f"{self.name} | NO_PRODUCT_HASHES | skipping site hash update")
                return False
            
            # Sort for deterministic hash
            sorted_hashes = [f"{pid}:{h}" for pid, h in sorted(product_hashes.items())]
            combined = "|".join(sorted_hashes)
            site_hash = hashlib.md5(combined.encode()).hexdigest()
            
            redis = get_redis_client()
            redis.set("skymil:site_hash", site_hash)
            redis.set("skymil:hash_timestamp", datetime.now().isoformat())
            
            logger.info(f"{self.name} | SITE_HASH_UPDATED | {site_hash[:8]} | products={len(product_hashes)}")
            return True
            
        except Exception as e:
            logger.error(f"{self.name} | SITE_HASH_UPDATE_ERROR | {e}")
            return False

    def _product_has_changed(self, product_data: Dict, product_id: str) -> bool:
        """
        Check if a product has changed by comparing hashes.
        """
        try:
            new_hash = self.compute_product_hash(product_data)
            cached_hash = self._get_cached_hash(product_id)
            
            if cached_hash is None:
                logger.debug(f"{self.name} | NO_CACHED_HASH | {product_id} | treating as new")
                return True
            
            changed = cached_hash != new_hash
            
            if changed:
                logger.info(f"{self.name} | HASH_CHANGED | {product_id} | {cached_hash[:8]} -> {new_hash[:8]}")
            
            return changed
            
        except Exception as e:
            logger.error(f"{self.name} | HASH_COMPARE_ERROR | {product_id} | {e}")
            return True

    # =====================================================
    # PRODUCT PARSING
    # =====================================================

    def transform_product(self, product_data: Dict) -> Optional[Dict]:
        """
        Transform raw API product data into the canonical product schema.
        """
        try:
            product_id = product_data.get("id")
            name = product_data.get("name")
            
            if not product_id or not name:
                return None
            
            slug = product_data.get("slug") or product_data.get("url_slug") or str(product_id)
            url = f"https://skymil-shop.com/produit/{slug}"
            
            product = create_product_schema(self.name, url, str(product_id))
            product["name"] = name
            
            # ─── PRICE LOGIC ─────────────────────────────────────
            price = float(product_data.get("price", 0) or 0)
            old_price = product_data.get("old_price") or product_data.get("regular_price") or product_data.get("compare_at_price")
            
            if old_price and float(old_price) > price:
                product["price"] = float(old_price)
                product["discount_price"] = price
                product["discount_amount"] = round(float(old_price) - price, 2)
            else:
                product["price"] = price
                product["discount_price"] = 0.0
                product["discount_amount"] = 0.0
            
            # ─── BRAND ───────────────────────────────────────────
            brand_id = product_data.get("brand_id") or product_data.get("brandId")
            product["brand"] = self.brand_map.get(str(brand_id), "Unknown") if brand_id else "Unknown"
            
            # ─── CATEGORY ────────────────────────────────────────
            category_id = product_data.get("category_id") or product_data.get("categoryId")
            product["category"] = self.category_map.get(str(category_id), "Unknown") if category_id else "Unknown"
            
            # ─── IMAGES ──────────────────────────────────────────
            main_image = product_data.get("image_url") or product_data.get("main_image") or product_data.get("image")
            images = product_data.get("images", [])
            
            all_images = []
            if main_image:
                all_images.append(main_image)
            if images and isinstance(images, list):
                all_images.extend(images)
            
            product["images"] = all_images
            product["main_image"] = main_image
            
            # ─── DESCRIPTION ─────────────────────────────────────
            product["description"] = product_data.get("description", "") or ""
            
            # ─── SPECS ──────────────────────────────────────────
            specs = product_data.get("specs", {}) or product_data.get("attributes", {})
            clean_specs = {}
            if isinstance(specs, dict):
                for k, v in specs.items():
                    if k and v:
                        key = str(k).strip().lower()
                        value = str(v).strip()
                        if key and value and len(key) < 50:
                            clean_specs[key] = value
            
            product["short_description"] = clean_specs
            
            # ─── AVAILABILITY ──────────────────────────────────
            availability = product_data.get("availability") or product_data.get("stock_status") or product_data.get("in_stock")
            if availability is not None:
                if isinstance(availability, bool):
                    product["availability"] = "InStock" if availability else "OutOfStock"
                elif isinstance(availability, (int, float)):
                    product["availability"] = "InStock" if availability > 0 else "OutOfStock"
                else:
                    avail_str = str(availability).lower()
                    if avail_str in ['in_stock', 'instock', 'true', '1']:
                        product["availability"] = "InStock"
                    elif avail_str in ['on_order', 'preorder']:
                        product["availability"] = "OnOrder"
                    else:
                        product["availability"] = "OutOfStock"
            else:
                product["availability"] = "OutOfStock"
            
            # ─── SPECIAL_ID ──────────────────────────────────────
            product["special_id"] = product_data.get("reference") or product_data.get("sku") or product_data.get("special_id")
            
            # ─── PRICE_VALID_UNTIL ─────────────────────────────
            valid_until = product_data.get("price_valid_until")
            if valid_until:
                product["price_valid_until"] = str(valid_until)
            else:
                product["price_valid_until"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            
            return product
            
        except Exception as e:
            logger.error(f"{self.name} | TRANSFORM_ERROR | {e}")
            return None

    # =====================================================
    # OVERRIDE _save_new_product TO STORE HASH
    # =====================================================

    def _save_new_product(self, product: Dict, product_id: str) -> bool:
        """
        Override parent to store product hash for fast monitoring.
        """
        result = super()._save_new_product(product, product_id)
        
        if result:
            try:
                product_hash = self.compute_product_hash(product)
                self._store_product_hash(product_id, product_hash)
            except Exception as e:
                logger.warning(f"{self.name} | HASH_STORE_ERROR | {product_id} | {e}")
        
        return result

    # =====================================================
    # PROCESS SINGLE PRODUCT WITH HASH COMPARISON
    # =====================================================

    def process_product(self, product_data: Dict) -> Optional[Dict]:
        """
        Process single product with hash-based change detection.
        - If product is new → save it
        - If product exists → check hash → update if changed, skip if unchanged
        """
        try:
            if product_data is None:
                logger.warning(f"{self.name} | SKIP_NONE")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            if not isinstance(product_data, dict):
                logger.warning(f"{self.name} | SKIP_INVALID")
                with self.lock:
                    self.stats["errors"] += 1
                return None
            
            product_id = product_data.get("main_product_id")
            
            if not product_id:
                logger.warning(f"{self.name} | SKIP_NO_ID")
                with self.lock:
                    self.stats["errors"] += 1
                self._add_error("Product missing ID")
                return None
            
            product_id = str(product_id)
            
            # ─── Session dedupe ──────────────────────────────────
            with self.lock:
                if product_id in self.seen_products:
                    self.stats["skipped_duplicates"] += 1
                    return None
                self.seen_products.add(product_id)
            
            # ─── Check if product exists ────────────────────────
            product_exists = self._product_exists(product_id)
            
            if product_exists:
                # ─── Product exists - check if it changed ──────────
                has_changed = self._product_has_changed(product_data, product_id)
                
                if has_changed:
                    # ─── Product changed - update it ──────────────
                    logger.info(f"{self.name} | UPDATING_CHANGED | {product_id}")
                    
                    if self._save_new_product(product_data, product_id):
                        with self.lock:
                            self.stats["new_products"] += 1
                        return product_data
                    else:
                        with self.lock:
                            self.stats["errors"] += 1
                        self._add_error(f"Update failed: {product_id}")
                        return None
                else:
                    # ─── Product exists and hasn't changed ────────
                    with self.lock:
                        self.stats["skipped_duplicates"] += 1
                    logger.debug(f"{self.name} | SKIP_UNCHANGED | {product_id}")
                    return None
            else:
                # ─── New product - save it ──────────────────────────
                logger.info(f"{self.name} | NEW_PRODUCT | {product_id}")
                
                if self._save_new_product(product_data, product_id):
                    with self.lock:
                        self.stats["new_products"] += 1
                    return product_data
                else:
                    with self.lock:
                        self.stats["errors"] += 1
                    self._add_error(f"Save failed: {product_id}")
                    return None
            
        except Exception as e:
            logger.error(f"{self.name} | PRODUCT_ERROR | {product_id if 'product_id' in locals() else 'unknown'} | {e}")
            with self.lock:
                self.stats["errors"] += 1
            self._add_error(f"Product error: {str(e)[:50]}")
            return None

    def _add_error(self, error_msg: str):
        """Helper method to add error to dashboard."""
        try:
            from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
            progress = get_multi_scrape_progress()
            progress.increment_error(self.name, error_msg)
        except Exception as e:
            logger.debug(f"{self.name} | ERROR_DASHBOARD_FAILED | {e}")

    # =====================================================
    # MAIN SCRAPER WITH SITE-WIDE HASH CHECK
    # =====================================================

    def scrape_site(self):
        """
        Main scraping entry point.
        1. Check site-wide hash first
        2. If no changes → skip scraping
        3. If changes → fetch and process only changed products
        """
        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")
        
        results = []
        total_products = 0
        filtered_count = 0
        filtered_reasons = {}
        site_hash_changed = False
        product_hashes = {}

        # Reset dashboard stats
        try:
            from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
            progress = get_multi_scrape_progress()
            progress.reset_site(self.name)
        except Exception as e:
            logger.debug(f"{self.name} | DASHBOARD_RESET_ERROR | {e}")
        
        # Initialize connections using inherited method
        try:
            if not self._init_connections():
                logger.error(f"{self.name} | CONNECTION_FAILED")
                return {"site": self.name, "total_products": 0, "products": [], "stats": self.stats}
        except Exception as e:
            logger.error(f"{self.name} | CONNECTION_FAILED | {e}")
            return {"site": self.name, "total_products": 0, "products": [], "stats": self.stats}
        
        try:
            # ─── STEP 1: Fetch brands and categories ──────────
            logger.info(f"{self.name} | FETCHING_METADATA")
            self.brand_map = self.fetch_brands()
            self.category_map = self.fetch_categories()
            
            logger.info(f"{self.name} | METADATA_LOADED | brands={len(self.brand_map)} | categories={len(self.category_map)}")
            
            # ─── STEP 2: Fetch all products ────────────────────
            logger.info(f"{self.name} | FETCHING_PRODUCTS")
            products = self.fetch_products()
            
            if not products:
                logger.warning(f"{self.name} | NO_PRODUCTS_FETCHED")
                return {"site": self.name, "total_products": 0, "products": [], "stats": self.stats}
            
            total_products = len(products)
            logger.info(f"{self.name} | PRODUCTS_FETCHED | total={total_products}")
            
            # ─── STEP 3: Compute site hash from fetched products ──
            logger.info(f"{self.name} | COMPUTING_SITE_HASH")
            new_site_hash, product_hashes = self.compute_site_hash_from_products(products)
            logger.info(f"{self.name} | NEW_SITE_HASH | {new_site_hash[:8]}")
            
            # ─── STEP 4: Compare with cached site hash ────────
            cached_site_hash = self._get_cached_site_hash()
            
            if cached_site_hash and cached_site_hash == new_site_hash:
                # ─── NO CHANGES! Skip scraping ──────────────────
                logger.info(f"{self.name} | SITE_HASH_MATCH | {new_site_hash[:8]} | NO CHANGES - SKIPPING SCRAPE")
                log_global(f"{self.name} | SITE_HASH_MATCH | NO CHANGES - SKIPPING SCRAPE")
                
                print("\n" + "=" * 60)
                print(f" {self.name} - NO CHANGES DETECTED")
                print("=" * 60)
                print(f" Site hash: {new_site_hash[:8]} (unchanged)")
                print(f" Total products: {total_products}")
                print(" SKIPPING SCRAPE - No changes detected")
                print("=" * 60)
                
                # Still update stats to show no changes
                self.stats = {
                    "new_products": 0,
                    "skipped_duplicates": 0,
                    "errors": 0,
                    "redis_saved": 0,
                    "postgres_saved": 0,
                    "total_products_found": total_products,
                    "completed_categories": 1,
                    "total_categories": 1,
                    "current_category": "No changes detected",
                    "current_product": "",
                    "current_page": 0,
                    "total_pages": 1,
                    "category_progress": 100
                }
                
                return {
                    "site": self.name,
                    "total_products": 0,
                    "products": [],
                    "stats": self.stats,
                    "skipped": True,
                    "reason": "No changes detected"
                }
            
            # ─── STEP 5: HAS CHANGED - Process products ────────
            site_hash_changed = True
            logger.info(f"{self.name} | SITE_HASH_CHANGED | old={cached_site_hash[:8] if cached_site_hash else 'None'} | new={new_site_hash[:8]}")
            
            # ─── STEP 6: Reset stats ──────────────────────────
            self.stats = {
                "new_products": 0,
                "skipped_duplicates": 0,
                "errors": 0,
                "redis_saved": 0,
                "postgres_saved": 0,
                "total_products_found": total_products,
                "completed_categories": 1,
                "total_categories": 1,
                "current_category": "Processing Skymil products",
                "current_product": "",
                "current_page": 0,
                "total_pages": 1,
                "category_progress": 0
            }
            self.seen_products.clear()
            
            # ─── STEP 7: Process only changed products ────────
            changed_count = 0
            unchanged_count = 0
            
            for i, product_data in enumerate(products):
                product_id = str(product_data.get("id"))
                
                # Update progress
                self.stats['current_product'] = f"{i+1}/{total_products}"
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                # ─── VALIDATE REQUIRED FIELDS ──────────────────
                if not product_data.get("name"):
                    filtered_count += 1
                    filtered_reasons["missing_name"] = filtered_reasons.get("missing_name", 0) + 1
                    continue
                
                price = product_data.get("price", 0)
                if price <= 0:
                    filtered_count += 1
                    filtered_reasons["zero_or_negative_price"] = filtered_reasons.get("zero_or_negative_price", 0) + 1
                    continue
                
                if not product_id or product_id == "None":
                    filtered_count += 1
                    filtered_reasons["missing_id"] = filtered_reasons.get("missing_id", 0) + 1
                    continue
                
                # ─── Check if this specific product changed ────
                cached_product_hash = self._get_cached_hash(product_id)
                new_product_hash = product_hashes.get(product_id)
                
                if cached_product_hash and cached_product_hash == new_product_hash:
                    # Product hasn't changed - skip
                    unchanged_count += 1
                    continue
                
                # ─── Product changed or is new - process it ────
                try:
                    product = self.transform_product(product_data)
                except Exception as e:
                    self._add_error(f"Transform error {product_id}: {str(e)[:50]}")
                    filtered_count += 1
                    filtered_reasons[f"transform_error"] = filtered_reasons.get("transform_error", 0) + 1
                    continue
                
                if not product:
                    filtered_count += 1
                    filtered_reasons["transform_failed"] = filtered_reasons.get("transform_failed", 0) + 1
                    continue
                
                # ─── Process and save ──────────────────────────
                saved_product = self.process_product(product)
                if saved_product:
                    results.append(saved_product)
                    self.performance.add_product()
                    changed_count += 1
                    
                    # Write to results.txt
                    try:
                        write_product_result(saved_product, self.name)
                    except Exception as e:
                        logger.warning(f"{self.name} | RESULT_WRITE_ERROR | {e}")
                
                self.stats['new_products'] = len(results)
                if hasattr(self, '_update_stats'):
                    self._update_stats(self.stats)
                
                # Log progress every 50 products
                if self.performance.total_products % 50 == 0:
                    speed = self.performance.get_speed()
                    logger.info(f"{self.name} | PROGRESS | {self.performance.total_products}/{total_products} | speed={speed:.2f} prod/s")
            
            # ─── STEP 8: Log summary ──────────────────────────
            logger.info(f"{self.name} | SCRAPE_SUMMARY | total={total_products} | changed={changed_count} | unchanged={unchanged_count} | filtered={filtered_count}")
            
            if filtered_count > 0:
                logger.warning(f"{self.name} | FILTERED_SUMMARY | total={filtered_count} | reasons={filtered_reasons}")
            
            # Mark as complete
            self.stats['completed_categories'] = 1
            if hasattr(self, '_update_stats'):
                self._update_stats(self.stats)
            
            # ─── STEP 9: Update site-wide hash ──────────────────
            logger.info(f"{self.name} | UPDATING_SITE_HASH")
            self._update_site_hash(product_hashes)
            
        except KeyboardInterrupt:
            logger.warning(f"{self.name} | INTERRUPTED")
            try:
                from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
                progress = get_multi_scrape_progress()
                progress.set_status(self.name, "⚠️ Interrupted")
            except:
                pass
        except Exception as e:
            error_msg = f"Scrape failed: {str(e)[:50]}"
            logger.error(f"{self.name} | SCRAPE_ERROR | {e}")
            import traceback
            traceback.print_exc()
            self._add_error(error_msg)
        finally:
            # ─── STEP 10: Flush any remaining products ──────────
            batch_size = self.get_pending_batch_size()
            if batch_size > 0:
                logger.info(f"{self.name} | FLUSHING_PENDING | {batch_size} products")
                try:
                    self.flush_pending_batch()
                except Exception as e:
                    logger.error(f"{self.name} | FLUSH_ERROR | {e}")
            
            # Log final summary
            self.log_final_summary(results)
            
            # Close connections
            self._close_connections()
        
        logger.info(f"{self.name} | SCRAPE_DONE | total={len(results)}")
        log_global(f"{self.name} | SCRAPE_DONE | total={len(results)}")
        
        # ─── STEP 11: Print summary ────────────────────────────
        print("\n" + "=" * 60)
        print(f" {self.name} SCRAPING COMPLETE")
        print("=" * 60)
        print(f" TOTAL PRODUCTS FOUND:    {total_products}")
        print(f" SITE HASH CHANGED:       {site_hash_changed}")
        if site_hash_changed:
            print(f" PRODUCTS CHANGED:        {changed_count}")
            print(f" PRODUCTS UNCHANGED:      {unchanged_count}")
            print(f" FILTERED OUT:            {filtered_count}")
            if filtered_count > 0:
                for reason, count in filtered_reasons.items():
                    print(f"   - {reason}: {count}")
        print(f" PRODUCTS SAVED:          {len(results)}")
        print(f" NEW PRODUCTS:            {self.stats['new_products']}")
        print(f" SKIPPED (EXISTING):      {self.stats['skipped_duplicates']}")
        print(f" ERRORS:                  {self.stats['errors']}")
        print(f" REDIS SAVES:             {self.stats['redis_saved']}")
        print(f" POSTGRES SAVES:          {self.stats['postgres_saved']}")
        print("=" * 60)
        
        return {
            "site": self.name,
            "total_products": len(results),
            "products": results,
            "stats": self.stats,
            "skipped": not site_hash_changed,
            "changed_count": changed_count if site_hash_changed else 0
        }

    # =====================================================
    # UNIMPLEMENTED METHODS (for CLI compatibility)
    # =====================================================

    def search(self, query):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return

    def parse_product(self, url, product_id):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return

    def extract_categories(self):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return


def create_skymil_site() -> SkymilSite:
    """Factory function for site loader."""
    return SkymilSite()