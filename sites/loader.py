# sites/loader.py
import os
import importlib
import inspect
from typing import Dict, Optional

from sites.base_site import BaseSite
from core.logger import setup_logger


logger = setup_logger("site_loader", "scraper")

# =====================================================
# SINGLETON CACHE - Only load sites once
# =====================================================
_SITE_CACHE: Dict[str, BaseSite] = {}
_LOADED = False


def load_sites() -> Dict[str, BaseSite]:
    """
    Load all site classes and instantiate them.
    Uses a cache to ensure each site is loaded only once.
    """
    global _SITE_CACHE, _LOADED
    
    # Return cached sites if already loaded
    if _LOADED and _SITE_CACHE:
        logger.info(f"SITE LOADER | returning cached sites ({len(_SITE_CACHE)} sites)")
        return _SITE_CACHE
    
    # Clear cache before loading
    _SITE_CACHE = {}
    
    sites_folder = os.path.dirname(__file__)
    logger.info(f"SITE LOADER | scanning sites folder: {sites_folder}")
    
    for file in os.listdir(sites_folder):
        if file.endswith("_site.py") and file != "base_site.py" and file != "loader.py":
            module_name = f"sites.{file[:-3]}"
            try:
                module = importlib.import_module(module_name)
                
                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and issubclass(obj, BaseSite) and obj != BaseSite:
                        # Check if we already have an instance of this site name
                        # Create a temporary instance to get the name
                        try:
                            # Try to get the site name without full instantiation if possible
                            # Some sites may need special init, so we create the instance
                            temp_instance = obj()
                            site_name = temp_instance.name
                            
                            # Check if we already have this site name
                            if site_name not in _SITE_CACHE:
                                # Use the instance we already created
                                _SITE_CACHE[site_name] = temp_instance
                                logger.info(f"SITE LOADER | loaded {site_name} (instance={id(temp_instance)})")
                            else:
                                logger.warning(f"SITE LOADER | skipping duplicate {site_name} (already loaded)")
                                
                        except Exception as e:
                            logger.error(f"SITE LOADER | failed to instantiate {name}: {e}")
                            
            except Exception as e:
                logger.error(f"SITE LOADER | failed to load module {module_name}: {e}")
    
    _LOADED = True
    logger.info(f"SITE LOADER | loaded {len(_SITE_CACHE)} sites")
    return _SITE_CACHE


def get_site(site_name: str) -> Optional[BaseSite]:
    """Get a specific site by name."""
    sites = load_sites()
    return sites.get(site_name.upper())


def reload_sites() -> Dict[str, BaseSite]:
    """Force reload all sites (clears cache)."""
    global _SITE_CACHE, _LOADED
    _SITE_CACHE = {}
    _LOADED = False
    logger.info("SITE LOADER | reloading all sites")
    return load_sites()


def get_site_instance(site_class):
    """
    Get or create a site instance.
    Use this when you need a specific site class instance.
    """
    sites = load_sites()
    for site in sites.values():
        if site.__class__ == site_class:
            return site
    return None


def list_loaded_sites() -> Dict[str, str]:
    """Get a list of loaded sites with their instance IDs."""
    sites = load_sites()
    return {name: str(id(instance)) for name, instance in sites.items()}