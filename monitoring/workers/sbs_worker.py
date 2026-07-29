# monitoring/workers/sbs_worker.py
from core.Web.http_client import get
from core.logger import setup_logger
from monitoring.extractors.sbs_fast import extract_sbs_fast

logger = setup_logger("sbs_worker", "monitor")


def sbs_worker(product: dict, processor) -> bool:
    """
    Worker for SBS monitoring with failure handling
    """
    try:
        main_product_id = product.get("main_product_id")
        site = product.get("site")
        url = product.get("url")

        if not url:
            logger.warning(f"{site} | MISSING_URL | {main_product_id}")
            processor.handle_failed_product(site, main_product_id)
            return False

        # Fetch HTML with anti-detection
        html = get(
            url=url,
            max_retries=4,
            timeout=45,
            requests_per_minute=20,
            use_referer=True,
            verify_ssl=True
        )

        if not html:
            logger.warning(f"{site} | PRODUCT_REMOVED | {main_product_id} | {url}")
            processor.handle_failed_product(site, main_product_id)
            return False

        # Extract product data (reuses the scraper's parser for identical fields)
        data = extract_sbs_fast(html, url=url, main_product_id=main_product_id)

        if not data:
            logger.warning(f"{site} | EXTRACTION_FAILED | {main_product_id}")
            processor.handle_failed_product(site, main_product_id)
            return False

        # Enrich data
        data["site"] = site
        data["url"] = url

        # Process changes using the processor
        changed = processor.process(
            site=site,
            main_product_id=main_product_id,
            new_data=data
        )

        if not changed:
            logger.debug(f"{site} | SKIPPED_NO_CHANGE | {main_product_id}")
            return False

        logger.info(f"{site} | CHANGE_SAVED | {main_product_id}")
        return True

    except Exception as e:
        logger.error(f"SBS_WORKER_ERROR | {product.get('url')} | {e}")
        if 'processor' in locals():
            processor.handle_failed_product(site, main_product_id)
        return False
