# monitoring/controller.py
import threading
import time
import sys
import signal
import os
from datetime import datetime
from typing import Dict, Optional

from core.console_manager import get_console, Colors
from monitoring.engine.site_registry import (
    SITE_WORKERS, SITE_CONFIGS, get_enabled_sites, 
    set_site_enabled, update_site_config, print_registry_status
)
from core.logger import setup_logger

logger = setup_logger("controller", "monitor")

# Get global console
console = get_console()


class MonitorController:
    """
    Interactive controller for running monitors with clean console UI
    """
    
    def __init__(self):
        self.running_monitors: Dict[str, threading.Thread] = {}
        self.stop_events: Dict[str, threading.Event] = {}
        self.pause_events: Dict[str, threading.Event] = {}
        self.monitor_engine = None
        self.console_thread = None
        self.is_running = False
        self.paused_sites: set = set()
        self.stopped_sites: set = set()
        
        # Setup signal handler
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C to stop all monitors immediately"""
        console.print("\n", Colors.RED)
        console.print("🛑 STOPPING ALL MONITORS", Colors.RED)
        console.print("=" * 60, Colors.RED)
        self.stop_all()
        console.print("✅ All monitors stopped. Exiting...", Colors.GREEN)
        sys.exit(0)
    
    def _clear_line(self):
        """Clear the current line"""
        console._clear_line()
    
    def set_engine(self, engine):
        """Set the monitor engine reference"""
        self.monitor_engine = engine
        if hasattr(self.monitor_engine, 'pause_events'):
            self.monitor_engine.pause_events = self.pause_events
    
    def start_monitor(self, site: str):
        """Start monitoring for a specific site"""
        site = site.lower()
        
        if site in self.running_monitors and self.running_monitors[site].is_alive():
            console.print_warning(f"{site.upper()} is already running!")
            return False
        
        if site not in SITE_WORKERS:
            console.print_error(f"Site '{site}' not registered!")
            return False
        
        if site in self.stopped_sites:
            self.stopped_sites.remove(site)
        
        if site in self.paused_sites:
            self.paused_sites.remove(site)
        
        stop_event = threading.Event()
        pause_event = threading.Event()
        self.stop_events[site] = stop_event
        self.pause_events[site] = pause_event
        
        if hasattr(self.monitor_engine, 'pause_events'):
            self.monitor_engine.pause_events = self.pause_events
        
        thread = threading.Thread(
            target=self._monitor_with_pause,
            args=(site, stop_event, pause_event),
            name=f"Monitor-{site}",
            daemon=True
        )
        thread.start()
        
        self.running_monitors[site] = thread
        console.print_success(f"Started: {site.upper()}")
        return True
    
    def _monitor_with_pause(self, site: str, stop_event: threading.Event, pause_event: threading.Event):
        """Monitor a site with pause/resume capability"""
        try:
            while not stop_event.is_set():
                if pause_event.is_set():
                    console.print_warning(f"{site.upper()} paused...")
                    while pause_event.is_set() and not stop_event.is_set():
                        time.sleep(1)
                    if stop_event.is_set():
                        break
                    console.print_success(f"{site.upper()} resumed...")
                    continue
                
                try:
                    self.monitor_engine.monitor_cycle(
                        site=site,
                        show_progress=True,
                        stop_event=stop_event,
                        pause_event=pause_event
                    )
                except Exception as e:
                    console.print_error(f"{site.upper()} cycle error: {e}")
                    logger.error(f"{site} | CYCLE_ERROR | {e}")
                
                if stop_event.is_set():
                    break
                
                interval = SITE_CONFIGS.get(site, {}).get("interval", 300)
                for _ in range(interval):
                    if stop_event.is_set() or pause_event.is_set():
                        break
                    time.sleep(1)
                    
        except Exception as e:
            console.print_error(f"{site.upper()} monitor error: {e}")
            logger.error(f"{site} | MONITOR_ERROR | {e}")
        finally:
            # Cleanup
            if site in self.running_monitors:
                del self.running_monitors[site]
            if site in self.stop_events:
                del self.stop_events[site]
            if site in self.pause_events:
                del self.pause_events[site]
            if site in self.paused_sites:
                self.paused_sites.remove(site)
            if site in self.stopped_sites:
                self.stopped_sites.remove(site)
            console.print_info(f"{site.upper()} monitoring stopped")
    
    def pause_monitor(self, site: str):
        """Pause monitoring for a specific site"""
        site = site.lower()
        
        if site not in self.pause_events:
            console.print_warning(f"{site.upper()} is not running!")
            return False
        
        if site in self.paused_sites:
            console.print_warning(f"{site.upper()} is already paused!")
            return True
        
        if site in self.stopped_sites:
            console.print_warning(f"{site.upper()} is stopped! Use 'start {site}' first.")
            return False
        
        self.pause_events[site].set()
        self.paused_sites.add(site)
        console.print_warning(f"Paused: {site.upper()}")
        return True
    
    def resume_monitor(self, site: str):
        """Resume monitoring for a specific site"""
        site = site.lower()
        
        if site not in self.pause_events:
            console.print_warning(f"{site.upper()} is not running!")
            return False
        
        if site not in self.paused_sites:
            console.print_warning(f"{site.upper()} is not paused!")
            return True
        
        if site in self.stopped_sites:
            console.print_warning(f"{site.upper()} was stopped. Use 'start {site}' to restart.")
            return False
        
        self.pause_events[site].clear()
        self.paused_sites.remove(site)
        console.print_success(f"Resumed: {site.upper()}")
        return True
    
    def pause_all(self):
        """Pause all running monitors"""
        if not self.pause_events:
            console.print_warning("No monitors running!")
            return
        
        console.print("\n", Colors.YELLOW)
        console.print("⏸️ PAUSING ALL MONITORS", Colors.YELLOW)
        console.print("=" * 60, Colors.YELLOW)
        
        paused_count = 0
        for site in list(self.pause_events.keys()):
            if site not in self.paused_sites and site not in self.stopped_sites:
                self.pause_events[site].set()
                self.paused_sites.add(site)
                console.print_warning(f"   Paused: {site.upper()}")
                paused_count += 1
        
        if paused_count == 0:
            console.print_warning("   No running monitors to pause")
        else:
            console.print_success(f"   Paused {paused_count} monitor(s)")
        
        console.print("=" * 60, Colors.YELLOW)
        console.print_warning("   Use 'resume all' to resume")
        console.print("=" * 60, Colors.YELLOW)
    
    def resume_all(self):
        """Resume all paused monitors"""
        if not self.paused_sites:
            console.print_warning("No monitors are paused!")
            return
        
        console.print("\n", Colors.GREEN)
        console.print("▶️ RESUMING ALL MONITORS", Colors.GREEN)
        console.print("=" * 60, Colors.GREEN)
        
        resumed_count = 0
        for site in list(self.paused_sites):
            if site in self.pause_events and site not in self.stopped_sites:
                self.pause_events[site].clear()
                self.paused_sites.remove(site)
                console.print_success(f"   Resumed: {site.upper()}")
                resumed_count += 1
        
        if resumed_count == 0:
            console.print_warning("   No paused monitors to resume")
        else:
            console.print_success(f"   Resumed {resumed_count} monitor(s)")
        
        console.print("=" * 60, Colors.GREEN)
    
    def stop_monitor(self, site: str):
        """Stop monitoring for a specific site"""
        site = site.lower()
        
        if site not in self.stop_events:
            console.print_warning(f"{site.upper()} is not running!")
            return False
        
        if site in self.paused_sites:
            self.paused_sites.remove(site)
            self.pause_events[site].clear()
        
        self.stop_events[site].set()
        self.stopped_sites.add(site)
        console.print_warning(f"Stopping: {site.upper()}...")
        return True
    
    def stop_all(self):
        """Stop all running monitors immediately"""
        console.print("\n", Colors.RED)
        console.print("🛑 STOPPING ALL MONITORS", Colors.RED)
        console.print("=" * 60, Colors.RED)
        
        self.paused_sites.clear()
        
        for site in list(self.stop_events.keys()):
            self.stop_events[site].set()
            self.stopped_sites.add(site)
            console.print_warning(f"   Stopping: {site.upper()}")
        
        time.sleep(0.5)
        
        for site, thread in list(self.running_monitors.items()):
            if thread.is_alive():
                thread.join(timeout=2)
        
        self.running_monitors.clear()
        self.stop_events.clear()
        self.pause_events.clear()
        
        console.print("=" * 60, Colors.RED)
        console.print_success("All monitors stopped")
        console.print("=" * 60, Colors.RED)
    
    def list_running(self):
        """List all running monitors with their status"""
        console.print("\n", Colors.CYAN)
        console.print("🏃‍♂️ MONITOR STATUS", Colors.CYAN)
        console.print("=" * 50, Colors.CYAN)
        
        if not self.running_monitors and not self.stopped_sites:
            console.print("   No monitors running", Colors.DIM)
        else:
            all_sites = set(self.running_monitors.keys()) | set(self.stopped_sites)
            for site in all_sites:
                is_running = site in self.running_monitors and self.running_monitors[site].is_alive()
                is_paused = site in self.paused_sites
                is_stopped = site in self.stopped_sites
                
                if is_running and is_paused:
                    status = f"{Colors.YELLOW}⏸️ PAUSED{Colors.END}"
                elif is_running:
                    status = f"{Colors.GREEN}🟢 RUNNING{Colors.END}"
                elif is_stopped:
                    status = f"{Colors.RED}⏹️ STOPPED{Colors.END}"
                else:
                    status = f"{Colors.DIM}⚪ IDLE{Colors.END}"
                
                console.print(f"   {site.upper():10} | {status}")
        
        console.print("=" * 50, Colors.CYAN)
        return list(self.running_monitors.keys())
    
    def show_status(self):
        """Show detailed status of all sites"""
        console.print("\n", Colors.MAGENTA)
        console.print("📊 MONITORING STATUS", Colors.MAGENTA)
        console.print("=" * 50, Colors.MAGENTA)
        
        all_sites = set(SITE_WORKERS.keys()) | set(self.running_monitors.keys()) | set(self.stopped_sites)
        
        for site in all_sites:
            config = SITE_CONFIGS.get(site, {})
            enabled = config.get("enabled", True)
            is_running = site in self.running_monitors and self.running_monitors[site].is_alive()
            is_paused = site in self.paused_sites
            is_stopped = site in self.stopped_sites
            
            if is_running and is_paused:
                status = f"{Colors.YELLOW}⏸️ PAUSED{Colors.END}"
            elif is_running:
                status = f"{Colors.GREEN}🟢 RUNNING{Colors.END}"
            elif is_stopped:
                status = f"{Colors.RED}⏹️ STOPPED{Colors.END}"
            elif enabled:
                status = f"{Colors.DIM}⚪ STOPPED{Colors.END}"
            else:
                status = f"{Colors.RED}🔴 DISABLED{Colors.END}"
            
            workers = config.get("workers", 20)
            interval = config.get("interval", 300)
            
            console.print(f"   {site.upper():10} | {status:12} | W:{workers} | I:{interval}s")
        
        console.print("=" * 50, Colors.MAGENTA)
    
    def _get_input(self, prompt):
        """Thread-safe input function"""
        console.hide_prompt()
        console._clear_line()
        return input(f"{Colors.CYAN}{prompt}{Colors.END}")
    
    def console_loop(self):
        """Interactive console loop with clean colored UI"""
        # Welcome screen
        console.print("", Colors.CYAN)
        console.print("                    🎮 MONITORING CONSOLE", Colors.CYAN)
        console.print("=" * 60, Colors.CYAN)
        console.print("")
        console.print("  Commands:", Colors.BOLD)
        console.print("")
        console.print(f"\n    {Colors.GREEN}start <site>{Colors.END}   - Start monitoring")
        console.print(f"    {Colors.YELLOW}stop <site>{Colors.END}    - Stop monitoring")
        console.print(f"    {Colors.RED}stop all{Colors.END}       - Stop all")
        console.print(f"    {Colors.YELLOW}pause <site>{Colors.END}   - Pause monitoring")
        console.print(f"    {Colors.YELLOW}pause all{Colors.END}      - Pause all")
        console.print(f"    {Colors.GREEN}resume <site>{Colors.END}  - Resume monitoring")
        console.print(f"    {Colors.GREEN}resume all{Colors.END}     - Resume all")
        console.print(f"    {Colors.BLUE}enable <site>{Colors.END}  - Enable site")
        console.print(f"    {Colors.BLUE}disable <site>{Colors.END} - Disable site")
        console.print(f"    {Colors.CYAN}list{Colors.END}           - Running monitors")
        console.print(f"    {Colors.CYAN}status{Colors.END}         - All sites")
        console.print(f"    {Colors.RED}exit{Colors.END}           - Exit console")
        console.print("", Colors.DIM)
        console.print("-" * 60, Colors.DIM)
        console.print(f"  {Colors.YELLOW}💡 Press Ctrl+C to stop ALL monitors immediately{Colors.END}")
        console.print("=" * 60, Colors.CYAN)
        console.print("")
        
        while self.is_running:
            try:
                cmd = self._get_input("> ").strip().lower()
                
                if not cmd:
                    continue
                
                parts = cmd.split()
                command = parts[0]
                
                if command in ['exit', 'quit']:
                    console.print_warning("👋 Exiting console. Monitors continue running.")
                    console.print_warning("   Press Ctrl+C to stop everything.")
                    break
                
                elif command == 'stop' and len(parts) > 1 and parts[1] == 'all':
                    self.stop_all()
                
                elif command == 'stop' and len(parts) > 1:
                    self.stop_monitor(parts[1])
                
                elif command == 'pause' and len(parts) > 1 and parts[1] == 'all':
                    self.pause_all()
                
                elif command == 'pause' and len(parts) > 1:
                    self.pause_monitor(parts[1])
                
                elif command == 'resume' and len(parts) > 1 and parts[1] == 'all':
                    self.resume_all()
                
                elif command == 'resume' and len(parts) > 1:
                    self.resume_monitor(parts[1])
                
                elif command == 'start' and len(parts) > 1:
                    self.start_monitor(parts[1])
                
                elif command == 'enable' and len(parts) > 1:
                    site = parts[1]
                    set_site_enabled(site, True)
                    console.print_success(f"{site.upper()} enabled")
                
                elif command == 'disable' and len(parts) > 1:
                    site = parts[1]
                    set_site_enabled(site, False)
                    if site in self.running_monitors:
                        self.stop_monitor(site)
                    console.print_warning(f"{site.upper()} disabled")
                
                elif command == 'config' and len(parts) >= 3:
                    site = parts[1]
                    try:
                        key_value = ' '.join(parts[2:])
                        key, value = key_value.split('=', 1)
                        try:
                            value = int(value)
                        except:
                            pass
                        update_site_config(site, **{key: value})
                        console.print_success(f"{site.upper()}: {key}={value}")
                    except:
                        console.print_error("Format: config site key=value")
                
                elif command in ['list', 'ls']:
                    self.list_running()
                
                elif command in ['status', 'st']:
                    self.show_status()
                
                elif command in ['registry', 'reg']:
                    print_registry_status()
                
                elif command == 'reload':
                    console.print_warning("🔄 Reloading registry...")
                    from monitoring.engine.site_registry import auto_discover_workers
                    auto_discover_workers()
                    console.print_success("✅ Registry reloaded")
                
                elif command in ['help', '?']:
                    console.print("\n" + "="*50, Colors.CYAN)
                    console.print("Available Commands:", Colors.BOLD)
                    console.print(f"  {Colors.GREEN}start <site>{Colors.END}   - Start monitoring")
                    console.print(f"  {Colors.YELLOW}stop <site>{Colors.END}    - Stop monitoring")
                    console.print(f"  {Colors.RED}stop all{Colors.END}       - Stop all")
                    console.print(f"  {Colors.YELLOW}pause <site>{Colors.END}   - Pause monitoring")
                    console.print(f"  {Colors.YELLOW}pause all{Colors.END}      - Pause all")
                    console.print(f"  {Colors.GREEN}resume <site>{Colors.END}  - Resume monitoring")
                    console.print(f"  {Colors.GREEN}resume all{Colors.END}     - Resume all")
                    console.print(f"  {Colors.BLUE}enable <site>{Colors.END}  - Enable site")
                    console.print(f"  {Colors.BLUE}disable <site>{Colors.END} - Disable site")
                    console.print(f"  {Colors.CYAN}config site k=v{Colors.END} - Update config")
                    console.print(f"  {Colors.CYAN}list{Colors.END}           - Running monitors")
                    console.print(f"  {Colors.CYAN}status{Colors.END}         - All sites")
                    console.print(f"  {Colors.RED}exit{Colors.END}           - Exit console")
                    console.print("="*50, Colors.CYAN)
                    console.print(f"{Colors.YELLOW}💡 Ctrl+C = Stop ALL immediately{Colors.END}")
                
                else:
                    console.print_error(f"Unknown: {command}. Type 'help'")
                    
            except KeyboardInterrupt:
                console.print("\n", Colors.RED)
                console.print("🛑 Stopping all monitors...", Colors.RED)
                self.stop_all()
                console.print_success("✅ Exiting...")
                break
            except Exception as e:
                console.print_error(f"Error: {e}")
    
    def start_console(self):
        """Start the interactive console"""
        self.is_running = True
        self.console_thread = threading.Thread(target=self.console_loop, daemon=True)
        self.console_thread.start()
    
    def stop_console(self):
        """Stop the console"""
        self.is_running = False


# Global controller instance
_controller = None


def get_controller():
    """Get global controller instance"""
    global _controller
    if _controller is None:
        _controller = MonitorController()
    return _controller