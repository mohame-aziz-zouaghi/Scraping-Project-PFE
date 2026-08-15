# menu/cli_menu.py
"""
Main CLI menu for Scraping & Monitoring System
Redesigned with better organization and flow
"""
import json
import locale
import os
import sys
import time

from Database.Redis.product_cache import ProductCache
from core.utils.clean_text_parser import clean_and_decode_text
from menu.monitoring_menu import monitoring_menu
from menu.scraping_menu import show_scraping_menu
from menu.redis_manager import RedisManager
from menu.preload_cache import preload_cache
from parsers.tunisianet.debug_tunisianet_product import extract_and_clean_tunisianet_product
from sites.loader import load_sites
from core.logger import setup_logger
from core.console_manager import get_console, open_python_script_terminal, Colors
from Database.Postgresql.load_data_db import run

# Load sites once
sites = load_sites()
logger = setup_logger()
console = get_console()


# =====================================================
# UI HELPERS
# =====================================================

def clear_screen():
    """Clear the terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_banner():
    """Print the application banner"""
    console.print("", Colors.CYAN)
    console.print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║                                                              ║
    ║     ████████╗██╗   ██╗███╗   ██╗██╗███████╗██╗ █████╗     ║
    ║     ╚══██╔══╝╚██╗ ██╔╝████╗  ██║██║██╔════╝██║██╔══██╗    ║
    ║        ██║    ╚████╔╝ ██╔██╗ ██║██║███████╗██║███████║    ║
    ║        ██║     ╚██╔╝  ██║╚██╗██║██║╚════██║██║██╔══██║    ║
    ║        ██║      ██║   ██║ ╚████║██║███████║██║██║  ██║    ║
    ║        ╚═╝      ╚═╝   ╚═╝  ╚═══╝╚═╝╚══════╝╚═╝╚═╝  ╚═╝    ║
    ║                                                              ║
    ║              SCRAPING & MONITORING SYSTEM                    ║
    ║                                                              ║
    ╚══════════════════════════════════════════════════════════════╝
    """, Colors.CYAN)
    console.print(f"   📅 {time.strftime('%Y-%m-%d %H:%M:%S')}", Colors.DIM)
    console.print(f"   📍 Sites Available: {len(sites)}", Colors.DIM)
    console.print("", Colors.DIM)


def print_header(title, subtitle=None):
    """Print a formatted header"""
    console.print("", Colors.CYAN)
    console.print(f"  ┌─ {title}", Colors.BOLD + Colors.CYAN)
    if subtitle:
        console.print(f"  │  {subtitle}", Colors.DIM)
    console.print(f"  └─{'─' * (len(title) + 2)}", Colors.DIM)


def print_error(msg):
    """Print an error message"""
    console.print(f"  ❌ {msg}", Colors.RED)
    logger.warning(f"CLI | {msg}")


def print_success(msg):
    """Print a success message"""
    console.print(f"  ✅ {msg}", Colors.GREEN)
    logger.info(f"CLI | {msg}")


def print_info(msg):
    """Print an info message"""
    console.print(f"  ℹ️  {msg}", Colors.CYAN)
    logger.info(f"CLI | {msg}")


def print_warning(msg):
    """Print a warning message"""
    console.print(f"  ⚠️  {msg}", Colors.YELLOW)
    logger.warning(f"CLI | {msg}")


def print_option(number, text, description=None, color=Colors.WHITE):
    """Print a formatted menu option"""
    if description:
        console.print(f"  {number}. {text:<30} {Colors.DIM}─ {description}{Colors.END}", color)
    else:
        console.print(f"  {number}. {text}", color)


def get_input(prompt, default=None):
    """Get user input with optional default"""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    
    value = input(f"  {Colors.CYAN}➜{Colors.END} {prompt}").strip()
    
    # Return default if value is empty, otherwise return value
    if not value and default is not None:
        return default
    return value if value else ""


def confirm_action(message):
    """Get confirmation from user"""
    response = get_input(f"{message} (y/N)")
    return response.lower() == 'y'


# =====================================================
# SITE SELECTION
# =====================================================

def choose_site():
    """Interactive site selection"""
    keys = list(sites.keys())
    
    if not keys:
        print_error("No sites available!")
        return None
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ AVAILABLE SITES", Colors.BOLD + Colors.CYAN)
    
    for i, name in enumerate(keys, 1):
        console.print(f"  │  {i}. {name.upper()}", Colors.WHITE)
    
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Select site number")
    
    if not choice.isdigit():
        print_error(f"Invalid input (not a number): {choice}")
        return None
    
    choice = int(choice)
    
    if choice < 1 or choice > len(keys):
        print_error(f"Invalid selection: {choice}")
        return None
    
    site = sites[keys[choice - 1]]
    print_success(f"Selected site: {site.name.upper()}")
    return site


