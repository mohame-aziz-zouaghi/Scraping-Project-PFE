# menu/monitoring_menu.py
"""
Monitoring menu - Interactive with option to open new terminal
"""
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from core.console_manager import get_console, open_python_script_terminal, Colors
from monitoring.engine.site_registry import register_all_sites, get_all_sites, print_registry_status

logger = setup_logger("monitoring_menu", "cli")
console = get_console()


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


def confirm_action(message):
    response = get_input(f"{message} (y/N)")
    return response.lower() == 'y'


def open_monitoring_dashboard(mode="interactive"):
    """Open monitoring dashboard in a new terminal window"""
    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                               "monitoring", "run_monitor.py")
    
    console.print("", Colors.CYAN)
    console.print(f"  🚀 Launching Monitoring System in new terminal...", Colors.CYAN)
    console.print("  │  This will open a separate window", Colors.DIM)
    console.print("  │  The monitoring dashboard will appear there", Colors.DIM)
    console.print("  │  Type 'help' for commands", Colors.DIM)
    console.print("  └─", Colors.DIM)
    
    success = open_python_script_terminal(
        script_path, 
        ["--mode", mode], 
        "📊 Monitoring System"
    )
    
    if success:
        print_success("Monitoring console opened in new window")
    else:
        print_warning("Could not open new console. Running in current window...")
        from monitoring.run_monitor import run_interactive_mode 
        if mode == "interactive":
            from monitoring.run_monitor import run_interactive_mode
            run_interactive_mode()
        else:
            from monitoring.run_monitor import run_headless_mode
            run_headless_mode()
    
    input("\n  Press Enter to continue...")


def run_all_sites_headless():
    """Run monitoring for all sites in headless mode (current terminal)"""
    console.print("", Colors.CYAN)
    console.print("  🚀 Starting Monitoring System (Headless Mode)...", Colors.CYAN)
    console.print("  │  All enabled sites will be monitored", Colors.DIM)
    console.print("  │  Press Ctrl+C to stop monitoring", Colors.DIM)
    console.print("  └─", Colors.DIM)
    
    from monitoring.run_monitor import run_headless_mode
    try:
        run_headless_mode()
    except KeyboardInterrupt:
        console.print("", Colors.RED)
        console.print("  🛑 Monitoring stopped by user", Colors.RED)
    except Exception as e:
        print_error(f"Error: {e}")


def run_single_site():
    """Run monitoring for a single site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print_error("No sites registered!")
        return
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ SELECT SITE TO MONITOR", Colors.BOLD + Colors.CYAN)
    
    for i, site in enumerate(sites, 1):
        from monitoring.engine.site_registry import get_site_config
        config = get_site_config(site)
        enabled = config.get("enabled", True)
        status = "✅" if enabled else "⏸️"
        console.print(f"  │  {i}. {status} {site.upper()}", Colors.WHITE)
    
    console.print("  │  0. Back", Colors.DIM)
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Choose site")
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            
            from monitoring.engine.site_registry import get_site_config, update_site_config
            current_config = get_site_config(site)
            
            console.print("", Colors.CYAN)
            console.print(f"  📋 Monitoring: {site.upper()}", Colors.CYAN)
            console.print(f"  │  Current workers: {current_config.get('workers', 20)}", Colors.DIM)
            console.print(f"  │  Current interval: {current_config.get('interval', 300)}s", Colors.DIM)
            console.print("  └─", Colors.DIM)
            
            workers_input = get_input("Workers", current_config.get('workers', 20))
            workers = int(workers_input) if workers_input else current_config.get('workers', 20)
            
            interval_input = get_input("Interval (seconds)", current_config.get('interval', 300))
            interval = int(interval_input) if interval_input else current_config.get('interval', 300)
            
            if workers != current_config.get('workers'):
                update_site_config(site, workers=workers)
            if interval != current_config.get('interval'):
                update_site_config(site, interval=interval)
            
            console.print("", Colors.CYAN)
            console.print(f"  🚀 Starting monitoring for {site.upper()}...", Colors.CYAN)
            console.print("  │  Press Ctrl+C to stop", Colors.DIM)
            console.print("  └─", Colors.DIM)
            
            from monitoring.run_monitor import run_single_site_mode
            try:
                run_single_site_mode(site, workers, interval)
            except KeyboardInterrupt:
                console.print("", Colors.RED)
                console.print(f"  🛑 Monitoring stopped for {site.upper()}", Colors.RED)
        else:
            print_error("Invalid selection!")
    except ValueError:
        print_error("Invalid input!")


def enable_site():
    """Enable a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print_error("No sites registered!")
        return
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ ENABLE SITE", Colors.BOLD + Colors.CYAN)
    
    for i, site in enumerate(sites, 1):
        console.print(f"  │  {i}. {site.upper()}", Colors.WHITE)
    
    console.print("  │  0. Back", Colors.DIM)
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Choose site to enable")
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            from monitoring.run_monitor import enable_site
            enable_site(site)
            print_success(f"{site.upper()} has been enabled")
        else:
            print_error("Invalid selection!")
    except ValueError:
        print_error("Invalid input!")
    input("\n  Press Enter to continue...")


