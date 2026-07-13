# core/utils/debug_logger.py
"""
Debug logger for writing small log files during scraping/monitoring
Used for checking specific values and debugging issues
- One file per day (rotates daily)
- Appends to existing file within the same day
- Generates a recap file for each scraping session
- ONLY logs when there are actual changes (no "no change" logs)
"""
import os
import json
from datetime import datetime, date
from typing import Any, Dict, List, Optional
import threading


class DebugLogger:
    """
    Simple debug logger that writes to daily log files
    - Creates a new file each day
    - Appends to the same file throughout the day
    - Auto-rotates at midnight
    - Generates recap files for each scraping session
    - ONLY logs when there are actual changes
    """
    
    def __init__(self, log_dir: str = "debug_logs"):
        """
        Initialize debug logger
        
        Args:
            log_dir: Directory to store log files
        """
        self.log_dir = log_dir
        self._ensure_directory()
        self.current_date = date.today()
        self.current_log_file = None
        self.lock = threading.Lock()
        self.session_start_time = datetime.now()
        self.session_products = []
        self.session_stats = {}
        self._get_daily_log_file()
    
    def _ensure_directory(self):
        """Ensure the log directory exists"""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
    
    def _get_daily_log_filename(self, prefix: str = "debug") -> str:
        """Generate a log filename with daily date"""
        today = datetime.now().strftime("%Y%m%d")
        return os.path.join(self.log_dir, f"{prefix}_{today}.log")
    
    def _get_recap_filename(self) -> str:
        """Generate a recap filename with timestamp"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.log_dir, f"recap_{timestamp}.log")
    
    def _check_rotation(self):
        """Check if we need to rotate to a new daily file"""
        today = date.today()
        if today != self.current_date:
            self.current_date = today
            self.current_log_file = None
    
    def _get_daily_log_file(self, prefix: str = "debug") -> str:
        """Get the current daily log file path"""
        self._check_rotation()
        if self.current_log_file is None:
            self.current_log_file = self._get_daily_log_filename(prefix)
        return self.current_log_file
    
    def _write_log_entry(self, log_entry: Dict, prefix: str = "debug") -> str:
        """
        Write a log entry to the daily log file (appends to existing file)
        """
        log_path = self._get_daily_log_file(prefix)
        
        with self.lock:
            file_exists = os.path.exists(log_path)
            
            with open(log_path, 'a', encoding='utf-8') as f:
                if file_exists:
                    f.write("\n" + "="*80 + "\n")
                
                json.dump(log_entry, f, indent=2, default=str)
                f.write("\n")
        
        return log_path
    
    # =====================================================
    # RECAP GENERATION
    # =====================================================
    
    def _generate_recap(self) -> str:
        """
        Generate a recap file for the current scraping session
        """
        timestamp = datetime.now().isoformat()
        recap_path = self._get_recap_filename()
        
        recap_data = {
            "recap": {
                "session_start": self.session_start_time.isoformat(),
                "session_end": timestamp,
                "duration": str(datetime.now() - self.session_start_time),
                "site": self.session_stats.get("site", "Unknown"),
                "total_products_found": self.session_stats.get("total_products_found", 0),
                "new_products_added": self.session_stats.get("new_products", 0),
                "existing_products_skipped": self.session_stats.get("skipped_duplicates", 0),
                "errors": self.session_stats.get("errors", 0),
                "success_rate": self.session_stats.get("success_rate", "0%")
            },
            "new_products": self.session_products,
            "statistics": self.session_stats
        }
        
        with open(recap_path, 'w', encoding='utf-8') as f:
            json.dump(recap_data, f, indent=2, default=str)
        
        print(f"📊 Recap file generated: {recap_path}")
        return recap_path
    
    def add_product_to_session(self, product_id: str, product_data: Dict, action: str):
        """
        Add a product to the current session for recap
        
        Args:
            product_id: Product ID
            product_data: Product data
            action: 'new', 'existing', 'failed', 'skipped', 'updated'
        """
        with self.lock:
            self.session_products.append({
                "product_id": product_id,
                "action": action,
                "name": product_data.get("name", "Unknown"),
                "price": product_data.get("price", 0),
                "availability": product_data.get("availability", "Unknown"),
                "url": product_data.get("url", ""),
                "timestamp": datetime.now().isoformat()
            })
    
    def update_session_stats(self, stats: Dict):
        """
        Update session statistics
        """
        with self.lock:
            self.session_stats.update(stats)
            
            # Calculate success rate
            total = stats.get("new_products", 0) + stats.get("skipped_duplicates", 0)
            if total > 0:
                self.session_stats["success_rate"] = f"{(stats.get('new_products', 0) / total * 100):.1f}%"
            else:
                self.session_stats["success_rate"] = "0%"
    
    def end_session(self) -> str:
        """
        End the current session and generate a recap file
        """
        recap_path = self._generate_recap()
        
        # Reset session data for next run
        self.session_start_time = datetime.now()
        self.session_products = []
        self.session_stats = {}
        
        return recap_path
    
    # =====================================================
    # LOGGING METHODS (ONLY LOG WHEN THERE ARE CHANGES)
    # =====================================================
    
    def log_dict(self, data: Dict, prefix: str = "debug") -> str:
        """Write a dictionary to the daily log file"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "data": data
        }
        
        log_path = self._write_log_entry(log_entry, prefix)
        return log_path
    
    def log_change_comparison(
        self, 
        product_id: str,
        cached_product: Dict,
        final_product: Dict,
        has_changes: bool,
        changed_fields: List[str],
        prefix: str = "change_comparison"
    ) -> str:
        """
        Log product change comparison data
        - ONLY logs if has_changes is True
        - If 1 change: Shows simple format
        - If > 1 changes: Shows all with details
        """
        # ONLY log if there are actual changes
        if not has_changes or not changed_fields:
            return None
        
        # Build comparison data
        comparison = {}
        formatted_changes = []
        
        fields_to_compare = ["price", "discount_price", "discount_amount", "availability", "special_id", "price_valid_until"]
        
        for field in fields_to_compare:
            old_val = cached_product.get(field)
            new_val = final_product.get(field)
            
            # Normalize for display
            if field in ["price", "discount_price", "discount_amount"]:
                old_display = f"{old_val} TND" if old_val is not None else "None"
                new_display = f"{new_val} TND" if new_val is not None else "None"
            else:
                old_display = str(old_val) if old_val is not None else "None"
                new_display = str(new_val) if new_val is not None else "None"
            
            comparison[field] = {
                "old": old_val,
                "new": new_val,
                "old_display": old_display,
                "new_display": new_display,
                "changed": field in changed_fields
            }
            
            if field in changed_fields:
                formatted_changes.append(f"{field}: {old_display} → {new_display}")
        
        # Create summary
        if len(changed_fields) == 1:
            summary = f"1 change: {formatted_changes[0]}"
        elif len(changed_fields) > 1:
            summary = f"{len(changed_fields)} changes: " + " | ".join(formatted_changes)
        else:
            summary = "No changes"
        
        log_data = {
            "log_type": "change_comparison",
            "product_id": product_id,
            "timestamp": datetime.now().isoformat(),
            "has_changes": has_changes,
            "changed_fields": changed_fields,
            "summary": summary,
            "changes_count": len(changed_fields),
            "comparison": comparison,
            "cached_product": cached_product,
            "final_product": final_product
        }
        
        return self._write_log_entry(log_data, prefix)
    
    def log_simple(self, message: str, data: Any = None, prefix: str = "debug") -> str:
        """
        Write a simple log message with optional data
        ONLY logs if data indicates a change or important event
        """
        # Skip if data contains "no change" indicators
        if isinstance(data, dict):
            if data.get("action") == "no_change" or data.get("no_change") is True:
                return None
            if data.get("message") and "no change" in str(data.get("message")).lower():
                return None
        
        # Skip if message indicates no change
        if message and "no change" in message.lower():
            return None
        
        log_entry = {
            "log_type": "simple",
            "timestamp": datetime.now().isoformat(),
            "message": message,
            "data": data
        }
        
        return self._write_log_entry(log_entry, prefix)
    
    def log_exception(self, exception: Exception, context: Dict = None, prefix: str = "exception") -> str:
        """
        Log an exception with context
        Exceptions are always logged (they indicate issues)
        """
        import traceback
        
        log_entry = {
            "log_type": "exception",
            "timestamp": datetime.now().isoformat(),
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "traceback": traceback.format_exc(),
            "context": context or {}
        }
        
        return self._write_log_entry(log_entry, prefix)
    
    def log_product_check(
        self,
        product_id: str,
        site: str,
        action: str,
        details: Dict = None,
        prefix: str = "product_checks"
    ) -> str:
        """
        Log product check actions and add to session recap
        - Only logs for: 'new', 'updated', 'failed', 'disabled'
        - Skips: 'no_change', 'skipped' (existing products)
        """
        # Skip logging for "no change" actions
        if action in ["no_change", "skipped", "existing"]:
            return None
        
        # Add to session for recap
        product_data = details or {}
        product_data["action"] = action
        self.add_product_to_session(product_id, product_data, action)
        
        # Write to daily log
        log_entry = {
            "log_type": "product_check",
            "timestamp": datetime.now().isoformat(),
            "product_id": product_id,
            "site": site,
            "action": action,
            "details": details or {}
        }
        
        return self._write_log_entry(log_entry, prefix)
    
    def log_scrape_stats(
        self,
        site: str,
        stats: Dict,
        prefix: str = "scrape_stats"
    ) -> str:
        """
        Log scraping statistics and update session recap
        - Always logs (summary of session)
        """
        # Update session stats
        self.update_session_stats(stats)
        
        # Write to daily log
        log_entry = {
            "log_type": "scrape_stats",
            "timestamp": datetime.now().isoformat(),
            "site": site,
            "stats": stats
        }
        
        return self._write_log_entry(log_entry, prefix)


