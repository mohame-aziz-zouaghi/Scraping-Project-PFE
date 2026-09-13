# monitoring/run.py
"""
Single entry point for monitoring system.
Supports interactive dashboard, headless, single site modes,
and management commands (enable/disable/toggle/list/status).
"""

import sys
import os
import argparse
import time
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitoring.engine.monitor_engine import MonitorEngine
from monitoring.config import (
    get_enabled_sites, get_site_config, get_all_sites,
    set_site_enabled, toggle_site, print_registry_status,
    update_site_config, SITE_CONFIGS
)
from core.logger import setup_logger
from core.console_manager import get_console, Colors

logger = setup_logger("run_monitor", "monitor")
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


# ─── COMMANDS ──────────────────────────────────────────────────────

def cmd_list_sites():
    """List all sites with their status."""
    print("\n📋 ALL SITES")
    print("=" * 60)
    print(f"   {'SITE':<12} | {'STATUS':<10} | {'WORKERS':<8} | {'INTERVAL':<10} | {'RPM':<8}")
    print("   " + "-" * 60)
    
    for site in get_all_sites():
        config = get_site_config(site)
        enabled = config.get('enabled', True)
        status = "✅ ACTIVE" if enabled else "⏸️ DISABLED"
        workers = config.get('workers', 20)
        interval = config.get('interval', 300)
        rpm = config.get('requests_per_minute', 20)
        print(f"   {site.upper():<12} | {status:<10} | {workers:<8} | {interval}s{' ' * 6} | {rpm:<8}")
    
    print("=" * 60)


def cmd_enable_site(site: str):
    """Enable a specific site."""
    site = site.lower()
    if site not in get_all_sites():
        print_error(f"Site '{site}' not found!")
        return
    
    if set_site_enabled(site, True):
        print_success(f"{site.upper()} has been ENABLED")
    else:
        print_error(f"Failed to enable {site.upper()}")


def cmd_disable_site(site: str):
    """Disable a specific site."""
    site = site.lower()
    if site not in get_all_sites():
        print_error(f"Site '{site}' not found!")
        return
    
    if set_site_enabled(site, False):
        print_warning(f"{site.upper()} has been DISABLED")
    else:
        print_error(f"Failed to disable {site.upper()}")


def cmd_toggle_site(site: str):
    """Toggle a site's enabled status."""
    site = site.lower()
    if site not in get_all_sites():
        print_error(f"Site '{site}' not found!")
        return
    
    new_status = toggle_site(site)
    status = "ENABLED" if new_status else "DISABLED"
    print_info(f"{site.upper()} is now {status}")


def cmd_show_config(site: str):
    """Show configuration for a specific site."""
    site = site.lower()
    config = get_site_config(site)
    
    if not config:
        print_error(f"Site '{site}' not found!")
        return
    
    print(f"\n📋 CONFIGURATION FOR: {site.upper()}")
    print("=" * 50)
    for key, value in config.items():
        print(f"   {key:25} : {value}")
    print("=" * 50)


def cmd_update_config(site: str, key: str, value: str):
    """Update a configuration value for a site."""
    site = site.lower()
    if site not in get_all_sites():
        print_error(f"Site '{site}' not found!")
        return
    
    # Convert value to appropriate type
    try:
        if '.' in value:
            converted = float(value)
        else:
            converted = int(value)
    except ValueError:
        converted = value
    
    if update_site_config(site, **{key: converted}):
        print_success(f"{site.upper()} {key} = {converted}")
    else:
        print_error(f"Failed to update {site.upper()} {key}")


def cmd_show_status():
    """Show detailed system status."""
    print_registry_status()


def cmd_run_site(site: str, workers: int = None, interval: int = None):
    """Run a single site."""
    site = site.lower()
    config = get_site_config(site)
    
    if not config:
        print_error(f"Site '{site}' not found!")
        return
    
    if workers:
        update_site_config(site, workers=workers)
    if interval:
        update_site_config(site, interval=interval)
    
    config = get_site_config(site)
    
    print_header(f"🚀 MONITORING: {site.upper()}")
    print(f"   Workers: {config.get('workers', 20)} | Interval: {config.get('interval', 300)}s")
    print(f"   RPM: {config.get('requests_per_minute', 20)}")
    print("   Press Ctrl+C to stop\n")
    
    engine = MonitorEngine()
    try:
        engine.run_site(site)
    except KeyboardInterrupt:
        print(f"\n🛑 Stopped {site.upper()}")


def cmd_run_all():
    """Run all enabled sites."""
    print_header("🚀 MONITORING ALL SITES")
    print("   Press Ctrl+C to stop all\n")
    
    engine = MonitorEngine()
    try:
        engine.run_all()
    except KeyboardInterrupt:
        print("\n🛑 Stopped all sites")


