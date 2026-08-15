# scripts/run_scraping_dashboard.py
"""
Scraping Dashboard - Runs in a separate console window
"""
import sys
import os
import time
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraping.engine.scrape_engine import ScrapeEngine
from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
from sites.loader import load_sites
from core.logger import setup_logger
from core.shutdown_handler import get_shutdown_handler

logger = setup_logger("scraping_dashboard", "scraper")


def run_scraping_dashboard(site_name: str = None, categories: list = None):
    """
    Run the scraping dashboard in a separate console
    """
    print("\n" + "="*60)
    print("🕷️  SCRAPING DASHBOARD")
    print("="*60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    print("")
    print("📊 Dashboard is now live!")
    print("   Press Ctrl+C to stop scraping")
    print("")
    
    # Load sites
    sites = load_sites()
    
    # Setup shutdown handler
    shutdown_handler = get_shutdown_handler()
    
    engine = ScrapeEngine()
    engine.progress.start()
    
    try:
        if site_name:
            # Scrape specific site
            site = sites.get(site_name.lower())
            if not site:
                print(f"❌ Site '{site_name}' not found")
                print(f"   Available sites: {', '.join(sites.keys())}")
                engine.progress.stop()
                engine.progress.close()
                return
            
            print(f"🔄 Scraping: {site_name.upper()}")
            if categories:
                print(f"   Categories: {len(categories)}")
            
            result = engine.scrape_site(site_name, site, categories)
            
            # Keep dashboard open for a moment to show results
            time.sleep(2)
            
        else:
            # Scrape all sites
            print(f"🔄 Scraping all sites: {', '.join(sites.keys())}")
            site_list = []
            for site in sites.values():
                site_list.append((site.name, site, None))
            
            results = engine.scrape_multiple_sites(site_list)
            
            # Keep dashboard open to show results
            time.sleep(3)
        
    except KeyboardInterrupt:
        print("\n⏹️ Stopping scraping...")
        engine.stop_all()
        time.sleep(1)
    finally:
        engine.progress.stop()
        engine.progress.close()
        print("\n✅ Scraping dashboard closed")


def main():
    """Main entry point for the scraping dashboard"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Scraping Dashboard")
    parser.add_argument("--site", "-s", help="Site to scrape (optional, scrapes all if not specified)")
    parser.add_argument("--categories", "-c", nargs="+", help="Specific categories to scrape")
    
    args = parser.parse_args()
    
    run_scraping_dashboard(args.site, args.categories)


if __name__ == "__main__":
    main()