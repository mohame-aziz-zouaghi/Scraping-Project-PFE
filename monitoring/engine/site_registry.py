# monitoring/engine/site_registry.py
import os
import importlib
from core.logger import setup_logger

logger = setup_logger("site_registry", "monitor")

# Registry of available site workers
SITE_WORKERS = {}

# Registry of site configurations
SITE_CONFIGS = {}


def register_site(site_name: str, worker_func, config: dict = None):
    """
    Register a site worker and its configuration
    
    Args:
        site_name: Name of the site (e.g., 'megapc')
        worker_func: Worker function for this site
        config: Site configuration (workers, interval, etc.)
    """
    site_name = site_name.lower()
    SITE_WORKERS[site_name] = worker_func
    
    # Default configuration if not provided
    if config is None:
        config = {}
    
    default_config = {
        "workers": 20,
        "interval": 300,
        "enabled": True,
        "timeout": 30,
        "retries": 3,
        "requests_per_minute": 20,
        "use_referer": True,
        "verify_ssl": True
    }
    
    default_config.update(config)
    SITE_CONFIGS[site_name] = default_config
    
    logger.info(f"Registered site: {site_name} | workers={default_config['workers']} | interval={default_config['interval']}s")
    print(f"✅ Registered site: {site_name.upper()} (workers: {default_config['workers']}, interval: {default_config['interval']}s)")


def unregister_site(site_name: str):
    """Unregister a site worker"""
    site_name = site_name.lower()
    if site_name in SITE_WORKERS:
        del SITE_WORKERS[site_name]
    if site_name in SITE_CONFIGS:
        del SITE_CONFIGS[site_name]
    logger.info(f"Unregistered site: {site_name}")


def get_worker(site_name: str):
    """Get worker function for a site"""
    site_name = site_name.lower()
    return SITE_WORKERS.get(site_name)


def get_site_config(site_name: str) -> dict:
    """Get configuration for a site"""
    site_name = site_name.lower()
    return SITE_CONFIGS.get(site_name, {
        "workers": 20,
        "interval": 300,
        "enabled": True
    })


def get_all_sites() -> list:
    """Get list of all registered sites"""
    return list(SITE_WORKERS.keys())


def get_enabled_sites() -> list:
    """Get list of enabled sites"""
    return [
        site for site in SITE_WORKERS.keys() 
        if SITE_CONFIGS.get(site, {}).get("enabled", True)
    ]


def update_site_config(site_name: str, **kwargs):
    """Update configuration for a site"""
    site_name = site_name.lower()
    if site_name in SITE_CONFIGS:
        SITE_CONFIGS[site_name].update(kwargs)
        logger.info(f"Updated config for {site_name}: {kwargs}")
        return True
    return False


def set_site_enabled(site_name: str, enabled: bool):
    """Enable or disable a site"""
    site_name = site_name.lower()
    if site_name in SITE_CONFIGS:
        SITE_CONFIGS[site_name]["enabled"] = enabled
        status = "enabled" if enabled else "disabled"
        logger.info(f"Site {site_name} {status}")
        print(f"   ✅ {site_name.upper()} {status}")
        return True
    return False


def print_registry_status():
    """Print status of all registered sites"""
    print("\n" + "="*60)
    print("📋 SITE REGISTRY STATUS")
    print("="*60)
    
    if not SITE_WORKERS:
        print("   No sites registered")
    else:
        for site in SITE_WORKERS.keys():
            config = SITE_CONFIGS.get(site, {})
            enabled = config.get("enabled", True)
            status = "✅ ACTIVE" if enabled else "⏸️ DISABLED"
            workers = config.get("workers", 20)
            interval = config.get("interval", 300)
            print(f"   {site.upper():15} | {status:12} | workers={workers} | interval={interval}s")
    
    print("="*60)


def register_all_sites():
    """
    Register all available site workers
    This is the main registration function that imports and registers all workers
    """
    
    # Import MegaPC worker
    try:
        from monitoring.workers.megapc_worker import megapc_worker
        
        register_site("megapc", megapc_worker, {
            "workers": 20,
            "interval": 300,
            "enabled": True,
            "timeout": 30,
            "retries": 3,
            "requests_per_minute": 20,
            "use_referer": True,
            "verify_ssl": True
        })
    except ImportError as e:
        logger.warning(f"Could not import megapc_worker: {e}")
    except Exception as e:
        logger.error(f"Error registering megapc: {e}")
    
    # Import Tunisianet worker
    try:
        from monitoring.workers.tunisianet_worker import tunisianet_worker

        register_site("tunisianet", tunisianet_worker, {
            "workers": 15,
            "interval": 600,
            "enabled": True,
            "timeout": 45,
            "retries": 4,
            "requests_per_minute": 10,
            "use_referer": True,
            "verify_ssl": True
        })
    except ImportError as e:
        logger.warning(f"Could not import tunisianet_worker: {e}")
    except Exception as e:
        logger.error(f"Error registering tunisianet: {e}")

    # Import SBS worker
    try:
        from monitoring.workers.sbs_worker import sbs_worker

        register_site("sbs", sbs_worker, {
            "workers": 15,
            "interval": 600,
            "enabled": True,
            "timeout": 45,
            "retries": 4,
            "requests_per_minute": 20,
            "use_referer": True,
            "verify_ssl": True
        })
    except ImportError as e:
        logger.warning(f"Could not import sbs_worker: {e}")
    except Exception as e:
        logger.error(f"Error registering sbs: {e}")
    
    # try:
    #     from monitoring.workers.mytek_worker import mytek_worker
    #     
    #     register_site("mytek", mytek_worker, {
    #         "workers": 15,
    #         "interval": 600,
    #         "enabled": False,
    #         "timeout": 30,
    #         "retries": 3,
    #         "requests_per_minute": 15,
    #         "use_referer": True,
    #         "verify_ssl": True
    #     })
    # except ImportError as e:
    #     logger.debug(f"MyTek worker not available: {e}")


def auto_discover_workers():
    """
    Auto-discover workers from the workers directory
    This dynamically finds and registers any worker files
    """
    workers_dir = os.path.join(os.path.dirname(__file__), "..", "workers")
    
    if not os.path.exists(workers_dir):
        logger.warning(f"Workers directory not found: {workers_dir}")
        return
    
    for filename in os.listdir(workers_dir):
        if filename.endswith("_worker.py"):
            module_name = filename[:-3]  # Remove .py
            site_name = module_name.replace("_worker", "")
            
            # Skip if already registered
            if site_name in SITE_WORKERS:
                continue
            
            try:
                # Import the module dynamically
                module = importlib.import_module(f"monitoring.workers.{module_name}")
                
                # Look for worker function (usually named {site}_worker)
                worker_func_name = f"{site_name}_worker"
                if hasattr(module, worker_func_name):
                    worker_func = getattr(module, worker_func_name)
                    
                    # Try to load site-specific config
                    config = {}
                    config_file = os.path.join(os.path.dirname(__file__), "..", "config", f"{site_name}_config.py")
                    if os.path.exists(config_file):
                        try:
                            config_module = importlib.import_module(f"monitoring.config.{site_name}_config")
                            if hasattr(config_module, "CONFIG"):
                                config = config_module.CONFIG
                        except Exception:
                            pass
                    
                    register_site(site_name, worker_func, config)
                    logger.info(f"Auto-discovered worker for: {site_name}")
                else:
                    logger.warning(f"No worker function found in {module_name}")
                    
            except Exception as e:
                logger.warning(f"Failed to load worker for {site_name}: {e}")


# Auto-discover workers when module loads
auto_discover_workers()