def cmd_show_help():
    """Show available commands."""
    print("\n" + "=" * 60)
    print("📖 AVAILABLE COMMANDS")
    print("=" * 60)
    print("\n  RUN COMMANDS:")
    print("    run all                    - Start monitoring all enabled sites")
    print("    run <site>                 - Start monitoring a specific site")
    print("    run <site> workers=30      - Start with custom workers")
    print("    run <site> interval=120    - Start with custom interval")
    print("\n  MANAGEMENT COMMANDS:")
    print("    list                       - List all sites with status")
    print("    status                     - Show detailed system status")
    print("    enable <site>              - Enable a site")
    print("    disable <site>             - Disable a site")
    print("    toggle <site>              - Toggle site status")
    print("    config <site>              - Show site configuration")
    print("    config <site> key=value    - Update site configuration")
    print("\n  OTHER:")
    print("    help                       - Show this help")
    print("    exit / quit                - Exit monitoring console")
    print("=" * 60)


# ─── INTERACTIVE CONSOLE ──────────────────────────────────────────

def interactive_console():
    """Interactive console with live commands."""
    print("\n" + "=" * 60)
    print("🤖 MONITORING CONSOLE")
    print("=" * 60)
    print("   Type 'help' for available commands")
    print("   Type 'run all' to start monitoring all sites")
    print("   Type 'exit' to quit")
    print("=" * 60)
    print("")
    
    engine = None
    running = False
    
    while True:
        try:
            cmd = get_input("", "> ").strip().lower()
            
            if not cmd:
                continue
            
            # ─── EXIT ──────────────────────────────────────────────────
            if cmd in ['exit', 'quit', 'q']:
                if running:
                    print_warning("Stopping monitoring...")
                    if engine:
                        engine.stop_all()
                print_info("Goodbye!")
                break
            
            # ─── HELP ──────────────────────────────────────────────────
            if cmd in ['help', '?', 'h']:
                cmd_show_help()
                continue
            
            # ─── LIST ──────────────────────────────────────────────────
            if cmd == 'list' or cmd == 'ls':
                cmd_list_sites()
                continue
            
            # ─── STATUS ────────────────────────────────────────────────
            if cmd == 'status' or cmd == 'st':
                cmd_show_status()
                continue
            
            # ─── ENABLE ────────────────────────────────────────────────
            if cmd.startswith('enable '):
                site = cmd.replace('enable ', '').strip()
                if site:
                    cmd_enable_site(site)
                else:
                    print_error("Usage: enable <site>")
                continue
            
            # ─── DISABLE ────────────────────────────────────────────────
            if cmd.startswith('disable '):
                site = cmd.replace('disable ', '').strip()
                if site:
                    cmd_disable_site(site)
                else:
                    print_error("Usage: disable <site>")
                continue
            
            # ─── TOGGLE ────────────────────────────────────────────────
            if cmd.startswith('toggle '):
                site = cmd.replace('toggle ', '').strip()
                if site:
                    cmd_toggle_site(site)
                else:
                    print_error("Usage: toggle <site>")
                continue
            
            # ─── CONFIG ────────────────────────────────────────────────
            if cmd.startswith('config '):
                parts = cmd.replace('config ', '').strip().split()
                if len(parts) >= 1:
                    site = parts[0]
                    if len(parts) >= 2 and '=' in parts[1]:
                        key, value = parts[1].split('=', 1)
                        cmd_update_config(site, key, value)
                    else:
                        cmd_show_config(site)
                else:
                    print_error("Usage: config <site> or config <site> key=value")
                continue
            
            # ─── RUN ────────────────────────────────────────────────────
            if cmd.startswith('run '):
                parts = cmd.replace('run ', '').strip().split()
                if not parts:
                    print_error("Usage: run all or run <site>")
                    continue
                
                target = parts[0]
                workers = None
                interval = None
                
                # Parse extra args
                for part in parts[1:]:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        if key == 'workers':
                            try:
                                workers = int(value)
                            except ValueError:
                                print_error(f"Invalid workers value: {value}")
                        elif key == 'interval':
                            try:
                                interval = int(value)
                            except ValueError:
                                print_error(f"Invalid interval value: {value}")
                
                if target == 'all':
                    if running:
                        print_warning("Monitoring already running!")
                    else:
                        engine = MonitorEngine()
                        running = True
                        # Run in a separate thread so console stays responsive
                        def run_thread():
                            try:
                                engine.run_all()
                            except KeyboardInterrupt:
                                pass
                            finally:
                                nonlocal running
                                running = False
                        thread = threading.Thread(target=run_thread, daemon=True)
                        thread.start()
                        print_success("Started monitoring all enabled sites")
                        print_info("Type 'stop' to stop all sites")
                
                elif target in get_all_sites():
                    if running:
                        print_warning("Monitoring already running! Stop first with 'stop'")
                    else:
                        engine = MonitorEngine()
                        running = True
                        def run_site_thread():
                            try:
                                engine.run_site(target, interval)
                            except KeyboardInterrupt:
                                pass
                            finally:
                                nonlocal running
                                running = False
                        thread = threading.Thread(target=run_site_thread, daemon=True)
                        thread.start()
                        print_success(f"Started monitoring {target.upper()}")
                        print_info("Type 'stop' to stop monitoring")
                else:
                    print_error(f"Site '{target}' not found!")
                    print_info(f"Available sites: {', '.join(get_all_sites())}")
                continue
            
            # ─── STOP ──────────────────────────────────────────────────
            if cmd == 'stop':
                if running and engine:
                    print_warning("Stopping monitoring...")
                    engine.stop_all()
                    running = False
                    print_success("Stopped all monitoring")
                else:
                    print_warning("No monitoring running")
                continue
            
            # ─── UNKNOWN ──────────────────────────────────────────────
            print_error(f"Unknown command: '{cmd}'")
            print_info("Type 'help' for available commands")
            
        except KeyboardInterrupt:
            print("\n")
            if running and engine:
                print_warning("Stopping monitoring...")
                engine.stop_all()
                running = False
            print_info("Exiting...")
            break
        except Exception as e:
            print_error(f"Error: {e}")


