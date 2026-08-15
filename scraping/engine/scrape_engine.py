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
    
    def scrape_site(self, site_name: str, site_instance, categories: List[str] = None) -> Dict:
        """
        Scrape a single site with progress tracking
        """
        site = site_name.lower()

        self.progress.reset_site(site)
        
        # GET TOTAL CATEGORIES
        total_categories = 0
        
        if hasattr(site_instance, 'CATEGORIES'):
            if isinstance(site_instance.CATEGORIES, dict):
                total_categories = len(site_instance.CATEGORIES)
                logger.info(f"{site} | Found CATEGORIES dict: {total_categories}")
            elif isinstance(site_instance.CATEGORIES, list):
                total_categories = len(site_instance.CATEGORIES)
                logger.info(f"{site} | Found CATEGORIES list: {total_categories}")
        
        if categories:
            total_categories = len(categories)
            logger.info(f"{site} | Using provided categories: {total_categories}")
        
        if total_categories == 0:
            try:
                from config.categoriesMegaPC import CATEGORIES as MEGAPC_CATS
                if site == "megapc":
                    total_categories = len(MEGAPC_CATS)
            except:
                pass
            
            try:
                from config.categoriesTunisiaNet import CATEGORIES as TUNISIANET_CATS
                if site == "tunisianet":
                    total_categories = len(TUNISIANET_CATS)
            except:
                pass
            
            try:
                from config.categoriesSBS import CATEGORIES as SBS_CATS
                if site == "sbs":
                    total_categories = len(SBS_CATS)
            except:
                pass
        
        if total_categories == 0:
            total_categories = 1
            logger.warning(f"{site} | No categories found, setting total to 1")
        
        # ADD SITE TO DASHBOARD (ONLY ONCE)
        if site not in self._scraped_sites:
            self.progress.add_site(site, total_categories)
            self.progress.set_status(site, "🔄 Starting...")
            self.progress.set_current_item(site, "Starting...")
            self._scraped_sites.add(site)
            logger.info(f"{site} | Site added to dashboard")
            
            interval = getattr(site_instance, 'scrape_interval', 300)
            self.progress.set_next_scrape(site, interval)
        else:
            if total_categories > self.progress.sites[site]['total']:
                self.progress.update(site, total=total_categories)
        
        start_time = time.time()
        
        with self._lock:
            self.running_sites[site] = True
        
        try:
            original_save = getattr(site_instance, '_save_new_product', None)
            original_stats = getattr(site_instance, '_update_stats', None)
            
            # PATCH PRODUCT SAVING
            def progress_save_wrapper(product, product_id=None):
                if original_save:
                    if product_id is not None:
                        result = original_save(product, product_id)
                    else:
                        result = original_save(product)
                else:
                    result = False
                    if hasattr(site_instance, '_save_new_product'):
                        if product_id is not None:
                            result = site_instance._save_new_product(product, product_id)
                        else:
                            result = site_instance._save_new_product(product)
                
                if result:
                    self.progress.increment_new_product(site)
                    # DO NOT increment stats here - _save_new_product already does it
                return result
            
            # PATCH STATS UPDATE
            # scraping/engine/scrape_engine.py - Update progress_stats_wrapper

            def progress_stats_wrapper(stats):
                if stats:
                    # Update dashboard stats
                    self.progress.update_stats(
                        site,
                        new_products=stats.get('new_products', 0),
                        skipped=stats.get('skipped_duplicates', 0),
                        errors=stats.get('errors', 0),
                        redis_saved=stats.get('redis_saved', 0),
                        postgres_saved=stats.get('postgres_saved', 0)
                    )
                    
                    # Update category progress - GET TOTAL FROM STATS
                    completed_cats = stats.get('completed_categories', 0)
                    total_cats = stats.get('total_categories', 1)
                    
                    # Always update with both completed and total
                    self.progress.update(
                        site,
                        completed=completed_cats,
                        total=total_cats
                    )
                    
                    # Update current category - show clean category name
                    if 'current_category' in stats:
                        category = stats.get('current_category', '')
                        # Clean up category name
                        if '|' in category:
                            category = category.split('|')[0].strip()
                        # Truncate if too long
                        if len(category) > 35:
                            category = category[:32] + '...'
                        self.progress.set_current_item(site, f"📁 {category}")
                    
                    # Update current product progress
                    if 'current_product' in stats and stats.get('current_product'):
                        product = stats.get('current_product', '')
                        # Show product progress but keep it brief
                        self.progress.set_current_item(site, f"📦 {product[:30]}")
                
                if original_stats:
                    original_stats(stats)
            
            # APPLY PATCHES
            if original_save:
                site_instance._save_new_product = progress_save_wrapper
            
            site_instance._update_stats = progress_stats_wrapper
            
            # RUN THE SCRAPE
            self.progress.set_status(site, "🔄 Running")
            
            if categories and hasattr(site_instance, 'scrape_categories'):
                result = site_instance.scrape_categories(categories)
            else:
                result = site_instance.scrape_site()
            
            # PROCESS RESULTS
            if isinstance(result, dict) and 'stats' in result:
                stats = result['stats']
                self.progress.update_stats(
                    site,
                    new_products=stats.get('new_products', 0),
                    skipped=stats.get('skipped_duplicates', 0),
                    errors=stats.get('errors', 0),
                    redis_saved=stats.get('redis_saved', 0),
                    postgres_saved=stats.get('postgres_saved', 0)
                )
                self.progress.update(site, completed=total_categories)
            
            # Flush pending products
            if hasattr(site_instance, 'flush_pending_batch'):
                try:
                    site_instance.flush_pending_batch()
                except Exception as e:
                    logger.warning(f"{site} | FLUSH_ERROR | {e}")
            
            # FINAL PROGRESS UPDATE
            final_stats = site_instance.stats if hasattr(site_instance, 'stats') else {}
            
            self.progress.update(site, completed=total_categories)
            self.progress.set_status(site, f"✅ Complete - {final_stats.get('new_products', 0)} new")
            self.progress.sites[site]['is_complete'] = True
            
            interval = getattr(site_instance, 'scrape_interval', 300)
            self.progress.set_next_scrape(site, interval)
            self.progress.update(site, cycle_count=self.progress.sites[site].get('cycle_count', 0) + 1)
            
            return {
                'site': site,
                'new_products': final_stats.get('new_products', 0),
                'skipped': final_stats.get('skipped_duplicates', 0),
                'errors': final_stats.get('errors', 0),
                'redis_saved': final_stats.get('redis_saved', 0),
                'postgres_saved': final_stats.get('postgres_saved', 0),
                'elapsed': round(time.time() - start_time, 2)
            }
            
        except Exception as e:
            self.progress.set_status(site, f"❌ Error: {str(e)[:30]}")
            logger.error(f"{site} | SCRAPE_ERROR | {e}")
            import traceback
            traceback.print_exc()
            
            if hasattr(site_instance, 'flush_pending_batch'):
                try:
                    site_instance.flush_pending_batch()
                except:
                    pass
            
            return {
                'site': site,
                'error': str(e),
                'elapsed': round(time.time() - start_time, 2)
            }
        finally:
            if original_save:
                site_instance._save_new_product = original_save
            
            with self._lock:
                self.running_sites[site] = False
    
    def scrape_multiple_sites(self, sites: List[tuple], max_workers: int = None) -> Dict:
        """Scrape multiple sites in parallel"""
        if max_workers is None:
            max_workers = min(len(sites), 4)
        
        self.progress.start()
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for site_name, site_instance, categories in sites:
                logger.info(f"🚀 Submitting {site_name} for scraping...")
                future = executor.submit(self.scrape_site, site_name, site_instance, categories)
                futures[future] = site_name
            
            for future in as_completed(futures):
                site_name = futures[future]
                try:
                    result = future.result(timeout=3600)
                    results[site_name] = result
                    logger.info(f"✅ {site_name} completed: {result.get('new_products', 0)} new products")
                except Exception as e:
                    logger.error(f"{site_name} | SCRAPE_FUTURE_ERROR | {e}")
                    results[site_name] = {'site': site_name, 'error': str(e)}
                    self.progress.set_status(site_name, f"❌ Error: {str(e)[:30]}")
        
        self.progress.stop()
        self.progress.close()
        return results
    
    def scrape_all_sites(self, categories: Dict[str, List[str]] = None) -> Dict:
        """Scrape all available sites in parallel"""
        sites_dict = load_sites()
        site_list = []
        
        for site_name, site_instance in sites_dict.items():
            categories_for_site = categories.get(site_name) if categories else None
            site_list.append((site_name, site_instance, categories_for_site))
        
        if not site_list:
            logger.warning("No sites loaded")
            return {}
        
        logger.info(f"🚀 Starting parallel scrape for {len(site_list)} sites")
        return self.scrape_multiple_sites(site_list)
    
    def stop_all(self):
        """Stop all running scrapes"""
        with self._lock:
            for site in list(self.running_sites.keys()):
                self.running_sites[site] = False
                self.progress.set_status(site, "⏹️ Stopped")
    
    def set_site_interval(self, site: str, interval: int):
        """Set the scrape interval for a site"""
        self.progress.set_next_scrape(site, interval)