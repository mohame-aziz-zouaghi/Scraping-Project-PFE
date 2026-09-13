"""
Unified Monitoring Dashboard - Thread-safe, snapshot-rendered, explicit states.

Key features:
- Single RLock guarding all state mutations
- Snapshot-based rendering (no mutation during render)
- Dedicated render thread at fixed FPS (decoupled from write load)
- Explicit state machine (running / waiting / completed / error / starting)
- Per-site error lists with deduplication
- Cycle counter incremented only via mark_complete()
- Graceful handling of 300+ concurrent writers
"""

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
import threading
import time
import logging

logger = logging.getLogger("multi_monitor_progress")

console = Console()


# =====================================================
# STATE CONSTANTS
# =====================================================

class SiteState:
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"      # Between cycles
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


# =====================================================
# SITE DATA (mutable per-site)
# =====================================================

@dataclass
class SiteData:
    site: str
    total: int = 0
    completed: int = 0
    changed: int = 0
    no_change: int = 0
    errors: int = 0
    current_cycle: int = 0            # Increments on cycle start
    state: str = SiteState.STARTING
    current_item: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    cycle_started_at: datetime = field(default_factory=datetime.now)
    last_update: datetime = field(default_factory=datetime.now)
    next_monitor: Optional[datetime] = None
    monitor_interval: int = 300
    rate: float = 0.0
    eta: str = "--"
    recent_errors: List[Dict] = field(default_factory=list)
    last_cycle_changed: int = 0
    last_cycle_completed: int = 0

    def snapshot(self) -> dict:
        """Immutable snapshot for rendering (avoids render-time mutation)."""
        return {
            'site': self.site,
            'total': self.total,
            'completed': self.completed,
            'changed': self.changed,
            'no_change': self.no_change,
            'errors': self.errors,
            'current_cycle': self.current_cycle,
            'state': self.state,
            'current_item': self.current_item,
            'started_at': self.started_at,
            'cycle_started_at': self.cycle_started_at,
            'last_update': self.last_update,
            'next_monitor': self.next_monitor,
            'monitor_interval': self.monitor_interval,
            'rate': self.rate,
            'eta': self.eta,
            'recent_errors': list(self.recent_errors[-3:]),
            'last_cycle_changed': self.last_cycle_changed,
            'last_cycle_completed': self.last_cycle_completed,
        }


# =====================================================
# DASHBOARD
# =====================================================