def disable_site():
    """Disable a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print_error("No sites registered!")
        return
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ DISABLE SITE", Colors.BOLD + Colors.CYAN)
    
    for i, site in enumerate(sites, 1):
        console.print(f"  │  {i}. {site.upper()}", Colors.WHITE)
    
    console.print("  │  0. Back", Colors.DIM)
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Choose site to disable")
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            from monitoring.run_monitor import disable_site
            disable_site(site)
            print_warning(f"{site.upper()} has been disabled")
        else:
            print_error("Invalid selection!")
    except ValueError:
        print_error("Invalid input!")
    input("\n  Press Enter to continue...")


def show_site_config():
    """Show configuration for a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print_error("No sites registered!")
        return
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ VIEW SITE CONFIGURATION", Colors.BOLD + Colors.CYAN)
    
    for i, site in enumerate(sites, 1):
        console.print(f"  │  {i}. {site.upper()}", Colors.WHITE)
    
    console.print("  │  0. Back", Colors.DIM)
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Choose site")
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            from monitoring.run_monitor import show_site_config
            show_site_config(site)
        else:
            print_error("Invalid selection!")
    except ValueError:
        print_error("Invalid input!")
    input("\n  Press Enter to continue...")


def update_site_config_menu():
    """Update configuration for a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print_error("No sites registered!")
        return
    
    console.print("", Colors.CYAN)
    console.print("  ┌─ UPDATE SITE CONFIGURATION", Colors.BOLD + Colors.CYAN)
    
    for i, site in enumerate(sites, 1):
        console.print(f"  │  {i}. {site.upper()}", Colors.WHITE)
    
    console.print("  │  0. Back", Colors.DIM)
    console.print(f"  └─{'─' * 30}", Colors.DIM)
    
    choice = get_input("Choose site")
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            
            from monitoring.engine.site_registry import get_site_config
            current_config = get_site_config(site)
            
            console.print("", Colors.CYAN)
            console.print(f"  📋 Current configuration for {site.upper()}:", Colors.CYAN)
            for key, value in current_config.items():
                console.print(f"  │  {key}: {value}", Colors.DIM)
            console.print("  └─", Colors.DIM)
            
            workers = get_input("Workers", current_config.get('workers', 20))
            interval = get_input("Interval (seconds)", current_config.get('interval', 300))
            
            updates = {}
            if workers and workers != str(current_config.get('workers', 20)):
                updates['workers'] = int(workers)
            if interval and interval != str(current_config.get('interval', 300)):
                updates['interval'] = int(interval)
            
            if updates:
                from monitoring.run_monitor import update_config
                for key, value in updates.items():
                    update_config(site, key, str(value))
                print_success("Configuration updated!")
            else:
                print_warning("No changes made")
        else:
            print_error("Invalid selection!")
    except ValueError:
        print_error("Invalid input!")
    input("\n  Press Enter to continue...")


def show_status():
    """Show monitoring system status"""
    from monitoring.run_monitor import show_status
    show_status()
    input("\n  Press Enter to continue...")


def list_sites():
    """List all registered sites"""
    from monitoring.run_monitor import list_sites
    list_sites()
    input("\n  Press Enter to continue...")


def monitoring_menu():
    """Display monitoring submenu"""
    while True:
        console.print("", Colors.CYAN)
        console.print("  ┌─ 📡 MONITORING SYSTEM", Colors.BOLD + Colors.CYAN)
        console.print("  │", Colors.DIM)
        console.print("  │  RUN OPTIONS:", Colors.BOLD)
        print_option("1", "Run All Sites (Interactive Dashboard)", "New window with full dashboard", Colors.WHITE)
        print_option("2", "Run All Sites (Headless)", "Current window, no dashboard", Colors.WHITE)
        print_option("3", "Run Single Site", "Current window, choose site", Colors.WHITE)
        console.print("  │", Colors.DIM)
        console.print("  │  MANAGEMENT:", Colors.BOLD)
        print_option("4", "List All Sites", "Show all registered sites", Colors.WHITE)
        print_option("5", "Show System Status", "View monitoring status", Colors.WHITE)
        print_option("6", "Enable a Site", "Enable a disabled site", Colors.WHITE)
        print_option("7", "Disable a Site", "Disable an enabled site", Colors.WHITE)
        print_option("8", "View Site Configuration", "Show site settings", Colors.WHITE)
        print_option("9", "Update Site Configuration", "Change site settings", Colors.WHITE)
        console.print("  │", Colors.DIM)
        print_option("0", "Back to Main Menu", Colors.YELLOW)
        console.print("  └─" + "─" * 40, Colors.DIM)
        
        choice = get_input("Choice")
        
        if choice == "1":
            open_monitoring_dashboard("interactive")
        elif choice == "2":
            run_all_sites_headless()
        elif choice == "3":
            run_single_site()
        elif choice == "4":
            list_sites()
        elif choice == "5":
            show_status()
        elif choice == "6":
            enable_site()
        elif choice == "7":
            disable_site()
        elif choice == "8":
            show_site_config()
        elif choice == "9":
            update_site_config_menu()
        elif choice == "0":
            break
        else:
            print_error("Invalid choice!")
    
    return False


if __name__ == "__main__":
    monitoring_menu()