import time

class PerformanceTracker:

    def __init__(self):
        self.start_time = time.time()
        self.total_products = 0

    def add_product(self):
        self.total_products += 1

    def get_speed(self):
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0
        return self.total_products / elapsed

    def get_eta(self, remaining_products):
        speed = self.get_speed()
        if speed == 0:
            return "N/A"

        seconds = remaining_products / speed

        mins = int(seconds // 60)
        secs = int(seconds % 60)

        return f"{mins}m {secs}s"