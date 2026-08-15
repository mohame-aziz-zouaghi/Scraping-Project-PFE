# scraping/run_scraper.py
"""
Main entry point for scraping system
Supports interactive, headless, single-site, and management commands
"""
import sys
import os
import argparse
import threading
import time
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraping.engine.scrape_engine import ScrapeEngine
from scraping.controller import ScrapeController, get_scrape_controller
from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
from sites.loader import load_sites
from core.logger import setup_logger
from core.shutdown_handler import get_shutdown_handler
from core.console_manager import get_console, Colors

logger = setup_logger("run_scraper", "scraper")
console = get_console()


def run_interactive_mode():
    """Run scraping with interactive console - Multi-Progress Dashboard"""
    
    console.print("", Colors.CYAN)
    console.print("=" * 60, Colors.CYAN)
    console.print("🕷️ SCRAPING SYSTEM - MULTI-SITE DASHBOARD", Colors.CYAN)
    console.print("=" * 60, Colors.CYAN)
    console.print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", Colors.DIM)
    console.print("=" * 60, Colors.CYAN)
    console.print("")
    console.print("📊 All sites will be displayed in the dashboard below", Colors.CYAN)
    console.print("   Type 'help' for commands | Press Ctrl+C to stop all", Colors.DIM)
    console.print("")
    
    # Load sites
    sites = load_sites()
    
    if not sites:
        console.print_error("No sites loaded!")
        return
    
    console.print(f"📋 Sites: {', '.join([s.upper() for s in sites.keys()])}", Colors.CYAN)
    console.print("")
    
    engine = ScrapeEngine()
    controller = get_scrape_controller()
    controller.set_engine(engine)
    controller.start_console()
    
    console.print("", Colors.GREEN)
    console.print("🚀 Starting scraping dashboard...", Colors.GREEN)
    console.print("   Use 'start <site>' to scrape a site", Colors.DIM)
    console.print("   Use 'start all' to scrape all sites", Colors.DIM)
    console.print("   Use 'status' to see current status", Colors.DIM)
    console.print("")
    
    # Start multi-progress display
    multi_progress = get_multi_scrape_progress()
    multi_progress.start()
    
    console.print("", Colors.CYAN)
    console.print("=" * 60, Colors.CYAN)
    console.print("🟢 SYSTEM READY - Dashboard Active", Colors.GREEN)
    console.print("   Type 'help' for commands", Colors.DIM)
    console.print("   Press Ctrl+C to stop all", Colors.DIM)
    console.print("=" * 60, Colors.CYAN)
    console.print("")
    
    # Auto-start all sites after 2 second delay
    console.print("⏳ Auto-starting all sites in 2 seconds...", Colors.YELLOW)
    time.sleep(2)
    
    # Start all sites automatically
    console.print("🚀 Starting all sites...", Colors.GREEN)
    for site_name, site_instance in sites.items():
        # Get interval from site or use default
        interval = getattr(site_instance, 'scrape_interval', 300)
        engine.set_site_interval(site_name, interval)
    controller.start_all_scrapers()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("", Colors.RED)
        console.print("🛑 Shutting down...", Colors.RED)
        controller.stop_all()
        multi_progress.stop()
        multi_progress.close()
        console.print("✅ System stopped", Colors.GREEN)


def run_headless_mode():
    """Run scraping without interactive console (background mode)"""
    
    console.print("", Colors.CYAN)
    console.print("=" * 70, Colors.CYAN)
    console.print("🕷️ TUNISIAN TECH PRODUCTS SCRAPING SYSTEM (HEADLESS)", Colors.CYAN)
    console.print("=" * 70, Colors.CYAN)
    console.print("Running in background mode - no console", Colors.DIM)
    console.print("Press Ctrl+C to stop", Colors.DIM)
    console.print("=" * 70, Colors.CYAN)
    
    sites = load_sites()
    
    if not sites:
        console.print_error("No sites loaded!")
        return
    
    engine = ScrapeEngine()
    
    # Start multi-progress
    multi_progress = get_multi_scrape_progress()
    multi_progress.start()
    
    try:
        # Scrape all sites in parallel
        site_list = []
        for site_name, site_instance in sites.items():
            site_list.append((site_name, site_instance, None))
        
        if site_list:
            console.print(f"\n🔄 Scraping {len(site_list)} sites in parallel...", Colors.CYAN)
            results = engine.scrape_multiple_sites(site_list, max_workers=4)
            
            console.print("", Colors.CYAN)
            console.print("=" * 60, Colors.CYAN)
            console.print("📊 SCRAPING SUMMARY", Colors.CYAN)
            console.print("=" * 60, Colors.CYAN)
            
            total_new = 0
            total_errors = 0
            total_redis = 0
            total_postgres = 0
            
            for site_name, result in results.items():
                new_products = result.get('new_products', 0)
                errors = result.get('errors', 0)
                redis_saved = result.get('redis_saved', 0)
                postgres_saved = result.get('postgres_saved', 0)
                
                total_new += new_products
                total_errors += errors
                total_redis += redis_saved
                total_postgres += postgres_saved
                
                status = "✅" if new_products > 0 else "⏸️"
                console.print(f"   {status} {site_name.upper():15} | New: {new_products:4} | Redis: {redis_saved:4} | PG: {postgres_saved:4} | Errors: {errors:4}")
            
            console.print("=" * 60, Colors.CYAN)
            console.print(f"   Total new products: {total_new}", Colors.GREEN)
            console.print(f"   Total Redis saved: {total_redis}", Colors.WHITE)
            console.print(f"   Total Postgres saved: {total_postgres}", Colors.WHITE)
            console.print(f"   Total errors: {total_errors}", Colors.RED)
            console.print("=" * 60, Colors.CYAN)
        else:
            console.print("❌ No sites available", Colors.RED)
            
    except KeyboardInterrupt:
        console.print("\n\n🛑 Shutting down...", Colors.RED)
        engine.stop_all()
    finally:
        multi_progress.stop()
        multi_progress.close()
        console.print("✅ System stopped", Colors.GREEN)


