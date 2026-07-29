# monitoring/engine/result_processor.py
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from monitoring.engine.adaptive_batch_processor import AdaptiveBatchProcessor
from core.logger import setup_logger
from core.utils.clean_date_logic import clean_date
from core.utils.debug_logger import log_debug, log_product_check, log_change_comparison

logger = setup_logger("monitor_changes", "monitor")


class ResultProcessor:
    """
    Process monitoring results - Redis immediate, PostgreSQL batched using AdaptiveBatchProcessor
    
    Architecture:
    - Redis: Updated immediately with essential fields only (real-time)
    - PostgreSQL: Updated in batches using AdaptiveBatchProcessor
    - Adaptive batching: 50 per site OR 100 global OR 60s timeout
    - Background timer checks every 10 seconds
    """
    
    def __init__(self, batch_processor: AdaptiveBatchProcessor = None):
        self.cache = ProductCache()
        self.db = Database()
        self.batch_processor = batch_processor or AdaptiveBatchProcessor(self.db)
        self.total_processed = 0
        self.total_changes = 0

    # =====================================================
    # NORMALIZATION HELPERS
    # =====================================================
    
    def _normalize_value(self, field: str, value):
        """Normalize values for accurate comparison"""
        if value is None:
            return None
        
        if field in ["price", "discount_price", "discount_amount"]:
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0
        
        if field == "price_valid_until":
            if value:
                return clean_date(value)
            return None
        
        if field in ["availability", "special_id"]:
            return str(value).strip() if value else None
        
        return value
    
    def _resolve_price_valid_until(self, new_date: Optional[str], old_date: Optional[str]) -> str:
        """
        Resolve price_valid_until with smart logic.
        
        Logic:
        1. If site provides a date:
           - ALWAYS use it AS-IS (trust the site completely)
           - NO modification, NO expiration checking
        2. If site does NOT provide a date:
           - Use cached date if it exists and is NOT expired → KEEP IT
           - Use cached date if it's expired → EXTEND BY 3 DAYS
           - If no cached date → SET TO 3 DAYS FROM NOW
        
        Why? Some sites (like MegaPC) have static dates from years ago.
        We should NEVER modify dates that the site provides.
        Only generate dates for sites that don't provide them.
        """
        today = datetime.now().date()
        
        # =============================================
        # CASE 1: Site provides a date (TRUST THE SITE COMPLETELY)
        # =============================================
        if new_date:
            logger.debug(f"DATE_RESOLVE | USING_SITE_DATE | {new_date} | trusted")
            return new_date
        
        # =============================================
        # CASE 2: Site does NOT provide a date
        # =============================================
        if old_date:
            try:
                date_obj = datetime.strptime(old_date, "%Y-%m-%d").date()
                
                # If cached date is still valid (today or future), KEEP IT
                if date_obj >= today:
                    logger.debug(f"DATE_RESOLVE | KEEP_CACHED | {old_date} | valid")
                    return old_date
                
                # Cached date is expired, EXTEND BY 3 DAYS
                if date_obj < today:
                    extended = (date_obj + timedelta(days=3)).strftime("%Y-%m-%d")
                    logger.debug(f"DATE_RESOLVE | EXTENDED_EXPIRED_CACHED | {old_date} -> {extended}")
                    return extended
                    
            except ValueError as e:
                # Invalid cached date
                logger.warning(f"DATE_RESOLVE | INVALID_CACHED_DATE | {old_date} | {e}")
                # Fall through to CASE 3
        
        # =============================================
        # CASE 3: No valid date anywhere
        # =============================================
        default_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        logger.debug(f"DATE_RESOLVE | DEFAULT | {default_date}")
        return default_date

    def _log_change_details(self, site: str, product_id: str, old_value, new_value, field: str):
        """Log detailed field change"""
        logger.warning(f"{site} | FIELD_CHANGED | {product_id} | {field} | '{old_value}' -> '{new_value}'")

    # =====================================================
    # MAIN PROCESSING
    # =====================================================
    
    def process(self, site: str, main_product_id: str, new_data: dict) -> bool:
        """
        Process monitoring result - Redis immediate, PostgreSQL batched adaptively
        
        Args:
            site: Site name (e.g., 'megapc')
            main_product_id: Product ID from the source site
            new_data: Newly scraped product data
        
        Returns:
            True if changes were detected and saved, False otherwise
        """
        try:
            # Get old data from Redis (now returns minimal payload)
            old_data = self.cache.get_product(site, main_product_id)
            
            # Validate old_data
            if old_data is not None and not isinstance(old_data, dict):
                old_data = None
            
            # =====================================================
            # RESOLVE PRICE_VALID_UNTIL (with old data context)
            # =====================================================
            if new_data:
                raw_new_date = new_data.get("price_valid_until")
                old_date = old_data.get("price_valid_until") if old_data else None
                
                # Resolve the final date using the processor logic
                resolved_date = self._resolve_price_valid_until(raw_new_date, old_date)
                new_data["price_valid_until"] = resolved_date
            
            # First time seeing this product
            if not old_data:
                logger.warning(f"{site} | FIRST_CACHE_SAVE | {main_product_id}")
                self.cache.set_product(site, main_product_id, new_data)
                log_product_check(
                    product_id=main_product_id,
                    site=site,
                    action="first_seen",
                    details={"price": new_data.get("price"), "availability": new_data.get("availability")}
                )
                return True
            
            # Detect changes
            changed_fields = []
            tracked_fields = ["price", "discount_price", "discount_amount", "availability", "special_id", "price_valid_until"]
            
            for field in tracked_fields:
                old_value = self._normalize_value(field, old_data.get(field) if isinstance(old_data, dict) else None)
                new_value = self._normalize_value(field, new_data.get(field) if isinstance(new_data, dict) else None)
                if old_value != new_value:
                    changed_fields.append({"field": field, "old": old_value, "new": new_value})
                    logger.debug(f"{site} | CHANGE_DETECTED: {field}")
            
            # No changes detected
            if not changed_fields:
                return False
            
            logger.info(f"{site} | CHANGES_FOUND | {main_product_id} | fields={len(changed_fields)}")
            
            # Log change comparison for debugging
            log_change_comparison(
                product_id=main_product_id,
                cached_product=old_data,
                final_product=new_data,
                has_changes=True,
                changed_fields=[c["field"] for c in changed_fields]
            )
            
            # Determine change types
            is_price_change = any(c["field"] in ["price", "discount_price", "discount_amount"] for c in changed_fields)
            is_availability_change = any(c["field"] == "availability" for c in changed_fields)
            is_special_id_change = any(c["field"] == "special_id" for c in changed_fields)
            is_valid_until_change = any(c["field"] == "price_valid_until" for c in changed_fields)
            
            # =====================================================
            # STEP 1: UPDATE REDIS CACHE (IMMEDIATE - MINIMAL PAYLOAD)
            # =====================================================
            # Only update essential fields in Redis
            self.cache.set_product(site, main_product_id, new_data)
            
            # Update last_checked timestamp
            self.cache.update_last_checked(site, main_product_id)
            
            logger.info(f"{site} | REDIS_UPDATED | {main_product_id}")
            
            # =====================================================
            # STEP 2: ADD TO ADAPTIVE BATCH PROCESSOR (POSTGRESQL)
            # =====================================================
            # All detailed monitoring stats are updated in PostgreSQL via batch
            self.batch_processor.add_update(site, main_product_id, old_data, new_data, site)
            
            # =====================================================
            # STEP 3: LOG CHANGES
            # =====================================================
            for change in changed_fields:
                self._log_change_details(site, main_product_id, change["old"], change["new"], change["field"])
            
            self.total_processed += 1
            self.total_changes += len(changed_fields)
            
            logger.info(
                f"{site} | PROCESSING_COMPLETE | {main_product_id} | "
                f"price_change={is_price_change} | avail_change={is_availability_change} | "
                f"special_id_change={is_special_id_change} | valid_until_change={is_valid_until_change} | "
                f"fields={len(changed_fields)}"
            )
            return True
            
        except Exception as e:
            logger.error(f"{site} | PROCESS_ERROR | {main_product_id} | {e}")
            import traceback
            traceback.print_exc()
            log_debug(
                f"Error processing {site}/{main_product_id}",
                {"error": str(e), "traceback": traceback.format_exc()}
            )
            return False

    # =====================================================
    # BATCH MANAGEMENT
    # =====================================================
    
    def flush_site(self, site: str) -> int:
        """Force flush pending updates for a specific site"""
        return self.batch_processor.flush_site(site)
    
    def flush_all(self) -> Dict[str, int]:
        """Force flush ALL pending updates"""
        return self.batch_processor.flush_all()
    
    def get_stats(self) -> Dict:
        """Get processor statistics"""
        return {
            "total_processed": self.total_processed,
            "total_changes": self.total_changes,
            "batch_stats": self.batch_processor.get_stats()
        }
    
    def close(self):
        """Clean up resources - flush remaining batch and close connections"""
        self.batch_processor.close()
        if self.db:
            self.db.close()

    # =====================================================
    # HANDLE FAILED PRODUCT
    # =====================================================
    
    def handle_failed_product(self, site: str, main_product_id: str, product_id: str = None) -> bool:
        """
        Handle a product that failed to fetch (404, timeout, etc.)
        
        Returns:
            True if product is now disabled or already disabled
        """
        try:
            log_debug(
                f"Handling failed product {site}/{main_product_id}",
                {"action": "failure_handling_started"}
            )
            
            # Increment failure count in PostgreSQL (handles disable logic)
            result = self.db.increment_check_failure(main_product_id, site)
            
            failure_count = result.get("failure_count", 0)
            is_disabled = result.get("is_disabled", False)
            
            # Update Redis cache with failure info (minimal fields)
            product = self.cache.get_product(site, main_product_id)
            if product:
                # Update only essential fields in Redis
                product["check_failures"] = failure_count
                if is_disabled:
                    product["is_enabled"] = False
                self.cache.set_product(site, main_product_id, product)
            
            log_product_check(
                product_id=main_product_id,
                site=site,
                action="failed" if not is_disabled else "disabled",
                details={
                    "failure_count": failure_count,
                    "is_disabled": is_disabled,
                    "check_failures": failure_count
                }
            )
            
            if is_disabled:
                logger.warning(f"{site} | PRODUCT_DISABLED | {main_product_id} | failures={failure_count}")
                log_debug(
                    f"Product disabled {site}/{main_product_id}",
                    {"failure_count": failure_count, "reason": "consecutive_failures"}
                )
            elif failure_count > 0:
                logger.warning(f"{site} | FAILURE_COUNT | {main_product_id} | failures={failure_count}/3")
            else:
                logger.debug(f"{site} | FAILURE_HANDLED | {main_product_id} | already disabled")
            
            return is_disabled
            
        except Exception as e:
            logger.error(f"{site} | HANDLE_FAILURE_ERROR | {main_product_id} | {e}")
            log_debug(
                f"Error handling failed product {site}/{main_product_id}",
                {"error": str(e)}
            )
            return False