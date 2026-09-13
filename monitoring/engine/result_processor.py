"""
Result processor - Fixed to use shared database instance and prevent connection exhaustion.
Preserves complete product data in Redis, never overwrites URL/name/brand/category.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from core.shutdown_handler import register_flush_hook
from monitoring.engine.adaptive_batch_processor import AdaptiveBatchProcessor
from core.logger import setup_logger
from core.utils.clean_date_logic import clean_date
from core.utils.debug_logger import log_debug, log_product_check, log_change_comparison

logger = setup_logger("monitor_changes", "monitor")


class ResultProcessor:
    """
    Process monitoring results - Redis immediate, PostgreSQL batched.
    
    Architecture:
    - Redis: Updated immediately with tracked fields only (real-time)
    - PostgreSQL: Updated in batches using AdaptiveBatchProcessor
    - Uses SHARED database instance to prevent connection exhaustion
    - Preserves complete product data (URL, name, brand, category)
    """
    
    # Class-level shared database instance
    _shared_db = None
    _shared_db_lock = None
    
    # Fields that should NEVER be overwritten by monitoring
    PROTECTED_FIELDS = [
        "url", "name", "brand", "category", "main_product_id",
        "site", "monitoring_priority", "monitoring_group",
        "is_enabled", "status"
    ]
    
    # Fields that monitoring CAN update
    TRACKED_FIELDS = [
        "price", "discount_price", "discount_amount",
        "availability", "special_id", "price_valid_until"
    ]
    
    def __init__(self, batch_processor: AdaptiveBatchProcessor = None):
        self.cache = ProductCache()
        
        # Use shared database instance to prevent connection exhaustion
        self.db = self._get_shared_db()
        
        self.batch_processor = batch_processor or AdaptiveBatchProcessor(self.db)
        self.total_processed = 0
        self.total_changes = 0
        
        register_flush_hook(self.flush_all, "ResultProcessor")
    
    @classmethod
    def _get_shared_db(cls):
        """Get or create a shared database instance."""
        import threading
        
        if cls._shared_db_lock is None:
            cls._shared_db_lock = threading.Lock()
        
        with cls._shared_db_lock:
            if cls._shared_db is None:
                cls._shared_db = Database()
                logger.info("RESULT_PROCESSOR | Created shared database instance")
            return cls._shared_db
    
    # =====================================================
    # NORMALIZATION HELPERS
    # =====================================================
    
    def _normalize_value(self, field: str, value):
        """Normalize values for accurate comparison."""
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
        1. If site provides a date → TRUST IT COMPLETELY
        2. If site does NOT provide a date:
           - Use cached date if valid → KEEP IT
           - Use cached date if expired → EXTEND BY 3 DAYS
           - No cached date → SET TO 3 DAYS FROM NOW
        """
        today = datetime.now().date()
        
        # Trust site-provided dates completely
        if new_date:
            return new_date
        
        # Use cached date if valid
        if old_date:
            try:
                date_obj = datetime.strptime(old_date, "%Y-%m-%d").date()
                if date_obj >= today:
                    return old_date
                if date_obj < today:
                    return (date_obj + timedelta(days=3)).strftime("%Y-%m-%d")
            except ValueError:
                pass
        
        # Default to 3 days from now
        return (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    
    def _log_change_details(self, site: str, product_id: str, old_value, new_value, field: str):
        """Log detailed field change."""
        logger.warning(f"{site.upper()} | FIELD_CHANGED | {product_id} | {field} | '{old_value}' -> '{new_value}'")
    
    # =====================================================
    # MAIN PROCESSING
    # =====================================================
    
    def process(self, site: str, main_product_id: str, new_data: dict) -> bool:
        """
        Process monitoring result - NEVER overwrites protected fields.
        
        Strategy:
        1. First time: Save only tracked fields (URL already exists from scraping)
        2. Changes detected: Merge new tracked fields with existing complete data
        3. No changes: Only update last_checked timestamp
        
        Returns:
            True if changes were detected and saved, False otherwise
        """
        try:
            site = site.lower()
            old_data = self.cache.get_product(site, main_product_id)
            
            if old_data is not None and not isinstance(old_data, dict):
                old_data = None
            
            # ─── FIRST TIME SEEING THIS PRODUCT ──────────────────
            if not old_data:
                logger.info(f"{site.upper()} | FIRST_SEEN | {main_product_id}")
                
                # Check if data already exists with URL
                existing = self.cache.get_product(site, main_product_id)
                if existing and existing.get("url"):
                    # Data exists with URL - merge instead of overwrite
                    logger.warning(f"{site.upper()} | DATA_EXISTS_WITH_URL | {main_product_id} | Merging")
                    merged = existing.copy()
                    for field in self.TRACKED_FIELDS:
                        if field in new_data and new_data[field] is not None:
                            merged[field] = new_data[field]
                    merged["last_checked"] = int(datetime.now().timestamp())
                    self.cache.set_product(site, main_product_id, merged)
                    return True
                
                # No existing data - save only tracked fields
                tracked_only = {}
                for field in self.TRACKED_FIELDS:
                    if field in new_data and new_data[field] is not None:
                        tracked_only[field] = new_data[field]
                tracked_only["last_checked"] = int(datetime.now().timestamp())
                
                self.cache.set_product(site, main_product_id, tracked_only)
                
                log_product_check(
                    product_id=main_product_id,
                    site=site,
                    action="first_seen",
                    details={"price": new_data.get("price"), "availability": new_data.get("availability")}
                )
                return True
            
            # ─── DETECT CHANGES ──────────────────────────────────
            changed_fields = []
            
            for field in self.TRACKED_FIELDS:
                old_value = self._normalize_value(field, old_data.get(field))
                new_value = self._normalize_value(field, new_data.get(field))
                if old_value != new_value:
                    changed_fields.append({"field": field, "old": old_value, "new": new_value})
            
            # ─── NO CHANGES - ONLY UPDATE TIMESTAMP ─────────────
            if not changed_fields:
                self.cache.update_last_checked(site, main_product_id)
                return False
            
            logger.info(f"{site.upper()} | CHANGES_FOUND | {main_product_id} | fields={len(changed_fields)}")
            
            # ─── UPDATE REDIS - PRESERVE COMPLETE DATA ───────────
            # Start with old complete data
            merged_data = old_data.copy()
            
            # Update only tracked fields
            for field in self.TRACKED_FIELDS:
                if field in new_data and new_data[field] is not None:
                    merged_data[field] = new_data[field]
            
            # Ensure price_valid_until is resolved
            if new_data.get("price_valid_until") is not None:
                merged_data["price_valid_until"] = new_data["price_valid_until"]
            else:
                merged_data["price_valid_until"] = self._resolve_price_valid_until(
                    None, old_data.get("price_valid_until")
                )
            
            # Update last_checked
            merged_data["last_checked"] = int(datetime.now().timestamp())
            
            # Verify protected fields are preserved
            for field in self.PROTECTED_FIELDS:
                if field in old_data and field not in merged_data:
                    merged_data[field] = old_data[field]
                    logger.warning(f"{site.upper()} | RESTORED_FIELD | {main_product_id} | {field}")
            
            # Log if URL is preserved
            if merged_data.get("url"):
                logger.debug(f"{site.upper()} | URL_PRESERVED | {main_product_id}")
            else:
                logger.error(f"{site.upper()} | URL_MISSING | {main_product_id} | URL was not in old data")
            
            # Save merged data back to Redis
            self.cache.set_product(site, main_product_id, merged_data)
            
            # Update last_checked timestamp
            self.cache.update_last_checked(site, main_product_id)
            
            # Log change comparison
            log_change_comparison(
                product_id=main_product_id,
                cached_product=old_data,
                final_product=merged_data,
                has_changes=True,
                changed_fields=[c["field"] for c in changed_fields]
            )
            
            # Determine change types
            is_price_change = any(c["field"] in ["price", "discount_price", "discount_amount"] for c in changed_fields)
            is_availability_change = any(c["field"] == "availability" for c in changed_fields)
            is_special_id_change = any(c["field"] == "special_id" for c in changed_fields)
            is_valid_until_change = any(c["field"] == "price_valid_until" for c in changed_fields)
            
            # ─── ADD TO BATCH PROCESSOR FOR POSTGRESQL ───────────
            self.batch_processor.add_update(site, main_product_id, old_data, merged_data, site)
            
            # ─── LOG CHANGES ─────────────────────────────────────
            for change in changed_fields:
                self._log_change_details(site, main_product_id, change["old"], change["new"], change["field"])
            
            self.total_processed += 1
            self.total_changes += len(changed_fields)
            
            logger.info(
                f"{site.upper()} | PROCESSING_COMPLETE | {main_product_id} | "
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
        """Force flush pending updates for a specific site."""
        return self.batch_processor.flush_site(site)
    
    def flush_all(self) -> Dict[str, int]:
        """Force flush ALL pending updates."""
        return self.batch_processor.flush_all()
    
    def get_stats(self) -> Dict:
        """Get processor statistics."""
        return {
            "total_processed": self.total_processed,
            "total_changes": self.total_changes,
            "batch_stats": self.batch_processor.get_stats()
        }
    
    def close(self):
        """Clean up resources - do NOT close shared database."""
        self.batch_processor.close()
        # Do NOT close shared db - it's shared across all instances
    
    # =====================================================
    # HANDLE FAILED PRODUCT
    # =====================================================
    
    def handle_failed_product(self, site: str, main_product_id: str, product_id: str = None) -> bool:
        """
        Handle a product that failed to fetch (404, timeout, etc.)
        Uses context manager for short-lived connection.
        
        Returns:
            True if product is now disabled or already disabled
        """
        try:
            # Use context manager for short-lived connection
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        SELECT check_failures, is_enabled, id
                        FROM products 
                        WHERE site = %s AND main_product_id = %s
                    """, (site.lower(), main_product_id))
                    
                    result = cursor.fetchone()
                    if not result:
                        logger.warning(f"{site.upper()} | PRODUCT_NOT_FOUND | {main_product_id}")
                        return False
                    
                    current_failures = result[0] or 0
                    is_enabled = result[1]
                    product_uuid = result[2]
                    
                    if not is_enabled or current_failures >= 3:
                        logger.debug(f"{site.upper()} | ALREADY_DISABLED | {main_product_id} | failures={current_failures}")
                        return True
                    
                    new_failure_count = current_failures + 1
                    
                    cursor.execute("""
                        UPDATE products 
                        SET check_failures = %s,
                            consecutive_errors = %s,
                            last_updated_at = NOW()
                        WHERE id = %s
                        RETURNING check_failures
                    """, (new_failure_count, new_failure_count, product_uuid))
                    
                    result = cursor.fetchone()
                    new_count = result[0] if result else new_failure_count
                    
                    is_disabled = False
                    
                    if new_count >= 3:
                        cursor.execute("""
                            UPDATE products 
                            SET is_enabled = FALSE, 
                                status = 'disabled',
                                last_updated_at = NOW()
                            WHERE id = %s
                        """, (product_uuid,))
                        is_disabled = True
                        logger.warning(f"{site.upper()} | PRODUCT_DISABLED | {main_product_id} | failures={new_count}")
                    
                    conn.commit()
                    
                    # Update Redis cache - PRESERVE COMPLETE DATA
                    product = self.cache.get_product(site, main_product_id)
                    if product:
                        product["check_failures"] = new_count
                        product["consecutive_errors"] = new_count
                        if is_disabled:
                            product["is_enabled"] = False
                            product["status"] = "disabled"
                        
                        # Verify URL is preserved
                        if not product.get("url"):
                            logger.error(f"{site.upper()} | URL_LOST | {main_product_id}")
                        
                        self.cache.set_product(site, main_product_id, product)
                    
                    log_product_check(
                        product_id=main_product_id,
                        site=site,
                        action="failed" if not is_disabled else "disabled",
                        details={
                            "failure_count": new_count,
                            "is_disabled": is_disabled,
                            "check_failures": new_count
                        }
                    )
                    
                    return is_disabled
                    
                except Exception as e:
                    conn.rollback()
                    logger.error(f"{site} | HANDLE_FAILURE_ERROR | {main_product_id} | {e}")
                    return False
                finally:
                    cursor.close()
            # Connection automatically returned to pool here
                    
        except Exception as e:
            logger.error(f"{site} | HANDLE_FAILURE_CONNECTION_ERROR | {main_product_id} | {e}")
            return False
    
    # =====================================================
    # RESET CHECK FAILURES
    # =====================================================
    
    def reset_check_failures(self, site: str, main_product_id: str) -> bool:
        """Reset check failures for a product."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        UPDATE products 
                        SET check_failures = 0,
                            consecutive_errors = 0,
                            last_updated_at = NOW()
                        WHERE site = %s AND main_product_id = %s
                    """, (site.lower(), main_product_id))
                    conn.commit()
                    
                    # Update Redis cache
                    product = self.cache.get_product(site, main_product_id)
                    if product:
                        product["check_failures"] = 0
                        product["consecutive_errors"] = 0
                        self.cache.set_product(site, main_product_id, product)
                    
                    return cursor.rowcount > 0
                except Exception as e:
                    conn.rollback()
                    logger.error(f"{site} | RESET_FAILURES_ERROR | {main_product_id} | {e}")
                    return False
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"{site} | RESET_FAILURES_CONNECTION_ERROR | {main_product_id} | {e}")
            return False
    
    # =====================================================
    # GET PRODUCT FROM CACHE
    # =====================================================
    
    def get_cached_product(self, site: str, main_product_id: str) -> Optional[Dict]:
        """Get product from Redis cache."""
        return self.cache.get_product(site, main_product_id)
    
    # =====================================================
    # UPDATE PRODUCT IN CACHE
    # =====================================================
    
    def update_cached_product(self, site: str, main_product_id: str, data: Dict) -> bool:
        """Update product in Redis cache."""
        try:
            self.cache.set_product(site, main_product_id, data)
            return True
        except Exception as e:
            logger.error(f"{site} | CACHE_UPDATE_ERROR | {main_product_id} | {e}")
            return False