# ─── MAIN ──────────────────────────────────────────────────────────

def run_interactive():
    """Run with interactive dashboard (direct mode)."""
    print("\n" + "=" * 60)
    print("🤖 MONITORING DASHBOARD")
    print("=" * 60)
    print("   Press Ctrl+C to stop all")
    print("=" * 60 + "\n")
    
    engine = MonitorEngine()
    engine.run_all()


def run_headless():
    """Run in headless mode (no dashboard)."""
    print("\n🤖 MONITORING (HEADLESS)")
    print("   Running in background. Press Ctrl+C to stop.\n")
    
    engine = MonitorEngine()
    try:
        engine.run_all()
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping all sites...")


def run_single_site(site: str, workers: int = None, interval: int = None):
    """Run a single site."""
    site = site.lower()
    config = get_site_config(site)
    
    if not config:
        print(f"❌ Site '{site}' not found!")
        print(f"   Available sites: {', '.join(get_enabled_sites())}")
        return
    
    if workers:
        update_site_config(site, workers=workers)
    if interval:
        update_site_config(site, interval=interval)
    
    config = get_site_config(site)
    
    print(f"\n🤖 MONITORING: {site.upper()}")
    print(f"   Workers: {config.get('workers', 20)} | Interval: {config.get('interval', 300)}s")
    print(f"   RPM: {config.get('requests_per_minute', 20)} | Basic Headers: {config.get('use_basic_headers', False)}")
    print("   Press Ctrl+C to stop\n")
    
    engine = MonitorEngine()
    try:
        engine.run_site(site)
    except KeyboardInterrupt:
        print(f"\n🛑 Stopped {site.upper()}")


def list_sites():
    """List all enabled sites with details."""
    cmd_list_sites()


def show_status():
    """Show detailed system status."""
    cmd_show_status()


def enable_site(site: str):
    """Enable a specific site."""
    cmd_enable_site(site)


def disable_site(site: str):
    """Disable a specific site."""
    cmd_disable_site(site)


def toggle_site_command(site: str):
    """Toggle a site's enabled status."""
    cmd_toggle_site(site)


def show_config(site: str):
    """Show detailed configuration for a specific site."""
    cmd_show_config(site)


def main():
    parser = argparse.ArgumentParser(
        description="Monitoring System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive console (recommended)
  python run.py                     # Opens interactive console with commands
  
  # Direct modes
  python run.py --dashboard         # Interactive dashboard (all enabled sites)
  python run.py --headless          # Headless mode
  python run.py --site megapc       # Single site
  
  # Management commands
  python run.py --list              # List enabled sites
  python run.py --status            # Show system status
  python run.py --enable megapc     # Enable a site
  python run.py --disable mytek     # Disable a site
  python run.py --toggle sbs        # Toggle site status
  python run.py --config megapc     # Show site configuration
        """
    )
    
    # Run modes
    parser.add_argument("--dashboard", "-d", action="store_true", help="Run interactive dashboard")
    parser.add_argument("--headless", action="store_true", help="Run without dashboard")
    parser.add_argument("--site", "-s", help="Run single site")
    parser.add_argument("--workers", "-w", type=int, help="Number of workers (for single site)")
    parser.add_argument("--interval", "-i", type=int, help="Check interval in seconds (for single site)")
    
    # Management commands
    parser.add_argument("--list", "-l", action="store_true", help="List enabled sites")
    parser.add_argument("--status", action="store_true", help="Show system status")
    parser.add_argument("--enable", help="Enable a specific site")
    parser.add_argument("--disable", help="Disable a specific site")
    parser.add_argument("--toggle", help="Toggle a site's enabled status")
    parser.add_argument("--config", help="Show configuration for a specific site")
    parser.add_argument("--console", "-c", action="store_true", help="Run interactive console (default)")
    
    args = parser.parse_args()
    
    # ─── Management commands ────────────────────────────────────────
    if args.list:
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
    
    if args.toggle:
        toggle_site_command(args.toggle)
        return
    
    if args.config:
        show_config(args.config)
        return
    
    # ─── Run modes ──────────────────────────────────────────────────
    if args.site:
        run_single_site(args.site, args.workers, args.interval)
        return
    
    if args.dashboard:
        run_interactive()
        return
    
    if args.headless:
        run_headless()
        return
    
    # ─── Default: Interactive console ──────────────────────────────
    try:
        interactive_console()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()