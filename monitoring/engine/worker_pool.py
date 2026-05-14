from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

from core.logger import setup_logger


logger = setup_logger(
    "worker_pool",
    "monitor"
)


class WorkerPool:

    def __init__(
        self,
        workers=20
    ):

        self.workers = workers

    # ---------------------------------
    # RUN
    # ---------------------------------
    def run(
        self,
        worker,
        products,
        processor
    ):

        changed_count = 0

        with ThreadPoolExecutor(
            max_workers=self.workers
        ) as executor:

            futures = [

                executor.submit(
                    worker,
                    product,
                    processor
                )

                for product in products
            ]

            for future in as_completed(futures):

                try:

                    result = future.result()

                    if result:
                        changed_count += 1

                except Exception as e:

                    logger.error(
                        f"WORKER_ERROR | {e}"
                    )

        return changed_count