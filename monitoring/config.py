# monitoring/config.py
"""
Centralized monitoring configuration - Each site has independent settings.
"""

from typing import Dict, Callable, Optional, List
import importlib
from core.logger import setup_logger

logger = setup_logger("monitor_config", "monitor")


# ─── SITE CONFIGURATIONS ──────────────────────────────────────────────
SITE_CONFIGS: Dict[str, Dict] = {
    "megapc": {
        "enabled": True,
        "workers": 20,           # Increased from 30
        "batch_size": 100,       # Increased from 50
        "interval": 300,
        "timeout": 15,           # Reduced from 30
        "retries": 2,            # Reduced from 3
        "requests_per_minute": 300,  # Increased from 200
        "use_referer": True,
        "verify_ssl": True,
        "use_basic_headers": True,
        "worker_module": "megapc_worker",
        "worker_func": "megapc_worker"
    },
    "tunisianet": {
        "enabled": True,
        "workers": 20,           # Increased from 20
        "batch_size": 100,       # Increased from 75
        "interval": 600,
        "timeout": 15,           # Reduced from 45
        "retries": 2,            # Reduced from 4
        "requests_per_minute": 100,  # Increased from 40
        "use_referer": True,
        "verify_ssl": True,
        "use_basic_headers": False,
        "worker_module": "tunisianet_worker",
        "worker_func": "tunisianet_worker"
    },
    "sbs": {
        "enabled": True,
        "workers": 15,           # Increased from 20
        "batch_size": 100,       # Increased from 75
        "interval": 600,
        "timeout": 15,           # Reduced from 45
        "retries": 2,            # Reduced from 4
        "requests_per_minute": 100,  # Increased from 40
        "use_referer": True,
        "verify_ssl": True,
        "use_basic_headers": False,
        "worker_module": "sbs_worker",
        "worker_func": "sbs_worker"
    },
    "mytek": {
        "enabled": True,
        "workers": 15,           # Increased from 20
        "batch_size": 100,       # Increased from 75
        "interval": 3600,
        "timeout": 15,           # Reduced from 45
        "retries": 2,            # Reduced from 4
        "requests_per_minute": 100,  # Increased from 40
        "use_referer": True,
        "verify_ssl": True,
        "use_basic_headers": True,
        "worker_module": "mytek_worker",
        "worker_func": "mytek_worker"
    },
    "spacenet": {
        "enabled": True,
        "workers": 15,           # Increased from 20
        "batch_size": 100,       # Increased from 75
        "interval": 3600,
        "timeout": 15,           # Reduced from 45
        "retries": 2,            # Reduced from 4
        "requests_per_minute": 100,  # Increased from 40
        "use_referer": True,
        "verify_ssl": True,
        "use_basic_headers": True,
        "worker_module": "spacenet_worker",
        "worker_func": "spacenet_worker"
    },
    "skymil": {
        "enabled": True,
        "workers": 5,            # API-based, few workers enough
        "batch_size": 1000,      # All products in one batch
        "interval": 3600,
        "timeout": 45,
        "retries": 2,
        "requests_per_minute": 60,
        "use_referer": True,
        "verify_ssl": True,
        "use_basic_headers": False,
        "worker_module": "skymil_worker",
        "worker_func": "create_skymil_worker"
    }
}


# ─── WORKER CACHE ──────────────────────────────────────────────────────
_worker_cache: Dict[str, Callable] = {}


def get_requester_config(site: str) -> Dict:
    """Get SmartRequester configuration for a specific site (INDEPENDENT)."""
    site = site.lower()
    config = SITE_CONFIGS.get(site, {})
    return {
        "requests_per_minute": config.get("requests_per_minute", 20),
        "max_retries": config.get("retries", 3),
        "timeout": config.get("timeout", 30),
        "use_session": True,
        "use_referer": config.get("use_referer", True),
        "verify_ssl": config.get("verify_ssl", True),
        "delay_between_requests": (0.5, 2.0),
        "use_basic_headers": config.get("use_basic_headers", False),
    }


def get_worker(site: str) -> Optional[Callable]:
    """Get worker function for a site (lazy loaded)."""
    site = site.lower()
    
    if site in _worker_cache:
        return _worker_cache[site]
    
    config = SITE_CONFIGS.get(site)
    if not config:
        logger.error(f"No configuration for site: {site}")
        return None
    
    try:
        module_name = config.get("worker_module")
        func_name = config.get("worker_func")
        
        module = importlib.import_module(f"monitoring.workers.{module_name}")
        worker = getattr(module, func_name)
        
        if func_name.startswith("create_"):
            worker = worker()
        
        _worker_cache[site] = worker
        logger.info(f"Loaded worker for: {site}")
        return worker
        
    except ImportError as e:
        logger.error(f"Could not import worker for {site}: {e}")
        return None
    except AttributeError as e:
        logger.error(f"Could not find worker function for {site}: {e}")
        return None


def get_site_config(site: str) -> Dict:
    site = site.lower()
    return SITE_CONFIGS.get(site, {})


def get_enabled_sites() -> List[str]:
    return [site for site, config in SITE_CONFIGS.items() if config.get("enabled", True)]


def get_all_sites() -> List[str]:
    return list(SITE_CONFIGS.keys())


def update_site_config(site: str, **kwargs) -> bool:
    site = site.lower()
    if site in SITE_CONFIGS:
        for key, value in kwargs.items():
            if key in SITE_CONFIGS[site]:
                SITE_CONFIGS[site][key] = value
        logger.info(f"Updated config for {site}: {kwargs}")
        return True
    return False


def set_site_enabled(site: str, enabled: bool) -> bool:
    site = site.lower()
    if site in SITE_CONFIGS:
        SITE_CONFIGS[site]["enabled"] = enabled
        logger.info(f"Site {site} {'enabled' if enabled else 'disabled'}")
        return True
    return False


def toggle_site(site: str) -> bool:
    site = site.lower()
    if site in SITE_CONFIGS:
        current = SITE_CONFIGS[site].get("enabled", True)
        SITE_CONFIGS[site]["enabled"] = not current
        logger.info(f"Site {site} toggled to {'enabled' if not current else 'disabled'}")
        return not current
    return False


def print_registry_status() -> None:
    print("\n" + "=" * 80)
    print("📋 MONITORING SITES STATUS")
    print("=" * 80)
    
    if not SITE_CONFIGS:
        print("   No sites registered")
    else:
        print(f"   {'SITE':<15} | {'STATUS':<10} | {'WORKERS':<8} | {'BATCH':<8} | {'INTERVAL':<10} | {'RPM':<8} | {'HEADERS':<8}")
        print("   " + "-" * 80)
        for site, config in SITE_CONFIGS.items():
            enabled = config.get("enabled", True)
            status = "✅ ACTIVE" if enabled else "⏸️ DISABLED"
            workers = config.get("workers", 20)
            batch = config.get("batch_size", 50)
            interval = config.get("interval", 300)
            rpm = config.get("requests_per_minute", 20)
            headers = "Basic" if config.get("use_basic_headers", False) else "Standard"
            print(f"   {site.upper():<15} | {status:<10} | {workers:<8} | {batch:<8} | {interval}s{' ' * 6} | {rpm:<8} | {headers:<8}")
    
    print("=" * 80)