from parsers.tunisianet.debug_tunisianet_product import extract_and_clean_tunisianet_product
from sites.loader import load_sites
from core.logger import setup_logger
import json

sites = load_sites()
logger = setup_logger()

def choose_site():
    """
    Show available ecommerce sites and allow the user to select one.
    Validates input and logs warnings for invalid choices.
    """
    keys = list(sites.keys())

    while True:
        print("\nAvailable Sites:")
        for i, name in enumerate(keys):
            print(f"{i+1} - {name}")

        choice = input("Select site: ")

        # Validate input
        if not choice.isdigit():
            print("❌ Invalid input, please enter a number.")
            logger.warning(f"CLI | invalid site input (non-digit): {choice}")
            continue

        choice_num = int(choice)

        if choice_num < 1 or choice_num > len(keys):
            print("❌ Invalid number, choose a number from the list.")
            logger.warning(f"CLI | invalid site number: {choice_num}")
            continue

        # Valid choice
        selected_site = sites[keys[choice_num - 1]]
        logger.info(f"CLI | site selected: {selected_site.name}")
        return selected_site


def menu():

    while True:

        print("\n===== SCRAPER MENU =====")

        print("1 - Scrape Ecommerce Site")
        print("2 - Search Products")
        print("3 - Parse Product by Link")
        print("4 - Extract Categories")
        print("5 - Debug Tunisianet Product (data-product)")
        print("0 - Exit")

        choice = input("Choice: ")

        if choice == "1":

            site = choose_site()

            logger.info(f"CLI | full scrape | {site.name}")

            site.scrape_site()

        elif choice == "2":

            site = choose_site()

            query = input("Enter search query: ")

            logger.info(f"CLI | search | {site.name} | {query}")

            products = site.search(query)

            if products :
                print(f"\n Found {(products)["total_results"]} products \n")
                logger.info(f"CLI | search_completed | {site.name} | {query}")
            else : 
                print("\n FEATURE_NOT_IMPLEMENTED_YET | ERROR HAPPENED ! \n")



        elif choice == "3":

            site = choose_site()

            url = input("Enter product URL: ")

            logger.info(f"CLI | product page | {site.name}")

            product = site.parse_product(url,None)

            if product :
                print(json.dumps(product, indent=4, ensure_ascii=False))
            else : 
                print("\n FEATURE_NOT_IMPLEMENTED_YET | ERROR HAPPENED ! \n")

        elif choice == "4":

            site = choose_site()

            logger.info(f"CLI | category extraction | {site.name}")

            site.extract_categories()

        elif choice == "5":

            url = input("Enter Tunisianet product URL: ")

            logger.info(f"CLI | debug tunisianet product | {url}")

            extract_and_clean_tunisianet_product(url)

        elif choice == "0":

            logger.info("CLI | exit")

            break