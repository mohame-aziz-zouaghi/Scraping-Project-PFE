# monitoring/workers/megapc_worker.py
from core.Web.http_client import get
from core.logger import setup_logger
from monitoring.extractors.megapc_fast import extract_megapc_fast

logger = setup_logger("megapc_worker", "monitor")


def megapc_worker(product: dict, processor) -> bool:
    """
    Worker for MegaPC monitoring with failure handling
    """
    try:
        main_product_id = product.get("main_product_id")
        site = product.get("site")
        url = product.get("url")
        
        if not url:
            logger.warning(f"{site} | MISSING_URL | {main_product_id}")
            # Handle failure due to missing URL
            processor.handle_failed_product(site, main_product_id)
            return False
        
        # Fetch HTML with anti-detection
        html = get(
            url=url,
            max_retries=3,
            timeout=30,
            requests_per_minute=500,
            use_referer=True,
            verify_ssl=True
        )
        
        if not html:
            logger.warning(f"{site} | PRODUCT_REMOVED | {main_product_id} | {url}")
            # Handle failure (product not accessible)
            processor.handle_failed_product(site, main_product_id)
            return False
        
        # Success! Reset failures if product was failing before
        # This will be handled in result_processor when changes are saved
        
        # Extract product data
        data = extract_megapc_fast(html)
        
        if not data:
            logger.warning(f"{site} | EXTRACTION_FAILED | {main_product_id}")
            # Handle failure (extraction failed)
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
        logger.error(f"MEGAPC_WORKER_ERROR | {product.get('url')} | {e}")
        # Handle exception as failure
        if 'processor' in locals():
            processor.handle_failed_product(site, main_product_id)
        return False