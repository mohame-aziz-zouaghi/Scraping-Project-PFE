# Database/Postgresql/db.py
import json
import psycopg2
from psycopg2.extras import Json, execute_batch
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from Database.Postgresql.config import DBConfig
from core.logger import setup_logger

logger = setup_logger("db", "database")


class Database:

    def __init__(self):
        self.conn = None
        self._connect()

    def _connect(self):
        try:
            self.conn = psycopg2.connect(
                host=DBConfig.HOST,
                port=DBConfig.PORT,
                database=DBConfig.NAME,
                user=DBConfig.USER,
                password=DBConfig.PASSWORD
            )
            self.conn.autocommit = False
            logger.info("Database connected")
            print("✅ DB CONNECTED")
        except Exception as e:
            logger.error(f"DB connection error: {e}")
            raise

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("Database closed")
            print("DB CLOSED")

    # =====================================================
    # LOAD PRODUCTS
    # =====================================================
    
    def load_complete_products_for_preload(self, limit: int = None) -> List[Dict]:
        """
        Load ALL product data including monitoring stats for Redis preload
        (Include disabled products for Redis cache)
        """
        cursor = self.conn.cursor()
        query = """
        SELECT 
            p.main_product_id, p.site, p.url, p.special_id, p.price_valid_until,
            p.availability, p.price, p.discount_price, p.discount_amount,
            p.name, p.brand, p.category,
            COALESCE(ms.check_count, 0) as check_count,
            COALESCE(ms.change_count, 0) as change_count,
            COALESCE(ms.price_change_count, 0) as price_change_count,
            COALESCE(ms.availability_change_count, 0) as availability_change_count,
            COALESCE(ms.consecutive_errors, 0) as consecutive_errors,
            COALESCE(p.monitoring_priority, 3) as monitoring_priority,
            COALESCE(p.monitoring_group, 'regular') as monitoring_group,
            COALESCE(p.price_volatility_score, 50) as price_volatility_score,
            COALESCE(p.price_trend, 'stable') as price_trend,
            p.last_checked, p.next_check_at,
            p.avg_price, p.min_price, p.max_price,
            p.price_change_count as product_price_change_count,
            p.availability_change_count as product_availability_change_count,
            p.is_enabled, p.check_failures, p.last_updated_at
        FROM products p
        LEFT JOIN monitoring_stats ms ON ms.product_id = p.id AND ms.site = p.site
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()

        products = []
        for row in rows:
            products.append({
                "main_product_id": row[0], "site": row[1], "url": row[2], "special_id": row[3],
                "price_valid_until": row[4], "availability": row[5], "price": float(row[6]) if row[6] else 0,
                "discount_price": float(row[7]) if row[7] else 0, "discount_amount": float(row[8]) if row[8] else 0,
                "name": row[9], "brand": row[10], "category": row[11], "check_count": row[12],
                "change_count": row[13], "price_change_count": row[14], "availability_change_count": row[15],
                "consecutive_errors": row[16], "monitoring_priority": row[17], "monitoring_group": row[18],
                "price_volatility_score": row[19], "price_trend": row[20], "last_checked": row[21],
                "next_check_at": row[22], "avg_price": float(row[23]) if row[23] else None,
                "min_price": float(row[24]) if row[24] else None, "max_price": float(row[25]) if row[25] else None,
                "product_price_change_count": row[26], "product_availability_change_count": row[27],
                "is_enabled": row[28], "check_failures": row[29] or 0, "last_updated_at": row[30]
            })
        
        logger.info(f"Loaded {len(products)} products for preload")
        return products

    def load_products_for_monitoring(self, limit: int = None) -> List[Dict]:
        """
        Load ONLY enabled products for monitoring (is_enabled = TRUE)
        """
        cursor = self.conn.cursor()
        query = """
        SELECT main_product_id, site, url, special_id, price_valid_until,
            availability, price, discount_price, discount_amount,
            is_enabled, check_failures, last_updated_at
        FROM products
        WHERE is_enabled = TRUE
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()

        products = []
        for row in rows:
            products.append({
                "main_product_id": row[0],
                "site": row[1],
                "url": row[2],
                "special_id": row[3],
                "price_valid_until": row[4],
                "availability": row[5],
                "price": float(row[6]) if row[6] else 0,
                "discount_price": float(row[7]) if row[7] else 0,
                "discount_amount": float(row[8]) if row[8] else 0,
                "is_enabled": row[9],
                "check_failures": row[10] or 0,
                "last_updated_at": row[11]
            })
        
        return products

    # =====================================================
    # UPSERT PRODUCTS (From Scraping)
    # =====================================================
    
    def upsert_products(self, products: List[Dict]) -> int:
        """Insert or update products from scraping"""
        if not products:
            return 0

        cursor = self.conn.cursor()
        upserted_count = 0

        upsert_query = """
        INSERT INTO products (
            site, main_product_id, special_id, name, brand, category,
            price, discount_price, discount_amount, availability, price_valid_until,
            url, description, short_description, main_image, images,
            scraped_at, last_updated_at
        )
        VALUES (
            %(site)s, %(main_product_id)s, %(special_id)s, %(name)s, %(brand)s, %(category)s,
            %(price)s, %(discount_price)s, %(discount_amount)s, %(availability)s, %(price_valid_until)s,
            %(url)s, %(description)s, %(short_description)s, %(main_image)s, %(images)s, 
            NOW(), NOW()
        )
        ON CONFLICT (url)
        DO UPDATE SET
            name = EXCLUDED.name, brand = EXCLUDED.brand, category = EXCLUDED.category,
            price = EXCLUDED.price, discount_price = EXCLUDED.discount_price,
            discount_amount = EXCLUDED.discount_amount, availability = EXCLUDED.availability,
            price_valid_until = EXCLUDED.price_valid_until, description = EXCLUDED.description,
            short_description = EXCLUDED.short_description, main_image = EXCLUDED.main_image,
            images = EXCLUDED.images, last_updated_at = NOW()
        RETURNING id
        """

        try:
            for product in products:
                # Normalizations
                if isinstance(product.get("short_description"), (dict, list)):
                    product["short_description"] = Json(product["short_description"])
                elif product.get("short_description") is None:
                    product["short_description"] = Json([])
                
                images = product.get("images")
                if isinstance(images, set):
                    images = list(images)
                if isinstance(images, (list, dict)):
                    product["images"] = Json(images)
                elif images is None:
                    product["images"] = Json([])
                
                val = product.get("price_valid_until")
                if val in ("", " ", None):
                    product["price_valid_until"] = None
                
                if product.get("price") is not None:
                    product["price"] = float(product["price"])
                if product.get("discount_price") is not None:
                    product["discount_price"] = float(product["discount_price"])
                if product.get("discount_amount") is not None:
                    product["discount_amount"] = float(product["discount_amount"])
                
                cursor.execute(upsert_query, product)
                cursor.fetchone()
                upserted_count += 1

            self.conn.commit()
            logger.info(f"Upsert complete: {upserted_count} products")
            return upserted_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Upsert error: {e}")
            return 0
        finally:
            cursor.close()

    # =====================================================
    # GET PRODUCT
    # =====================================================
    
    def get_product_by_site_and_main_id(self, site: str, main_product_id: str) -> Optional[Dict]:
        """Get product by composite key"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT id, site, main_product_id, special_id, url, price,
                       discount_price, discount_amount, availability, price_valid_until,
                       monitoring_priority
                FROM products 
                WHERE site = %s AND main_product_id = %s
                LIMIT 1
            """, (site.lower(), main_product_id))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            return {
                "id": row[0], "site": row[1], "main_product_id": row[2], "special_id": row[3],
                "url": row[4], "price": float(row[5]) if row[5] else 0,
                "discount_price": float(row[6]) if row[6] else 0,
                "discount_amount": float(row[7]) if row[7] else 0,
                "availability": row[8], "price_valid_until": row[9], "monitoring_priority": row[10]
            }
        except Exception as e:
            logger.error(f"Failed to get product: {e}")
            return None
        finally:
            cursor.close()

    # =====================================================
    # CORE MONITORING UPDATE (ALL DB UPDATES IN ONE METHOD)
    # =====================================================
    
    def update_product_with_monitoring_data(
        self,
        site: str,
        main_product_id: str,
        old_data: Dict,
        new_data: Dict,
        source: str = None
    ) -> Tuple[bool, Dict]:
        """
        Complete product update with all monitoring fields
        First detect changes, then update if needed
        """
        cursor = self.conn.cursor()
        change_summary = {
            "price_changed": False,
            "availability_changed": False,
            "old_price": None,
            "new_price": None,
            "old_availability": None,
            "new_availability": None
        }
        
        try:
            # =====================================================
            # STEP 1: GET CURRENT VALUES BEFORE ANY UPDATE
            # =====================================================
            cursor.execute("""
                SELECT id, price, discount_price, availability, price_change_count,
                    avg_price, min_price, max_price, monitoring_priority
                FROM products 
                WHERE site = %s AND main_product_id = %s
            """, (site.lower(), main_product_id))
            
            result = cursor.fetchone()
            if not result:
                return False, change_summary
            
            product_id, db_price, db_discount, db_availability, current_price_change_count, \
            current_avg_price, current_min_price, current_max_price, current_priority = result
            
            # Get new values from new_data
            price = new_data.get("price")
            discount_price = new_data.get("discount_price")
            discount_amount = new_data.get("discount_amount")
            availability = new_data.get("availability")
            special_id = new_data.get("special_id")
            price_valid_until = new_data.get("price_valid_until")
            
            # =====================================================
            # STEP 2: DETECT CHANGES (using current DB values)
            # =====================================================
            price_changed = (db_price != price or db_discount != discount_price)
            availability_changed = (db_availability != availability)
            
            change_summary["price_changed"] = price_changed
            change_summary["availability_changed"] = availability_changed
            change_summary["old_price"] = db_price
            change_summary["new_price"] = price
            change_summary["old_availability"] = db_availability
            change_summary["new_availability"] = availability
            
            # If nothing changed, return early
            if not price_changed and not availability_changed:
                logger.debug(f"No changes detected for {site}/{main_product_id}")
                return True, change_summary
            
            logger.info(f"Changes detected for {site}/{main_product_id}: price_changed={price_changed}, availability_changed={availability_changed}")
            
            # =====================================================
            # STEP 3: UPDATE PRODUCTS TABLE (with all new values)
            # =====================================================
            cursor.execute("""
                UPDATE products 
                SET price = %s, discount_price = %s, discount_amount = %s,
                    availability = %s, special_id = %s, price_valid_until = %s,
                    last_updated_at = NOW()
                WHERE id = %s
            """, (price, discount_price, discount_amount, availability, special_id,
                price_valid_until, product_id))
            
            # =====================================================
            # STEP 4: UPDATE PRICE STATISTICS (only if price changed)
            # =====================================================
            if price_changed and price is not None:
                self._update_price_statistics(cursor, product_id, price, current_price_change_count)
            
            # Initialize price stats if first time
            elif price is not None and current_min_price is None:
                cursor.execute("""
                    UPDATE products SET min_price = %s, max_price = %s, avg_price = %s WHERE id = %s
                """, (price, price, price, product_id))
            
            # =====================================================
            # STEP 5: UPDATE AVAILABILITY COUNTER
            # =====================================================
            if availability_changed:
                cursor.execute("""
                    UPDATE products SET availability_change_count = COALESCE(availability_change_count, 0) + 1 WHERE id = %s
                """, (product_id,))
            
            # =====================================================
            # STEP 6: INSERT PRICE HISTORY
            # =====================================================
            if price_changed:
                cursor.execute("""
                    INSERT INTO price_history (product_id, price, discount_price, discount_amount, availability, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (product_id, price, discount_price, discount_amount, availability))
            
            # =====================================================
            # STEP 7: INSERT PRODUCT CHANGES
            # =====================================================
            self._insert_product_changes(cursor, product_id, old_data, new_data, source)
            
            self.conn.commit()
            logger.info(f"DB Updated: {site}/{main_product_id} | price_change={price_changed} | avail_change={availability_changed}")
            return True, change_summary
            
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Update error: {e}")
            return False, change_summary
        finally:
            cursor.close()

    def _update_price_statistics(self, cursor, product_id: str, new_price: float, current_change_count: int):
        """Update price statistics (min, max, avg)"""
        cursor.execute("""
            SELECT price FROM price_history WHERE product_id = %s ORDER BY recorded_at DESC LIMIT 100
        """, (product_id,))
        
        price_history = [row[0] for row in cursor.fetchall() if row[0] is not None]
        all_prices = price_history + [new_price]
        
        new_min = min(all_prices)
        new_max = max(all_prices)
        new_avg = round(sum(all_prices) / len(all_prices), 2)
        
        cursor.execute("""
            UPDATE products 
            SET min_price = %s, max_price = %s, avg_price = %s,
                price_change_count = COALESCE(price_change_count, 0) + 1,
                last_price_change = NOW()
            WHERE id = %s
        """, (new_min, new_max, new_avg, product_id))

    def _insert_product_changes(self, cursor, product_id: str, old_data: Dict, new_data: Dict, source: str):
        """Insert records for all changed fields"""
        tracked_fields = ["price", "discount_price", "discount_amount", "availability", "special_id", "price_valid_until"]
        
        for field in tracked_fields:
            old_val = old_data.get(field)
            new_val = new_data.get(field)
            
            # Normalize
            if field in ["price", "discount_price", "discount_amount"]:
                old_val = float(old_val) if old_val else 0
                new_val = float(new_val) if new_val else 0
            elif field == "price_valid_until":
                if old_val:
                    old_val = str(old_val).split("T")[0] if "T" in str(old_val) else str(old_val)
                if new_val:
                    new_val = str(new_val).split("T")[0] if "T" in str(new_val) else str(new_val)
            
            if old_val != new_val:
                old_json = json.dumps(str(old_val)) if old_val is not None else None
                new_json = json.dumps(str(new_val)) if new_val is not None else None
                cursor.execute("""
                    INSERT INTO product_changes (product_id, change_type, old_value, new_value, source, created_at)
                    VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW())
                """, (product_id, field, old_json, new_json, source))

    # =====================================================
    # MONITORING STATS
    # =====================================================
    
    def increment_monitoring_stats(
        self,
        site: str,
        main_product_id: str,
        has_change: bool = False,
        is_price_change: bool = False,
        is_availability_change: bool = False,
        duration: float = 0
    ) -> bool:
        """Update monitoring_stats table"""
        cursor = self.conn.cursor()
        
        try:
            cursor.execute("SELECT id FROM products WHERE site = %s AND main_product_id = %s", (site.lower(), main_product_id))
            result = cursor.fetchone()
            if not result:
                return False
            
            product_uuid = result[0]
            
            cursor.execute("""
                INSERT INTO monitoring_stats (site, product_id, check_count, change_count,
                    price_change_count, availability_change_count, last_check_duration,
                    consecutive_errors, last_success, updated_at)
                VALUES (%s, %s, 1, %s, %s, %s, %s, 0, NOW(), NOW())
                ON CONFLICT (site, product_id) DO UPDATE SET
                    check_count = monitoring_stats.check_count + 1,
                    change_count = monitoring_stats.change_count + %s,
                    price_change_count = monitoring_stats.price_change_count + %s,
                    availability_change_count = monitoring_stats.availability_change_count + %s,
                    last_check_duration = EXCLUDED.last_check_duration,
                    consecutive_errors = 0,
                    last_success = NOW(),
                    updated_at = NOW()
            """, (site.lower(), product_uuid,
                  1 if has_change else 0, 1 if is_price_change else 0, 1 if is_availability_change else 0, duration,
                  1 if has_change else 0, 1 if is_price_change else 0, 1 if is_availability_change else 0))
            
            cursor.execute("UPDATE products SET last_checked = NOW(), last_updated_at = NOW() WHERE id = %s", (product_uuid,))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to increment stats: {e}")
            return False
        finally:
            cursor.close()

    # =====================================================
    # OTHER MONITORING METHODS
    # =====================================================
    
    def update_next_check_time(self, site: str, main_product_id: str, next_check_at: datetime) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE products SET next_check_at = %s, last_updated_at = NOW()
                WHERE site = %s AND main_product_id = %s
            """, (next_check_at, site.lower(), main_product_id))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to update next check: {e}")
            return False
        finally:
            cursor.close()

    def create_monitoring_alert(self, product_id: str, site: str, alert_type: str, old_value: str, new_value: str, severity: str = "medium") -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO monitoring_alerts (product_id, site, alert_type, old_value, new_value, severity, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """, (product_id, site, alert_type, old_value, new_value, severity))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to create alert: {e}")
            return False
        finally:
            cursor.close()

    def get_site_config(self, site: str) -> Dict:
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT risk_level, base_interval, min_interval, max_interval,
                       batch_size, batch_delay, requests_per_minute, use_proxies, enabled
                FROM site_configs WHERE site = %s
            """, (site.lower(),))
            row = cursor.fetchone()
            if not row:
                return {"risk_level": "medium", "base_interval": 7200, "min_interval": 3600,
                        "max_interval": 21600, "batch_size": 50, "batch_delay": 10,
                        "requests_per_minute": 15, "use_proxies": False, "enabled": True}
            return {"risk_level": row[0], "base_interval": row[1], "min_interval": row[2],
                    "max_interval": row[3], "batch_size": row[4], "batch_delay": row[5],
                    "requests_per_minute": row[6], "use_proxies": row[7], "enabled": row[8]}
        except Exception as e:
            logger.error(f"Failed to get site config: {e}")
            return {}
        finally:
            cursor.close()

    def is_site_enabled(self, site: str) -> bool:
        config = self.get_site_config(site)
        return config.get("enabled", True)


    # =====================================================
    # PRICE VOLATILITY
    # =====================================================
    
    def update_price_volatility(self, site: str, main_product_id: str, new_price: float) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT price, price_volatility_score FROM products WHERE site = %s AND main_product_id = %s", (site.lower(), main_product_id))
            row = cursor.fetchone()
            if not row:
                return False
            
            old_price, current_volatility = row[0] or 0, row[1] or 50
            
            if old_price > 0:
                change_percent = abs(new_price - old_price) / old_price * 100
                if change_percent > 20:
                    new_volatility = min(100, current_volatility + 10)
                elif change_percent > 10:
                    new_volatility = min(100, current_volatility + 5)
                elif change_percent > 5:
                    new_volatility = current_volatility
                else:
                    new_volatility = max(0, current_volatility - 2)
            else:
                new_volatility = current_volatility
            
            trend = "upward" if new_price > old_price else "downward" if new_price < old_price else "stable"
            
            cursor.execute("""
                UPDATE products SET price_volatility_score = %s, price_trend = %s, last_updated_at = NOW()
                WHERE site = %s AND main_product_id = %s
            """, (new_volatility, trend, site.lower(), main_product_id))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to update volatility: {e}")
            return False
        finally:
            cursor.close()



    def resolve_alert(self, alert_id: int) -> bool:
        """Mark an alert as resolved"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE id = %s AND is_resolved = FALSE
            """, (alert_id,))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve alert: {e}")
            return False
        finally:
            cursor.close()

    def resolve_price_drop_alerts(self, product_id: str) -> int:
        """Resolve all price drop alerts for a product if price recovered"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE product_id = %s 
                AND alert_type = 'price_drop'
                AND is_resolved = FALSE
                AND EXISTS (
                    SELECT 1 FROM products 
                    WHERE id = %s 
                    AND price >= (old_value::numeric * 0.95)
                )
            """, (product_id, product_id))
            resolved_count = cursor.rowcount
            self.conn.commit()
            return resolved_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve price drop alerts: {e}")
            return 0
        finally:
            cursor.close()



    def resolve_back_in_stock_alerts(self, product_id: str) -> int:
        """Resolve back-in-stock alerts when product goes out of stock again"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE product_id = %s 
                AND alert_type = 'back_in_stock'
                AND is_resolved = FALSE
                AND EXISTS (
                    SELECT 1 FROM products 
                    WHERE id = %s 
                    AND availability = 'OutOfStock'
                )
            """, (product_id, product_id))
            resolved_count = cursor.rowcount
            self.conn.commit()
            return resolved_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve back-in-stock alerts: {e}")
            return 0
        finally:
            cursor.close()




    # =====================================================
    # COMPLETE ALERT MANAGEMENT METHODS
    # =====================================================

    def resolve_alert(self, alert_id: int) -> bool:
        """Mark a specific alert as resolved"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE id = %s AND is_resolved = FALSE
            """, (alert_id,))
            self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve alert {alert_id}: {e}")
            return False
        finally:
            cursor.close()

    def resolve_price_drop_alerts(self, product_id: str, current_price: float) -> int:
        """
        Resolve all price drop alerts for a product if price recovered
        Resolves when current price is within 5% of original price
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE product_id = %s 
                AND alert_type = 'price_drop'
                AND is_resolved = FALSE
                AND old_value::numeric * 0.95 <= %s
            """, (product_id, current_price))
            resolved_count = cursor.rowcount
            self.conn.commit()
            if resolved_count > 0:
                logger.info(f"Resolved {resolved_count} price drop alerts for product {product_id} (price recovered to {current_price})")
            return resolved_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve price drop alerts: {e}")
            return 0
        finally:
            cursor.close()

    def resolve_back_in_stock_alerts(self, product_id: str, current_availability: str) -> int:
        """
        Resolve back-in-stock alerts when product goes out of stock again
        This resolves the "back_in_stock" alert because the product is no longer available
        """
        cursor = self.conn.cursor()
        try:
            # Resolve back-in-stock alerts when product is out of stock again
            if current_availability == "OutOfStock":
                cursor.execute("""
                    UPDATE monitoring_alerts 
                    SET is_resolved = TRUE, resolved_at = NOW()
                    WHERE product_id = %s 
                    AND alert_type = 'back_in_stock'
                    AND is_resolved = FALSE
                """, (product_id,))
                resolved_count = cursor.rowcount
                self.conn.commit()
                if resolved_count > 0:
                    logger.info(f"Resolved {resolved_count} back-in-stock alerts for product {product_id} (product out of stock again)")
                return resolved_count
            return 0
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve back-in-stock alerts: {e}")
            return 0
        finally:
            cursor.close()

    def resolve_all_product_alerts(self, product_id: str) -> int:
        """
        Resolve ALL unresolved alerts for a specific product
        Useful when product is discontinued or removed
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE product_id = %s AND is_resolved = FALSE
            """, (product_id,))
            resolved_count = cursor.rowcount
            self.conn.commit()
            if resolved_count > 0:
                logger.info(f"Resolved {resolved_count} alerts for product {product_id}")
            return resolved_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve product alerts: {e}")
            return 0
        finally:
            cursor.close()

    def get_active_alerts(self, product_id: str = None, alert_type: str = None, limit: int = 100) -> List[Dict]:
        """Get active (unresolved) alerts"""
        cursor = self.conn.cursor()
        try:
            query = """
                SELECT id, product_id, site, alert_type, old_value, new_value, severity, created_at
                FROM monitoring_alerts 
                WHERE is_resolved = FALSE
            """
            params = []
            
            if product_id:
                query += " AND product_id = %s"
                params.append(product_id)
            
            if alert_type:
                query += " AND alert_type = %s"
                params.append(alert_type)
            
            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [{
                "id": r[0], "product_id": r[1], "site": r[2],
                "alert_type": r[3], "old_value": r[4], "new_value": r[5],
                "severity": r[6], "created_at": r[7]
            } for r in rows]
        except Exception as e:
            logger.error(f"Failed to get active alerts: {e}")
            return []
        finally:
            cursor.close()

    def cleanup_old_alerts(self, days: int = 30) -> int:
        """
        Auto-resolve alerts older than specified days
        Returns number of alerts cleaned up
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE is_resolved = FALSE 
                AND created_at < NOW() - INTERVAL '%s days'
            """, (days,))
            cleaned_count = cursor.rowcount
            self.conn.commit()
            if cleaned_count > 0:
                logger.info(f"Auto-resolved {cleaned_count} alerts older than {days} days")
            return cleaned_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to cleanup old alerts: {e}")
            return 0
        finally:
            cursor.close()


    # =====================================================
    # PRODUCT ENABLE/DISABLE METHODS
    # =====================================================

    def disable_product(self, product_id: str, reason: str = "consecutive_failures") -> bool:
        """Disable a product (stop monitoring it)"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE products 
                SET is_enabled = FALSE, 
                    last_updated_at = NOW()
                WHERE id = %s
            """, (product_id,))
            self.conn.commit()
            logger.info(f"Product disabled: {product_id} | reason={reason}")
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to disable product: {e}")
            return False
        finally:
            cursor.close()

    def enable_product(self, product_id: str) -> bool:
        """Re-enable a product for monitoring"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE products 
                SET is_enabled = TRUE, 
                    check_failures = 0,
                    last_updated_at = NOW()
                WHERE id = %s
            """, (product_id,))
            self.conn.commit()
            logger.info(f"Product enabled: {product_id}")
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to enable product: {e}")
            return False
        finally:
            cursor.close()

    def increment_check_failure(self, main_product_id: str, site: str) -> int:
        """
        Increment check_failures counter for a product
        Returns the new failure count
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE products 
                SET check_failures = COALESCE(check_failures, 0) + 1,
                    last_updated_at = NOW()
                WHERE site = %s AND main_product_id = %s
                RETURNING check_failures
            """, (site.lower(), main_product_id))
            result = cursor.fetchone()
            new_failure_count = result[0] if result else 0
            self.conn.commit()
            
            # If failure count >= 3, disable the product
            if new_failure_count >= 3:
                cursor.execute("""
                    SELECT id FROM products 
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), main_product_id))
                product = cursor.fetchone()
                if product:
                    self.disable_product(product[0], "consecutive_failures")
                    logger.warning(f"Product disabled due to {new_failure_count} consecutive failures: {site}/{main_product_id}")
            
            return new_failure_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to increment check failure: {e}")
            return 0
        finally:
            cursor.close()

    def reset_check_failures(self, main_product_id: str, site: str) -> bool:
        """Reset check_failures counter (when product becomes accessible)"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE products 
                SET check_failures = 0,
                    last_updated_at = NOW()
                WHERE site = %s AND main_product_id = %s
            """, (site.lower(), main_product_id))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to reset check failures: {e}")
            return False
        finally:
            cursor.close()

    def get_disabled_products_older_than(self, days: int = 3, limit: int = 100) -> List[Dict]:
        """Get disabled products that haven't been updated for X days"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT main_product_id, site, url, name, check_failures, last_updated_at
                FROM products 
                WHERE is_enabled = FALSE 
                AND last_updated_at < NOW() - INTERVAL '%s days'
                LIMIT %s
            """, (days, limit))
            rows = cursor.fetchall()
            
            return [{
                "main_product_id": r[0],
                "site": r[1],
                "url": r[2],
                "name": r[3],
                "check_failures": r[4],
                "last_updated_at": r[5]
            } for r in rows]
        except Exception as e:
            logger.error(f"Failed to get disabled products: {e}")
            return []
        finally:
            cursor.close()