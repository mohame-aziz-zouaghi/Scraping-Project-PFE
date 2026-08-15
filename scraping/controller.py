# scraping/controller.py
"""
Interactive controller for running scrapers with clean console UI
Mirrors the monitoring controller architecture
"""
import threading
import time
import sys
import signal
import os
from datetime import datetime
from typing import Dict, Optional

from core.console_manager import get_console, Colors
from sites.loader import load_sites
from scraping.engine.scrape_engine import ScrapeEngine
from scraping.engine.multi_scrape_progress import get_multi_scrape_progress
from core.logger import setup_logger

logger = setup_logger("scrape_controller", "scraper")

# Get global console
console = get_console()

class ScrapeController:
    """
    Interactive controller for running scrapers with clean console UI
    """
    
    def __init__(self):
        self.running_scrapers: Dict[str, threading.Thread] = {}
        self.stop_events: Dict[str, threading.Event] = {}
        self.scrape_engine = None
        self.console_thread = None
        self.is_running = False
        self.stopped_sites: set = set()
        self.sites = load_sites()  # Load all sites
        
        # Setup signal handler
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C to stop all scrapers immediately"""
        console.print("\n", Colors.RED)
        console.print("🛑 STOPPING ALL SCRAPERS", Colors.RED)
        console.print("=" * 60, Colors.RED)
        self.stop_all()
        console.print("✅ All scrapers stopped. Exiting...", Colors.GREEN)
        sys.exit(0)
    
    def _clear_line(self):
        """Clear the current line"""
        console._clear_line()
    
    def set_engine(self, engine):
        """Set the scrape engine reference"""
        self.scrape_engine = engine
    
    def start_scraper(self, site_name: str, categories: list = None):
        """Start scraping for a specific site"""
        site_name = site_name.upper()
        
        if site_name in self.running_scrapers and self.running_scrapers[site_name].is_alive():
            console.print_warning(f"{site_name.upper()} is already running!")
            return False
        
        # Get site instance
        site = self.sites.get(site_name)
        if not site:
            console.print_error(f"Site '{site_name}' not found!")
            console.print_info(f"   Available sites: {', '.join(self.sites.keys())}")
            return False
        
        if site_name in self.stopped_sites:
            self.stopped_sites.remove(site_name)
        
        stop_event = threading.Event()
        self.stop_events[site_name] = stop_event
        
        thread = threading.Thread(
            target=self._scrape_with_stop,
            args=(site_name, site, categories, stop_event),
            name=f"Scraper-{site_name}",
            daemon=True
        )
        thread.start()
        
        self.running_scrapers[site_name] = thread
        console.print_success(f"Started: {site_name.upper()}")
        return True
    
    def start_all_scrapers(self):
        """Start scraping for all sites"""
        if not self.sites:
            console.print_error("No sites available!")
            return False
        
        console.print("\n", Colors.CYAN)
        console.print("🚀 STARTING ALL SCRAPERS", Colors.CYAN)
        console.print("=" * 60, Colors.CYAN)
        
        started_count = 0
        for site_name in self.sites.keys():
            if site_name not in self.running_scrapers or not self.running_scrapers[site_name].is_alive():
                if self.start_scraper(site_name):
                    started_count += 1
        
        console.print_success(f"Started {started_count} scraper(s)")
        console.print("=" * 60, Colors.CYAN)
        return started_count > 0
    
    def _scrape_with_stop(self, site_name: str, site, categories: list, stop_event: threading.Event):
        """Scrape a site with stop capability"""
        try:
            # The engine handles progress setup - DO NOT add site here
            # Run the scrape
            result = self.scrape_engine.scrape_site(site_name, site, categories)
            
            # Check if stopped
            if stop_event.is_set():
                console.print_warning(f"{site_name.upper()} stopped by user")
                self.scrape_engine.progress.set_status(site_name, "⏹️ Stopped")
            else:
                console.print_success(f"{site_name.upper()} completed - {result.get('new_products', 0)} new products")
                
        except Exception as e:
            console.print_error(f"{site_name.upper()} scrape error: {e}")
            logger.error(f"{site_name} | SCRAPE_ERROR | {e}")
            self.scrape_engine.progress.set_status(site_name, f"❌ Error")
        finally:
            # Cleanup
            if site_name in self.running_scrapers:
                del self.running_scrapers[site_name]
            if site_name in self.stop_events:
                del self.stop_events[site_name]
            if site_name in self.stopped_sites:
                self.stopped_sites.remove(site_name)
    
    def stop_scraper(self, site_name: str):
        """Stop scraping for a specific site"""
        site_name = site_name.lower()
        
        if site_name not in self.stop_events:
            console.print_warning(f"{site_name.upper()} is not running!")
            return False
        
        self.stop_events[site_name].set()
        self.stopped_sites.add(site_name)
        console.print_warning(f"Stopping: {site_name.upper()}...")
        return True
    
    def stop_all(self):
        """Stop all running scrapers immediately"""
        console.print("\n", Colors.RED)
        console.print("🛑 STOPPING ALL SCRAPERS", Colors.RED)
        console.print("=" * 60, Colors.RED)
        
        for site_name in list(self.stop_events.keys()):
            self.stop_events[site_name].set()
            self.stopped_sites.add(site_name)
            console.print_warning(f"   Stopping: {site_name.upper()}")
        
        time.sleep(0.5)
        
        for site_name, thread in list(self.running_scrapers.items()):
            if thread.is_alive():
                thread.join(timeout=2)
        
        self.running_scrapers.clear()
        self.stop_events.clear()
        
        console.print("=" * 60, Colors.RED)
        console.print_success("All scrapers stopped")
        console.print("=" * 60, Colors.RED)
    
    def list_running(self):
        """List all running scrapers with their status"""
        console.print("\n", Colors.CYAN)
        console.print("🕷️ SCRAPER STATUS", Colors.CYAN)
        console.print("=" * 50, Colors.CYAN)
        
        if not self.running_scrapers and not self.stopped_sites:
            console.print("   No scrapers running", Colors.DIM)
        else:
            all_sites = set(self.running_scrapers.keys()) | set(self.stopped_sites)
            for site in all_sites:
                is_running = site in self.running_scrapers and self.running_scrapers[site].is_alive()
                is_stopped = site in self.stopped_sites
                
                if is_running:
                    status = f"{Colors.GREEN}🟢 RUNNING{Colors.END}"
                elif is_stopped:
                    status = f"{Colors.RED}⏹️ STOPPED{Colors.END}"
                else:
                    status = f"{Colors.DIM}⚪ IDLE{Colors.END}"
                
                console.print(f"   {site.upper():10} | {status}")
        
        console.print("=" * 50, Colors.CYAN)
        return list(self.running_scrapers.keys())
    
    def show_status(self):
        """Show detailed status of all sites"""
        console.print("\n", Colors.MAGENTA)
        console.print("📊 SCRAPING STATUS", Colors.MAGENTA)
        console.print("=" * 50, Colors.MAGENTA)
        
        all_sites = set(self.sites.keys()) | set(self.running_scrapers.keys()) | set(self.stopped_sites)
        
        for site in all_sites:
            is_running = site in self.running_scrapers and self.running_scrapers[site].is_alive()
            is_stopped = site in self.stopped_sites
            
            if is_running:
                status = f"{Colors.GREEN}🟢 RUNNING{Colors.END}"
            elif is_stopped:
                status = f"{Colors.RED}⏹️ STOPPED{Colors.END}"
            else:
                status = f"{Colors.DIM}⚪ IDLE{Colors.END}"
            
            console.print(f"   {site.upper():10} | {status}")
        
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
        console.print("                    🕷️ SCRAPING CONSOLE", Colors.CYAN)
        console.print("=" * 60, Colors.CYAN)
        console.print("")
        console.print("  Commands:", Colors.BOLD)
        console.print("")
        console.print(f"\n    {Colors.GREEN}start <site>{Colors.END}   - Start scraping site")
        console.print(f"    {Colors.GREEN}start all{Colors.END}      - Start all sites")
        console.print(f"    {Colors.YELLOW}stop <site>{Colors.END}    - Stop scraping site")
        console.print(f"    {Colors.RED}stop all{Colors.END}       - Stop all")
        console.print(f"    {Colors.CYAN}list{Colors.END}           - Running scrapers")
        console.print(f"    {Colors.CYAN}status{Colors.END}         - All sites")
        console.print(f"    {Colors.RED}exit{Colors.END}           - Exit console")
        console.print("", Colors.DIM)
        console.print("-" * 60, Colors.DIM)
        console.print(f"  {Colors.YELLOW}💡 Press Ctrl+C to stop ALL scrapers immediately{Colors.END}")
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
                    console.print_warning("👋 Exiting console. Scrapers continue running.")
                    console.print_warning("   Press Ctrl+C to stop everything.")
                    break
                
                elif command == 'stop' and len(parts) > 1 and parts[1] == 'all':
                    self.stop_all()
                
                elif command == 'stop' and len(parts) > 1:
                    self.stop_scraper(parts[1])
                
                elif command == 'start' and len(parts) > 1 and parts[1] == 'all':
                    self.start_all_scrapers()
                
                elif command == 'start' and len(parts) > 1:
                    self.start_scraper(parts[1])
                
                elif command in ['list', 'ls']:
                    self.list_running()
                
                elif command in ['status', 'st']:
                    self.show_status()
                
                elif command in ['help', '?']:
                    console.print("\n" + "="*50, Colors.CYAN)
                    console.print("Available Commands:", Colors.BOLD)
                    console.print(f"  {Colors.GREEN}start <site>{Colors.END}   - Start scraping site")
                    console.print(f"  {Colors.GREEN}start all{Colors.END}      - Start all sites")
                    console.print(f"  {Colors.YELLOW}stop <site>{Colors.END}    - Stop scraping site")
                    console.print(f"  {Colors.RED}stop all{Colors.END}       - Stop all")
                    console.print(f"  {Colors.CYAN}list{Colors.END}           - Running scrapers")
                    console.print(f"  {Colors.CYAN}status{Colors.END}         - All sites")
                    console.print(f"  {Colors.RED}exit{Colors.END}           - Exit console")
                    console.print("="*50, Colors.CYAN)
                    console.print(f"{Colors.YELLOW}💡 Ctrl+C = Stop ALL immediately{Colors.END}")
                
                else:
                    console.print_error(f"Unknown: {command}. Type 'help'")
                    
            except KeyboardInterrupt:
                console.print("\n", Colors.RED)
                console.print("🛑 Stopping all scrapers...", Colors.RED)
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
_scrape_controller = None


def get_scrape_controller():
    """Get global scrape controller instance"""
    global _scrape_controller
    if _scrape_controller is None:
        _scrape_controller = ScrapeController()
    return _scrape_controller