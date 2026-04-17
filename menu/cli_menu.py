import json
from parsers.tunisianet.debug_tunisianet_product import extract_and_clean_tunisianet_product
from sites.loader import load_sites
from core.logger import setup_logger

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

    while True:

        print_header("SCRAPER MAIN MENU")

        print("1 - Full Scrape Site")
        print("2 - Search Products")
        print("3 - Parse Product by URL")
        print("4 - Extract Categories")
        print("5 - Debug Tunisianet Product")
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
        # EXIT
        # ---------------------------------
        elif choice == "0":
            print_success("Exiting CLI")
            logger.info("CLI | exit")
            break

        else:
            print_error(f"Unknown option: {choice}")