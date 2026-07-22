# menu/monitoring_menu.py
import sys
import os
import subprocess
import platform
import threading
import time
import signal

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from monitoring.engine.site_registry import register_all_sites, get_all_sites, print_registry_status

logger = setup_logger("monitoring_menu", "cli")

# Global flag for monitoring status
monitoring_process = None
monitoring_running = False


def open_new_terminal(command):
    """Open a new terminal window with the given command"""
    system = platform.system()
    
    if system == "Windows":
        # Create a temporary batch file
        bat_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp_monitor.bat")
        
        # Write the batch file content with cleaner UI
        bat_content = f'''@echo off
title Monitoring System
color 0A
echo.
echo ============================================================
echo     TUNISIAN TECH PRODUCTS MONITORING SYSTEM
echo ============================================================
echo.
echo [INFO] Starting monitoring system...
echo.
{command}
echo.
echo ============================================================
echo [SUCCESS] Monitoring system closed.
echo ============================================================
echo.
echo Press any key to close this window...
pause > nul
del "%~f0"
'''
        
        with open(bat_path, 'w', encoding='utf-8') as f:
            f.write(bat_content)
        
        # Open a new cmd window with the batch file
        subprocess.Popen(f'start "Monitoring System" cmd /c "{bat_path}"', shell=True)
        
    elif system == "Linux":
        command = f'''
echo "============================================================"
echo "    TUNISIAN TECH PRODUCTS MONITORING SYSTEM"
echo "============================================================"
echo.
{command}
echo.
echo "============================================================"
echo "✅ Monitoring system closed."
echo "============================================================"
echo.
echo "Press Enter to close..."
read'''
        subprocess.Popen(["gnome-terminal", "--", "bash", "-c", command])
    elif system == "Darwin":  # macOS
        command = f'''
echo "============================================================"
echo "    TUNISIAN TECH PRODUCTS MONITORING SYSTEM"
echo "============================================================"
echo.
{command}
echo.
echo "============================================================"
echo "✅ Monitoring system closed."
echo "============================================================"
echo.
echo "Press any key to close..."
read'''
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{command}"'])


def run_all_sites_interactive():
    """Run monitoring for all sites in interactive mode (new terminal)"""
    global monitoring_process, monitoring_running
    
    python_exe = sys.executable
    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "monitoring", "run_monitor.py")
    
    command = f'"{python_exe}" "{script_path}" --mode interactive'
    
    print("\n🚀 Launching Monitoring System in new terminal...")
    print("   This will open a separate Command Prompt window")
    print("   Type 'stop all' to stop all monitors")
    print("   Type 'exit' to close the console")
    print("   Close that window to stop monitoring\n")
    
    open_new_terminal(command)
    input("   Press Enter to return to main menu...")


def run_all_sites_headless():
    """Run monitoring for all sites in headless mode (current terminal)"""
    global monitoring_process, monitoring_running
    
    print("\n🚀 Starting Monitoring System (Headless Mode)...")
    print("   All enabled sites will be monitored")
    print("   Press Ctrl+C to stop monitoring\n")
    
    from monitoring.run_monitor import run_headless_mode
    try:
        run_headless_mode()
    except KeyboardInterrupt:
        print("\n\n🛑 Monitoring stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")


def run_single_site():
    """Run monitoring for a single site"""
    
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print("\n❌ No sites registered!")
        print("   Make sure workers are set up correctly")
        return False
    
    print("\n" + "="*60)
    print("SELECT SITE TO MONITOR")
    print("="*60)
    
    for i, site in enumerate(sites, 1):
        from monitoring.engine.site_registry import get_site_config
        config = get_site_config(site)
        enabled = config.get("enabled", True)
        status = "✅" if enabled else "⏸️"
        print(f"   {i}. {status} {site.upper()}")
    
    print("   0. Back")
    print("="*60)
    
    choice = input("\n👉 Choose site: ").strip()
    
    if choice == "0":
        return False
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            
            from monitoring.engine.site_registry import get_site_config, update_site_config
            current_config = get_site_config(site)
            
            print(f"\n📋 Monitoring: {site.upper()}")
            print(f"   Current workers: {current_config.get('workers', 20)}")
            print(f"   Current interval: {current_config.get('interval', 300)}s")
            print("\n   Press Enter to use defaults, or enter custom values:")
            
            workers_input = input("   Workers (default: 20): ").strip()
            workers = int(workers_input) if workers_input else current_config.get('workers', 20)
            
            interval_input = input("   Interval in seconds (default: 300): ").strip()
            interval = int(interval_input) if interval_input else current_config.get('interval', 300)
            
            if workers != current_config.get('workers'):
                update_site_config(site, workers=workers)
            if interval != current_config.get('interval'):
                update_site_config(site, interval=interval)
            
            print(f"\n🚀 Starting monitoring for {site.upper()}...")
            print("   Press Ctrl+C to stop\n")
            
            from monitoring.run_monitor import run_single_site_mode
            try:
                run_single_site_mode(site, workers, interval)
            except KeyboardInterrupt:
                print(f"\n\n🛑 Monitoring stopped for {site.upper()}")
            return True
        else:
            print("❌ Invalid selection!")
            return False
    except ValueError:
        print("❌ Invalid input!")
        return False


