# monitoring/controller.py
import threading
import time
import sys
import signal
import os
from datetime import datetime
from typing import Dict, Optional

from monitoring.engine.site_registry import (
    SITE_WORKERS, SITE_CONFIGS, get_enabled_sites, 
    set_site_enabled, update_site_config, print_registry_status
)
from core.logger import setup_logger

logger = setup_logger("monitor_controller", "monitor")


class MonitorController:
    """
    Interactive controller for running monitors
    Allows adding/removing sites while running
    """
    
    def __init__(self):
        self.running_monitors: Dict[str, threading.Thread] = {}
        self.stop_events: Dict[str, threading.Event] = {}
        self.monitor_engine = None
        self.console_thread = None
        self.is_running = False
        self.input_lock = threading.Lock()
        self.current_cycle_running = False
        
        # Setup signal handler for Ctrl+C
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C to stop all monitors immediately"""
        print("\n\n" + "="*50)
        print("🛑 STOPPING ALL MONITORS")
        print("="*50)
        self.stop_all()
        print("✅ All monitors stopped. Exiting...")
        sys.exit(0)
    
    def _clear_line(self):
        """Clear the current line in terminal"""
        sys.stdout.write('\r' + ' ' * 120 + '\r')
        sys.stdout.flush()
    
    def set_engine(self, engine):
        """Set the monitor engine reference"""
        self.monitor_engine = engine
    
    def start_monitor(self, site: str):
        """Start monitoring for a specific site"""
        site = site.lower()
        
        if site in self.running_monitors and self.running_monitors[site].is_alive():
            self._clear_line()
            print(f"⚠️ {site.upper()} is already running!")
            return False
        
        if site not in SITE_WORKERS:
            self._clear_line()
            print(f"❌ Site '{site}' not registered!")
            return False
        
        # Create stop event
        stop_event = threading.Event()
        self.stop_events[site] = stop_event
        
        # Create and start thread
        thread = threading.Thread(
            target=self.monitor_engine.monitor_site_continuous,
            args=(site, stop_event),
            name=f"Monitor-{site}",
            daemon=True
        )
        thread.start()
        
        self.running_monitors[site] = thread
        self._clear_line()
        print(f"✅ Started: {site.upper()}")
        return True
    
    def stop_monitor(self, site: str):
        """Stop monitoring for a specific site"""
        site = site.lower()
        
        if site in self.stop_events:
            self.stop_events[site].set()
            self._clear_line()
            print(f"⏹️ Stopping: {site.upper()}...")
            return True
        else:
            self._clear_line()
            print(f"⚠️ {site.upper()} is not running!")
            return False
    
    # monitoring/controller.py - Update stop_all method

    def stop_all(self):
        """Stop all running monitors immediately"""
        self._clear_line()
        print("\n" + "="*50)
        print("🛑 STOPPING ALL MONITORS")
        print("="*50)
        
        # Set all stop events first
        for site in self.stop_events:
            self.stop_events[site].set()
            print(f"   ⏹️ Stopping: {site.upper()}")
        
        # Give a moment for threads to see the event
        time.sleep(0.5)
        
        # Force any remaining threads to stop
        for site, thread in list(self.running_monitors.items()):
            if thread.is_alive():
                thread.join(timeout=2)
        
        self.running_monitors.clear()
        self.stop_events.clear()
        
        print("="*50)
        print("✅ All monitors stopped")
        print("="*50)
    
    def list_running(self):
        """List all running monitors"""
        self._clear_line()
        print("\n" + "="*50)
        print("🏃‍♂️ RUNNING MONITORS")
        print("="*50)
        
        running = []
        for site, thread in self.running_monitors.items():
            if thread.is_alive():
                running.append(site.upper())
        
        if running:
            for site in running:
                print(f"   • {site}")
        else:
            print("   None")
        
        print("="*50)
        return running
    
    def show_status(self):
        """Show detailed status of all sites"""
        self._clear_line()
        print("\n" + "="*50)
        print("📊 MONITORING STATUS")
        print("="*50)
        
        for site in SITE_WORKERS.keys():
            config = SITE_CONFIGS.get(site, {})
            enabled = config.get("enabled", True)
            is_running = site in self.running_monitors and self.running_monitors[site].is_alive()
            
            if is_running:
                status = "🟢 RUNNING"
            elif enabled:
                status = "⚪ STOPPED"
            else:
                status = "🔴 DISABLED"
            
            workers = config.get("workers", 20)
            interval = config.get("interval", 300)
            
            print(f"   {site.upper():10} | {status:10} | W:{workers} | I:{interval}s")
        
        print("="*50)
    
    def _get_input(self, prompt):
        """Thread-safe input function with line clearing"""
        with self.input_lock:
            self._clear_line()
            return input(prompt)
    
    def console_loop(self):
        """Interactive console loop for commands"""
        self._clear_line()
        print("\n" + "="*50)
        print("🎮 MONITORING CONSOLE")
        print("="*50)
        print("Commands:")
        print("  start <site>   - Start monitoring")
        print("  stop <site>    - Stop monitoring")
        print("  stop all       - Stop all")
        print("  resume <site>  - Resume")
        print("  resume all     - Resume all")
        print("  enable <site>  - Enable site")
        print("  disable <site> - Disable site")
        print("  list           - Running monitors")
        print("  status         - All sites")
        print("  exit           - Exit console")
        print("="*50)
        print("💡 Ctrl+C = Stop ALL immediately")
        print("="*50)
        
        while self.is_running:
            try:
                cmd = self._get_input("\n> ").strip().lower()
                
                if not cmd:
                    continue
                
                parts = cmd.split()
                command = parts[0]
                
                if command in ['exit', 'quit']:
                    print("👋 Exiting console. Monitors continue.")
                    print("   Press Ctrl+C to stop everything.")
                    break
                
                elif command == 'stop' and len(parts) > 1 and parts[1] == 'all':
                    self.stop_all()
                
                elif command == 'stop' and len(parts) > 1:
                    self.stop_monitor(parts[1])
                
                elif command == 'resume' and len(parts) > 1 and parts[1] == 'all':
                    print("🔄 Resuming all enabled sites...")
                    enabled_sites = get_enabled_sites()
                    for site in enabled_sites:
                        self.start_monitor(site)
                
                elif command == 'resume' and len(parts) > 1:
                    self.start_monitor(parts[1])
                
                elif command == 'start' and len(parts) > 1:
                    self.start_monitor(parts[1])
                
                elif command == 'enable' and len(parts) > 1:
                    site = parts[1]
                    set_site_enabled(site, True)
                    print(f"✅ {site.upper()} enabled")
                
                elif command == 'disable' and len(parts) > 1:
                    site = parts[1]
                    set_site_enabled(site, False)
                    if site in self.running_monitors:
                        self.stop_monitor(site)
                    print(f"⏸️ {site.upper()} disabled")
                
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
                        print(f"✅ {site.upper()}: {key}={value}")
                    except:
                        print("❌ Format: config site key=value")
                
                elif command in ['list', 'ls']:
                    self.list_running()
                
                elif command in ['status', 'st']:
                    self.show_status()
                
                elif command in ['registry', 'reg']:
                    print_registry_status()
                
                elif command == 'reload':
                    print("🔄 Reloading registry...")
                    from monitoring.engine.site_registry import auto_discover_workers
                    auto_discover_workers()
                    print("✅ Registry reloaded")
                
                elif command in ['help', '?']:
                    print("\n" + "="*50)
                    print("Commands:")
                    print("  start <site>   - Start monitoring")
                    print("  stop <site>    - Stop monitoring")
                    print("  stop all       - Stop all")
                    print("  resume <site>  - Resume")
                    print("  resume all     - Resume all")
                    print("  enable <site>  - Enable site")
                    print("  disable <site> - Disable site")
                    print("  config site k=v - Update config")
                    print("  list / ls      - Running monitors")
                    print("  status / st    - All sites")
                    print("  exit           - Exit console")
                    print("="*50)
                    print("💡 Ctrl+C = Stop ALL immediately")
                
                else:
                    print(f"❌ Unknown: {command}. Type 'help'")
                    
            except KeyboardInterrupt:
                print("\n\n🛑 Stopping all monitors...")
                self.stop_all()
                print("✅ Exiting...")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
    
    def start_console(self):
        """Start the interactive console in a separate thread"""
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