def run_single_site_mode(site_name: str, categories: list = None):
    """Run scraping for a single site with multi-progress"""
    
    sites = load_sites()
    
    if site_name not in sites:
        console.print(f"\n❌ Site '{site_name}' not found!", Colors.RED)
        console.print(f"   Available sites: {', '.join(sites.keys())}", Colors.DIM)
        return
    
    console.print("", Colors.CYAN)
    console.print("=" * 70, Colors.CYAN)
    console.print(f"🕷️ SCRAPING: {site_name.upper()}", Colors.CYAN)
    console.print("=" * 70, Colors.CYAN)
    if categories:
        console.print(f"   Categories: {len(categories)}", Colors.DIM)
    console.print("Press Ctrl+C to stop", Colors.DIM)
    console.print("=" * 70, Colors.CYAN)
    console.print("")
    
    engine = ScrapeEngine()
    multi_progress = get_multi_scrape_progress()
    multi_progress.start()
    
    try:
        site = sites[site_name]
        result = engine.scrape_site(site_name, site, categories)
        
        console.print("", Colors.GREEN)
        console.print(f"✅ Scraping complete for {site_name.upper()}", Colors.GREEN)
        console.print(f"   New products: {result.get('new_products', 0)}", Colors.WHITE)
        console.print(f"   Skipped: {result.get('skipped', 0)}", Colors.WHITE)
        console.print(f"   Errors: {result.get('errors', 0)}", Colors.WHITE)
        console.print(f"   Redis saved: {result.get('redis_saved', 0)}", Colors.WHITE)
        console.print(f"   Postgres saved: {result.get('postgres_saved', 0)}", Colors.WHITE)
        
    except KeyboardInterrupt:
        console.print(f"\n\n🛑 Scraping stopped for {site_name.upper()}", Colors.RED)
        engine.stop_all()
    finally:
        multi_progress.stop()
        multi_progress.close()


def list_sites():
    """List all available sites"""
    sites = load_sites()
    
    if not sites:
        console.print("\n❌ No sites available!", Colors.RED)
        return
    
    console.print("", Colors.CYAN)
    console.print("=" * 60, Colors.CYAN)
    console.print("📋 AVAILABLE SITES", Colors.CYAN)
    console.print("=" * 60, Colors.CYAN)
    
    for i, (name, site) in enumerate(sites.items(), 1):
        console.print(f"   {i}. {name.upper()}", Colors.WHITE)
    
    console.print("=" * 60, Colors.CYAN)


def main():
    """Main entry point with all commands"""
    parser = argparse.ArgumentParser(
        description="Tunisian Tech Products Scraping System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run modes
  python run_scraper.py --mode interactive     # Interactive console mode
  python run_scraper.py --mode headless        # Headless mode
  python run_scraper.py --mode single --site megapc  # Single site mode
  
  # Management commands
  python run_scraper.py --list-sites           # List all available sites
  
  # Quick info
  python run_scraper.py --help                 # Show this help
        """
    )
    
    # Run modes
    parser.add_argument("--mode", "-m", type=str, choices=["interactive", "headless", "single"], 
                        help="Run mode: interactive, headless, or single")
    parser.add_argument("--site", "-s", type=str, help="Specific site to scrape (for single mode)")
    parser.add_argument("--categories", "-c", nargs="+", help="Specific categories to scrape")
    
    # Management commands
    parser.add_argument("--list-sites", action="store_true", help="List all available sites")
    
    args = parser.parse_args()
    
    # Handle management commands (exit after)
    if args.list_sites:
        list_sites()
        return
    
    # Handle run modes
    if args.mode == "interactive":
        run_interactive_mode()
    elif args.mode == "headless":
        run_headless_mode()
    elif args.mode == "single" and args.site:
        run_single_site_mode(args.site, args.categories)
    elif args.mode == "single" and not args.site:
        console.print("❌ Please specify --site for single mode", Colors.RED)
        parser.print_help()
    else:
        # Default: show help
        parser.print_help()


if __name__ == "__main__":
    main()