class MultiMonitorProgress:
    """Thread-safe monitoring dashboard."""

    RENDER_FPS = 4              # Render 4 times per second
    MAX_ERRORS_PER_SITE = 5     # Per-site error list cap
    ERROR_DEDUP_WINDOW = 30     # Dedup errors within 30s

    def __init__(self):
        self.sites: Dict[str, SiteData] = {}
        self._state_lock = threading.RLock()

        self.live: Optional[Live] = None
        self.running = False
        self._render_thread: Optional[threading.Thread] = None
        self._render_stop_event = threading.Event()

        # Snapshot cache (rendered from lock-free copy)
        self._snapshot_cache: Dict[str, dict] = {}
        self._snapshot_lock = threading.Lock()

        # Track unique render errors to avoid log spam
        self._seen_render_errors = set()

    # =================================================
    # PUBLIC API — all thread-safe
    # =================================================

    def add_site(self, site: str, total: int = 0) -> bool:
        """Add a site. If site exists, this is a no-op (safe to call repeatedly)."""
        site = site.lower()
        with self._state_lock:
            if site in self.sites:
                return False
            self.sites[site] = SiteData(
                site=site,
                total=total if total > 0 else 1,
            )
        return True

    def remove_site(self, site: str):
        site = site.lower()
        with self._state_lock:
            self.sites.pop(site, None)

    def start_cycle(self, site: str, total: int = 0, interval: int = 300):
        """
        Called when a new cycle begins. Resets stats and increments cycle counter.
        This is the ONLY place cycle counter increments.
        """
        site = site.lower()
        now = datetime.now()
        with self._state_lock:
            if site not in self.sites:
                self.sites[site] = SiteData(site=site)
            d = self.sites[site]
            # Save previous cycle stats
            d.last_cycle_changed = d.changed
            d.last_cycle_completed = d.completed

            # Reset current cycle
            d.total = total if total > 0 else 1
            d.completed = 0
            d.changed = 0
            d.no_change = 0
            d.errors = 0
            d.current_cycle += 1
            d.state = SiteState.RUNNING
            d.current_item = "Starting..."
            d.cycle_started_at = now
            d.last_update = now
            d.next_monitor = None
            d.monitor_interval = interval
            d.rate = 0.0
            d.eta = "--"
            d.recent_errors = []

    def mark_waiting(self, site: str, interval: int):
        """Called after a cycle completes — waiting for next monitor."""
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            d = self.sites[site]
            d.state = SiteState.WAITING
            d.monitor_interval = interval
            d.next_monitor = datetime.now() + timedelta(seconds=interval)
            d.last_update = datetime.now()

    def mark_running(self, site: str):
        """Called when a site's thread starts or resumes."""
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            d = self.sites[site]
            d.state = SiteState.RUNNING
            d.last_update = datetime.now()

    def mark_complete(self, site: str):
        """
        Called when a cycle completes. Does NOT increment cycle count
        (that happens in start_cycle).
        """
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            d = self.sites[site]
            d.state = SiteState.COMPLETED
            d.last_update = datetime.now()

    def mark_error(self, site: str, message: str = ""):
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            d = self.sites[site]
            d.state = SiteState.ERROR
            d.last_update = datetime.now()
            if message:
                self._append_error_locked(d, message)

    def mark_stopped(self, site: str):
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            d = self.sites[site]
            d.state = SiteState.STOPPED
            d.last_update = datetime.now()

    def update(self, site: str, **kwargs):
        """
        Generic state update. Only updates known fields.
        'completed' and 'changed' use max() to prevent regression.
        """
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            d = self.sites[site]
            for key, value in kwargs.items():
                if not hasattr(d, key):
                    continue
                if key in ('completed', 'changed', 'no_change', 'errors'):
                    current = getattr(d, key, 0)
                    setattr(d, key, max(current, value))
                else:
                    setattr(d, key, value)
            d.last_update = datetime.now()

    def update_stats(self, site: str, **kwargs):
        """Alias for update() — kept for API compatibility."""
        self.update(site, **kwargs)

    def set_status(self, site: str, status: str):
        """Legacy API — sets status text only, doesn't change state."""
        # No-op for text status; use mark_* methods for state.
        # Kept for compatibility with workers that still call it.
        pass

    def set_current_item(self, site: str, item: str):
        site = site.lower()
        item = item.replace('📦', '').replace('📊', '').strip()
        with self._state_lock:
            if site not in self.sites:
                return
            self.sites[site].current_item = item[:40] if item else ''

    def set_next_monitor(self, site: str, interval: int):
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            d = self.sites[site]
            d.monitor_interval = interval
            d.next_monitor = datetime.now() + timedelta(seconds=interval)

    def add_error(self, site: str, message: str, error_type: str = "error"):
        site = site.lower()
        with self._state_lock:
            if site not in self.sites:
                return
            self._append_error_locked(self.sites[site], message, error_type)

    def clear_errors(self, site: Optional[str] = None):
        with self._state_lock:
            if site is None:
                for d in self.sites.values():
                    d.recent_errors = []
            else:
                site = site.lower()
                if site in self.sites:
                    self.sites[site].recent_errors = []

    def _append_error_locked(self, d: SiteData, message: str, error_type: str = "error"):
        """Dedupe errors within a short window; keep at most MAX per site."""
        now = datetime.now()
        msg_short = message[:80]
        # Dedup: same message within window
        for e in reversed(d.recent_errors):
            if e['message'] == msg_short and (now - e['time']).total_seconds() < self.ERROR_DEDUP_WINDOW:
                e['count'] = e.get('count', 1) + 1
                return
        d.recent_errors.append({
            'message': msg_short,
            'type': error_type,
            'time': now,
            'count': 1,
        })
        if len(d.recent_errors) > self.MAX_ERRORS_PER_SITE:
            d.recent_errors = d.recent_errors[-self.MAX_ERRORS_PER_SITE:]

    # =================================================
    # RENDER LOOP
    # =================================================

    def start(self):
        """Start the live display and render thread."""
        if self.running:
            return
        self.running = True
        self._render_stop_event.clear()

        # Initial render
        initial_layout = self._safe_build_layout()
        self.live = Live(initial_layout, console=console, refresh_per_second=self.RENDER_FPS)
        self.live.__enter__()

        # Dedicated render thread at fixed FPS
        self._render_thread = threading.Thread(
            target=self._render_loop, name="progress_render", daemon=True
        )
        self._render_thread.start()

    def stop(self):
        """Stop the render thread and close the live display."""
        if not self.running:
            return
        self.running = False
        self._render_stop_event.set()
        if self._render_thread:
            self._render_thread.join(timeout=2)
            self._render_thread = None
        if self.live:
            try:
                self.live.__exit__(None, None, None)
            except Exception:
                pass
            self.live = None

    def close(self):
        self.stop()

    def _render_loop(self):
        """Dedicated thread that renders at a fixed cadence."""
        interval = 1.0 / self.RENDER_FPS
        while not self._render_stop_event.is_set():
            t0 = time.time()
            try:
                if self.live:
                    self.live.update(self._safe_build_layout())
            except Exception as e:
                self._log_render_error_once(e)
            sleep_for = interval - (time.time() - t0)
            if sleep_for > 0:
                time.sleep(sleep_for)

    def _log_render_error_once(self, e: Exception):
        key = f"{type(e).__name__}:{str(e)[:60]}"
        if key not in self._seen_render_errors:
            self._seen_render_errors.add(key)
            logger.error(f"RENDER_ERROR | {key}")

    # =================================================
    # LAYOUT BUILDING (read-only)
    # =================================================

    def _safe_build_layout(self):
        """Build layout from a locked snapshot; never mutates state."""
        try:
            with self._state_lock:
                snapshot = {s: d.snapshot() for s, d in self.sites.items()}
            return self._create_layout(snapshot)
        except Exception as e:
            self._log_render_error_once(e)
            # Return a minimal fallback so Live doesn't crash
            return Panel("[red]Dashboard render error[/red]")

    def _format_countdown(self, seconds: float) -> str:
        if seconds < 0:
            return "🔴 NOW"
        total = int(seconds)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _calc_eta(self, d: dict) -> str:
        if d['state'] == SiteState.COMPLETED:
            return "✅ Done"
        if d['state'] != SiteState.RUNNING:
            return "--"
        completed = d['completed']
        total = d['total']
        if completed <= 0 or total <= 0:
            return "--"
        elapsed = (datetime.now() - d['cycle_started_at']).total_seconds()
        if elapsed <= 0:
            return "--"
        rate = completed / elapsed
        if rate <= 0:
            return "--"
        remaining = (total - completed) / rate
        if remaining < 60:
            return f"{int(remaining)}s"
        elif remaining < 3600:
            return f"{int(remaining // 60)}m {int(remaining % 60)}s"
        else:
            return f"{int(remaining // 3600)}h {int((remaining % 3600) // 60)}m"

    def _status_display(self, d: dict):
        state = d['state']
        cycle = d['current_cycle']

        if state == SiteState.COMPLETED:
            return (f"✅ Cycle {cycle} Done", "green")
        if state == SiteState.WAITING:
            return (f"⏸️ Waiting (C{cycle})", "yellow")
        if state == SiteState.RUNNING:
            return (f"🔄 Cycle {cycle}", "cyan")
        if state == SiteState.STARTING:
            return ("⏳ Starting...", "yellow")
        if state == SiteState.ERROR:
            return ("❌ Error", "red")
        if state == SiteState.STOPPED:
            return ("⏹️ Stopped", "dim")
        return (f"Cycle {cycle}", "white")

    def _create_layout(self, sites_snapshot: Dict[str, dict]):
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        header = Panel(
            f"[bold cyan]📊 MONITORING DASHBOARD[/bold cyan]  |  "
            f"[dim]{now_str}[/dim]",
            style="bold white"
        )

        table = Table(
            show_header=True, header_style="bold cyan", box=None,
            padding=(0, 1), show_lines=False, border_style="dim"
        )
        table.add_column("Site", style="bold white", width=14, no_wrap=True)
        table.add_column("Progress", width=35)
        table.add_column("Stats", style="white", width=22)
        table.add_column("Status", style="yellow", width=18)
        table.add_column("ETA", style="dim", width=14)
        table.add_column("⏱️ Next", style="dim", width=12)

        table.add_row(*["─" * w for w in (14, 35, 22, 18, 14, 12)], style="dim")

        site_list = list(sites_snapshot.items())
        for idx, (site, d) in enumerate(site_list):
            total = d['total']
            completed = d['completed']
            changed = d['changed']
            no_change = d['no_change']
            errors = d['errors']
            state = d['state']

            percent = min((completed / total * 100) if total > 0 else 0, 100.0)

            status_text, status_color = self._status_display(d)

            # Progress bar
            if total > 0:
                bar_len = 20
                filled = min(int(bar_len * completed / total), bar_len)
                bar = f"[green]{'█' * filled}[/green]{'░' * (bar_len - filled)}"
                progress_text = f"{bar}  {percent:>5.1f}%"
            else:
                progress_text = "[dim]Loading...[/dim]"

            if d['current_item'] and d['current_item'] != 'Starting...':
                ci = d['current_item'][:28]
                progress_text += f"\n   [dim]{ci}[/dim]"

            if total > 0:
                progress_text += f"\n   [dim]{completed:,}/{total:,} products[/dim]"

            # Stats
            parts = []
            parts.append(f"[bold green]✓ {changed}[/bold green]" if changed > 0 else f"✓ {changed}")
            parts.append(f"[yellow]○ {no_change}[/yellow]" if no_change > 0 else f"○ {no_change}")
            parts.append(f"[red]✗ {errors}[/red]" if errors > 0 else f"✗ {errors}")
            stats_text = "  ".join(parts)

            # Rate
            rate = d['rate']
            if rate > 0:
                if rate >= 1:
                    stats_text += f"\n   [dim]{rate:.1f} items/s[/dim]"
                else:
                    stats_text += f"\n   [dim]{rate * 60:.1f} items/min[/dim]"

            # ETA
            eta = self._calc_eta(d)
            if eta == "✅ Done":
                eta_display = "[green]✅ Done[/green]"
            elif eta == "--":
                eta_display = "--"
            else:
                eta_display = f"[dim]{eta}[/dim]"

            # Next monitor
            next_display = "--"
            if state == SiteState.WAITING and d['next_monitor']:
                remaining = (d['next_monitor'] - datetime.now()).total_seconds()
                next_display = self._format_countdown(remaining) if remaining > 0 else "🔴 NOW"
            elif state == SiteState.COMPLETED:
                next_display = "✅ Done"

            # Site name with cycle
            cycle = d['current_cycle']
            site_display = (
                f"[bold]{site.upper()}[/bold] [dim]#{cycle}[/dim]"
                if cycle > 0 else f"[bold]{site.upper()}[/bold]"
            )

            table.add_row(
                site_display, progress_text, stats_text,
                f"[{status_color}]{status_text}[/{status_color}]",
                eta_display, next_display
            )
            if idx < len(site_list) - 1:
                table.add_row(*["─" * w for w in (14, 35, 22, 18, 14, 12)], style="dim")

        # Footer
        total_completed = sum(d['completed'] for d in sites_snapshot.values())
        total_all = sum(d['total'] for d in sites_snapshot.values())
        total_changed = sum(d['changed'] for d in sites_snapshot.values())
        total_errors = sum(d['errors'] for d in sites_snapshot.values())
        overall_percent = (total_completed / total_all * 100) if total_all > 0 else 0

        footer_parts = [
            f"📊 {total_completed:,}/{total_all:,} products ({overall_percent:.1f}%)",
            f"🔄 {total_changed} changed",
        ]
        if total_errors > 0:
            footer_parts.append(f"[red]❌ {total_errors} errors[/red]")
        else:
            footer_parts.append("✅ No errors")

        footer = Panel("  |  ".join(footer_parts), style="dim")

        # Collect all per-site errors for a small errors panel
        all_errors = []
        for site, d in sites_snapshot.items():
            for e in d.get('recent_errors', []):
                all_errors.append((site, e))
        all_errors.sort(key=lambda x: x[1]['time'], reverse=True)

        if all_errors:
            lines = []
            for site, e in all_errors[:5]:
                cnt = f" (x{e['count']})" if e.get('count', 1) > 1 else ""
                lines.append(f"[red]⚠ {site.upper()}:[/red] [dim]{e['message']}{cnt}[/dim]")
            error_panel = Panel(
                "\n".join(lines),
                title=f"[bold red]⚠ Recent Errors ({len(all_errors)})[/bold red]",
                style="dim",
            )
        else:
            error_panel = Panel("[dim]No recent errors[/dim]", style="dim")

        layout = Layout()
        layout.split(
            Layout(header, size=3),
            Layout(table, ratio=1),
            Layout(error_panel, size=6),
            Layout(footer, size=3),
        )
        return layout


# =====================================================
# SINGLETON
# =====================================================

_multi_monitor_progress: Optional[MultiMonitorProgress] = None
_monitor_progress_lock = threading.Lock()


def get_multi_monitor_progress() -> MultiMonitorProgress:
    global _multi_monitor_progress
    if _multi_monitor_progress is None:
        with _monitor_progress_lock:
            if _multi_monitor_progress is None:
                _multi_monitor_progress = MultiMonitorProgress()
    return _multi_monitor_progress