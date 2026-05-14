import json
import time

from Database.Postgresql.db import Database

from core.logger import setup_logger

from Database.Redis.product_cache import (
    ProductCache
)

from monitoring.engine.worker_pool import (
    WorkerPool
)

from monitoring.engine.result_processor import (
    ResultProcessor
)

from monitoring.engine.site_registry import (
    SITE_WORKERS
)


logger = setup_logger(
    "monitor_engine",
    "monitor"
)


class MonitorEngine:

    def __init__(self):

        self.db = Database()

        self.cache = ProductCache()

        self.processor = ResultProcessor(
            self.db
        )

    # ---------------------------------
    # LOAD PRODUCTS
    # ---------------------------------
    def load_products(self, site):

        site = site.lower()
        
        logger.info(
            f"{site} | LOADING_PRODUCTS"
        )

        products = []

        pattern = f"product:{site}:*"

        try:

            keys = list(
                self.cache.redis.scan_iter(pattern)
            )
            
            logger.info(
                f"{site} | REDIS_KEYS_FOUND | "
                f"{len(keys)}"
            )

        except Exception as e:

            logger.error(
                f"{site} | REDIS_SCAN_ERROR | {e}"
            )

            return []

        for key in keys:

            try:

                # ---------------------------------
                # KEY
                # ---------------------------------
                if isinstance(key, bytes):

                    key = key.decode("utf-8")

                product_id = key.split(":")[-1]

                # ---------------------------------
                # RAW CACHE
                # ---------------------------------
                raw = self.cache.redis.get(key)

                if not raw:
                    continue

                if isinstance(raw, bytes):

                    raw = raw.decode("utf-8")

                payload = json.loads(raw)

                # ---------------------------------
                # URL VALIDATION
                # ---------------------------------
                if not payload.get("url"):

                    logger.warning(
                        f"{site} | NO_URL | "
                        f"{product_id}"
                    )

                    continue

                # ---------------------------------
                # ENRICH
                # ---------------------------------
                payload["main_product_id"] = (
                    product_id
                )

                payload["site"] = (
                    site.lower()
                )

                products.append(payload)

            except Exception as e:

                logger.error(
                    f"{site} | LOAD_ERROR | "
                    f"{e}"
                )

        logger.info(
            f"{site} | PRODUCTS_READY | "
            f"{len(products)}"
        )

        return products

    # ---------------------------------
    # MONITOR CYCLE
    # ---------------------------------
    def monitor_cycle(
        self,
        site,
        workers
    ):

        start = time.time()

        products = self.load_products(site)

        if not products:

            logger.warning(
                f"{site} | NO_PRODUCTS"
            )

            return

        worker = SITE_WORKERS.get(site)

        if not worker:

            logger.error(
                f"{site} | NO_WORKER"
            )

            return

        pool = WorkerPool(workers)

        changed_count = pool.run(

            worker=worker,

            products=products,

            processor=self.processor
        )

        elapsed = round(
            time.time() - start,
            2
        )

        logger.info(

            f"{site} | CYCLE_FINISHED | "

            f"checked={len(products)} | "

            f"changed={changed_count} | "

            f"time={elapsed}s"
        )

    # ---------------------------------
    # RUN LOOP
    # ---------------------------------
    def run(
        self,
        site,
        workers=20,
        interval=300
    ):

        logger.info(

            f"{site} | MONITOR_STARTED | "

            f"workers={workers} | "

            f"interval={interval}"
        )

        while True:

            try:

                self.monitor_cycle(
                    site,
                    workers
                )

            except Exception as e:

                logger.error(
                    f"{site} | LOOP_ERROR | "
                    f"{e}"
                )

            logger.info(
                f"{site} | SLEEPING | "
                f"{interval}s"
            )

            time.sleep(interval)