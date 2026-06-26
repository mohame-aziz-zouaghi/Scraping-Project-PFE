# core/performance.py
import time
from datetime import datetime, timedelta
from typing import Optional


class PerformanceTracker:
    """Track performance metrics for scraping operations"""

    def __init__(self):
        self.start_time = time.time()
        self.total_products = 0
        self.total_categories = 0
        self.total_subcategories = 0
        self.total_errors = 0
        self.total_success = 0
        self.start_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def add_product(self, count: int = 1):
        """Add a product to the count"""
        self.total_products += count

    def add_category(self, count: int = 1):
        """Add a category to the count"""
        self.total_categories += count

    def add_subcategory(self, count: int = 1):
        """Add a subcategory to the count"""
        self.total_subcategories += count

    def add_success(self, count: int = 1):
        """Add a successful product"""
        self.total_success += count

    def add_error(self, count: int = 1):
        """Add an error"""
        self.total_errors += count

    def get_speed(self) -> float:
        """Get current processing speed (products per second)"""
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0
        return self.total_products / elapsed

    def get_eta(self, remaining_products: int) -> str:
        """Get estimated time to completion"""
        speed = self.get_speed()
        if speed == 0:
            return "N/A"

        seconds = remaining_products / speed
        mins = int(seconds // 60)
        secs = int(seconds % 60)

        if mins > 60:
            hours = mins // 60
            mins = mins % 60
            return f"{hours}h {mins}m {secs}s"
        return f"{mins}m {secs}s"

    def get_elapsed_time(self) -> str:
        """Get elapsed time as a formatted string"""
        elapsed = time.time() - self.start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        if mins > 60:
            hours = mins // 60
            mins = mins % 60
            return f"{hours}h {mins}m {secs}s"
        return f"{mins}m {secs}s"

    def get_speed_display(self) -> str:
        """Get speed as a formatted string"""
        speed = self.get_speed()
        if speed > 1:
            return f"{speed:.1f} products/sec"
        elif speed > 0:
            return f"{speed * 60:.1f} products/min"
        return "N/A"

    def print_summary(self, site_name: Optional[str] = None):
        """Print performance summary after the scrape is complete"""
        elapsed = self.get_elapsed_time()
        speed = self.get_speed_display()
        total_products = self.total_products
        total_errors = self.total_errors
        total_success = self.total_success
        success_rate = (total_success / total_products * 100) if total_products > 0 else 0

        print("\n" + "="*60)
        print("📊 PERFORMANCE SUMMARY")
        if site_name:
            print(f"   Site: {site_name.upper()}")
        print("="*60)
        print(f"   Start time:     {self.start_time_str}")
        print(f"   Total time:     {elapsed}")
        print(f"   Products:       {total_products:,}")
        print(f"   Success:        {total_success:,}")
        print(f"   Errors:         {total_errors:,}")
        print(f"   Success rate:   {success_rate:.1f}%")
        print(f"   Speed:          {speed}")
        print("="*60)
        print(f"   Categories:     {self.total_categories}")
        print(f"   Subcategories:  {self.total_subcategories}")
        print("="*60)


# Example usage in your scraper:
# self.performance.add_product()
# self.performance.add_error()
# self.performance.add_category()
# self.performance.print_summary(self.name)