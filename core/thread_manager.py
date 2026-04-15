from concurrent.futures import ThreadPoolExecutor, as_completed
from core.logger import log_global
from core.performance import PerformanceTracker


class ThreadManager:

    def __init__(self, site_name, start_time, total_products_ref):
        self.site_name = site_name
        self.start_time = start_time
        self.total_products_ref = total_products_ref  # reference (function or lambda)
        self.performance = PerformanceTracker()

        

    # ---------------------------------
    # ADAPTIVE WORKERS
    # ---------------------------------
    def get_optimal_workers(self):

        total_products = self.total_products_ref()
        speed = self.performance.get_speed()

        if speed < 1:
            workers = 20
        elif speed < 3:
            workers = 20
        elif speed < 6:
            workers = 20
        else:
            workers = 30

        log_global(
            f"{self.site_name} | THREAD_ADAPT | workers={workers} | speed={speed:.2f}"
        )

        return workers

    # ---------------------------------
    # EXECUTE TASKS WITH PROGRESS
    # ---------------------------------
    def run_tasks(self, tasks, desc, update_callback=None):

        results = []

        workers = self.get_optimal_workers()

        with ThreadPoolExecutor(max_workers=workers) as executor:

            futures = [executor.submit(*task) for task in tasks]

            from tqdm import tqdm
            with tqdm(total=len(futures), desc=desc, unit="prod", leave=False) as pbar:

                for future in as_completed(futures):

                    try:
                        result = future.result()

                        if result:
                            results.append(result)

                            if update_callback:
                                update_callback()

                    except Exception as e:
                        log_global(f"{self.site_name} | THREAD_ERROR | {e}")

                    pbar.update(1)

        return results