# =====================================================
# MAIN MENU
# =====================================================

def show_main_menu():
    """Display the main menu"""
    clear_screen()
    print_banner()
    
    console.print("  ┌─ MAIN MENU", Colors.BOLD + Colors.CYAN)
    console.print("  │", Colors.DIM)
    console.print("  │  🕷️  SCRAPING OPERATIONS", Colors.BOLD + Colors.GREEN)
    print_option("1", "Full Scrape Site", "Scrape all products from a site", Colors.WHITE)
    print_option("2", "Search Products", "Search for products on a site", Colors.WHITE)
    print_option("3", "Parse Product by URL", "Test parse a single product", Colors.WHITE)
    print_option("4", "Extract Categories", "Extract all categories from a site", Colors.WHITE)
    print_option("5", "Debug Tunisianet Product", "Debug parser for Tunisianet", Colors.WHITE)
    print_option("6", "Scraping Dashboard Menu", "Advanced scraping with dashboard options", Colors.GREEN)
    console.print("  │", Colors.DIM)
    console.print("  │  📊 MONITORING OPERATIONS", Colors.BOLD + Colors.CYAN)
    print_option("7", "Monitoring Menu", "Open monitoring submenu", Colors.WHITE)
    console.print("  │", Colors.DIM)
    console.print("  │  🗄️  DATA MANAGEMENT", Colors.BOLD + Colors.MAGENTA)
    print_option("10", "Load Data Into DB", "Load scraped data to PostgreSQL", Colors.WHITE)
    print_option("11", "Test Redis", "Test Redis connection", Colors.WHITE)
    print_option("12", "Preload Redis Cache", "Preload Redis from PostgreSQL", Colors.WHITE)
    print_option("13", "Redis Management", "Manage Redis cache", Colors.WHITE)
    console.print("  │", Colors.DIM)
    console.print("  │  🛠️  UTILITIES", Colors.BOLD + Colors.YELLOW)
    print_option("99", "Clean Text", "Clean and decode text", Colors.WHITE)
    console.print("  │", Colors.DIM)
    console.print("  │  🚪 EXIT", Colors.BOLD + Colors.RED)
    print_option("0", "Exit", "Exit the application", Colors.RED)
    console.print("  │", Colors.DIM)
    console.print("  └─" + "─" * 40, Colors.DIM)


