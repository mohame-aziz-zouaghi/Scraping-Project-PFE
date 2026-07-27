import json
import locale
import os
import subprocess
import sys
from Database.Redis.product_cache import ProductCache
from core.utils.clean_text_parser import clean_and_decode_text
from menu.monitoring_menu import monitoring_menu
from menu.redis_manager import RedisManager
from menu.preload_cache import preload_cache
from parsers.tunisianet.debug_tunisianet_product import extract_and_clean_tunisianet_product
from sites.loader import load_sites
from core.logger import setup_logger
from Database.Postgresql.load_data_db import run

sites = load_sites()
logger = setup_logger()



# ---------------------------------
# UI HELPERS
# ---------------------------------
def print_header(title):
    print("\n" + "=" * 40)
    print(f" {title}")
    print("=" * 40 + "\n")


def print_error(msg):
    print(f"❌ {msg}")
    logger.warning(f"CLI | {msg}")


def print_success(msg):
    print(f"✅ {msg}")
    logger.info(f"CLI | {msg}")


# ---------------------------------
# SITE SELECTION
# ---------------------------------
def choose_site():
    keys = list(sites.keys())

    while True:
        print_header("AVAILABLE SITES")

        for i, name in enumerate(keys, 1):
            print(f"{i} - {name}")

        choice = input("\nSelect site: ").strip()

        if not choice.isdigit():
            print_error(f"Invalid input (not a number): {choice}")
            continue

        choice = int(choice)

        if choice < 1 or choice > len(keys):
            print_error(f"Invalid selection: {choice}")
            continue

        site = sites[keys[choice - 1]]
        print_success(f"Selected site: {site.name}")
        return site
    

# ---------------------------------
# MENU
# ---------------------------------
def menu():

    print("\n=== SYSTEM DEBUG START ===")
    print("Python encoding:", sys.getdefaultencoding())
    print("Locale encoding:", locale.getpreferredencoding())
    print("==========================\n")
    while True:

        print_header("SCRAPER MAIN MENU")

        print("1 - Full Scrape Site")
        print("2 - Search Products")
        print("3 - Parse Product by URL")
        print("4 - Extract Categories")
        print("5 - Debug Tunisianet Product")
        print("10 - Load Data Into DB")
        print("11 - Test Redis")
        print("12 - Preoading Data to Redis")
        print("13 - 🗄️  Redis Management (NEW)") 
        print("30 - Start Monitoring Products")
        print("99 - Clean Text")
        print("0 - Exit")

        choice = input("\nChoice: ").strip()

        # ---------------------------------
        # FULL SCRAPE
        # ---------------------------------
        if choice == "1":
            site = choose_site()
            print_success(f"Starting full scrape: {site.name}")
            site.scrape_site()

        # ---------------------------------
        # SEARCH
        # ---------------------------------
        elif choice == "2":
            site = choose_site()
            query = input("Enter search query: ").strip()

            print_success(f"Searching: {query}")

            result = site.search(query)

            if result and isinstance(result, dict):
                products = result.get("products", [])
                total = result.get("total_results", len(products))

                print(f"\n🔎 Found {total} products\n")

                for i, p in enumerate(products[:10], 1):
                    link = p.get("product_link", "Unknown")
                    print(f"{i}. {link}")

                print_success("Search completed")
            else:
                print_error("Search failed or not implemented")

        # ---------------------------------
        # PRODUCT PARSER
        # ---------------------------------
        elif choice == "3":
            site = choose_site()
            url = input("Enter product URL: ").strip()

            print_success(f"Parsing product...")

            product = site.parse_product(url, None)

            if product:
                print(json.dumps(product, indent=4, ensure_ascii=False))
            else:
                print_error("Product parsing failed")

        # ---------------------------------
        # CATEGORIES
        # ---------------------------------
        elif choice == "4":
            site = choose_site()
            print_success(f"Extracting categories for {site.name}")
            site.extract_categories()

        # ---------------------------------
        # DEBUG TOOL
        # ---------------------------------
        elif choice == "5":
            url = input("Enter Tunisianet product URL: ").strip()
            print_success("Running debug parser...")
            extract_and_clean_tunisianet_product(url)


        # ---------------------------------
        # Start Monitoring
        # ---------------------------------
        elif choice == "30":
            print("\nStarting monitoring system...\n")

            monitoring_menu()   

        # ---------------------------------
        # Loading Data into the DB
        # ---------------------------------
        elif choice == "10":
            logger.info("CLI | Loading Data Started ! ")
            run()
            logger.info("CLI | Loading Data Competed ! ")

        # ---------------------------------
        # Testing Redis
        # ---------------------------------

        elif choice == "11":
            logger.info ("CLi | Testing Redis")
            cache = ProductCache()

            product = {
                "price": 500,
                "availability": "InStock",
                "special_id": "ABC123",
                "price_valid_until": "2027-01-01"
            }

            cache.set_product("MEGAPC", "123", product)

            print(
                cache.get_product("MEGAPC", "123")
            )


        # ---------------------------------
        # Prelloading Data into Redis 
        # ---------------------------------

        elif choice == "12":
            logger.info("CLI | Preloading Data Into Redis Started ")
            preload_cache()
            logger.info("CLI | Preloading Data Into Redis Completed Succesfully ")


        elif choice == "13":
            redis_manager = RedisManager()
            redis_manager.show_menu()

        elif choice =="99":
            text = input("Enter product URL: ").strip()
            print (clean_and_decode_text(text)) 

        # ---------------------------------
        # EXIT
        # ---------------------------------
        elif choice == "0":
            print_success("Exiting CLI")
            logger.info("CLI | exit")
            break

        else:
            print_error(f"Unknown option: {choice}")