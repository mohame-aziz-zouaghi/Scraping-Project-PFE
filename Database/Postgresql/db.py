# Database/Postgresql/db.py
import json
import time
import psycopg2
from psycopg2.extras import Json, execute_batch
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from Database.Postgresql.config import DBConfig
from core.logger import setup_logger
from core.utils.clean_date_logic import clean_date

logger = setup_logger("db", "database")


class Database:

    def __init__(self):
        self.conn = None
        self._connect()
        # Track failed products for debugging
        self.failed_products_log = []

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
    # DEBUGGING METHODS
    # =====================================================
    
    def check_product_exists(self, site: str, main_product_id: str) -> bool:
        """Quick check if a product exists in the database"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT id FROM products 
                WHERE site = %s AND main_product_id = %s
            """, (site.lower(), main_product_id))
            result = cursor.fetchone()
            exists = result is not None
            if not exists:
                logger.warning(f"DB_CHECK | Product NOT in DB: {site}/{main_product_id}")
            return exists
        except Exception as e:
            logger.error(f"DB_CHECK_ERROR | {site}/{main_product_id} | {e}")
            return False
        finally:
            cursor.close()
    
    def get_product_details(self, site: str, main_product_id: str) -> Optional[Dict]:
        """Get full product details for debugging"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT id, site, main_product_id, url, name, price, 
                       discount_price, availability, special_id, is_enabled,
                       price_valid_until
                FROM products 
                WHERE site = %s AND main_product_id = %s
            """, (site.lower(), main_product_id))
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "site": row[1],
                    "main_product_id": row[2],
                    "url": row[3],
                    "name": row[4],
                    "price": row[5],
                    "discount_price": row[6],
                    "availability": row[7],
                    "special_id": row[8],
                    "is_enabled": row[9],
                    "price_valid_until": row[10]
                }
            return None
        except Exception as e:
            logger.error(f"GET_PRODUCT_DETAILS_ERROR | {site}/{main_product_id} | {e}")
            return None
        finally:
            cursor.close()

    def get_products_not_in_db(self, site: str, product_ids: List[str]) -> List[str]:
        """
        Check which product IDs are NOT in the database
        Useful for finding missing products after scraping
        """
        if not product_ids:
            return []
        
        cursor = self.conn.cursor()
        try:
            existing_ids = []
            for pid in product_ids:
                cursor.execute("""
                    SELECT main_product_id FROM products 
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), pid))
                result = cursor.fetchone()
                if result:
                    existing_ids.append(pid)
            
            missing = [pid for pid in product_ids if pid not in existing_ids]
            
            if missing:
                logger.warning(f"MISSING_IN_DB | site={site} | count={len(missing)} | ids={missing[:10]}")
            
            return missing
        except Exception as e:
            logger.error(f"GET_MISSING_ERROR | {site} | {e}")
            return []
        finally:
            cursor.close()
    
    def log_failed_product(self, site: str, main_product_id: str, reason: str, data: Dict = None):
        """
        Log a product that failed to upsert for debugging
        """
        self.failed_products_log.append({
            "site": site,
            "main_product_id": main_product_id,
            "reason": reason,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
        
        logger.error(
            f"FAILED_UPSERT | site={site} | "
            f"product_id={main_product_id} | "
            f"reason={reason}"
        )
        
        # Log to a separate file for easier tracking
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/failed_upserts.log", "a") as f:
                f.write(f"{datetime.now().isoformat()} | {site} | {main_product_id} | {reason}\n")
                if data:
                    f.write(f"  Data: {json.dumps(data, default=str)[:500]}\n")
        except:
            pass
    
    def get_failed_products(self) -> List[Dict]:
        """Get all logged failed products"""
        return self.failed_products_log.copy()
    
    def clear_failed_products(self):
        """Clear the failed products log"""
        self.failed_products_log = []

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

    def load_products_for_monitoring(self, limit: int = None, redis_db: bool = False) -> List[Dict]:
        """
        Load ONLY enabled products for monitoring (is_enabled = TRUE)
        """
        cursor = self.conn.cursor()
        if redis_db:
            query = """
            SELECT main_product_id, site, url, special_id, price_valid_until,
                availability, price, discount_price, discount_amount,
                is_enabled, check_failures, last_updated_at
            FROM products
            """
        else:
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
        """
        Insert or update products from scraping with full change detection
        Handles ALL tables: products, price_history, product_changes, 
        monitoring_alerts, and monitoring_stats
        
        Only updates if:
        1. Product has all required fields (main_product_id, url, name, price)
        2. There are actual changes detected
        3. Data is valid and complete
        """
        if not products:
            return 0

        cursor = self.conn.cursor()
        upserted_count = 0
        
        # =====================================================
        # FILTER: Skip products with missing required fields
        # =====================================================
        valid_products = []
        skipped_products = []
        
        for p in products:
            product_issues = []
            
            # Required fields check
            if not p.get("main_product_id"):
                product_issues.append("missing main_product_id")
                logger.warning(f"SKIP | missing main_product_id | data={p.get('url', '')[:50]}")
                continue
            if not p.get("url"):
                product_issues.append("missing url")
                logger.warning(f"SKIP | {p.get('main_product_id')} | missing url")
                continue
            if not p.get("name"):
                product_issues.append("missing name")
                logger.warning(f"SKIP | {p.get('main_product_id')} | missing name")
                continue
            if p.get("price") is None:
                product_issues.append("missing price")
                logger.warning(f"SKIP | {p.get('main_product_id')} | missing price")
                continue
            
            # Log if price is 0 (might be valid for free items)
            if p.get("price") == 0:
                logger.debug(f"PRICE_ZERO | {p.get('main_product_id')} | price=0")
            
            valid_products.append(p)
            
            if product_issues:
                skipped_products.append({
                    "product_id": p.get("main_product_id"),
                    "issues": product_issues,
                    "url": p.get("url")
                })
        
        if not valid_products:
            logger.warning(f"UPSERT | No valid products after filtering | total={len(products)}")
            return 0
        
        logger.info(f"UPSERT | Processing {len(valid_products)} valid products out of {len(products)} total")

        # =====================================================
        # BULK: Check existing products
        # =====================================================
        urls = [p.get('url') for p in valid_products if p.get('url')]
        main_ids = [p.get('main_product_id') for p in valid_products if p.get('main_product_id')]
        
        existing_products = {}
        new_product_ids = []
        
        # Log the product IDs we're about to process
        logger.info(f"UPSERT_PRODUCTS | Processing {len(main_ids)} products")
        
        if urls:
            cursor.execute("""
                SELECT id, site, main_product_id, url, price, discount_price, 
                    discount_amount, availability, special_id, price_valid_until,
                    name, brand, category, description, short_description, 
                    main_image, images, scraped_at, is_enabled, check_failures,
                    price_change_count, availability_change_count
                FROM products 
                WHERE url = ANY(%s)
            """, (urls,))
            
            for row in cursor.fetchall():
                existing_products[row[2]] = {
                    "id": row[0],
                    "site": row[1],
                    "main_product_id": row[2],
                    "url": row[3],
                    "price": float(row[4]) if row[4] is not None else 0,
                    "discount_price": float(row[5]) if row[5] is not None else 0,
                    "discount_amount": float(row[6]) if row[6] is not None else 0,
                    "availability": row[7],
                    "special_id": row[8],
                    "price_valid_until": row[9],
                    "name": row[10],
                    "brand": row[11],
                    "category": row[12],
                    "description": row[13],
                    "short_description": row[14],
                    "main_image": row[15],
                    "images": row[16],
                    "scraped_at": row[17],
                    "is_enabled": row[18],
                    "check_failures": row[19] or 0,
                    "price_change_count": row[20] or 0,
                    "availability_change_count": row[21] or 0
                }
            
            logger.info(f"EXISTING_PRODUCTS | Found {len(existing_products)} existing products in DB")
        
        # Identify new products
        for p in valid_products:
            if p.get("main_product_id") not in existing_products:
                new_product_ids.append(p.get("main_product_id"))
        
        if new_product_ids:
            logger.info(f"NEW_PRODUCTS | {len(new_product_ids)} new products")
        
        # =====================================================
        # UPSERT QUERY
        # =====================================================
        upsert_query = """
        INSERT INTO products (
            site, main_product_id, special_id, name, brand, category,
            price, discount_price, discount_amount, availability, price_valid_until,
            url, description, short_description, main_image, images,
            scraped_at, last_updated_at, is_enabled
        )
        VALUES (
            %(site)s, %(main_product_id)s, %(special_id)s, %(name)s, %(brand)s, %(category)s,
            %(price)s, %(discount_price)s, %(discount_amount)s, %(availability)s, %(price_valid_until)s,
            %(url)s, %(description)s, %(short_description)s, %(main_image)s, %(images)s, 
            NOW(), NOW(), TRUE
        )
        ON CONFLICT (url)
        DO UPDATE SET
            name = COALESCE(EXCLUDED.name, products.name),
            brand = COALESCE(EXCLUDED.brand, products.brand),
            category = COALESCE(EXCLUDED.category, products.category),
            price = COALESCE(EXCLUDED.price, products.price),
            discount_price = COALESCE(EXCLUDED.discount_price, products.discount_price),
            discount_amount = COALESCE(EXCLUDED.discount_amount, products.discount_amount),
            availability = COALESCE(EXCLUDED.availability, products.availability),
            price_valid_until = COALESCE(EXCLUDED.price_valid_until, products.price_valid_until),
            description = COALESCE(EXCLUDED.description, products.description),
            short_description = COALESCE(EXCLUDED.short_description, products.short_description),
            main_image = COALESCE(EXCLUDED.main_image, products.main_image),
            images = COALESCE(EXCLUDED.images, products.images),
            special_id = COALESCE(EXCLUDED.special_id, products.special_id),
            last_updated_at = NOW()
        RETURNING id, site, main_product_id, price, discount_price, 
                discount_amount, availability, special_id, price_valid_until,
                name, brand, category, is_enabled,
                price_change_count, availability_change_count
        """

        # Track successful and failed products
        processed_product_ids = []
        failed_products = []
        
        try:
            for product in valid_products:
                main_product_id = product.get("main_product_id")
                
                # Log progress for new products
                if main_product_id in new_product_ids:
                    logger.info(f"INSERT_NEW | {main_product_id} | name={product.get('name', '')[:30]}")
                
                # =====================================================
                # NORMALIZE DATA
                # =====================================================
                
                # Normalize short_description
                if isinstance(product.get("short_description"), (dict, list)):
                    product["short_description"] = Json(product["short_description"])
                elif product.get("short_description") is None:
                    product["short_description"] = Json([])
                
                # Normalize images
                images = product.get("images")
                if isinstance(images, set):
                    images = list(images)
                if isinstance(images, (list, dict)):
                    product["images"] = Json(images)
                elif images is None:
                    product["images"] = Json([])
                
                # Normalize price_valid_until
                val = product.get("price_valid_until")
                if val in ("", " ", None):
                    product["price_valid_until"] = None
                
                # Ensure numeric values are floats
                if product.get("price") is not None:
                    product["price"] = float(product["price"])
                if product.get("discount_price") is not None:
                    product["discount_price"] = float(product["discount_price"])
                if product.get("discount_amount") is not None:
                    product["discount_amount"] = float(product["discount_amount"])
                
                # =====================================================
                # GET EXISTING PRODUCT DATA
                # =====================================================
                existing = existing_products.get(main_product_id)
                
                # Execute upsert
                try:
                    cursor.execute(upsert_query, product)
                    result = cursor.fetchone()
                    
                    if not result:
                        logger.error(f"UPSERT_NO_RESULT | {main_product_id}")
                        self.log_failed_product(
                            product.get("site", "unknown"),
                            main_product_id,
                            "upsert_returned_no_result",
                            product
                        )
                        failed_products.append(main_product_id)
                        continue
                    
                except psycopg2.IntegrityError as e:
                    self.conn.rollback()
                    logger.error(f"INTEGRITY_ERROR | {main_product_id} | {e}")
                    self.log_failed_product(
                        product.get("site", "unknown"),
                        main_product_id,
                        f"integrity_error: {str(e)[:100]}",
                        product
                    )
                    failed_products.append(main_product_id)
                    continue
                except Exception as e:
                    self.conn.rollback()
                    logger.error(f"UPSERT_EXEC_ERROR | {main_product_id} | {e}")
                    self.log_failed_product(
                        product.get("site", "unknown"),
                        main_product_id,
                        f"execution_error: {str(e)[:100]}",
                        product
                    )
                    failed_products.append(main_product_id)
                    continue
                
                # Extract results
                product_id, site, main_product_id, db_price, db_discount, \
                db_discount_amount, db_availability, db_special_id, db_price_valid_until, \
                db_name, db_brand, db_category, db_is_enabled, \
                db_price_change_count, db_availability_change_count = result
                
                processed_product_ids.append(main_product_id)
                
                # =====================================================
                # CHANGE DETECTION
                # =====================================================
                new_price = product.get("price", 0)
                new_discount_price = product.get("discount_price", 0)
                new_discount_amount = product.get("discount_amount", 0)
                new_availability = product.get("availability")
                new_special_id = product.get("special_id")
                new_price_valid_until = product.get("price_valid_until")
                new_name = product.get("name")
                new_brand = product.get("brand")
                new_category = product.get("category")
                new_description = product.get("description", "")
                new_short_description = product.get("short_description", [])
                new_main_image = product.get("main_image")
                new_images = product.get("images", [])
                
                # Check each field for changes
                price_changed = (
                    db_price != new_price or 
                    db_discount != new_discount_price or 
                    db_discount_amount != new_discount_amount
                )
                availability_changed = (db_availability != new_availability)
                special_id_changed = (db_special_id != new_special_id)
                price_valid_until_changed = (db_price_valid_until != new_price_valid_until)
                name_changed = (db_name != new_name)
                brand_changed = (db_brand != new_brand)
                category_changed = (db_category != new_category)
                
                # Check if this is a new product or has changes
                is_new_product = not existing
                has_any_change = (
                    price_changed or availability_changed or special_id_changed or 
                    price_valid_until_changed or name_changed or brand_changed or 
                    category_changed
                )
                
                # Log changes for debugging
                if is_new_product:
                    logger.info(f"NEW_PRODUCT_SAVED | {main_product_id} | price={new_price}")
                elif has_any_change:
                    changes = []
                    if price_changed: changes.append("price")
                    if availability_changed: changes.append("availability")
                    if special_id_changed: changes.append("special_id")
                    if name_changed: changes.append("name")
                    if brand_changed: changes.append("brand")
                    if category_changed: changes.append("category")
                    logger.info(f"PRODUCT_UPDATED | {main_product_id} | changes={','.join(changes)}")
                
                # =====================================================
                # UPDATE TRACKING TABLES
                # =====================================================
                if existing and has_any_change:
                    changed_fields = []
                    
                    # --- Price changes ---
                    if price_changed:
                        cursor.execute("""
                            UPDATE products 
                            SET price_change_count = COALESCE(price_change_count, 0) + 1,
                                last_price_change = NOW()
                            WHERE id = %s
                        """, (product_id,))
                        
                        cursor.execute("""
                            INSERT INTO price_history (
                                product_id, price, discount_price, 
                                discount_amount, availability, recorded_at
                            )
                            VALUES (%s, %s, %s, %s, %s, NOW())
                        """, (product_id, new_price, new_discount_price, 
                            new_discount_amount, new_availability))
                        
                        changed_fields.append(("price", db_price, new_price))
                        changed_fields.append(("discount_price", db_discount, new_discount_price))
                        changed_fields.append(("discount_amount", db_discount_amount, new_discount_amount))
                        
                        # Update price statistics
                        self._update_price_statistics(cursor, product_id, new_price, db_price_change_count)
                    
                    # --- Availability changes ---
                    if availability_changed:
                        cursor.execute("""
                            UPDATE products 
                            SET availability_change_count = COALESCE(availability_change_count, 0) + 1
                            WHERE id = %s
                        """, (product_id,))
                        changed_fields.append(("availability", db_availability, new_availability))
                    
                    # --- Other field changes ---
                    if special_id_changed:
                        changed_fields.append(("special_id", db_special_id, new_special_id))
                    if price_valid_until_changed:
                        changed_fields.append(("price_valid_until", db_price_valid_until, new_price_valid_until))
                    if name_changed:
                        changed_fields.append(("name", db_name, new_name))
                    if brand_changed:
                        changed_fields.append(("brand", db_brand, new_brand))
                    if category_changed:
                        changed_fields.append(("category", db_category, new_category))
                    
                    # --- Insert into product_changes ---
                    for field, old_val, new_val in changed_fields:
                        old_json = json.dumps(str(old_val)) if old_val is not None else None
                        new_json = json.dumps(str(new_val)) if new_val is not None else None
                        
                        cursor.execute("""
                            INSERT INTO product_changes (
                                product_id, change_type, old_value, new_value, source, created_at
                            )
                            VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW())
                        """, (product_id, field, old_json, new_json, "scraping"))
                    
                    # --- Create alerts for significant changes ---
                    if price_changed and db_price > 0 and new_price < db_price * 0.9:
                        cursor.execute("""
                            INSERT INTO monitoring_alerts (
                                product_id, site, alert_type, old_value, new_value, severity, created_at
                            )
                            VALUES (%s, %s, 'price_drop', %s, %s, 'high', NOW())
                        """, (product_id, site, str(db_price), str(new_price)))
                        logger.warning(f"SCRAPING | ALERT | Price dropped >10%: {db_price} -> {new_price}")
                    
                    if price_changed and db_price > 0 and new_price > db_price * 1.2:
                        cursor.execute("""
                            INSERT INTO monitoring_alerts (
                                product_id, site, alert_type, old_value, new_value, severity, created_at
                            )
                            VALUES (%s, %s, 'price_increase', %s, %s, 'medium', NOW())
                        """, (product_id, site, str(db_price), str(new_price)))
                        logger.warning(f"SCRAPING | ALERT | Price increased >20%: {db_price} -> {new_price}")
                    
                    if availability_changed and db_availability == "OutOfStock" and new_availability == "InStock":
                        cursor.execute("""
                            INSERT INTO monitoring_alerts (
                                product_id, site, alert_type, old_value, new_value, severity, created_at
                            )
                            VALUES (%s, %s, 'back_in_stock', %s, %s, 'high', NOW())
                        """, (product_id, site, db_availability, new_availability))
                        logger.warning(f"SCRAPING | ALERT | Product back in stock: {main_product_id}")
                    
                    if availability_changed and db_availability == "InStock" and new_availability == "OutOfStock":
                        cursor.execute("""
                            INSERT INTO monitoring_alerts (
                                product_id, site, alert_type, old_value, new_value, severity, created_at
                            )
                            VALUES (%s, %s, 'out_of_stock', %s, %s, 'medium', NOW())
                        """, (product_id, site, db_availability, new_availability))
                        logger.warning(f"SCRAPING | ALERT | Product went out of stock: {main_product_id}")
                    
                    # --- Update monitoring_stats ---
                    cursor.execute("""
                        INSERT INTO monitoring_stats (site, product_id, change_count, updated_at)
                        VALUES (%s, %s, 1, NOW())
                        ON CONFLICT (site, product_id) 
                        DO UPDATE SET
                            change_count = monitoring_stats.change_count + 1,
                            updated_at = NOW()
                    """, (site.lower(), product_id))
                    
                    logger.info(f"Updated product: {main_product_id} | Changes: {len(changed_fields)}")
                    
                    # --- Resolve existing alerts ---
                    if price_changed and new_price > 0:
                        self.resolve_price_drop_alerts(product_id, new_price)
                        self.resolve_price_increase_alerts(product_id, new_price)
                    
                    if availability_changed and new_availability == "OutOfStock":
                        self.resolve_back_in_stock_alerts(product_id, new_availability)
                
                # =====================================================
                # IF PRODUCT IS NEW, CREATE INITIAL MONITORING_STATS
                # =====================================================
                elif is_new_product:
                    cursor.execute("""
                        INSERT INTO monitoring_stats (site, product_id, check_count, change_count, last_success, created_at, updated_at)
                        VALUES (%s, %s, 1, 0, NOW(), NOW(), NOW())
                        ON CONFLICT (site, product_id) DO NOTHING
                    """, (site.lower(), product_id))
                    
                    logger.info(f"New product inserted: {main_product_id}")
                
                upserted_count += 1
                
                # Cache the product for future iterations
                if main_product_id not in existing_products:
                    existing_products[main_product_id] = {
                        "id": product_id,
                        "site": site,
                        "main_product_id": main_product_id,
                        "price": new_price,
                        "discount_price": new_discount_price,
                        "discount_amount": new_discount_amount,
                        "availability": new_availability,
                        "special_id": new_special_id,
                        "price_valid_until": new_price_valid_until
                    }

            # Commit all changes
            self.conn.commit()
            
            # =====================================================
            # FINAL REPORT
            # =====================================================
            logger.info(f"UPSERT_COMPLETE | processed={len(processed_product_ids)} | upserted={upserted_count} | failed={len(failed_products)}")
            
            # Log any missing products
            if failed_products:
                logger.error(f"MISSING_PRODUCTS | count={len(failed_products)} | ids={failed_products[:10]}")
                
                # Write to file for later inspection
                try:
                    import os
                    os.makedirs("logs", exist_ok=True)
                    with open("logs/missing_products.log", "a") as f:
                        f.write(f"\n{'='*60}\n")
                        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                        site_name = site if 'site' in locals() else 'unknown'
                        f.write(f"Site: {site_name}\n")
                        f.write(f"Failed Products: {len(failed_products)}\n")
                        for pid in failed_products:
                            f.write(f"  - {pid}\n")
                        f.write(f"{'='*60}\n")
                except:
                    pass
            
            return upserted_count
            
        except Exception as e:
            self.conn.rollback()
            logger.error(f"UPSERT_ERROR | {e}")
            import traceback
            traceback.print_exc()
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
    # BATCH UPDATE PRODUCTS WITH MONITORING DATA
    # =====================================================
    
    def batch_update_products_with_monitoring_data(
        self,
        products_data: List[Tuple[str, str, Dict, Dict, str]]
    ) -> Tuple[int, List[Dict]]:
        """
        TRUE BATCH UPDATE - Optimized bulk operations with executemany
        Processes ALL products in 5-7 database trips regardless of batch size
        
        Args:
            products_data: List of (site, main_product_id, old_data, new_data, source)
        
        Returns:
            (success_count, failed_products_list)
        """
        if not products_data:
            return 0, []
        
        cursor = self.conn.cursor()
        start_time = time.time()
        
        # Bulk data collections
        update_params = []
        price_history_params = []
        product_change_params = []
        alert_params = []
        monitoring_stats_params = []
        failed_products = []
        
        try:
            # =====================================================
            # STEP 1: PREPARE ALL DATA
            # =====================================================
            logger.info(f"📦 Preparing batch update for {len(products_data)} products...")
            
            for site, main_product_id, old_data, new_data, source in products_data:
                try:
                    # Validate data types
                    if not isinstance(old_data, dict) or not isinstance(new_data, dict):
                        failed_products.append({
                            "site": site,
                            "main_product_id": main_product_id,
                            "reason": "invalid_data_type"
                        })
                        continue
                    
                    # Get current product data
                    cursor.execute("""
                        SELECT id, price, discount_price, availability, 
                            price_change_count, avg_price, min_price, max_price,
                            monitoring_priority, special_id, price_valid_until,
                            price_volatility_score, price_trend
                        FROM products 
                        WHERE site = %s AND main_product_id = %s
                    """, (site.lower(), main_product_id))
                    
                    result = cursor.fetchone()
                    if not result:
                        failed_products.append({
                            "site": site,
                            "main_product_id": main_product_id,
                            "reason": "product_not_found"
                        })
                        continue
                    
                    # Unpack results
                    product_id = result[0]
                    db_price = float(result[1]) if result[1] is not None else 0
                    db_discount = float(result[2]) if result[2] is not None else 0
                    db_availability = result[3]
                    db_price_change_count = int(result[4]) if result[4] is not None else 0
                    db_avg_price = float(result[5]) if result[5] is not None else None
                    db_min_price = float(result[6]) if result[6] is not None else None
                    db_max_price = float(result[7]) if result[7] is not None else None
                    db_priority = result[8] or 3
                    db_special_id = result[9]
                    db_price_valid_until = result[10]
                    db_volatility = int(result[11]) if result[11] is not None else 50
                    db_trend = result[12] or "stable"
                    
                    # Extract new values
                    price = new_data.get("price")
                    discount_price = new_data.get("discount_price")
                    discount_amount = new_data.get("discount_amount")
                    availability = new_data.get("availability")
                    special_id = new_data.get("special_id")
                    price_valid_until = new_data.get("price_valid_until")
                    
                    # Convert to float
                    price = float(price) if price is not None else None
                    discount_price = float(discount_price) if discount_price is not None else None
                    discount_amount = float(discount_amount) if discount_amount is not None else None
                    
                    # Normalize dates
                    if price_valid_until:
                        price_valid_until = clean_date(price_valid_until)
                    if db_price_valid_until:
                        db_price_valid_until = clean_date(db_price_valid_until)
                    
                    # Detect changes
                    price_changed = (db_price != price or db_discount != discount_price)
                    availability_changed = (db_availability != availability)
                    special_id_changed = (db_special_id != special_id)
                    price_valid_until_changed = (db_price_valid_until != price_valid_until)
                    
                    # Skip if no changes
                    if not any([price_changed, availability_changed, special_id_changed, price_valid_until_changed]):
                        continue
                    
                    # --- Calculate derived values ---
                    if price_changed and price is not None:
                        # Get price history for stats
                        cursor.execute("""
                            SELECT price FROM price_history 
                            WHERE product_id = %s ORDER BY recorded_at DESC LIMIT 100
                        """, (product_id,))
                        price_history = [float(row[0]) for row in cursor.fetchall() if row[0] is not None]
                        all_prices = price_history + [float(price)]
                        
                        new_min_price = float(min(all_prices))
                        new_max_price = float(max(all_prices))
                        new_avg_price = round(sum(all_prices) / len(all_prices), 2)
                        
                        # Volatility calculation
                        if db_price > 0:
                            change_pct = abs(price - db_price) / db_price * 100
                            if change_pct > 20:
                                new_volatility = min(100, db_volatility + 10)
                            elif change_pct > 10:
                                new_volatility = min(100, db_volatility + 5)
                            elif change_pct > 5:
                                new_volatility = db_volatility
                            else:
                                new_volatility = max(0, db_volatility - 2)
                        else:
                            new_volatility = db_volatility
                        
                        new_trend = "upward" if price > db_price else "downward" if price < db_price else "stable"
                    else:
                        new_min_price = db_min_price
                        new_max_price = db_max_price
                        new_avg_price = db_avg_price
                        new_volatility = db_volatility
                        new_trend = db_trend
                    
                    # Priority
                    new_priority = db_priority
                    if price_changed:
                        new_priority = max(1, db_priority - 1)
                    elif availability_changed and availability == "OutOfStock":
                        new_priority = min(4, db_priority + 1)
                    
                    # Monitoring group
                    new_group = "regular"
                    if price_changed and discount_price and discount_price < price:
                        new_group = "discounted"
                    elif availability == "InStock" and price and price > 2000:
                        new_group = "high_value"
                    elif availability == "OutOfStock":
                        new_group = "out_of_stock"
                    elif price_valid_until and price_valid_until <= (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"):
                        new_group = "flash_sale"
                    
                    # --- Build update params ---
                    update_params.append((
                        price or 0, discount_price or 0, discount_amount or 0,
                        availability, special_id, price_valid_until,
                        new_priority, new_group, new_volatility, new_trend,
                        new_avg_price, new_min_price, new_max_price,
                        1 if price_changed else 0,
                        1 if availability_changed else 0,
                        price_changed,
                        new_priority == 1, new_priority == 2, new_priority == 3, 
                        new_priority == 4, new_priority == 5,
                        product_id
                    ))
                    
                    # --- Price history ---
                    if price_changed:
                        price_history_params.append((
                            product_id, price, discount_price, discount_amount, availability
                        ))
                    
                    # --- Product changes ---
                    change_fields = []
                    if price_changed:
                        change_fields.extend([
                            (product_id, "price", db_price, price, source),
                            (product_id, "discount_price", db_discount, discount_price, source)
                        ])
                    if availability_changed:
                        change_fields.append((product_id, "availability", db_availability, availability, source))
                    if special_id_changed:
                        change_fields.append((product_id, "special_id", db_special_id, special_id, source))
                    if price_valid_until_changed:
                        change_fields.append((product_id, "price_valid_until", db_price_valid_until, price_valid_until, source))
                    
                    for pid, ftype, old_val, new_val, src in change_fields:
                        old_json = json.dumps(str(old_val)) if old_val is not None else None
                        new_json = json.dumps(str(new_val)) if new_val is not None else None
                        product_change_params.append((pid, ftype, old_json, new_json, src))
                    
                    # --- Alerts ---
                    if price_changed and db_price > 0:
                        if price < db_price * 0.9:
                            alert_params.append((product_id, site, "price_drop", str(db_price), str(price), "high"))
                        elif price > db_price * 1.2:
                            alert_params.append((product_id, site, "price_increase", str(db_price), str(price), "medium"))
                    
                    if availability_changed and db_availability == "OutOfStock" and availability == "InStock":
                        alert_params.append((product_id, site, "back_in_stock", db_availability, availability, "high"))
                    
                    # --- Monitoring stats ---
                    change_count = 1 if any([price_changed, availability_changed, special_id_changed, price_valid_until_changed]) else 0
                    monitoring_stats_params.append((
                        site.lower(),              # site
                        product_id,                # product_id
                        change_count,              # change_count
                        1 if price_changed else 0, # price_change_count
                        1 if availability_changed else 0, # availability_change_count
                        0.5,                       # last_check_duration
                        0.5                        # avg_check_duration
                    ))
                    
                except Exception as e:
                    failed_products.append({
                        "site": site,
                        "main_product_id": main_product_id,
                        "reason": str(e)
                    })
                    logger.error(f"❌ Prep failed: {site}/{main_product_id} - {e}")
            
            if not update_params:
                logger.info("✅ No changes to apply")
                return 0, failed_products
            
            # =====================================================
            # STEP 2: EXECUTE BULK OPERATIONS
            # =====================================================
            logger.info(f"🚀 Executing batch: {len(update_params)} products to update")
            
            # 1. UPDATE products
            if update_params:
                cursor.executemany("""
                    UPDATE products SET 
                        price = %s, discount_price = %s, discount_amount = %s,
                        availability = %s, special_id = %s, price_valid_until = %s,
                        last_updated_at = NOW(), last_checked = NOW(),
                        monitoring_priority = %s, monitoring_group = %s,
                        price_volatility_score = %s, price_trend = %s,
                        avg_price = %s, min_price = %s, max_price = %s,
                        price_change_count = COALESCE(price_change_count, 0) + %s,
                        availability_change_count = COALESCE(availability_change_count, 0) + %s,
                        last_price_change = CASE WHEN %s THEN NOW() ELSE last_price_change END,
                        next_check_at = CASE 
                            WHEN %s THEN NOW() + INTERVAL '1 hour'
                            WHEN %s THEN NOW() + INTERVAL '6 hours'
                            WHEN %s THEN NOW() + INTERVAL '1 day'
                            WHEN %s THEN NOW() + INTERVAL '3 days'
                            WHEN %s THEN NOW() + INTERVAL '7 days'
                            ELSE next_check_at
                        END
                    WHERE id = %s
                """, update_params)
                logger.info(f"   ✅ Products updated: {len(update_params)}")
            
            # 2. INSERT price_history
            if price_history_params:
                cursor.executemany("""
                    INSERT INTO price_history (product_id, price, discount_price, discount_amount, availability, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, price_history_params)
                logger.info(f"   ✅ Price history: {len(price_history_params)} records")
            
            # 3. INSERT product_changes
            if product_change_params:
                cursor.executemany("""
                    INSERT INTO product_changes (product_id, change_type, old_value, new_value, source, created_at)
                    VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW())
                """, product_change_params)
                logger.info(f"   ✅ Product changes: {len(product_change_params)} records")
            
            # 4. INSERT alerts
            if alert_params:
                cursor.executemany("""
                    INSERT INTO monitoring_alerts (product_id, site, alert_type, old_value, new_value, severity, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """, alert_params)
                logger.info(f"   ✅ Alerts: {len(alert_params)} records")
            
            # 5. UPSERT monitoring_stats
            if monitoring_stats_params:
                cursor.executemany("""
                    INSERT INTO monitoring_stats (
                        site, product_id, check_count, change_count, 
                        price_change_count, availability_change_count,
                        last_check_duration, avg_check_duration,
                        consecutive_errors, last_success, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, 1, %s, %s, %s, %s, %s, 0, NOW(), NOW(), NOW()
                    )
                    ON CONFLICT (site, product_id) DO UPDATE SET
                        check_count = monitoring_stats.check_count + 1,
                        change_count = monitoring_stats.change_count + 0,
                        price_change_count = monitoring_stats.price_change_count + 0,
                        availability_change_count = monitoring_stats.availability_change_count + 0,
                        last_check_duration = EXCLUDED.last_check_duration,
                        avg_check_duration = EXCLUDED.avg_check_duration,
                        consecutive_errors = 0,
                        last_success = NOW(),
                        updated_at = NOW()
                """, monitoring_stats_params)
                logger.info(f"   ✅ Monitoring stats: {len(monitoring_stats_params)} records")
            
            # Commit all changes
            self.conn.commit()
            
            elapsed = round(time.time() - start_time, 2)
            logger.info(f"✅ BATCH COMPLETE: {len(update_params)} updated, {len(failed_products)} failed in {elapsed}s")
            
            return len(update_params), failed_products
            
        except Exception as e:
            self.conn.rollback()
            logger.error(f"❌ Batch update failed: {e}")
            import traceback
            traceback.print_exc()
            return 0, failed_products
        finally:
            cursor.close()


    def _insert_product_changes(self, cursor, product_id: str, old_data: Dict, new_data: Dict, source: str):
        """Insert records for all changed fields - handles None properly"""
        tracked_fields = ["price", "discount_price", "discount_amount", "availability", "special_id", "price_valid_until"]
        
        for field in tracked_fields:
            old_val = old_data.get(field)
            new_val = new_data.get(field)
            
            # Store original for logging
            orig_old = old_val
            orig_new = new_val
            
            # Normalize for comparison
            if field in ["price", "discount_price", "discount_amount"]:
                old_val = float(old_val) if old_val is not None else None
                new_val = float(new_val) if new_val is not None else None
            elif field == "price_valid_until":
                if old_val:
                    old_val = clean_date(old_val)
                if new_val:
                    new_val = clean_date(new_val)
            elif field in ["availability", "special_id"]:
                old_val = str(old_val).strip() if old_val is not None else None
                new_val = str(new_val).strip() if new_val is not None else None
            
            # Compare (None vs None = no change, None vs Value = change)
            if old_val != new_val:
                # Handle None values properly for JSON
                old_json = json.dumps(str(old_val)) if old_val is not None else None
                new_json = json.dumps(str(new_val)) if new_val is not None else None
                
                cursor.execute("""
                    INSERT INTO product_changes (product_id, change_type, old_value, new_value, source, created_at)
                    VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW())
                """, (product_id, field, old_json, new_json, source))
                
                logger.info(f"Inserted product change: {field} | '{orig_old}' -> '{orig_new}'")

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

    def increment_check_failure(self, main_product_id: str, site: str) -> Dict:
        """
        Increment check_failures counter for a product
        Returns: {'failure_count': int, 'is_disabled': bool}
        """
        cursor = self.conn.cursor()
        try:
            # First, check current state
            cursor.execute("""
                SELECT check_failures, is_enabled, id
                FROM products 
                WHERE site = %s AND main_product_id = %s
            """, (site.lower(), main_product_id))
            
            result = cursor.fetchone()
            if not result:
                logger.warning(f"Product not found: {site}/{main_product_id}")
                return {"failure_count": 0, "is_disabled": False}
            
            current_failures = result[0] or 0
            is_enabled = result[1]
            product_uuid = result[0]
            
            # =====================================================
            # CASE 1: Already disabled - no increment
            # =====================================================
            if not is_enabled or current_failures >= 3:
                logger.debug(f"Product already disabled: {site}/{main_product_id} (failures={current_failures})")
                return {"failure_count": current_failures, "is_disabled": True}
            
            # =====================================================
            # CASE 2: Increment failures
            # =====================================================
            new_failure_count = current_failures + 1
            
            cursor.execute("""
                UPDATE products 
                SET check_failures = %s,
                    last_updated_at = NOW()
                WHERE site = %s AND main_product_id = %s
                RETURNING check_failures
            """, (new_failure_count, site.lower(), main_product_id))
            
            result = cursor.fetchone()
            new_count = result[0] if result else new_failure_count
            
            is_disabled = False
            
            # =====================================================
            # CASE 3: Check if should disable (reached 3)
            # =====================================================
            if new_count >= 3:
                # Get product UUID for disable
                cursor.execute("""
                    SELECT id FROM products 
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), main_product_id))
                product_row = cursor.fetchone()
                
                if product_row:
                    self.disable_product(product_row[0], "consecutive_failures")
                    is_disabled = True
                    logger.warning(f"Product disabled due to {new_count} consecutive failures: {site}/{main_product_id}")
            
            self.conn.commit()
            return {"failure_count": new_count, "is_disabled": is_disabled}
            
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to increment check failure: {e}")
            return {"failure_count": 0, "is_disabled": False}
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

    # =====================================================
    # RESOLVE PRICE INCREASE ALERTS
    # =====================================================

    def resolve_price_increase_alerts(self, product_id: str, current_price: float) -> int:
        """
        Resolve price increase alerts when price drops back to within 10% of original price
        
        Args:
            product_id: The UUID of the product
            current_price: The current price of the product
        
        Returns:
            Number of alerts resolved
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE product_id = %s 
                AND alert_type = 'price_increase'
                AND is_resolved = FALSE
                AND old_value::numeric * 1.1 >= %s
            """, (product_id, current_price))
            resolved_count = cursor.rowcount
            self.conn.commit()
            
            if resolved_count > 0:
                logger.info(f"Resolved {resolved_count} price increase alerts for product {product_id} (price dropped to {current_price})")
            
            return resolved_count
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to resolve price increase alerts: {e}")
            return 0
        finally:
            cursor.close()

    def update_price_volatility(self, site: str, main_product_id: str, new_price: float) -> bool:
        """Update price volatility score in PostgreSQL based on price change"""
        cursor = self.conn.cursor()
        try:
            # Get current product data - convert Decimal to float
            cursor.execute("""
                SELECT price, price_volatility_score, price_change_count
                FROM products 
                WHERE site = %s AND main_product_id = %s
            """, (site.lower(), main_product_id))
            
            row = cursor.fetchone()
            if not row:
                logger.warning(f"Product not found for volatility update: {site}/{main_product_id}")
                return False
            
            # Convert Decimal to float
            old_price = float(row[0]) if row[0] is not None else 0
            current_volatility = int(row[1]) if row[1] is not None else 50
            
            # Calculate new volatility
            if old_price > 0:
                price_change_percent = abs(new_price - old_price) / old_price * 100
                
                if price_change_percent > 20:
                    new_volatility = min(100, current_volatility + 10)
                elif price_change_percent > 10:
                    new_volatility = min(100, current_volatility + 5)
                elif price_change_percent > 5:
                    new_volatility = current_volatility
                else:
                    new_volatility = max(0, current_volatility - 2)
            else:
                new_volatility = current_volatility
            
            # Determine price trend
            if old_price > 0:
                if new_price > old_price:
                    trend = "upward"
                elif new_price < old_price:
                    trend = "downward"
                else:
                    trend = "stable"
            else:
                trend = "stable"
            
            # Update the product
            cursor.execute("""
                UPDATE products 
                SET price_volatility_score = %s,
                    price_trend = %s,
                    last_updated_at = NOW()
                WHERE site = %s AND main_product_id = %s
            """, (new_volatility, trend, site.lower(), main_product_id))
            
            self.conn.commit()
            logger.debug(f"Updated price volatility for {site}/{main_product_id}: {new_volatility} ({trend})")
            return True
            
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to update price volatility: {e}")
            return False
        finally:
            cursor.close()