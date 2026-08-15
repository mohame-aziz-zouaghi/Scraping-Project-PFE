# menu/scraping_menu.py
"""
Scraping menu - Interactive with option to open new terminal
"""
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.console_manager import get_console, open_python_script_terminal, Colors
from sites.loader import load_sites

logger = setup_logger("scraping_menu", "scraper")
console = get_console()

# Load sites once
sites = load_sites()


def print_header(title, subtitle=None):
    """Print a formatted header"""
    console.print("", Colors.CYAN)
    console.print(f"  ┌─ {title}", Colors.BOLD + Colors.CYAN)
    if subtitle:
        console.print(f"  │  {subtitle}", Colors.DIM)
    console.print(f"  └─{'─' * (len(title) + 2)}", Colors.DIM)


def print_error(msg):
    console.print(f"  ❌ {msg}", Colors.RED)


def print_success(msg):
    console.print(f"  ✅ {msg}", Colors.GREEN)


def print_info(msg):
    console.print(f"  ℹ️  {msg}", Colors.CYAN)


def print_warning(msg):
    console.print(f"  ⚠️  {msg}", Colors.YELLOW)


def print_option(number, text, description=None, color=Colors.WHITE):
    if description:
        console.print(f"  {number}. {text:<30} {Colors.DIM}─ {description}{Colors.END}", color)
    else:
        console.print(f"  {number}. {text}", color)


def get_input(prompt, default=None):
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    value = input(f"  {Colors.CYAN}➜{Colors.END} {prompt}").strip()
    return value if value else default


def get_all_site_names():
    """Get list of all site names from the sites dict"""
    return list(sites.keys())


def get_site_by_name(site_name: str):
    """Get site instance by name from the sites dict"""
    return sites.get(site_name.lower())


def get_categories_from_site(site):
    """Get categories from a site"""
    if hasattr(site, 'get_categories'):
        return site.get_categories()
    elif hasattr(site, 'categories'):
        return site.categories
    else:
        return []


def open_scraping_dashboard(site_name: str = None, categories: list = None):
    """
    Open a new terminal for scraping with dashboard
    """
    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                               "scraping", "run_scraper.py")
    
    args = ["--mode", "interactive"]
    if site_name:
        args.extend(["--site", site_name])
    if categories:
        args.extend(["--categories"] + categories)
    
    if site_name:
        title = f"🕷️ Scraping: {site_name.upper()}"
    else:
        title = "🕷️ Scraping: ALL SITES"
    
    console.print("", Colors.CYAN)
    console.print(f"  🚀 Launching {title}...", Colors.CYAN)
    console.print("  │  This will open a separate terminal window", Colors.DIM)
    console.print("  │  The scraping dashboard will appear there", Colors.DIM)
    console.print("  └─", Colors.DIM)
    
    success = open_python_script_terminal(script_path, args, title)
    
    if success:
        print_success("Scraping dashboard opened in new window")
    else:
        print_warning("Could not open new console. Running in current window...")
        from scraping.run_scraper import run_interactive_mode
        run_interactive_mode()
    
    input("\n  Press Enter to continue...")


def run_scrape_single_site():
    """Run scrape for a single site in current terminal"""
    site_names = get_all_site_names()
    
    if not site_names:
        print_error("No sites available")
        return
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ SELECT SITE TO SCRAPE", Colors.BOLD + Colors.CYAN)
    
    for i, name in enumerate(site_names, 1):
        console.print(f"  │  {i}. {name.upper()}", Colors.WHITE)
    
    console.print("  │  0. Back", Colors.DIM)
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Choose site")
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(site_names):
            site_name = site_names[idx]
            site = get_site_by_name(site_name)
            
            if site:
                console.print("", Colors.CYAN)
                console.print(f"  🚀 Scraping: {site_name.upper()}", Colors.CYAN)
                console.print("  │  Press Ctrl+C to stop", Colors.DIM)
                console.print("  └─", Colors.DIM)
                
                site.scrape_site()
                print_success(f"Scrape completed for {site_name.upper()}")
            else:
                print_error("Site not found")
        else:
            print_error("Invalid selection!")
    except ValueError:
        print_error("Invalid input!")
    input("\n  Press Enter to continue...")


def run_scrape_all_sites():
    """Scrape all sites in current terminal"""
    site_names = get_all_site_names()
    
    if not site_names:
        print_error("No sites available")
        return
    
    console.print("", Colors.CYAN)
    console.print(f"  🚀 Scraping all {len(site_names)} sites...", Colors.CYAN)
    console.print("  │  Press Ctrl+C to stop", Colors.DIM)
    console.print("  └─", Colors.DIM)
    
    for site_name in site_names:
        site = get_site_by_name(site_name)
        if site:
            console.print("", Colors.CYAN)
            console.print(f"  📍 Scraping: {site_name.upper()}", Colors.CYAN)
            site.scrape_site()
    
    print_success("All sites scraped successfully!")


