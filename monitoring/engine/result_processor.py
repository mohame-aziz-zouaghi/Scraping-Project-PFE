from Database.Redis.product_cache import (
    ProductCache
)

from core.logger import setup_logger


logger = setup_logger(
    "monitor_changes",
    "monitor"
)


class ResultProcessor:

    def __init__(self, db):

        self.db = db

        self.cache = ProductCache()

    # ---------------------------------
    # PROCESS
    # ---------------------------------
    def process(
        self,
        site,
        product_id,
        new_data
    ):

        try:

            # ---------------------------------
            # OLD CACHE
            # ---------------------------------
            old_data = self.cache.get_product(
                site,
                product_id
            )

            # ---------------------------------
            # FIRST TIME
            # ---------------------------------
            if not old_data:

                logger.warning(
                    f"{site} | FIRST_CACHE_SAVE | "
                    f"{product_id}"
                )

                self.cache.set_product(
                    site,
                    product_id,
                    new_data
                )

                return True

            # ---------------------------------
            # CHANGE DETECTION
            # ---------------------------------
            changed_fields = []

            tracked_fields = [

                "price",
                "discount_price",
                "discount_amount",
                "availability",
                "special_id",
                "price_valid_until"

            ]

            for field in tracked_fields:

                old_value = old_data.get(field)

                new_value = new_data.get(field)

                if old_value != new_value:

                    changed_fields.append({

                        "field": field,

                        "old": old_value,

                        "new": new_value
                    })

            # ---------------------------------
            # NO CHANGES
            # ---------------------------------
            if not changed_fields:

                logger.info(
                    f"{site} | NO_CHANGE | "
                    f"{product_id}"
                )

                return False

            # ---------------------------------
            # LOG CHANGES BEFORE UPDATE
            # ---------------------------------
            logger.warning(
                f"{site} | CHANGE_DETECTED | "
                f"{product_id}"
            )

            for change in changed_fields:

                logger.warning(

                    f"{site} | FIELD_CHANGED | "

                    f"{product_id} | "

                    f"{change['field']} | "

                    f"{change['old']} -> "

                    f"{change['new']}"
                )

            # ---------------------------------
            # UPDATE REDIS
            # ---------------------------------
            self.cache.set_product(

                site,
                product_id,
                new_data
            )

            logger.info(
                f"{site} | REDIS_UPDATED | "
                f"{product_id}"
            )

            # ---------------------------------
            # UPDATE DATABASE
            # ---------------------------------
            self.db.update_monitoring_fields(

                product_id=product_id,

                old_data=old_data,

                new_data=new_data,

                source=site
            )

            logger.info(
                f"{site} | DB_UPDATED | "
                f"{product_id}"
            )

            return True

        except Exception as e:

            logger.error(
                f"{site} | PROCESS_ERROR | "
                f"{product_id} | {e}"
            )

            return False