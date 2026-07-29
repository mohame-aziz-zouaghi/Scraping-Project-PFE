# monitoring/run_monitor.py
"""
Main entry point for monitoring system
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

from monitoring.engine.monitor_engine import MonitorEngine
from monitoring.engine.site_registry import (
    get_enabled_sites, register_all_sites, get_all_sites, print_registry_status,
    set_site_enabled, update_site_config, get_site_config,
    SITE_WORKERS, SITE_CONFIGS
)
from monitoring.controller import MonitorController, get_controller
from monitoring.engine.multi_progress import get_multi_progress
from core.logger import setup_logger

logger = setup_logger("run_monitor", "monitor")


def run_interactive_mode():
    """Run monitoring with interactive console - Multi-Progress Dashboard"""
    
    print("\n" + "="*60)
    print("🤖 MONITORING SYSTEM - MULTI-SITE DASHBOARD")
    print("="*60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    print("")
    print("📊 All sites will be displayed in the dashboard below")
    print("   Type 'help' for commands | Press Ctrl+C to stop all")
    print("")
    
    register_all_sites()
    
    engine = MonitorEngine()
    controller = get_controller()
    controller.set_engine(engine)
    controller.start_console()
    
    print("\n🚀 Starting enabled sites...")
    enabled_sites = get_enabled_sites()
    
    # Start multi-progress
    multi_progress = get_multi_progress()
    multi_progress.start()
    
    for site in enabled_sites:
        controller.start_monitor(site)
    
    print("\n" + "="*60)
    print("🟢 SYSTEM READY - Dashboard Active")
    print("   Type 'help' for commands")
    print("   Press Ctrl+C to stop all")
    print("="*60)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        controller.stop_all()
        multi_progress.stop()
        multi_progress.close()
        print("✅ System stopped")


def run_headless_mode():
    """Run monitoring without interactive console (background mode)"""
    
    print("\n" + "="*70)
    print("🤖 TUNISIAN TECH PRODUCTS MONITORING SYSTEM (HEADLESS)")
    print("="*70)
    print("Running in background mode - no console")
    print("Press Ctrl+C to stop")
    print("="*70)
    
    register_all_sites()
    
    engine = MonitorEngine()
    
    try:
        engine.run_all()
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        multi_progress = get_multi_progress()
        multi_progress.stop()
        multi_progress.close()
        print("✅ System stopped")


def run_single_site_mode(site: str, workers: int = None, interval: int = None):
    """Run monitoring for a single site with multi-progress"""
    
    register_all_sites()
    
    if site not in get_all_sites():
        print(f"\n❌ Site '{site}' not registered!")
        print(f"   Available sites: {', '.join(get_all_sites())}")
        return
    
    if workers or interval:
        if workers:
            update_site_config(site, workers=workers)
        if interval:
            update_site_config(site, interval=interval)
    
    print("\n" + "="*70)
    print(f"🤖 MONITORING: {site.upper()}")
    print("="*70)
    print("Press Ctrl+C to stop")
    print("="*70 + "\n")
    
    engine = MonitorEngine()
    
    try:
        engine.run_site(site)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        multi_progress = get_multi_progress()
        multi_progress.stop()
        multi_progress.close()
        print("✅ System stopped")


def enable_site(site: str):
    """Enable a specific site"""
    register_all_sites()
    
    if site not in get_all_sites():
        print(f"\n❌ Site '{site}' not registered!")
        print(f"   Available sites: {', '.join(get_all_sites())}")
        return
    
    set_site_enabled(site, True)
    print(f"\n✅ Site {site.upper()} has been ENABLED")
    print("   It will be included in 'Run All Sites' mode")


def disable_site(site: str):
    """Disable a specific site"""
    register_all_sites()
    
    if site not in get_all_sites():
        print(f"\n❌ Site '{site}' not registered!")
        print(f"   Available sites: {', '.join(get_all_sites())}")
        return
    
    set_site_enabled(site, False)
    print(f"\n⏸️ Site {site.upper()} has been DISABLED")
    print("   It will NOT be included in 'Run All Sites' mode")


def show_site_config(site: str):
    """Show configuration for a specific site"""
    register_all_sites()
    
    if site not in get_all_sites():
        print(f"\n❌ Site '{site}' not registered!")
        print(f"   Available sites: {', '.join(get_all_sites())}")
        return
    
    config = get_site_config(site)
    
    print(f"\n📋 CONFIGURATION FOR: {site.upper()}")
    print("="*50)
    for key, value in config.items():
        print(f"   {key:25} : {value}")
    print("="*50)


def update_config(site: str, key: str, value: str):
    """Update configuration for a specific site"""
    register_all_sites()
    
    if site not in get_all_sites():
        print(f"\n❌ Site '{site}' not registered!")
        print(f"   Available sites: {', '.join(get_all_sites())}")
        return
    
    # Convert value to appropriate type
    try:
        if '.' in value:
            converted_value = float(value)
        else:
            converted_value = int(value)
    except ValueError:
        converted_value = value
    
    update_site_config(site, **{key: converted_value})
    print(f"\n✅ Updated {site.upper()}: {key} = {converted_value}")


def list_sites():
    """List all registered sites with their status"""
    register_all_sites()
    
    sites = get_all_sites()
    
    if not sites:
        print("\n❌ No sites registered!")
        return
    
    print("\n" + "="*60)
    print("📋 REGISTERED SITES")
    print("="*60)
    
    for site in sites:
        config = get_site_config(site)
        enabled = config.get("enabled", True)
        status = "✅ ENABLED" if enabled else "⏸️ DISABLED"
        workers = config.get("workers", 20)
        interval = config.get("interval", 300)
        print(f"   {site.upper():15} | {status:12} | workers={workers} | interval={interval}s")
    
    print("="*60)


def show_status():
    """Show detailed status of monitoring system"""
    register_all_sites()
    
    print("\n" + "="*60)
    print("📊 MONITORING SYSTEM STATUS")
    print("="*60)
    
    # Show registry status
    print_registry_status()
    
    # Check for running processes
    import platform
    import subprocess
    
    print("\n🔍 PROCESS STATUS:")
    
    if platform.system() == "Windows":
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'], 
                                capture_output=True, text=True)
        if 'run_monitor' in str(result.stdout):
            print("   🟢 Monitoring processes are running")
        else:
            print("   ⚪ No monitoring processes found")
    else:
        result = subprocess.run(['pgrep', '-f', 'run_monitor.py'], capture_output=True)
        if result.returncode == 0:
            pids = result.stdout.decode().strip().split('\n')
            print(f"   🟢 Monitoring processes running (PIDs: {', '.join(pids)})")
        else:
            print("   ⚪ No monitoring processes found")


def main():
    """Main entry point with all commands"""
    parser = argparse.ArgumentParser(
        description="Tunisian Tech Products Monitoring System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run modes
  python run_monitor.py --mode interactive     # Interactive console mode
  python run_monitor.py --mode headless        # Headless mode
  python run_monitor.py --mode single --site megapc  # Single site mode
  
  # Management commands
  python run_monitor.py --list-sites           # List all registered sites
  python run_monitor.py --status               # Show system status
  python run_monitor.py --enable megapc        # Enable a site
  python run_monitor.py --disable tunisianet   # Disable a site
  python run_monitor.py --show-config megapc   # Show site configuration
  python run_monitor.py --update-config megapc workers=30  # Update config
  
  # Quick info
  python run_monitor.py --help                 # Show this help
        """
    )
    
    # Run modes
    parser.add_argument("--mode", "-m", type=str, choices=["interactive", "headless", "single"], 
                        help="Run mode: interactive, headless, or single")
    parser.add_argument("--site", "-s", type=str, help="Specific site to monitor (for single mode)")
    parser.add_argument("--workers", "-w", type=int, help="Number of worker threads")
    parser.add_argument("--interval", "-i", type=int, help="Check interval in seconds")
    
    # Management commands
    parser.add_argument("--list-sites", action="store_true", help="List all registered sites")
    parser.add_argument("--status", action="store_true", help="Show system status")
    parser.add_argument("--enable", type=str, help="Enable a specific site")
    parser.add_argument("--disable", type=str, help="Disable a specific site")
    parser.add_argument("--show-config", type=str, help="Show configuration for a specific site")
    parser.add_argument("--update-config", type=str, help="Update config for a site (format: site key=value)")
    
    args = parser.parse_args()
    
    # Handle management commands (exit after)
    if args.list_sites:
        list_sites()
        return
    
    if args.status:
        show_status()
        return
    
    if args.enable:
        enable_site(args.enable)
        return
    
    if args.disable:
        disable_site(args.disable)
        return
    
    if args.show_config:
        show_site_config(args.show_config)
        return
    
    if args.update_config:
        # Parse format: site key=value
        parts = args.update_config.split()
        if len(parts) >= 2:
            site = parts[0]
            key_value = parts[1]
            if '=' in key_value:
                key, value = key_value.split('=', 1)
                update_config(site, key, value)
            else:
                print("❌ Invalid format. Use: --update-config 'site key=value'")
        else:
            print("❌ Invalid format. Use: --update-config 'site key=value'")
        return
    
    # Handle run modes
    if args.mode == "interactive":
        run_interactive_mode()
    elif args.mode == "headless":
        run_headless_mode()
    elif args.mode == "single" and args.site:
        run_single_site_mode(args.site, args.workers, args.interval)
    elif args.mode == "single" and not args.site:
        print("❌ Please specify --site for single mode")
        parser.print_help()
    else:
        # Default: show help
        parser.print_help()


if __name__ == "__main__":
    main()