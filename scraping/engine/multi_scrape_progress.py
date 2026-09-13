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
from typing import Dict, List
import threading
import time
import logging

logger = logging.getLogger("multi_scrape_progress")

console = Console()


class MultiScrapeProgress:
    """Unified dashboard for all scraping sites"""
    
    def __init__(self):
        self.sites: Dict[str, dict] = {}
        self.live = None
        self.running = False
        
        # Error collection
        self.recent_errors: List[Dict] = []
        self.max_errors_display = 5
        self.error_lock = threading.Lock()
        
    def add_error(self, site: str, message: str, error_type: str = "error"):
        """Add an error to the recent errors list"""
        with self.error_lock:
            self.recent_errors.append({
                'site': site,
                'message': message[:60],
                'type': error_type,
                'time': datetime.now()
            })
            if len(self.recent_errors) > 20:
                self.recent_errors = self.recent_errors[-20:]
            self._refresh()
    
    def clear_errors(self):
        """Clear all recent errors"""
        with self.error_lock:
            self.recent_errors = []
            self._refresh()
    
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
                'page': 0,
                'total_pages': 0,
                'products_processed': 0,
                'total_products': 0,
                'last_cycle_complete': False,
                'is_running': False,
                'completed_cycle_count': 0,
            }
            self._refresh()
            return True
        else:
            if total > self.sites[site]['total'] and not self.sites[site].get('is_complete', False):
                self.sites[site]['total'] = total
                self._refresh()
            return False
    
    def reset_site(self, site: str):
        """
        Reset site stats for a NEW cycle.
        Resets ALL stats to 0 for the new cycle.
        """
        if site in self.sites:
            # ─── RESET ALL STATS TO 0 ────────────────────────────
            self.sites[site]['new_products'] = 0
            self.sites[site]['skipped'] = 0  # ← Reset to 0
            self.sites[site]['errors'] = 0
            self.sites[site]['redis_saved'] = 0
            self.sites[site]['postgres_saved'] = 0
            self.sites[site]['completed'] = 0
            self.sites[site]['status'] = '🔄 Starting...'
            self.sites[site]['current_item'] = 'Starting...'
            self.sites[site]['started_at'] = datetime.now()
            self.sites[site]['last_update'] = datetime.now()
            self.sites[site]['is_complete'] = False
            self.sites[site]['page'] = 0
            self.sites[site]['total_pages'] = 0
            self.sites[site]['products_processed'] = 0
            self.sites[site]['total_products'] = 0
            self.sites[site]['eta'] = '--'
            self.sites[site]['next_scrape'] = None
            self.sites[site]['is_running'] = True
            self.sites[site]['cycle_completed_at'] = None
            
            # Clear errors for this site
            with self.error_lock:
                self.recent_errors = [e for e in self.recent_errors if e.get('site') != site]
            
            logger.info(f"RESET_SITE | {site} | cycle={self.sites[site]['cycle_count'] + 1}")
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
            stat_mapping = {
                'new_products': 'new_products',
                'skipped_duplicates': 'skipped',
                'errors': 'errors',
                'redis_saved': 'redis_saved',
                'postgres_saved': 'postgres_saved',
                'total_products_found': 'total_products',
                'completed_categories': 'completed',
                'total_categories': 'total',
                'current_page': 'page',
                'total_pages': 'total_pages',
                'current_category': 'current_item',
            }
            
            for key, value in kwargs.items():
                if key in stat_mapping:
                    mapped_key = stat_mapping[key]
                    if mapped_key in self.sites[site]:
                        self.sites[site][mapped_key] = value
                elif key in self.sites[site]:
                    self.sites[site][key] = value
            
            self.sites[site]['last_update'] = datetime.now()
            self._calculate_eta(site)
            self._refresh()
    
    def set_status(self, site: str, status: str):
        if site in self.sites:
            self.sites[site]['status'] = status
            self.sites[site]['last_update'] = datetime.now()
            
            if 'Complete' in status or 'Done' in status:
                self.sites[site]['is_complete'] = True
                self.sites[site]['is_running'] = False
                self.sites[site]['eta'] = '✅ Done'
                self.sites[site]['cycle_completed_at'] = datetime.now()
                self.sites[site]['last_cycle_status'] = status
                self.sites[site]['cycle_count'] += 1
                self.sites[site]['completed_cycle_count'] = self.sites[site].get('completed_cycle_count', 0) + 1
                logger.info(f"CYCLE_COMPLETE | {site} | cycle={self.sites[site]['cycle_count']}")
            elif 'Starting' in status or 'Cycle' in status:
                self.sites[site]['is_complete'] = False
                self.sites[site]['is_running'] = True
                self.sites[site]['eta'] = '--'
            elif 'Error' in status:
                self.sites[site]['is_complete'] = True
                self.sites[site]['is_running'] = False
            else:
                self.sites[site]['is_complete'] = False
                self.sites[site]['is_running'] = True
            
            logger.debug(f"SET_STATUS | {site} | status={status}")
            self._refresh()
    
    def set_current_item(self, site: str, item: str):
        if site in self.sites:
            self.sites[site]['current_item'] = item[:40] if item else ''
            self.sites[site]['last_update'] = datetime.now()
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
            self.sites[site]['last_update'] = datetime.now()
            self._refresh()
    
    def increment_error(self, site: str, error_msg: str = ""):
        if site in self.sites:
            self.sites[site]['errors'] += 1
            self.sites[site]['last_update'] = datetime.now()
            if error_msg:
                self.add_error(site, error_msg)
            self._refresh()
    
    def _calculate_eta(self, site: str):
        """Calculate ETA based on current progress"""
        if site not in self.sites:
            return
        
        data = self.sites[site]
        total = data.get('total', 1)
        completed = data.get('completed', 0)
        started_at = data.get('started_at', datetime.now())
        is_complete = data.get('is_complete', False)
        is_running = data.get('is_running', False)
        
        if is_complete or completed >= total:
            data['eta'] = "✅ Done"
        elif is_running and completed > 0 and total > 0:
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
        table.add_column("Stats", style="white", width=28)
        table.add_column("Status", style="yellow", width=18)
        table.add_column("ETA", style="dim", width=14)
        table.add_column("⏱️ Next", style="dim", width=14)
        
        table.add_row("─" * 12, "─" * 38, "─" * 28, "─" * 18, "─" * 14, "─" * 14, style="dim")
        
        site_count = len(self.sites)
        for idx, (site, data) in enumerate(self.sites.items()):
            total = data.get('total', 1)
            completed = data.get('completed', 0)
            
            # Current cycle stats only
            new_products = data.get('new_products', 0)
            skipped = data.get('skipped', 0)  # ← Only current cycle skipped
            errors = data.get('errors', 0)
            redis_saved = data.get('redis_saved', 0)
            postgres_saved = data.get('postgres_saved', 0)
            
            status = data.get('status', '')
            current_item = data.get('current_item', '')
            page = data.get('page', 0)
            total_pages = data.get('total_pages', 0)
            total_products = data.get('total_products', 0)
            products_processed = data.get('products_processed', 0)
            is_complete = data.get('is_complete', False)
            cycle_count = data.get('cycle_count', 0)
            completed_cycle_count = data.get('completed_cycle_count', 0)
            eta = data.get('eta', '--')
            next_scrape = data.get('next_scrape')
            last_update = data.get('last_update', datetime.now())
            is_running = data.get('is_running', False)
            
            if completed > total and total > 0:
                completed = total
                self.sites[site]['completed'] = total
            
            if total > 0:
                percent = min((completed / total) * 100, 100.0)
            else:
                percent = 0
            
            # ─── DETERMINE STATUS DISPLAY ──────────────────────
            display_cycle = completed_cycle_count if completed_cycle_count > 0 else cycle_count
            
            if is_complete or 'Done' in status or 'Complete' in status:
                if display_cycle > 0:
                    status_display = f"✅ Cycle {display_cycle} Done"
                else:
                    status_display = "✅ Done"
                status_color = "green"
            elif is_running or 'Cycle' in status or 'Running' in status:
                if display_cycle >= 0:
                    status_display = f"🔄 Cycle {display_cycle + 1}"
                else:
                    status_display = "🔄 Starting..."
                status_color = "cyan"
            elif 'Error' in status:
                status_display = "❌ Error"
                status_color = "red"
            elif (datetime.now() - last_update).seconds > 60:
                status_display = "⏳ Waiting..."
                status_color = "yellow"
            else:
                status_display = "🔄 Running"
                status_color = "cyan"
            
            # ─── PROGRESS BAR ──────────────────────────────────
            if total > 0:
                bar_len = 20
                filled = int(bar_len * completed / total) if total > 0 else 0
                if filled > bar_len:
                    filled = bar_len
                bar = f"[green]{'█' * filled}[/green]{'░' * (bar_len - filled)}"
                progress_text = f"{bar}  {percent:>5.1f}%"
            else:
                progress_text = "[dim]No categories[/dim]"
            
            # ─── CURRENT ITEM ──────────────────────────────────
            if current_item and current_item != 'Starting...':
                category_display = current_item
                if page > 0 and total_pages > 0:
                    category_display = f"Page {page}/{total_pages}"
                elif len(category_display) > 30:
                    category_display = category_display[:27] + "..."
                progress_text += f"\n   [dim]📁 {category_display}[/dim]"
            
            # ─── CATEGORY PROGRESS ─────────────────────────────
            if total > 0:
                progress_text += f"\n   [dim]📊 {completed}/{total} categories[/dim]"
            
            # ─── PRODUCT PROGRESS ──────────────────────────────
            if total_products > 0:
                progress_text += f"\n   [dim]📦 {products_processed}/{total_products} products[/dim]"
            
            # ─── STATS DISPLAY ──────────────────────────────────
            # Only show current cycle stats
            stats_parts = []
            if new_products > 0:
                stats_parts.append(f"[bold green]✓ {new_products}[/bold green]")
            else:
                stats_parts.append(f"✓ {new_products}")
            
            # Show skipped only if > 0 for current cycle
            if skipped > 0:
                stats_parts.append(f"[yellow]○ {skipped}[/yellow]")
            else:
                stats_parts.append(f"○ {skipped}")
            
            if errors > 0:
                stats_parts.append(f"[red]✗ {errors}[/red]")
            else:
                stats_parts.append(f"✗ {errors}")
            
            stats_text = "  ".join(stats_parts)
            
            # ─── SAVED INFO ──────────────────────────────────────
            saved_parts = []
            if postgres_saved > 0:
                saved_parts.append(f"💾 {postgres_saved} PG")
            if redis_saved > 0:
                saved_parts.append(f"💾 {redis_saved} Redis")
            if saved_parts:
                stats_text += f"\n   [dim]{' | '.join(saved_parts)}[/dim]"
            
            # ─── ETA ──────────────────────────────────────────
            if eta == "✅ Done":
                eta_display = "[green]✅ Done[/green]"
            elif eta == "--":
                eta_display = "--"
            else:
                eta_display = f"[dim]{eta}[/dim]"
            
            # ─── NEXT COLUMN ──────────────────────────────────
            next_display = "--"
            if is_complete and next_scrape:
                remaining = (next_scrape - datetime.now()).total_seconds()
                if remaining > 0:
                    next_display = self._format_countdown(remaining)
                elif remaining <= 0:
                    next_display = "🔴 NOW"
            elif is_complete:
                next_display = "✅ Done"
            
            # ─── SITE NAME WITH CYCLE NUMBER ────────────────────
            if display_cycle > 0:
                site_display = f"[bold]{site.upper()}[/bold] [dim]#{display_cycle}[/dim]"
            else:
                site_display = f"[bold]{site.upper()}[/bold]"
            
            table.add_row(
                site_display,
                progress_text,
                stats_text,
                f"[{status_color}]{status_display}[/{status_color}]",
                eta_display,
                next_display
            )
            
            if idx < site_count - 1:
                table.add_row("─" * 12, "─" * 38, "─" * 28, "─" * 18, "─" * 14, "─" * 14, style="dim")
        
        # ─── FOOTER ─────────────────────────────────────────────
        total_new = sum(d.get('new_products', 0) for d in self.sites.values())
        total_skipped = sum(d.get('skipped', 0) for d in self.sites.values())  # ← Current cycle only
        total_errors = sum(d.get('errors', 0) for d in self.sites.values())
        total_completed = sum(d.get('completed', 0) for d in self.sites.values())
        total_all = sum(d.get('total', 0) for d in self.sites.values())
        total_redis = sum(d.get('redis_saved', 0) for d in self.sites.values())
        total_postgres = sum(d.get('postgres_saved', 0) for d in self.sites.values())
        
        overall_percent = (total_completed / total_all * 100) if total_all > 0 else 0
        
        footer_parts = [
            f"📊 {total_completed}/{total_all} categories ({overall_percent:.1f}%)",
            f"📦 {total_new} new",
        ]
        
        # Only show skipped in footer if > 0
        if total_skipped > 0:
            footer_parts.append(f"⏭️ {total_skipped} skipped")
        
        saved_parts = []
        if total_postgres > 0:
            saved_parts.append(f"💾 {total_postgres} PG")
        if total_redis > 0:
            saved_parts.append(f"💾 {total_redis} Redis")
        if saved_parts:
            footer_parts.append(f"{' | '.join(saved_parts)}")
        
        if total_errors > 0:
            footer_parts.append(f"[red]❌ {total_errors} errors[/red]")
        else:
            footer_parts.append("✅ No errors")
        
        footer_text = "  |  ".join(footer_parts)
        
        error_section = ""
        with self.error_lock:
            if self.recent_errors:
                recent = self.recent_errors[-self.max_errors_display:]
                error_lines = []
                for err in recent:
                    site_name = err['site'].upper() if err['site'] else 'UNKNOWN'
                    msg = err['message']
                    if len(msg) > 50:
                        msg = msg[:47] + "..."
                    error_lines.append(f"[red]⚠[/red] [red]{site_name}:[/red] [dim]{msg}[/dim]")
                
                error_section = "\n".join(error_lines)
                error_header = f"[bold red]⚠ {len(self.recent_errors)} errors | Most recent:[/bold red]"
                
                if error_section:
                    footer = Panel(
                        f"{footer_text}\n\n{error_header}\n{error_section}",
                        style="dim"
                    )
                else:
                    footer = Panel(footer_text, style="dim")
            else:
                footer = Panel(footer_text, style="dim")
        
        layout = Layout()
        layout.split(
            Layout(header, size=3),
            Layout(table, ratio=1),
            Layout(footer, size=8 if error_section else 3)
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