import time

from DB.db import Database
from core.cache.cache_keys import product_key
from core.cache.product_cache import ProductCache


def preload_cache():

    start_time = time.time()

    db = Database()
    cache = ProductCache()

    redis_client = cache.redis

    products = db.load_products_for_monitoring()

    total = len(products)

    added = 0
    skipped = 0

    print(f"\nPRELOADING {total} PRODUCTS...\n")

    pipe = redis_client.pipeline()

    for index, product in enumerate(products, start=1):

        site = product["site"]
        product_id = product["id"]

        # ---------------------------------
        # SKIP EXISTING
        # ---------------------------------
        if cache.exists(site, product_id):

            skipped += 1
            continue

        # ---------------------------------
        # ADD TO PIPELINE
        # ---------------------------------
        cache.set_product(
            site,
            product_id,
            product,
            pipe=pipe
        )

        added += 1

        # ---------------------------------
        # EXECUTE BATCH
        # ---------------------------------
        if added % 1000 == 0:

            pipe.execute()

            print(
                f"[CACHE] Uploaded {added}/{total}"
            )

    # ---------------------------------
    # FINAL EXECUTION
    # ---------------------------------
    pipe.execute()

    db.close()

    elapsed = round(time.time() - start_time, 2)

    print("\n===================================")
    print(" CACHE PRELOAD FINISHED")
    print("===================================")
    print(f"TOTAL PRODUCTS : {total}")
    print(f"ADDED           : {added}")
    print(f"SKIPPED         : {skipped}")
    print(f"TIME TAKEN      : {elapsed} sec")
    print("===================================\n")