# monitoring/engine/result_processor.py
from datetime import datetime, timedelta
from Database.Redis.product_cache import ProductCache
from Database.Postgresql.db import Database
from core.logger import setup_logger

logger = setup_logger("monitor_changes", "monitor")


class ResultProcessor:
    """Process monitoring results - orchestrates between Redis cache and PostgreSQL"""

    def __init__(self):
        self.cache = ProductCache()
        self.db = Database()

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
                date_str = str(value)
                if 'T' in date_str:
                    date_str = date_str.split('T')[0]
                return date_str.strip()
            return None
        
        if field in ["availability", "special_id"]:
            return str(value).strip() if value else None
        
        return value

    def _log_change_details(self, site: str, product_id: str, old_value, new_value, field: str):
        logger.warning(f"{site} | FIELD_CHANGED | {product_id} | {field} | '{old_value}' -> '{new_value}'")

    def process(self, site: str, main_product_id: str, new_data: dict) -> bool:
        """
        Process monitoring result - coordinates Redis and PostgreSQL updates
        """
        try:
            # Get old data from Redis
            old_data = self.cache.get_product(site, main_product_id)
            
            # First time seeing this product
            if not old_data:
                logger.warning(f"{site} | FIRST_CACHE_SAVE | {main_product_id}")
                self.cache.set_product(site, main_product_id, new_data)
                return True
            
            # Detect changes
            changed_fields = []
            tracked_fields = ["price", "discount_price", "discount_amount", "availability", "special_id", "price_valid_until"]
            
            for field in tracked_fields:
                old_value = self._normalize_value(field, old_data.get(field))
                new_value = self._normalize_value(field, new_data.get(field))
                if old_value != new_value:
                    changed_fields.append({"field": field, "old": old_value, "new": new_value})
                    logger.debug(f"{site} | CHANGE_DETECTED: {field}")
            
            if not changed_fields:
                logger.debug(f"{site} | NO_CHANGE | {main_product_id}")
                return False
            
            logger.info(f"{site} | CHANGES_FOUND | {main_product_id} | fields={len(changed_fields)}")
            
            # Determine change types
            is_price_change = any(c["field"] in ["price", "discount_price", "discount_amount"] for c in changed_fields)
            is_availability_change = any(c["field"] == "availability" for c in changed_fields)
            
            # =====================================================
            # STEP 1: UPDATE REDIS CACHE
            # =====================================================
            self.cache.set_product(site, main_product_id, new_data)
            self.cache.increment_check_count(site, main_product_id, True, is_price_change, is_availability_change, 0.5)
            
            if is_price_change:
                new_price = float(new_data.get("price", 0))
                self.db.update_price_volatility(site, main_product_id, new_price)
            
            logger.info(f"{site} | REDIS_UPDATED | {main_product_id}")
            
            # =====================================================
            # STEP 2: UPDATE POSTGRESQL (ALL DB OPERATIONS IN ONE CALL)
            # =====================================================
            success, change_summary = self.db.update_product_with_monitoring_data(
                site=site,
                main_product_id=main_product_id,
                old_data=old_data,
                new_data=new_data,
                source=site
            )
            
            # Reset check_failures on successful update (product is accessible)
            if success and main_product_id:
                self.db.reset_check_failures(main_product_id, site)
                logger.debug(f"{site} | FAILURES_RESET | {main_product_id}")


            if not success:
                logger.error(f"{site} | DB_UPDATE_FAILED | {main_product_id}")
                return False
            
            # =====================================================
            # STEP 3: UPDATE MONITORING STATS
            # =====================================================
            self.db.increment_monitoring_stats(
                site=site, main_product_id=main_product_id,
                has_change=True, is_price_change=is_price_change,
                is_availability_change=is_availability_change, duration=0.5
            )
            
            # =====================================================
            # STEP 4: UPDATE PRICE VOLATILITY IN POSTGRESQL
            # =====================================================
            if is_price_change:
                new_price = float(new_data.get("price", 0))
                self.db.update_price_volatility(site, main_product_id, new_price)
                logger.info(f"{site} | DB_PRICE_VOLATILITY_UPDATED | {main_product_id}")
            
            # =====================================================
            # STEP 5: UPDATE NEXT CHECK TIME
            # =====================================================
            product = self.db.get_product_by_site_and_main_id(site, main_product_id)
            if product:
                priority = product.get("monitoring_priority", 3)
                intervals = {1: 3600, 2: 21600, 3: 86400, 4: 259200, 5: 604800}
                next_check = datetime.now() + timedelta(seconds=intervals.get(priority, 86400))
                self.db.update_next_check_time(site, main_product_id, next_check)
            
            # =====================================================
            # STEP 6: CREATE ALERTS AND AUTO-RESOLVE
            # =====================================================
            product_id = product["id"] if product else None
            
            # --- CREATE NEW ALERTS ---
            if is_price_change:
                old_price = float(old_data.get("price", 0))
                new_price = float(new_data.get("price", 0))
                if old_price > 0 and new_price < old_price * 0.9:
                    self.db.create_monitoring_alert(product_id, site, "price_drop", str(old_price), str(new_price), "high")
                    logger.warning(f"{site} | ALERT | Price dropped >10%: {old_price} -> {new_price}")
                
                # Also check for price increase alert
                if old_price > 0 and new_price > old_price * 1.2:
                    self.db.create_monitoring_alert(product_id, site, "price_increase", str(old_price), str(new_price), "medium")
                    logger.warning(f"{site} | ALERT | Price increased >20%: {old_price} -> {new_price}")
            
            if is_availability_change:
                old_avail, new_avail = old_data.get("availability"), new_data.get("availability")
                if old_avail == "OutOfStock" and new_avail == "InStock":
                    self.db.create_monitoring_alert(product_id, site, "back_in_stock", old_avail, new_avail, "high")
                    logger.warning(f"{site} | ALERT | Back in stock: {main_product_id}")
            
            # --- AUTO-RESOLVE EXISTING ALERTS ---
            if product_id:
                # Resolve price drop alerts when price recovers (within 5%)
                if is_price_change:
                    new_price = float(new_data.get("price", 0))
                    resolved_count = self.db.resolve_price_drop_alerts(product_id, new_price)
                    if resolved_count > 0:
                        logger.info(f"{site} | RESOLVED | {resolved_count} price drop alerts")
                    
                    # Resolve price increase alerts when price drops back
                    resolved_count = self.db.resolve_price_increase_alerts(product_id, new_price)
                    if resolved_count > 0:
                        logger.info(f"{site} | RESOLVED | {resolved_count} price increase alerts")
                
                # Resolve back-in-stock alerts when product goes out of stock again
                if is_availability_change:
                    new_avail = new_data.get("availability")
                    resolved_count = self.db.resolve_back_in_stock_alerts(product_id, new_avail)
                    if resolved_count > 0:
                        logger.info(f"{site} | RESOLVED | {resolved_count} back-in-stock alerts")
            
            # Log changes
            for change in changed_fields:
                self._log_change_details(site, main_product_id, change["old"], change["new"], change["field"])
            
            logger.info(
                f"{site} | PROCESSING_COMPLETE | {main_product_id} | "
                f"price_change={is_price_change} | avail_change={is_availability_change} | "
                f"fields={len(changed_fields)}"
            )
            return True
            
        except Exception as e:
            logger.error(f"{site} | PROCESS_ERROR | {main_product_id} | {e}")
            import traceback
            traceback.print_exc()
            return False
        
    def handle_failed_product(self, site: str, main_product_id: str, product_id: str = None) -> bool:
        """
        Handle a product that failed to fetch (404, timeout, etc.)
        Uses increment_check_failure to handle the logic
        Returns True if product is now disabled or already disabled
        """
        try:
            # Use the existing method to handle failure
            result = self.db.increment_check_failure(main_product_id, site)
            
            failure_count = result.get("failure_count", 0)
            is_disabled = result.get("is_disabled", False)
            
            # Update Redis cache with failure info
            product = self.cache.get_product(site, main_product_id)
            if product:
                product["consecutive_errors"] = failure_count
                product["check_failures"] = failure_count
                if is_disabled:
                    product["is_enabled"] = False
                self.cache.set_product(site, main_product_id, product)
            
            # Log appropriately
            if is_disabled:
                logger.warning(f"{site} | PRODUCT_DISABLED | {main_product_id} | failures={failure_count}")
            elif failure_count > 0:
                logger.warning(f"{site} | FAILURE_COUNT | {main_product_id} | failures={failure_count}/3")
            else:
                logger.debug(f"{site} | FAILURE_HANDLED | {main_product_id} | already disabled")
            
            return is_disabled
            
        except Exception as e:
            logger.error(f"{site} | HANDLE_FAILURE_ERROR | {main_product_id} | {e}")
            return False