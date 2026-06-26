# core/utils/build_id_manager.py
"""
Dynamic Build ID Manager for MegaPC
Automatically discovers and caches the current build ID
Creates the cache file on first run
"""
import re
import json
import requests
import os
from datetime import datetime, timedelta
from typing import Optional, Dict
from core.logger import setup_logger

logger = setup_logger("build_id_manager", "scraper")


class BuildIDManager:
    """Manages dynamic discovery and caching of MegaPC build ID"""
    
    def __init__(self, cache_file: str = "build_id_cache.json"):
        self.cache_file = cache_file
        self.current_build_id = None
        self.cache_duration = timedelta(hours=24)  # Cache for 24 hours
        self.consecutive_failures = 0
        self.max_failures_before_refresh = 3
        self.last_refresh_attempt = None
        self.failure_cooldown = timedelta(minutes=5)
        self._ensure_cache_directory()
        self._load_from_cache()
    
    def _ensure_cache_directory(self) -> None:
        """Ensure the cache directory exists"""
        cache_dir = os.path.dirname(self.cache_file)
        if cache_dir and not os.path.exists(cache_dir):
            try:
                os.makedirs(cache_dir, exist_ok=True)
                logger.info(f"Created cache directory: {cache_dir}")
            except Exception as e:
                logger.warning(f"Failed to create cache directory: {e}")
    
    def _load_from_cache(self) -> bool:
        """Load cached build ID if file exists and is still valid"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                
                # Check if cache is still valid
                cached_time = datetime.fromisoformat(data.get('cached_at', ''))
                if datetime.now() - cached_time < self.cache_duration:
                    self.current_build_id = data.get('build_id')
                    self.consecutive_failures = data.get('consecutive_failures', 0)
                    logger.info(f"Loaded build ID from cache: {self.current_build_id}")
                    return True
                else:
                    logger.info("Build ID cache expired")
            else:
                logger.info("No build ID cache found. Will discover on first run.")
        except Exception as e:
            logger.warning(f"Failed to load build ID cache: {e}")
        return False
    
    def _save_to_cache(self) -> None:
        """Save build ID to cache"""
        try:
            data = {
                'build_id': self.current_build_id,
                'cached_at': datetime.now().isoformat(),
                'consecutive_failures': self.consecutive_failures
            }
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved build ID to cache: {self.current_build_id}")
        except Exception as e:
            logger.warning(f"Failed to save build ID cache: {e}")
    
    def _extract_from_script(self, html: str) -> Optional[str]:
        """Extract build ID from script tags"""
        # Pattern 1: From script src attribute
        # Matches: /_next/static/build-1779382291697/_buildManifest.js
        script_pattern = r'src="/_next/static/(build-\d+)/_buildManifest\.js"'
        match = re.search(script_pattern, html)
        if match:
            return match.group(1)
        
        # Pattern 2: From __NEXT_DATA__ JSON
        next_data_pattern = r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>'
        match = re.search(next_data_pattern, html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                build_id = data.get('buildId')
                if build_id:
                    return build_id
            except json.JSONDecodeError:
                pass
        
        return None
    
    def _extract_from_static_urls(self, html: str) -> Optional[str]:
        """Extract build ID from static URL patterns"""
        # Matches: /_next/static/build-1779382291697/
        static_pattern = r'/_next/static/(build-\d+)/'
        match = re.search(static_pattern, html)
        if match:
            return match.group(1)
        return None
    
    def discover_build_id(self, base_url: str = "https://megapc.tn/") -> Optional[str]:
        """
        Discover the current build ID from the site
        Returns build ID or None if not found
        """
        try:
            logger.info("Discovering MegaPC build ID...")
            print("\n🔍 Discovering MegaPC build ID...")
            
            # Fetch the main page
            response = requests.get(base_url, timeout=15)
            response.raise_for_status()
            html = response.text
            
            # Try multiple extraction methods
            build_id = None
            
            # Method 1: From script tags (most reliable)
            build_id = self._extract_from_script(html)
            if build_id:
                logger.info(f"Discovered build ID from script: {build_id}")
                print(f"✅ Discovered build ID from script: {build_id}")
                return build_id
            
            # Method 2: From static URLs
            build_id = self._extract_from_static_urls(html)
            if build_id:
                logger.info(f"Discovered build ID from static URLs: {build_id}")
                print(f"✅ Discovered build ID from static URLs: {build_id}")
                return build_id
            
            logger.warning("Could not discover build ID from page")
            print("❌ Could not discover build ID from page")
            return None
            
        except requests.RequestException as e:
            logger.error(f"Failed to fetch page for build ID discovery: {e}")
            print(f"❌ Failed to fetch MegaPC page: {e}")
            return None
        except Exception as e:
            logger.error(f"Error discovering build ID: {e}")
            print(f"❌ Error discovering build ID: {e}")
            return None
    
    def record_failure(self) -> int:
        """Record a failure and return the new failure count"""
        self.consecutive_failures += 1
        self._save_to_cache()
        logger.debug(f"Recorded failure {self.consecutive_failures}/{self.max_failures_before_refresh}")
        return self.consecutive_failures
    
    def record_success(self) -> None:
        """Reset failure counter on success"""
        if self.consecutive_failures > 0:
            self.consecutive_failures = 0
            self._save_to_cache()
            logger.debug("Reset consecutive failures (success)")
    
    def should_refresh_build_id(self) -> bool:
        """Check if we should attempt to refresh the build ID"""
        if self.consecutive_failures >= self.max_failures_before_refresh:
            if self.last_refresh_attempt:
                time_since_last = datetime.now() - self.last_refresh_attempt
                if time_since_last < self.failure_cooldown:
                    logger.debug(f"Build ID refresh on cooldown ({self.failure_cooldown.seconds}s)")
                    return False
            return True
        return False
    
    def get_build_id(self, force_refresh: bool = False) -> Optional[str]:
        """
        Get the current build ID.
        - If force_refresh=True: Always discover fresh
        - If no cache exists: Auto-discover
        - If failures >= threshold: Auto-refresh
        - Otherwise: Return cached
        """
        # Force refresh if requested
        if force_refresh:
            logger.info("Forcing build ID refresh...")
            print("🔄 Forcing build ID refresh...")
            build_id = self.discover_build_id()
            if build_id:
                self.current_build_id = build_id
                self.consecutive_failures = 0
                self.last_refresh_attempt = datetime.now()
                self._save_to_cache()
                return build_id
            return self.current_build_id  # Return cached if discovery fails
        
        # If no cache exists, discover on first run
        if self.current_build_id is None:
            logger.info("No cached build ID found. Discovering...")
            print("🔄 No cached build ID found. Discovering...")
            build_id = self.discover_build_id()
            if build_id:
                self.current_build_id = build_id
                self.consecutive_failures = 0
                self.last_refresh_attempt = datetime.now()
                self._save_to_cache()
                return build_id
            return None
        
        # Auto-refresh if failures threshold reached
        if self.should_refresh_build_id():
            logger.info(f"Auto-refreshing build ID after {self.consecutive_failures} failures...")
            print(f"🔄 Auto-refreshing build ID after {self.consecutive_failures} failures...")
            self.last_refresh_attempt = datetime.now()
            build_id = self.discover_build_id()
            if build_id:
                self.current_build_id = build_id
                self.consecutive_failures = 0
                self._save_to_cache()
                logger.info(f"Build ID refreshed: {build_id}")
                print(f"✅ Build ID refreshed: {build_id}")
                return build_id
            else:
                logger.warning("Build ID discovery failed, keeping existing")
        
        return self.current_build_id
    
    def get_base_next_url(self, force_refresh: bool = False) -> Optional[str]:
        """Get the full base Next.js URL with current build ID"""
        build_id = self.get_build_id(force_refresh)
        if build_id:
            return f"https://megapc.tn/_next/data/{build_id}"
        return None
    
    def invalidate_cache(self) -> None:
        """Force invalidation of build ID cache"""
        self.current_build_id = None
        self.consecutive_failures = 0
        if os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
                logger.info("Build ID cache file removed")
            except Exception as e:
                logger.warning(f"Failed to remove cache file: {e}")
        logger.info("Build ID cache invalidated")


# Singleton instance
_build_id_manager = None


def get_build_id_manager() -> BuildIDManager:
    """Get or create the global BuildIDManager instance"""
    global _build_id_manager
    if _build_id_manager is None:
        _build_id_manager = BuildIDManager()
    return _build_id_manager