def enable_site():
    """Enable a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print("\n❌ No sites registered!")
        return
    
    print("\n" + "="*60)
    print("ENABLE SITE")
    print("="*60)
    
    for i, site in enumerate(sites, 1):
        print(f"   {i}. {site.upper()}")
    
    print("   0. Back")
    print("="*60)
    
    choice = input("\n👉 Choose site to enable: ").strip()
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            from monitoring.run_monitor import enable_site
            enable_site(site)
            print(f"\n✅ {site.upper()} has been enabled")
        else:
            print("❌ Invalid selection!")
    except ValueError:
        print("❌ Invalid input!")
    input("\nPress Enter to continue...")


def disable_site():
    """Disable a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print("\n❌ No sites registered!")
        return
    
    print("\n" + "="*60)
    print("DISABLE SITE")
    print("="*60)
    
    for i, site in enumerate(sites, 1):
        print(f"   {i}. {site.upper()}")
    
    print("   0. Back")
    print("="*60)
    
    choice = input("\n👉 Choose site to disable: ").strip()
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            from monitoring.run_monitor import disable_site
            disable_site(site)
            print(f"\n⏸️ {site.upper()} has been disabled")
        else:
            print("❌ Invalid selection!")
    except ValueError:
        print("❌ Invalid input!")
    input("\nPress Enter to continue...")


def show_site_config():
    """Show configuration for a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print("\n❌ No sites registered!")
        return
    
    print("\n" + "="*60)
    print("VIEW SITE CONFIGURATION")
    print("="*60)
    
    for i, site in enumerate(sites, 1):
        print(f"   {i}. {site.upper()}")
    
    print("   0. Back")
    print("="*60)
    
    choice = input("\n👉 Choose site: ").strip()
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            from monitoring.run_monitor import show_site_config
            show_site_config(site)
        else:
            print("❌ Invalid selection!")
    except ValueError:
        print("❌ Invalid input!")
    input("\nPress Enter to continue...")


def update_site_config_menu():
    """Update configuration for a specific site"""
    register_all_sites()
    sites = get_all_sites()
    
    if not sites:
        print("\n❌ No sites registered!")
        return
    
    print("\n" + "="*60)
    print("UPDATE SITE CONFIGURATION")
    print("="*60)
    
    for i, site in enumerate(sites, 1):
        print(f"   {i}. {site.upper()}")
    
    print("   0. Back")
    print("="*60)
    
    choice = input("\n👉 Choose site: ").strip()
    
    if choice == "0":
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sites):
            site = sites[idx]
            
            from monitoring.engine.site_registry import get_site_config
            current_config = get_site_config(site)
            
            print(f"\n📋 Current configuration for {site.upper()}:")
            for key, value in current_config.items():
                print(f"   {key}: {value}")
            
            print("\n📝 Enter new values (press Enter to keep current):")
            
            workers = input(f"   workers [{current_config.get('workers', 20)}]: ").strip()
            interval = input(f"   interval [{current_config.get('interval', 300)}]: ").strip()
            
            updates = {}
            if workers:
                updates['workers'] = int(workers)
            if interval:
                updates['interval'] = int(interval)
            
            if updates:
                from monitoring.run_monitor import update_config
                for key, value in updates.items():
                    update_config(site, key, str(value))
                print("\n✅ Configuration updated!")
            else:
                print("   No changes made")
        else:
            print("❌ Invalid selection!")
    except ValueError:
        print("❌ Invalid input!")
    input("\nPress Enter to continue...")


def show_status():
    """Show monitoring system status"""
    from monitoring.run_monitor import show_status
    show_status()
    input("\nPress Enter to continue...")


def list_sites():
    """List all registered sites"""
    from monitoring.run_monitor import list_sites
    list_sites()
    input("\nPress Enter to continue...")


def monitoring_menu():
    """Display monitoring submenu"""
    
    while True:
        print("\n" + "="*60)
        print("📡 MONITORING SYSTEM MENU")
        print("="*60)
        print("   RUN OPTIONS:")
        print("   1. Run All Sites (Interactive Mode - New Window)")
        print("   2. Run All Sites (Headless Mode - Current Window)")
        print("   3. Run Single Site (Current Window)")
        print("")
        print("   MANAGEMENT OPTIONS:")
        print("   4. List All Sites")
        print("   5. Show System Status")
        print("   6. Enable a Site")
        print("   7. Disable a Site")
        print("   8. View Site Configuration")
        print("   9. Update Site Configuration")
        print("")
        print("   0. Back to Main Menu")
        print("="*60)
        
        choice = input("\n👉 Choice: ").strip()
        
        if choice == "1":
            run_all_sites_interactive()
        elif choice == "2":
            run_all_sites_headless()
            input("\nPress Enter to continue...")
        elif choice == "3":
            run_single_site()
            input("\nPress Enter to continue...")
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
            print("❌ Invalid choice!")
    
    return False


if __name__ == "__main__":
    monitoring_menu()