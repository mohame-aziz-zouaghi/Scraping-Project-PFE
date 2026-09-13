# scraping/engine/scrape_engine.py
"""
Enhanced scraping engine with parallel site execution and progress tracking
"""
import time
import threading
from datetime import datetime
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.logger import setup_logger
from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
from sites.loader import load_sites

logger = setup_logger("scrape_engine", "scraper")


class ScrapeEngine:
    """Manages scraping for multiple sites in parallel with live dashboard"""
    
    def __init__(self):
        self.progress = get_multi_scrape_progress()
        self.running_sites = {}
        self.results = {}
        self._lock = threading.Lock()
        self._scraped_sites = set()
        
        # Track active site threads for independent cycle management
        self.active_cycles = {}
        self._cycle_lock = threading.Lock()
    
    def _run_single_cycle(self, site_name: str, site_instance, categories: List[str] = None):
        """
        Run a single scraping cycle for a site.
        This is called independently for each site and each cycle.
        """
        site = site_name.lower()
        
        try:
            # ─── RESET FOR NEW CYCLE ──────────────────────────────
            self.progress.reset_site(site)
            
            # Get total categories
            total_categories = self._get_total_categories(site, site_instance, categories)
            
            # ─── ADD SITE TO DASHBOARD ─────────────────────────────
            if site not in self._scraped_sites:
                self.progress.add_site(site, total_categories)
                self._scraped_sites.add(site)
                interval = getattr(site_instance, 'scrape_interval', 300)
                self.progress.set_next_scrape(site, interval)
            
            # ─── SET CYCLE STATUS ──────────────────────────────────
            cycle_num = self.progress.sites[site].get('cycle_count', 0)
            status_msg = f"🔄 Cycle {cycle_num + 1}"
            self.progress.set_status(site, status_msg)
            self.progress.set_current_item(site, "Starting...")
            logger.info(f"{site} | {status_msg}")
            
            start_time = time.time()
            
            with self._lock:
                self.running_sites[site] = True
            
            # ─── PATCH AND RUN SCRAPE ──────────────────────────────
            result = self._run_scrape_with_patches(site, site_instance, categories)
            
            # ─── PROCESS RESULTS ──────────────────────────────────
            elapsed = time.time() - start_time
            
            if result and isinstance(result, dict):
                final_stats = result.get('stats', {})
                new_count = final_stats.get('new_products', 0)
                
                self.progress.update(site, completed=total_categories)
                self.progress.set_status(site, f"✅ Complete - {new_count} new")
                self.progress.sites[site]['is_complete'] = True
                self.progress.sites[site]['is_running'] = False
                
                interval = getattr(site_instance, 'scrape_interval', 300)
                self.progress.set_next_scrape(site, interval)
                
                logger.info(f"{site} | Cycle {cycle_num + 1} complete | {new_count} new products")
                
                # ─── SCHEDULE NEXT CYCLE FOR THIS SITE ──────────────
                self._schedule_next_cycle(site, site_instance, categories, interval)
            
            return result
            
        except Exception as e:
            logger.error(f"{site} | CYCLE_ERROR | {e}")
            self.progress.set_status(site, f"❌ Error: {str(e)[:30]}")
            self.progress.sites[site]['is_running'] = False
            
            # Schedule retry
            interval = getattr(site_instance, 'scrape_interval', 300)
            self._schedule_next_cycle(site, site_instance, categories, interval)
            
            return {'site': site, 'error': str(e)}
        finally:
            with self._lock:
                self.running_sites[site] = False
    
    def _get_total_categories(self, site: str, site_instance, categories: List[str] = None) -> int:
        """Get total categories for a site."""
        total_categories = 0
        
        if hasattr(site_instance, 'CATEGORIES'):
            if isinstance(site_instance.CATEGORIES, dict):
                total_categories = len(site_instance.CATEGORIES)
            elif isinstance(site_instance.CATEGORIES, list):
                total_categories = len(site_instance.CATEGORIES)
        
        if categories:
            total_categories = len(categories)
        
        if total_categories == 0:
            # Try config files
            config_map = {
                "megapc": "config.categoriesMegaPC",
                "tunisianet": "config.categoriesTunisiaNet",
                "sbs": "config.categoriesSBS",
                "mytek": "config.categoriesMytek",
                "spacenet": "config.categoriesSpacenet",
                "skymil": "config.categoriesSkymil",
            }
            try:
                import importlib
                if site in config_map:
                    mod = importlib.import_module(config_map[site])
                    if hasattr(mod, 'CATEGORIES'):
                        cats = getattr(mod, 'CATEGORIES')
                        total_categories = len(cats) if isinstance(cats, (dict, list)) else 1
            except:
                pass
        
        if total_categories == 0:
            total_categories = 1
        
        return total_categories
    
    def _run_scrape_with_patches(self, site: str, site_instance, categories: List[str] = None) -> Dict:
        """Run the scrape with progress patches applied."""
        original_save = getattr(site_instance, '_save_new_product', None)
        original_stats = getattr(site_instance, '_update_stats', None)
        
        try:
            # ─── PATCH PRODUCT SAVING ───────────────────────────
            def progress_save_wrapper(product, product_id=None):
                if original_save:
                    result = original_save(product, product_id) if product_id else original_save(product)
                else:
                    result = False
                    if hasattr(site_instance, '_save_new_product'):
                        result = site_instance._save_new_product(product, product_id) if product_id else site_instance._save_new_product(product)
                
                if result:
                    self.progress.increment_new_product(site)
                return result
            
            # ─── PATCH STATS UPDATE ─────────────────────────────
            def progress_stats_wrapper(stats):
                if stats:
                    self.progress.update_stats(
                        site,
                        new_products=stats.get('new_products', 0),
                        skipped=stats.get('skipped_duplicates', 0),
                        errors=stats.get('errors', 0),
                        redis_saved=stats.get('redis_saved', 0),
                        postgres_saved=stats.get('postgres_saved', 0)
                    )
                    
                    completed_cats = stats.get('completed_categories', 0)
                    total_cats = stats.get('total_categories', 1)
                    self.progress.update(site, completed=completed_cats, total=total_cats)
                    
                    if 'current_category' in stats:
                        category = stats.get('current_category', '')
                        if '|' in category:
                            category = category.split('|')[0].strip()
                        if len(category) > 35:
                            category = category[:32] + '...'
                        if category and category != 'Starting...':
                            self.progress.set_current_item(site, f"📁 {category}")
                    
                    if 'current_product' in stats and stats.get('current_product'):
                        product = stats.get('current_product', '')
                        if '|' in product:
                            product = product.split('|')[0].strip()
                        if len(product) > 30:
                            product = product[:27] + '...'
                        if product:
                            self.progress.set_current_item(site, f"📦 {product}")
                
                if original_stats:
                    original_stats(stats)
            
            # ─── APPLY PATCHES ──────────────────────────────────
            if original_save:
                site_instance._save_new_product = progress_save_wrapper
            site_instance._update_stats = progress_stats_wrapper
            
            # ─── RUN SCRAPE ──────────────────────────────────────
            if categories and hasattr(site_instance, 'scrape_categories'):
                return site_instance.scrape_categories(categories)
            else:
                return site_instance.scrape_site()
                
        finally:
            if original_save:
                site_instance._save_new_product = original_save
            site_instance._update_stats = original_stats
    
    def _schedule_next_cycle(self, site: str, site_instance, categories: List[str] = None, interval: int = 300):
        """Schedule the next cycle for a site independently."""
        def run_next():
            time.sleep(interval)
            logger.info(f"{site} | Starting next cycle...")
            self._run_single_cycle(site, site_instance, categories)
        
        # Start a new thread for this site's next cycle
        thread = threading.Thread(target=run_next, daemon=True)
        thread.start()
        
        with self._cycle_lock:
            self.active_cycles[site] = thread
        
        logger.info(f"{site} | Next cycle scheduled in {interval}s")
    
    def scrape_site(self, site_name: str, site_instance, categories: List[str] = None) -> Dict:
        """
        Start scraping a site - this runs ONE cycle and then schedules the next.
        """
        site = site_name.lower()
        return self._run_single_cycle(site, site_instance, categories)
    
    def scrape_multiple_sites(self, sites: List[tuple], max_workers: int = None) -> Dict:
        """Scrape multiple sites in parallel - each site runs independently."""
        if max_workers is None:
            max_workers = min(len(sites), 4)
        
        self.progress.start()
        results = {}
        
        # Pre-add all sites to dashboard
        for site_name, site_instance, categories in sites:
            site = site_name.lower()
            total = self._get_total_categories(site, site_instance, categories)
            self.progress.add_site(site, total)
            logger.info(f"Pre-added site: {site} ({total} categories)")
        
        # Start each site in its own thread - they'll run independently
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for site_name, site_instance, categories in sites:
                logger.info(f"🚀 Starting {site_name} (will run continuously)")
                future = executor.submit(self.scrape_site, site_name, site_instance, categories)
                futures[future] = site_name
            
            # Wait for initial cycles to complete
            for future in as_completed(futures):
                site_name = futures[future]
                try:
                    result = future.result(timeout=3600)
                    results[site_name] = result
                    logger.info(f"✅ {site_name} initial cycle completed")
                except Exception as e:
                    logger.error(f"{site_name} | ERROR | {e}")
                    results[site_name] = {'site': site_name, 'error': str(e)}
                    self.progress.set_status(site_name, f"❌ Error")
        
        # Keep dashboard running - sites will continue their own cycles
        logger.info("All sites started. Each site will run independently.")
        return results
    
    def scrape_all_sites(self, categories: Dict[str, List[str]] = None) -> Dict:
        """Scrape all available sites - each runs independently."""
        sites_dict = load_sites()
        site_list = []
        
        for site_name, site_instance in sites_dict.items():
            categories_for_site = categories.get(site_name) if categories else None
            site_list.append((site_name, site_instance, categories_for_site))
        
        if not site_list:
            logger.warning("No sites loaded")
            return {}
        
        logger.info(f"🚀 Starting parallel scrape for {len(site_list)} sites (independent cycles)")
        return self.scrape_multiple_sites(site_list)
    
    def stop_all(self):
        """Stop all running scrapes"""
        with self._lock:
            for site in list(self.running_sites.keys()):
                self.running_sites[site] = False
                self.progress.set_status(site, "⏹️ Stopped")
        
        # Cancel scheduled cycles
        with self._cycle_lock:
            self.active_cycles.clear()
    
    def set_site_interval(self, site: str, interval: int):
        """Set the scrape interval for a site"""
        self.progress.set_next_scrape(site, interval)