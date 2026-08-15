# scraping/engine/multi_scrape_progress.py
"""
Unified scraping dashboard - Efficient progress display with ETA and countdown
"""
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from datetime import datetime, timedelta
from typing import Dict
import threading
import time

console = Console()


class MultiScrapeProgress:
    """Unified dashboard for all scraping sites"""
    
    def __init__(self):
        self.sites: Dict[str, dict] = {}
        self.live = None
        self.running = False
        
    def add_site(self, site: str, total: int = 0):
        """Add a site to the dashboard - only if not already exists"""
        if site not in self.sites:
            self.sites[site] = {
                'total': total if total > 0 else 1,
                'completed': 0,
                'new_products': 0,
                'skipped': 0,
                'errors': 0,
                'redis_saved': 0,
                'postgres_saved': 0,
                'status': '🔄 Starting...',
                'current_item': '',
                'started_at': datetime.now(),
                'last_update': datetime.now(),
                'next_scrape': None,
                'scrape_interval': 300,
                'cycle_count': 0,
                'is_complete': False,
                'eta': '--',
            }
            self._refresh()
            return True
        else:
            if total > self.sites[site]['total'] and not self.sites[site].get('is_complete', False):
                self.sites[site]['total'] = total
                self._refresh()
            return False
    
    def reset_site(self, site: str):
        """Reset site stats for a new cycle"""
        if site in self.sites:
            self.sites[site]['new_products'] = 0
            self.sites[site]['skipped'] = 0
            self.sites[site]['errors'] = 0
            self.sites[site]['redis_saved'] = 0
            self.sites[site]['postgres_saved'] = 0
            self.sites[site]['completed'] = 0
            self.sites[site]['status'] = '🔄 Starting...'
            self.sites[site]['current_item'] = ''
            self.sites[site]['started_at'] = datetime.now()
            self.sites[site]['last_update'] = datetime.now()
            self.sites[site]['is_complete'] = False
            self.sites[site]['cycle_count'] = self.sites[site].get('cycle_count', 0) + 1
            self._refresh()
    
    def update(self, site: str, **kwargs):
        if site in self.sites:
            for key, value in kwargs.items():
                if key in self.sites[site]:
                    self.sites[site][key] = value
            self.sites[site]['last_update'] = datetime.now()
            self._calculate_eta(site)
            self._refresh()
    
    def update_stats(self, site: str, **kwargs):
        if site in self.sites:
            for key in ['new_products', 'skipped', 'errors', 'redis_saved', 'postgres_saved']:
                if key in kwargs:
                    self.sites[site][key] = kwargs[key]
            self.sites[site]['last_update'] = datetime.now()
            self._calculate_eta(site)
            self._refresh()
    
    def set_status(self, site: str, status: str):
        if site in self.sites:
            self.sites[site]['status'] = status
            if 'Complete' in status or 'Done' in status:
                self.sites[site]['is_complete'] = True
                self.sites[site]['eta'] = '✅ Done'
            else:
                self.sites[site]['is_complete'] = False
            self._refresh()
    
    def set_current_item(self, site: str, item: str):
        if site in self.sites:
            self.sites[site]['current_item'] = item[:40] if item else ''
            self._refresh()
    
    def set_current_category(self, site: str, category: str):
        self.set_current_item(site, category)
    
    def set_current_product(self, site: str, product: str):
        self.set_current_item(site, product)
    
    def set_next_scrape(self, site: str, interval: int):
        """Set when the next scrape will happen"""
        if site in self.sites:
            self.sites[site]['scrape_interval'] = interval
            self.sites[site]['next_scrape'] = datetime.now() + timedelta(seconds=interval)
            self._refresh()
    
    def increment_new_product(self, site: str):
        if site in self.sites:
            self.sites[site]['new_products'] += 1
            self.sites[site]['status'] = "✅ +1 NEW"
            self._refresh()
            def reset():
                time.sleep(1.5)
                if site in self.sites and self.sites[site]['status'] == "✅ +1 NEW":
                    self.sites[site]['status'] = "🔄 Running"
                    self._refresh()
            threading.Thread(target=reset, daemon=True).start()
    
    def _calculate_eta(self, site: str):
        """Calculate ETA based on current progress"""
        if site not in self.sites:
            return
        
        data = self.sites[site]
        total = data.get('total', 1)
        completed = data.get('completed', 0)
        started_at = data.get('started_at', datetime.now())
        
        if completed > 0 and total > 0 and completed < total:
            elapsed = (datetime.now() - started_at).total_seconds()
            if elapsed > 0:
                rate = completed / elapsed
                remaining = (total - completed) / rate if rate > 0 else 0
                
                if remaining < 60:
                    data['eta'] = f"{int(remaining)}s"
                elif remaining < 3600:
                    data['eta'] = f"{int(remaining/60)}m {int(remaining%60)}s"
                else:
                    hours = int(remaining / 3600)
                    minutes = int((remaining % 3600) / 60)
                    data['eta'] = f"{hours}h {minutes}m"
            else:
                data['eta'] = "--"
        elif completed >= total and total > 0:
            data['eta'] = "✅ Done"
        else:
            data['eta'] = "--"
    
    def _format_countdown(self, seconds: int) -> str:
        """Format countdown in hh:mm:ss format"""
        if seconds < 0:
            return "🔴 NOW"
        
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"
    
    def _refresh(self):
        if self.live:
            self.live.update(self._create_layout())
    
    # scraping/engine/multi_scrape_progress.py - Updated _create_layout

    def _create_layout(self):
        header = Panel(
            f"[bold green]🕷️ SCRAPING DASHBOARD[/bold green]  |  "
            f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
            style="bold white"
        )
        
        table = Table(
            show_header=True,
            header_style="bold green",
            box=None,
            padding=(0, 1),
            show_lines=False,
            border_style="dim"
        )
        
        table.add_column("Site", style="bold white", width=12, no_wrap=True)
        table.add_column("Progress", width=38)
        table.add_column("Stats", style="white", width=26)
        table.add_column("Status", style="yellow", width=16)
        table.add_column("ETA", style="dim", width=14)
        table.add_column("⏱️ Next", style="dim", width=14)
        
        table.add_row("─" * 12, "─" * 38, "─" * 26, "─" * 16, "─" * 14, "─" * 14, style="dim")
        
        site_count = len(self.sites)
        for idx, (site, data) in enumerate(self.sites.items()):
            total = data.get('total', 1)
            completed = data.get('completed', 0)
            new_products = data.get('new_products', 0)
            skipped = data.get('skipped', 0)
            errors = data.get('errors', 0)
            redis_saved = data.get('redis_saved', 0)
            postgres_saved = data.get('postgres_saved', 0)
            status = data.get('status', '')
            current_item = data.get('current_item', '')
            started_at = data.get('started_at', datetime.now())
            last_update = data.get('last_update', datetime.now())
            next_scrape = data.get('next_scrape')
            is_complete = data.get('is_complete', False)
            cycle_count = data.get('cycle_count', 0)
            eta = data.get('eta', '--')
            
            # Ensure completed never exceeds total
            if completed > total and total > 0:
                completed = total
                self.sites[site]['completed'] = total
            
            # Calculate percentage based on CATEGORIES
            if total > 0:
                percent = min((completed / total) * 100, 100.0)
            else:
                percent = 0
            
            # Check if site is stale
            is_stale = (datetime.now() - last_update).seconds > 45
            if is_stale and 'Complete' not in status and 'Error' not in status and '+1' not in status:
                status_display = "⏳ Waiting..."
            elif 'Complete' in status or 'Done' in status:
                status_display = "✅ Done"
            elif 'Error' in status:
                status_display = "❌ Error"
            elif '+1' in status:
                status_display = "🟡 +1 New"
            else:
                status_display = f"🔄 Cycle {cycle_count + 1}"
            
            # Progress bar - show category progress
            if total > 0:
                bar_len = 20
                filled = int(bar_len * completed / total) if total > 0 else 0
                if filled > bar_len:
                    filled = bar_len
                bar = f"[green]{'█' * filled}[/green]{'░' * (bar_len - filled)}"
                
                # Show category progress
                progress_text = f"{bar}  {percent:>5.1f}%"
                
                # Show category name with progress
                if current_item and current_item != 'Starting...':
                    # Extract category name without extra info
                    category_display = current_item
                    if '|' in category_display:
                        category_display = category_display.split('|')[0].strip()
                    progress_text += f"\n   [dim]📁 {category_display}[/dim]"
                
                # Show category count progress (e.g., 25/339)
                if total > 0:
                    progress_text += f"\n   [dim]📊 {completed}/{total} categories[/dim]"
            else:
                progress_text = "[dim]No categories[/dim]"
            
            # Stats
            if new_products > 0:
                stats = f"[bold green]✓ {new_products}[/bold green]  ○ {skipped}  ✗ {errors}"
            else:
                stats = f"✓ {new_products}  ○ {skipped}  ✗ {errors}"
            
            if postgres_saved > 0:
                stats += f"\n   [dim]💾 Saved: {postgres_saved} (PG)[/dim]"
            
            # Status color
            if 'Done' in status_display:
                status_color = "green"
            elif 'Error' in status_display:
                status_color = "red"
            elif 'Waiting' in status_display:
                status_color = "yellow"
            elif '+1' in status_display:
                status_color = "yellow"
            else:
                status_color = "cyan"
            
            # ETA display
            if eta == "✅ Done":
                eta_display = "[green]✅ Done[/green]"
            elif eta == "--":
                eta_display = "--"
            else:
                eta_display = f"[dim]{eta}[/dim]"
            
            # Next column
            next_display = "--"
            if is_complete and next_scrape:
                remaining = (next_scrape - datetime.now()).total_seconds()
                if remaining > 0:
                    next_display = self._format_countdown(remaining)
                elif remaining <= 0:
                    next_display = "🔴 NOW"
                else:
                    next_display = "--"
            elif 'Done' in status_display:
                next_display = "✅ Done"
            elif 'Error' in status_display:
                next_display = "--"
            
            table.add_row(
                f"[bold]{site.upper()}[/bold]",
                progress_text,
                stats,
                f"[{status_color}]{status_display}[/{status_color}]",
                eta_display,
                next_display
            )
            
            if idx < site_count - 1:
                table.add_row("─" * 12, "─" * 38, "─" * 26, "─" * 16, "─" * 14, "─" * 14, style="dim")
        
        # Footer
        total_new = sum(d.get('new_products', 0) for d in self.sites.values())
        total_errors = sum(d.get('errors', 0) for d in self.sites.values())
        total_completed = sum(d.get('completed', 0) for d in self.sites.values())
        total_all = sum(d.get('total', 0) for d in self.sites.values())
        total_skipped = sum(d.get('skipped', 0) for d in self.sites.values())
        
        overall_percent = (total_completed / total_all * 100) if total_all > 0 else 0
        
        footer = Panel(
            f"[dim]📊 {total_completed}/{total_all} categories  "
            f"({overall_percent:.1f}%)  |  "
            f"📦 {total_new} new  |  "
            f"⏭️ {total_skipped} skipped  |  "
            f"❌ {total_errors} errors[/dim]",
            style="dim"
        )
        
        layout = Layout()
        layout.split(
            Layout(header, size=3),
            Layout(table, ratio=1),
            Layout(footer, size=3)
        )
        return layout
    
    def start(self):
        self.running = True
        self.live = Live(self._create_layout(), console=console, refresh_per_second=2)
        self.live.__enter__()
    
    def stop(self):
        self.running = False
        if self.live:
            self.live.__exit__(None, None, None)
            self.live = None
    
    def close(self):
        self.stop()


_multi_scrape_progress = None


def get_multi_scrape_progress():
    global _multi_scrape_progress
    if _multi_scrape_progress is None:
        _multi_scrape_progress = MultiScrapeProgress()
    return _multi_scrape_progress