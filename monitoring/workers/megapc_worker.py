from core.Web.http_client import get

from core.logger import setup_logger

from monitoring.extractors.megapc_fast import (
    extract_megapc_fast
)


logger = setup_logger(
    "megapc_worker",
    "monitor"
)


# ---------------------------------
# WORKER
# ---------------------------------
def megapc_worker(
    product,
    processor
):

    try:

        product_id = product.get(
            "main_product_id"
        )

        site = product.get(
            "site"
        )

        url = product.get(
            "url"
        )

        # ---------------------------------
        # URL CHECK
        # ---------------------------------
        if not url:

            logger.warning(
                f"{site} | MISSING_URL | "
                f"{product_id}"
            )

            return False

        # ---------------------------------
        # FETCH HTML
        # ---------------------------------
        html = get(url,requests_per_minute=500)
        # ---------------------------------
        # PRODUCT REMOVED
        # ---------------------------------
        if not html:

            logger.warning(
                f"{site} | PRODUCT_REMOVED | "
                f"{product_id} | "
                f"{url}"
            )

            return False

        # ---------------------------------
        # FAST EXTRACTION
        # ---------------------------------
        data = extract_megapc_fast(html)

        if not data:

            logger.warning(
                f"{site} | EXTRACTION_FAILED | "
                f"{product_id}"
            )

            return False

        # ---------------------------------
        # ENRICH DATA
        # ---------------------------------
        data["site"] = site

        data["url"] = url

        # ---------------------------------
        # PROCESS IMMEDIATELY
        # ---------------------------------
        changed = processor.process(

            site=site,

            product_id=product_id,

            new_data=data
        )

        # ---------------------------------
        # SKIPPED
        # ---------------------------------
        if not changed:

            logger.info(
                f"{site} | SKIPPED_NO_CHANGE | "
                f"{product_id}"
            )

            return False

        logger.warning(
            f"{site} | CHANGE_SAVED | "
            f"{product_id}"
        )

        return True

    except Exception as e:

        logger.error(
            f"MEGAPC_WORKER_ERROR | "
            f"{product.get('url')} | {e}"
        )

        return False