def menu():
    """Main menu loop"""
    # System debug info
    print("\n=== SYSTEM DEBUG START ===")
    print("Python encoding:", sys.getdefaultencoding())
    print("Locale encoding:", locale.getpreferredencoding())
    print("==========================\n")
    
    while True:
        show_main_menu()
        
        # Then in menu():
        choice = get_input("Choice").strip()
        if not choice:  # Handle empty input
            continue
        
        # =====================================================
        # SCRAPING OPERATIONS (Direct)
        # =====================================================
        
        # 1. FULL SCRAPE
        if choice == "1":
            clear_screen()
            print_header("FULL SCRAPE", "Scrape all products from a site")
            site = choose_site()
            if site:
                print_success(f"Starting full scrape: {site.name.upper()}")
                console.print("", Colors.DIM)
                site.scrape_site()
                console.print("", Colors.DIM)
                print_success(f"Scrape completed for {site.name.upper()}")
            input("\n  Press Enter to continue...")
        
        # 2. SEARCH
        elif choice == "2":
            clear_screen()
            print_header("SEARCH PRODUCTS", "Search for products on a site")
            site = choose_site()
            if site:
                query = get_input("Enter search query")
                if query:
                    print_success(f"Searching: {query}")
                    result = site.search(query)
                    
                    if result and isinstance(result, dict):
                        products = result.get("products", [])
                        total = result.get("total_results", len(products))
                        
                        console.print("", Colors.CYAN)
                        console.print(f"  🔎 Found {total} products", Colors.CYAN)
                        console.print("", Colors.DIM)
                        
                        for i, p in enumerate(products[:10], 1):
                            link = p.get("product_link", "Unknown")
                            console.print(f"  {i}. {link}", Colors.WHITE)
                        
                        if len(products) > 10:
                            console.print(f"  ... and {len(products) - 10} more", Colors.DIM)
                        
                        print_success("Search completed")
                    else:
                        print_error("Search failed or not implemented")
            input("\n  Press Enter to continue...")
        
        # 3. PARSE PRODUCT
        elif choice == "3":
            clear_screen()
            print_header("PARSE PRODUCT", "Parse a single product by URL")
            site = choose_site()
            if site:
                url = get_input("Enter product URL")
                if url:
                    print_success("Parsing product...")
                    product = site.parse_product(url, None)
                    
                    if product:
                        console.print("", Colors.GREEN)
                        console.print("  ✅ Product parsed successfully:", Colors.GREEN)
                        console.print("", Colors.DIM)
                        print(json.dumps(product, indent=4, ensure_ascii=False))
                    else:
                        print_error("Product parsing failed")
            input("\n  Press Enter to continue...")
        
        # 4. EXTRACT CATEGORIES
        elif choice == "4":
            clear_screen()
            print_header("EXTRACT CATEGORIES", "Extract all categories from a site")
            site = choose_site()
            if site:
                print_success(f"Extracting categories for {site.name.upper()}")
                site.extract_categories()
            input("\n  Press Enter to continue...")
        
        # 5. DEBUG TUNISIANET
        elif choice == "5":
            clear_screen()
            print_header("DEBUG TUNISIANET", "Debug parser for Tunisianet")
            url = get_input("Enter Tunisianet product URL")
            if url:
                print_success("Running debug parser...")
                extract_and_clean_tunisianet_product(url)
            input("\n  Press Enter to continue...")
        
        # =====================================================
        # SCRAPING DASHBOARD MENU (NEW)
        # =====================================================
        
        # 6. SCRAPING DASHBOARD MENU
        elif choice == "6":
            clear_screen()
            print_header("SCRAPING DASHBOARD", "Advanced scraping with dashboard")
            show_scraping_menu()
        
        # =====================================================
        # MONITORING MENU
        # =====================================================
        
        # 7. MONITORING MENU
        elif choice == "7":
            clear_screen()
            print_header("MONITORING SYSTEM", "Interactive monitoring submenu")
            monitoring_menu()
        
        # =====================================================
        # DATA MANAGEMENT
        # =====================================================
        
        # 10. LOAD DATA
        elif choice == "10":
            clear_screen()
            print_header("LOAD DATA INTO DB", "Load scraped data to PostgreSQL")
            if confirm_action("Are you sure you want to load data into the database"):
                print_success("Loading data...")
                run()
                print_success("Data loaded successfully!")
            else:
                print_warning("Cancelled")
            input("\n  Press Enter to continue...")
        
        # 11. TEST REDIS
        elif choice == "11":
            clear_screen()
            print_header("TEST REDIS", "Test Redis connection")
            print_success("Testing Redis...")
            cache = ProductCache()
            
            product = {
                "price": 500,
                "availability": "InStock",
                "special_id": "ABC123",
                "price_valid_until": "2027-01-01"
            }
            
            cache.set_product("MEGAPC", "123", product)
            result = cache.get_product("MEGAPC", "123")
            
            if result:
                print_success("Redis test passed!")
                console.print("", Colors.DIM)
                print(json.dumps(result, indent=4, ensure_ascii=False))
            else:
                print_error("Redis test failed!")
            input("\n  Press Enter to continue...")
        
        # 12. PRELOAD REDIS
        elif choice == "12":
            clear_screen()
            print_header("PRELOAD REDIS CACHE", "Preload Redis from PostgreSQL")
            if confirm_action("Are you sure you want to preload Redis cache"):
                print_success("Preloading Redis cache...")
                preload_cache()
                print_success("Redis cache preloaded successfully!")
            else:
                print_warning("Cancelled")
            input("\n  Press Enter to continue...")
        
        # 13. REDIS MANAGEMENT
        elif choice == "13":
            clear_screen()
            print_header("REDIS MANAGEMENT", "Manage Redis cache")
            redis_manager = RedisManager()
            redis_manager.show_menu()
        
        # =====================================================
        # UTILITIES
        # =====================================================
        
        # 99. CLEAN TEXT
        elif choice == "99":
            clear_screen()
            print_header("CLEAN TEXT", "Clean and decode text")
            text = get_input("Enter text to clean")
            if text:
                cleaned = clean_and_decode_text(text)
                print_success("Cleaned text:")
                console.print("", Colors.DIM)
                console.print(f"  {cleaned}", Colors.WHITE)
            input("\n  Press Enter to continue...")
        
        # =====================================================
        # EXIT
        # =====================================================
        
        elif choice == "0":
            clear_screen()
            console.print("", Colors.CYAN)
            console.print("  ╔═══════════════════════════════════════════════╗", Colors.CYAN)
            console.print("  ║                                               ║", Colors.CYAN)
            console.print("  ║    👋 Thank you for using the system!         ║", Colors.CYAN)
            console.print("  ║                                               ║", Colors.CYAN)
            console.print("  ╚═══════════════════════════════════════════════╝", Colors.CYAN)
            console.print("", Colors.DIM)
            
            print_success("Exiting CLI")
            logger.info("CLI | exit")
            
            # Flush all data before exit
            from core.shutdown_handler import shutdown
            shutdown(0)
            break
        
        # =====================================================
        # INVALID OPTION
        # =====================================================
        
        else:
            print_error(f"Unknown option: {choice}")
            time.sleep(1)


# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    menu()