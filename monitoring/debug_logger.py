# monitoring/debug_logger.py
"""
Debug logger for monitoring system - logs every step in one clean file.
"""

import os
import time
from datetime import datetime
from threading import Lock
from typing import Dict, Any, Optional
import json

class MonitoringDebugLogger:
    """
    Centralized debug logger for monitoring system.
    Logs every step to a single file with timestamps.
    """
    
    def __init__(self, log_file: str = "logs/monitoring_debug.log"):
        self.log_file = log_file
        self.lock = Lock()
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        # Initialize log file
        self._log("=" * 100)
        self._log(f"MONITORING DEBUG LOG - {datetime.now().isoformat()}")
        self._log("=" * 100)
        self._log("")
    
    def _log(self, message: str):
        """Write a message to the log file."""
        with self.lock:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(f"{message}\n")
            except Exception:
                pass
    
    def _format_time(self) -> str:
        """Get formatted timestamp with milliseconds."""
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    # ─── CYCLE LOGGING ──────────────────────────────────────────────
    
    def log_cycle_start(self, site: str, total_products: int):
        self._log("")
        self._log(f"[{self._format_time()}] {site.upper():12} | 🔄 CYCLE_START | total={total_products}")
    
    def log_cycle_end(self, site: str, result: Dict):
        changed = result.get('changed', 0)
        no_change = result.get('no_change', 0)
        errors = result.get('errors', 0)
        elapsed = result.get('time', 0)
        self._log(f"[{self._format_time()}] {site.upper():12} | ✅ CYCLE_END | changed={changed} | no_change={no_change} | errors={errors} | time={elapsed:.2f}s")
    
    def log_cycle_error(self, site: str, error: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ❌ CYCLE_ERROR | {error}")
    
    # ─── BATCH LOGGING ──────────────────────────────────────────────
    
    def log_batch_start(self, site: str, batch_num: int, total_batches: int, batch_size: int):
        self._log(f"[{self._format_time()}] {site.upper():12} | 📦 BATCH_START | {batch_num:>3}/{total_batches} | size={batch_size}")
    
    def log_batch_end(self, site: str, batch_num: int, total_batches: int, changed: int, errors: int, elapsed: float):
        self._log(f"[{self._format_time()}] {site.upper():12} | ✅ BATCH_END   | {batch_num:>3}/{total_batches} | changed={changed} | errors={errors} | time={elapsed:.2f}s")
    
    # ─── LOAD LOGGING ────────────────────────────────────────────────
    
    def log_load_start(self, site: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | 📂 LOAD_START   | site={site}")
    
    def log_load_end(self, site: str, count: int, elapsed: float):
        self._log(f"[{self._format_time()}] {site.upper():12} | 📂 LOAD_END     | count={count} | time={elapsed:.2f}s")
    
    def log_load_error(self, site: str, error: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ❌ LOAD_ERROR   | {error}")
    
    # ─── WORKER LOGGING ─────────────────────────────────────────────
    
    def log_worker_start(self, site: str, product_id: str, url: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ▶️ WORKER_START | id={product_id} | url={url[:50]}...")
    
    def log_worker_end(self, site: str, product_id: str, result: bool, elapsed: float):
        status = "✅ SUCCESS" if result else "⏹️ NO_CHANGE"
        self._log(f"[{self._format_time()}] {site.upper():12} | 🏁 WORKER_END   | id={product_id} | {status} | time={elapsed:.2f}s")
    
    def log_worker_error(self, site: str, product_id: str, error: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ❌ WORKER_ERROR | id={product_id} | {error}")
    
    def log_worker_skip(self, site: str, product_id: str, reason: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ⏭️ WORKER_SKIP  | id={product_id} | {reason}")
    
    # ─── FETCH LOGGING ──────────────────────────────────────────────
    
    def log_fetch_start(self, site: str, product_id: str, url: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | 🌐 FETCH_START  | id={product_id} | {url[:60]}...")
    
    def log_fetch_end(self, site: str, product_id: str, status: str, elapsed: float, status_code: int = 0):
        code = f" (HTTP {status_code})" if status_code else ""
        self._log(f"[{self._format_time()}] {site.upper():12} | 🌐 FETCH_END    | id={product_id} | {status}{code} | time={elapsed:.2f}s")
    
    def log_fetch_error(self, site: str, product_id: str, error: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ❌ FETCH_ERROR  | id={product_id} | {error}")
    
    # ─── EXTRACT LOGGING ────────────────────────────────────────────
    
    def log_extract_start(self, site: str, product_id: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | 🔍 EXTRACT_START| id={product_id}")
    
    def log_extract_end(self, site: str, product_id: str, success: bool, fields: Dict = None):
        status = "✅ SUCCESS" if success else "❌ FAILED"
        if success and fields:
            fields_str = f" | price={fields.get('price')} | avail={fields.get('availability')}"
        else:
            fields_str = ""
        self._log(f"[{self._format_time()}] {site.upper():12} | 🔍 EXTRACT_END  | id={product_id} | {status}{fields_str}")
    
    def log_extract_error(self, site: str, product_id: str, error: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ❌ EXTRACT_ERROR| id={product_id} | {error}")
    
    # ─── PROCESS LOGGING ────────────────────────────────────────────
    
    def log_process_start(self, site: str, product_id: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ⚙️ PROCESS_START| id={product_id}")
    
    def log_process_end(self, site: str, product_id: str, changed: bool):
        status = "✅ CHANGED" if changed else "⏹️ UNCHANGED"
        self._log(f"[{self._format_time()}] {site.upper():12} | ⚙️ PROCESS_END  | id={product_id} | {status}")
    
    def log_process_error(self, site: str, product_id: str, error: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | ❌ PROCESS_ERROR| id={product_id} | {error}")
    
    # ─── FAILURE LOGGING ────────────────────────────────────────────
    
    def log_failure(self, site: str, product_id: str, count: int, is_disabled: bool):
        status = "🔴 DISABLED" if is_disabled else f"⚠️ FAILURE {count}/3"
        self._log(f"[{self._format_time()}] {site.upper():12} | 💀 FAILURE      | id={product_id} | {status}")
    
    # ─── RATE LIMIT LOGGING ─────────────────────────────────────────
    
    def log_rate_limit(self, site: str, wait_time: float, current_rate: int, max_rate: int):
        self._log(f"[{self._format_time()}] {site.upper():12} | ⏱️ RATE_LIMIT   | waiting={wait_time:.2f}s | rate={current_rate}/{max_rate}")
    
    # ─── SITE STATUS LOGGING ────────────────────────────────────────
    
    def log_site_start(self, site: str, interval: int):
        self._log(f"[{self._format_time()}] {site.upper():12} | 🚀 SITE_START   | interval={interval}s")
    
    def log_site_stop(self, site: str):
        self._log(f"[{self._format_time()}] {site.upper():12} | 🛑 SITE_STOP    | site stopped")
    
    def log_site_wait(self, site: str, remaining: int):
        if remaining % 30 == 0 or remaining < 10:
            self._log(f"[{self._format_time()}] {site.upper():12} | ⏳ SITE_WAIT    | remaining={remaining}s")


# Singleton instance
_debug_logger = None
_log_lock = Lock()


def get_debug_logger():
    global _debug_logger
    if _debug_logger is None:
        with _log_lock:
            if _debug_logger is None:
                _debug_logger = MonitoringDebugLogger()
    return _debug_logger