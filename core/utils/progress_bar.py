# core/utils/progress_bar.py
import time
from datetime import timedelta
from typing import Optional, Callable

# core/utils/progress_bar.py

class ProgressBar:
    """Universal progress bar with ETA and visual indicators"""

    def __init__(
        self,
        total: int,
        width: int = 40,
        prefix: str = "Progress",
        show_eta: bool = True,
        show_percentage: bool = True,
        show_count: bool = True
    ):
        self.total = total
        self.width = width
        self.prefix = prefix
        self.show_eta = show_eta
        self.show_percentage = show_percentage
        self.show_count = show_count
        self.start_time = time.time()
        self.current = 0
        self.last_update_time = 0
        self.min_update_interval = 0.1

    def update(self, current: int, extra_info: str = "", force: bool = False):
        self.current = min(current, self.total)
        
        now = time.time()
        if not force and (now - self.last_update_time) < self.min_update_interval:
            return
        self.last_update_time = now
        
        # FIXED: Multiply by 100 to get percentage
        percent = (self.current / self.total) * 100 if self.total > 0 else 0
        filled = int(self.width * (self.current / self.total)) if self.total > 0 else 0
        bar = '█' * filled + '░' * (self.width - filled)
        
        components = [f"\r{self.prefix}: |{bar}|"]
        
        if self.show_percentage:
            components.append(f" {percent:.1f}%")
        
        if self.show_count:
            components.append(f" ({self.current:,}/{self.total:,})")
        
        if self.show_eta and self.current > 0:
            elapsed = now - self.start_time
            eta_seconds = (elapsed / self.current) * (self.total - self.current)
            eta = str(timedelta(seconds=int(eta_seconds)))
            components.append(f" | ETA: {eta}")
        
        if extra_info:
            components.append(f" | {extra_info}")
        
        line = "".join(components)
        print(f"{line:<120}", end="", flush=True)
        
        if self.current == self.total:
            print()

    def increment(self, step: int = 1, extra_info: str = ""):
        self.update(self.current + step, extra_info)

    def close(self):
        elapsed = time.time() - self.start_time
        print(f"\n   ✅ Completed in {str(timedelta(seconds=int(elapsed)))}")

    def finish(self, message: str = "✅ Complete!"):
        self.update(self.total, message, force=True)
        self.close()


class MultiLevelProgressBar:
    """Progress bar for nested operations (categories > subcategories > products)"""
    
    def __init__(self, width: int = 40):
        self.width = width
        self.current_level = None
        self.bars = []
    
    def start_category(self, category_name: str, total_subs: int):
        """Start a new category level"""
        print(f"\n📦 {category_name}")
        self.current_bar = ProgressBar(
            total=total_subs,
            width=self.width,
            prefix=f"   Subcategories",
            show_eta=False
        )
    
    def update_category(self, current: int, extra_info: str = ""):
        """Update category progress"""
        if self.current_bar:
            self.current_bar.update(current, extra_info)
    
    def finish_category(self):
        """Finish current category"""
        if self.current_bar:
            self.current_bar.close()
    
    def start_subcategory(self, subcategory_name: str, total_products: int):
        """Start a new subcategory level"""
        print(f"\n   📁 {subcategory_name}")
        self.product_bar = ProgressBar(
            total=total_products,
            width=self.width,
            prefix=f"      Products",
            show_eta=True
        )
    
    def update_subcategory(self, current: int, extra_info: str = ""):
        """Update subcategory progress"""
        if self.product_bar:
            self.product_bar.update(current, extra_info)
    
    def finish_subcategory(self):
        """Finish current subcategory"""
        if self.product_bar:
            self.product_bar.close()


class BatchProgressBar:
    """Progress bar for batch operations with speed tracking"""
    
    def __init__(self, total: int, width: int = 40, prefix: str = "Processing"):
        self.total = total
        self.width = width
        self.prefix = prefix
        self.start_time = time.time()
        self.current = 0
        self.processed_count = 0
        
    def update(self, current: int, items_processed: int = 0):
        """Update with processed items count for speed calculation"""
        self.current = min(current, self.total)
        self.processed_count += items_processed
        
        percent = self.current / self.total if self.total > 0 else 0
        filled = int(self.width * percent)
        bar = '█' * filled + '░' * (self.width - filled)
        
        # Calculate speed
        elapsed = time.time() - self.start_time
        speed = self.processed_count / elapsed if elapsed > 0 else 0
        
        # Calculate ETA
        remaining_items = self.total - self.current
        eta_seconds = remaining_items / speed if speed > 0 else 0
        
        line = f"\r{self.prefix}: |{bar}| {percent:.1f}% ({self.current:,}/{self.total:,}) | Speed: {speed:.1f}/s | ETA: {str(timedelta(seconds=int(eta_seconds)))}"
        print(f"{line:<120}", end="", flush=True)
        
        if self.current == self.total:
            print()
    
    def close(self):
        """Close the progress bar"""
        elapsed = time.time() - self.start_time
        avg_speed = self.processed_count / elapsed if elapsed > 0 else 0
        print(f"   ✅ Completed {self.processed_count:,} items in {str(timedelta(seconds=int(elapsed)))} (avg: {avg_speed:.1f}/s)")


# ---------------------------------
# DECORATOR FOR EASY PROGRESS TRACKING
# ---------------------------------
def with_progress_bar(total_key: str = None):
    """
    Decorator to automatically add progress bar to functions
    
    Usage:
        @with_progress_bar()
        def process_items(items, progress_bar=None):
            for i, item in enumerate(items):
                progress_bar.update(i + 1)
                # process item
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Check if progress_bar is passed
            if 'progress_bar' in kwargs:
                return func(*args, **kwargs)
            
            # Try to get total from args or kwargs
            total = None
            if total_key and total_key in kwargs:
                total = len(kwargs[total_key])
            elif args and len(args) > 0:
                total = len(args[0])
            
            if total:
                progress_bar = ProgressBar(total=total)
                kwargs['progress_bar'] = progress_bar
                result = func(*args, **kwargs)
                progress_bar.close()
                return result
            else:
                return func(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------
# CONTEXT MANAGER FOR PROGRESS BARS
# ---------------------------------
class ProgressContext:
    """Context manager for progress bars"""
    
    def __init__(self, total: int, prefix: str = "Processing"):
        self.total = total
        self.prefix = prefix
        self.progress_bar = None
    
    def __enter__(self):
        self.progress_bar = ProgressBar(total=self.total, prefix=self.prefix)
        return self.progress_bar
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.progress_bar.close()