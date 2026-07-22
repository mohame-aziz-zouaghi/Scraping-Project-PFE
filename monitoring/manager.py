import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

from Database.Redis.Configuration.redis_client import RedisClient
from core.logger import setup_logger

from Database.Postgresql.db import Database



from Database.Redis.cache_keys import (
    product_key
)
from monitoring.engine.result_processor import ResultProcessor


logger = setup_logger(
    "monitor",
    "engine"
)


class MonitorEngine:

    def __init__(self):

        self.db = Database()

        self.processor = ResultProcessor(
            self.db
        )

        self.redis = RedisClient().get_client()

    # ---------------------------------
    # LOAD PRODUCTS FROM REDIS
    # ---------------------------------
    def load_products(self, site):

        logger.info(
            f"{site} | LOADING_PRODUCTS_FROM_REDIS"
        )

        pattern = product_key(site, "*")

        keys = self.redis.keys(pattern)

        products = []

        for key in keys:

            try:

                raw = self.redis.get(key)

                if not raw:
                    continue

                data = self.redis.json().loads(raw)

            except:

                import json

                data = json.loads(raw)

            product_id = key.split(":")[-1]

            data["main_product_id"] = product_id

            products.append(data)

        logger.info(
            f"{site} | PRODUCTS_LOADED | "
            f"{len(products)}"
        )

        return products

    # ---------------------------------
    # FAST CHECK WORKER
    # ---------------------------------
    def worker(self, site, product):

        try:

            product_id = product.get(
                "main_product_id"
            )

            url = product.get("url")

            if not url:
                return False

            # ---------------------------------
            # MEGAPC
            # ---------------------------------
            if site.lower() == "megapc":

                result = fast_check_megapc_product(
                    url=url,
                    product_id=product_id
                )

            else:

                logger.warning(
                    f"{site} | NO_FAST_SCRAPER"
                )

                return False

            if not result:
                return False

            changed = self.processor.process(
                site=site,
                product_id=product_id,
                new_data=result
            )

            return changed

        except Exception as e:

            logger.error(
                f"{site} | WORKER_ERROR | "
                f"{e}"
            )

            return False

    # ---------------------------------
    # SINGLE MONITORING CYCLE
    # ---------------------------------
    def run_cycle(
        self,
        site,
        workers=20
    ):

        start = time.time()

        logger.info(
            f"{site} | MONITOR_CYCLE_START"
        )

        products = self.load_products(site)

        total = len(products)

        if not total:

            logger.warning(
                f"{site} | NO_PRODUCTS_FOUND"
            )

            return

        changed_count = 0

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            futures = [
                executor.submit(
                    self.worker,
                    site,
                    product
                )
                for product in products
            ]

            for future in as_completed(futures):

                try:

                    changed = future.result()

                    if changed:
                        changed_count += 1

                except Exception as e:

                    logger.error(
                        f"{site} | FUTURE_ERROR | "
                        f"{e}"
                    )

        elapsed = round(
            time.time() - start,
            2
        )

        logger.info(
            f"{site} | MONITOR_CYCLE_FINISHED | "
            f"products={total} | "
            f"changes={changed_count} | "
            f"time={elapsed}s"
        )

    # ---------------------------------
    # INFINITE MONITOR LOOP
    # ---------------------------------
    def run(
        self,
        site,
        interval=300,
        workers=20
    ):

        logger.info(
            f"{site} | MONITOR_STARTED"
        )

        while True:

            try:

                self.run_cycle(
                    site=site,
                    workers=workers
                )

            except Exception as e:

                logger.error(
                    f"{site} | MONITOR_LOOP_ERROR | "
                    f"{e}"
                )

            logger.info(
                f"{site} | SLEEPING | "
                f"{interval}s"
            )

            time.sleep(interval)