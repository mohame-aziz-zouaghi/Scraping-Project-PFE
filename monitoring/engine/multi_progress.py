# monitoring/engine/multi_progress.py
"""
Multi-progress display for monitoring all sites simultaneously
"""
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from datetime import datetime
from typing import Dict, Optional
import time

console = Console()


class MultiSiteProgress:
    """
    Manages progress bars for multiple sites simultaneously
    """
    
    def __init__(self):
        self.sites: Dict[str, dict] = {}
        self.live = None
        self.running = False
        
    def add_site(self, site: str, total_products: int):
        """Add a site to the progress display"""
        self.sites[site] = {
            'total': total_products,
            'completed': 0,
            'changed': 0,
            'no_change': 0,
            'errors': 0,
            'status': '🔄 Starting...',
            'current_product': '',
            'started_at': datetime.now(),
            'elapsed': '0s',
            'eta': '--'
        }
        
    def update_progress(self, site: str, completed: int, changed: int, no_change: int, errors: int, status: str = None):
        """Update progress for a site"""
        if site in self.sites:
            self.sites[site]['completed'] = completed
            self.sites[site]['changed'] = changed
            self.sites[site]['no_change'] = no_change
            self.sites[site]['errors'] = errors
            if status:
                self.sites[site]['status'] = status
            self._refresh()
    
    def set_status(self, site: str, status: str):
        """Update status message for a site"""
        if site in self.sites:
            self.sites[site]['status'] = status
            self._refresh()
    
    def set_current_product(self, site: str, product_id: str):
        """Update current product being processed"""
        if site in self.sites:
            self.sites[site]['current_product'] = product_id
            self._refresh()
    
    def _refresh(self):
        """Refresh the display"""
        if self.live:
            self.live.update(self._create_layout())
    
    def _create_layout(self):
        """Create the layout with all site progress bars"""
        # Header
        header = Panel(
            f"[bold cyan]📊 MONITORING DASHBOARD[/bold cyan]  |  [dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
            style="bold white"
        )
        
        # Create table for all sites
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
        table.add_column("Site", style="bold white", width=12)
        table.add_column("Progress", width=35)
        table.add_column("Stats", style="white", width=25)
        table.add_column("Status", style="yellow", width=20)
        table.add_column("ETA", style="dim", width=12)
        
        for site, data in self.sites.items():
            total = data['total']
            completed = data['completed']
            changed = data['changed']
            no_change = data['no_change']
            errors = data['errors']
            status = data.get('status', '')
            
            # Progress bar
            if total > 0:
                percent = (completed / total) * 100 if total > 0 else 0
                bar_len = 25
                filled = int(bar_len * completed / total) if total > 0 else 0
                bar = f"[green]{'█' * filled}[/green]{'░' * (bar_len - filled)}"
                progress_text = f"{bar} {percent:>5.1f}%"
            else:
                progress_text = "[dim]No products[/dim]"
            
            # Stats
            stats = f"✓ {changed}  ○ {no_change}  ✗ {errors}"
            
            # Color code status
            if 'Complete' in status or 'Finished' in status:
                status_color = "green"
            elif 'Error' in status or 'Failed' in status:
                status_color = "red"
            elif 'Paused' in status:
                status_color = "yellow"
            elif 'Stopped' in status:
                status_color = "red"
            else:
                status_color = "cyan"
            
            # ETA calculation
            if completed > 0 and total > 0:
                started = data.get('started_at', datetime.now())
                elapsed = (datetime.now() - started).seconds
                if completed > 0 and elapsed > 0:
                    rate = completed / elapsed if elapsed > 0 else 0
                    remaining = (total - completed) / rate if rate > 0 else 0
                    if remaining < 60:
                        eta = f"{int(remaining)}s"
                    elif remaining < 3600:
                        eta = f"{int(remaining/60)}m"
                    else:
                        eta = f"{int(remaining/3600)}h"
                else:
                    eta = "--"
            else:
                eta = "--"
            
            table.add_row(
                f"[bold]{site.upper()}[/bold]",
                progress_text,
                stats,
                f"[{status_color}]{status}[/{status_color}]",
                eta
            )
        
        # Footer with summary
        total_products = sum(d.get('total', 0) for d in self.sites.values())
        total_completed = sum(d.get('completed', 0) for d in self.sites.values())
        total_changed = sum(d.get('changed', 0) for d in self.sites.values())
        total_errors = sum(d.get('errors', 0) for d in self.sites.values())
        
        footer = Panel(
            f"[dim]Total: {total_completed}/{total_products} products  |  Changes: {total_changed}  |  Errors: {total_errors}[/dim]",
            style="dim"
        )
        
        # Combine
        layout = Layout()
        layout.split(
            Layout(header, size=3),
            Layout(table, ratio=1),
            Layout(footer, size=3)
        )
        
        return layout
    
    def start(self):
        """Start the live display"""
        self.running = True
        self.live = Live(self._create_layout(), console=console, refresh_per_second=4)
        self.live.__enter__()
    
    def stop(self):
        """Stop the live display"""
        self.running = False
        if self.live:
            self.live.__exit__(None, None, None)
            self.live = None
    
    def close(self):
        """Close the progress display"""
        self.stop()


# Global instance
_multi_progress = None


def get_multi_progress():
    """Get or create the global multi-progress instance"""
    global _multi_progress
    if _multi_progress is None:
        _multi_progress = MultiSiteProgress()
    return _multi_progress