def run_scrape_categories():
    """Scrape specific categories for a site in current terminal"""
    site_names = get_all_site_names()
    
    if not site_names:
        print_error("No sites available")
        return
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ SELECT SITE", Colors.BOLD + Colors.CYAN)
    
    for i, name in enumerate(site_names, 1):
        console.print(f"  │  {i}. {name.upper()}", Colors.WHITE)
    
    console.print("  │  0. Back", Colors.DIM)
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Choose site")
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(site_names):
            site_name = site_names[idx]
            site = get_site_by_name(site_name)
            
            if not site:
                print_error("Site not found")
                return
            
            categories = get_categories_from_site(site)
            
            if not categories:
                print_error(f"No categories available for {site_name}")
                return
            
            console.print("", Colors.CYAN)
            console.print(f"  ┌─ CATEGORIES FOR {site_name.upper()}", Colors.BOLD + Colors.CYAN)
            
            for i, cat in enumerate(categories, 1):
                console.print(f"  │  {i}. {cat}", Colors.WHITE)
            
            console.print(f"  └─{'─' * 30}", Colors.DIM)
            
            cat_choice = get_input("Enter category numbers (comma separated) or 'all'")
            
            if cat_choice.lower() == 'all':
                selected_categories = categories
            else:
                try:
                    indices = [int(x.strip()) - 1 for x in cat_choice.split(',')]
                    selected_categories = [categories[i] for i in indices if 0 <= i < len(categories)]
                except:
                    print_error("Invalid selection")
                    return
            
            if not selected_categories:
                print_error("No categories selected")
                return
            
            console.print("", Colors.CYAN)
            console.print(f"  🚀 Scraping {len(selected_categories)} categories for {site_name.upper()}", Colors.CYAN)
            console.print("  │  Press Ctrl+C to stop", Colors.DIM)
            console.print("  └─", Colors.DIM)
            
            # Scrape specific categories
            if hasattr(site, 'scrape_categories'):
                site.scrape_categories(selected_categories)
            else:
                print_warning("Site doesn't support category scraping. Running full scrape...")
                site.scrape_site()
            
            print_success(f"Scrape completed for {site_name.upper()}")
        else:
            print_error("Invalid selection!")
    except ValueError:
        print_error("Invalid input!")
    input("\n  Press Enter to continue...")


def show_scraping_menu():
    """Display the enhanced scraping menu"""
    while True:
        console.print("", Colors.CYAN)
        console.print("  ┌─ 🕷️ SCRAPING SYSTEM", Colors.BOLD + Colors.CYAN)
        console.print("  │", Colors.DIM)
        console.print("  │  RUN IN CURRENT TERMINAL:", Colors.BOLD)
        print_option("1", "Scrape a Single Site", "Scrape all products from a site", Colors.WHITE)
        print_option("2", "Scrape All Sites", "Scrape all available sites", Colors.WHITE)
        print_option("3", "Scrape Specific Categories", "Scrape specific categories", Colors.WHITE)
        console.print("  │", Colors.DIM)
        console.print("  │  RUN IN NEW TERMINAL:", Colors.BOLD)
        print_option("4", "Open Scraping Dashboard", "Full dashboard in new window", Colors.GREEN)
        print_option("5", "Open Single Site Dashboard", "Dashboard for one site", Colors.GREEN)
        console.print("  │", Colors.DIM)
        print_option("0", "Back to Main Menu", Colors.YELLOW)
        console.print("  └─" + "─" * 40, Colors.DIM)
        
        choice = get_input("Choice")
        
        if choice == "1":
            run_scrape_single_site()
        elif choice == "2":
            run_scrape_all_sites()
        elif choice == "3":
            run_scrape_categories()
        elif choice == "4":
            open_scraping_dashboard()
        elif choice == "5":
            site_names = get_all_site_names()
            if not site_names:
                print_error("No sites available")
                continue
            
            console.print("", Colors.CYAN)
            console.print("  ┌─ SELECT SITE FOR DASHBOARD", Colors.BOLD + Colors.CYAN)
            for i, name in enumerate(site_names, 1):
                console.print(f"  │  {i}. {name.upper()}", Colors.WHITE)
            console.print(f"  └─{'─' * 30}", Colors.DIM)
            
            choice_site = get_input("Choose site")
            try:
                idx = int(choice_site) - 1
                if 0 <= idx < len(site_names):
                    open_scraping_dashboard(site_names[idx])
                else:
                    print_error("Invalid selection!")
            except ValueError:
                print_error("Invalid input!")
        elif choice == "0":
            break
        else:
            print_error("Invalid choice!")
    
    return False


if __name__ == "__main__":
    show_scraping_menu()