# =====================================================
# GLOBAL INSTANCE
# =====================================================

_debug_logger = None


def get_debug_logger() -> DebugLogger:
    """Get or create global debug logger instance"""
    global _debug_logger
    if _debug_logger is None:
        _debug_logger = DebugLogger()
    return _debug_logger


# =====================================================
# CONVENIENCE FUNCTIONS
# =====================================================

def log_change_comparison(
    product_id: str,
    cached_product: Dict,
    final_product: Dict,
    has_changes: bool,
    changed_fields: List[str]
) -> Optional[str]:
    """
    Log change comparison - ONLY logs if has_changes is True
    """
    logger = get_debug_logger()
    return logger.log_change_comparison(
        product_id=product_id,
        cached_product=cached_product,
        final_product=final_product,
        has_changes=has_changes,
        changed_fields=changed_fields
    )


def log_debug(message: str, data: Any = None) -> Optional[str]:
    """
    Log debug message - skips "no change" messages
    """
    logger = get_debug_logger()
    return logger.log_simple(message, data)


def log_product_check(
    product_id: str,
    site: str,
    action: str,
    details: Dict = None
) -> Optional[str]:
    """
    Log product check - skips "no_change" and "skipped" actions
    """
    logger = get_debug_logger()
    return logger.log_product_check(product_id, site, action, details)


def log_scrape_stats(site: str, stats: Dict) -> str:
    """Log scrape statistics - always logs"""
    logger = get_debug_logger()
    return logger.log_scrape_stats(site, stats)


def end_session() -> str:
    """End current session and generate recap file"""
    logger = get_debug_logger()
    return logger.end_session()