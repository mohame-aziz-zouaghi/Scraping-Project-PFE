# Database/Postgresql/db.py
import json
import time
import psycopg2
from psycopg2.extras import Json, execute_batch
from psycopg2.pool import SimpleConnectionPool
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import threading
from contextlib import contextmanager

from Database.Postgresql.config import DBConfig
from core.logger import setup_logger
from core.utils.clean_date_logic import clean_date

logger = setup_logger("db", "database")


class Database:
    """
    Database class with connection pooling - maximum 15 connections.
    Connections are shared across all sites, monitoring, and scraping.
    """

    # =====================================================
    # CLASS-LEVEL CONNECTION POOL (Shared across all instances)
    # =====================================================
    _pool = None
    _pool_lock = threading.Lock()
    _initialized = False
    
    def __init__(self):
        self.conn = None
        self._acquired_connection = False
        self.failed_products_log = []
        
        # Initialize the pool if not already done
        self._init_pool()
    
    @classmethod
    def _init_pool(cls):
        """Initialize the connection pool (class-level, shared)."""
        if cls._initialized:
            return
        
        with cls._pool_lock:
            if cls._initialized:
                return
            
            try:
                logger.info("DB_POOL | Initializing connection pool (max=15)...")
                
                cls._pool = SimpleConnectionPool(
                    minconn=1,           # Minimum connections in pool
                    maxconn=15,          # Maximum connections in pool
                    host=DBConfig.HOST,
                    port=DBConfig.PORT,
                    database=DBConfig.NAME,
                    user=DBConfig.USER,
                    password=DBConfig.PASSWORD,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                
                cls._initialized = True
                logger.info(f"DB_POOL | Pool initialized | min=1, max=15")
                
            except Exception as e:
                logger.error(f"DB_POOL | Initialization error: {e}")
                raise
    
    @contextmanager
    def get_connection(self):
        """
        Get a connection from the pool.
        Use as a context manager to automatically return the connection.
        """
        conn = None
        try:
            conn = self._pool.getconn()
            # Ensure autocommit is False for transaction control
            conn.autocommit = False
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"DB_POOL | Connection error: {e}")
            raise
        finally:
            if conn:
                try:
                    self._pool.putconn(conn)
                except Exception as e:
                    logger.error(f"DB_POOL | Error returning connection: {e}")
    
    def _acquire_connection(self):
        """
        Acquire a connection from the pool for this instance.
        Used for backward compatibility with methods that use self.conn.
        """
        if self.conn is not None and not self.conn.closed:
            return
        
        try:
            self.conn = self._pool.getconn()
            self.conn.autocommit = False
            self._acquired_connection = True
            logger.debug(f"DB_POOL | Connection acquired | id={id(self.conn)}")
        except Exception as e:
            logger.error(f"DB_POOL | Failed to acquire connection: {e}")
            raise
    
    def _release_connection(self):
        """Release the connection back to the pool."""
        if self.conn is not None and self._acquired_connection:
            try:
                self._pool.putconn(self.conn)
                logger.debug(f"DB_POOL | Connection released | id={id(self.conn)}")
            except Exception as e:
                logger.error(f"DB_POOL | Error releasing connection: {e}")
            finally:
                self.conn = None
                self._acquired_connection = False
    
    def _connect(self):
        """
        Legacy method - now uses connection pool.
        """
        self._acquire_connection()
    
    def close(self):
        """
        Close/release the connection back to the pool.
        """
        self._release_connection()
    
    @classmethod
    def close_all_connections(cls):
        """
        Close all connections in the pool (shutdown only).
        """
        with cls._pool_lock:
            if cls._pool:
                try:
                    cls._pool.closeall()
                    cls._initialized = False
                    logger.info("DB_POOL | All connections closed")
                except Exception as e:
                    logger.error(f"DB_POOL | Error closing all connections: {e}")
                finally:
                    cls._pool = None

    # =====================================================
    # DEBUGGING METHODS
    # =====================================================
    
    def check_product_exists(self, site: str, main_product_id: str) -> bool:
        """Quick check if a product exists in the database."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
        """Get full product details for debugging."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
        """Check which product IDs are NOT in the database."""
        if not product_ids:
            return []
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
        """Log a product that failed to upsert for debugging."""
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
        """Get all logged failed products."""
        return self.failed_products_log.copy()
    
    def clear_failed_products(self):
        """Clear the failed products log."""
        self.failed_products_log = []

    # =====================================================
    # LOAD PRODUCTS
    # =====================================================
    
    def load_complete_products_for_preload(self, limit: int = None) -> List[Dict]:
        """Load ALL product data including monitoring stats for Redis preload."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
        """Load ONLY enabled products for monitoring (is_enabled = TRUE)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
    # UPSERT PRODUCTS (From Scraping) - URL as PRIMARY KEY
    # =====================================================
    
    def upsert_products(self, products: List[Dict]) -> int:
        """
        Insert or update products from scraping with full change detection.
        URL is the IMMUTABLE primary key - never changes once set.
        """
        if not products:
            return 0

        upserted_count = 0
        
        # =====================================================
        # FILTER: Skip products with missing required fields
        # =====================================================
        valid_products = []
        
        for p in products:
            if not p.get("main_product_id"):
                logger.warning(f"SKIP | missing main_product_id | url={p.get('url', '')[:50]}")
                continue
            if not p.get("url"):
                logger.warning(f"SKIP | {p.get('main_product_id')} | missing url")
                continue
            if not p.get("name"):
                logger.warning(f"SKIP | {p.get('main_product_id')} | missing name")
                continue
            if p.get("price") is None:
                logger.warning(f"SKIP | {p.get('main_product_id')} | missing price")
                continue
            
            valid_products.append(p)
        
        if not valid_products:
            logger.warning(f"UPSERT | No valid products after filtering | total={len(products)}")
            return 0
        
        logger.info(f"UPSERT | Processing {len(valid_products)} valid products")

        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # =====================================================
            # BULK LOOKUP: Find existing products by URL
            # =====================================================
            urls = [p.get('url') for p in valid_products if p.get('url')]
            
            existing_products = {}
            
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
                    existing_products[row[3]] = {
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
            
            logger.info(f"EXISTING_PRODUCTS | Found {len(existing_products)} existing products by URL")

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
                main_product_id = EXCLUDED.main_product_id,
                special_id = EXCLUDED.special_id,
                name = EXCLUDED.name,
                brand = EXCLUDED.brand,
                category = EXCLUDED.category,
                price = EXCLUDED.price,
                discount_price = EXCLUDED.discount_price,
                discount_amount = EXCLUDED.discount_amount,
                availability = EXCLUDED.availability,
                price_valid_until = EXCLUDED.price_valid_until,
                description = EXCLUDED.description,
                short_description = EXCLUDED.short_description,
                main_image = EXCLUDED.main_image,
                images = EXCLUDED.images,
                is_enabled = EXCLUDED.is_enabled,
                last_updated_at = NOW(),
                scraped_at = NOW()
            RETURNING id, main_product_id, price, discount_price, discount_amount, availability
            """

            processed_count = 0
            failed_products = []
            
            for product in valid_products:
                try:
                    main_product_id = product.get("main_product_id")
                    url = product.get("url")
                    site = product.get('site', '').lower()
                    
                    existing = existing_products.get(url)
                    is_new = existing is None
                    
                    # Handle main_product_id change
                    if existing and existing.get('main_product_id') != main_product_id:
                        old_main_id = existing.get('main_product_id')
                        logger.info(f"MAIN_ID_CHANGED | {old_main_id} -> {main_product_id} | url={url[:50]}")
                        
                        cursor.execute("""
                            SELECT id, url FROM products 
                            WHERE main_product_id = %s AND site = %s AND url != %s
                        """, (main_product_id, site, url))
                        conflict = cursor.fetchone()
                        
                        if conflict:
                            conflict_id, conflict_url = conflict
                            logger.error(f"MAIN_ID_CONFLICT | main_id={main_product_id} | conflict_product_id={conflict_id} | url={conflict_url}")
                            logger.warning(f"MAIN_ID_CONFLICT_RESOLVED | keeping existing main_product_id={old_main_id}")
                            product['main_product_id'] = old_main_id
                            main_product_id = old_main_id
                    
                    # Normalize data
                    product = self._normalize_product_data(product)
                    
                    # Execute upsert
                    cursor.execute(upsert_query, product)
                    result = cursor.fetchone()
                    
                    if not result:
                        logger.error(f"UPSERT_NO_RESULT | {main_product_id}")
                        self.log_failed_product(site, main_product_id, "upsert_returned_no_result", product)
                        failed_products.append(main_product_id)
                        continue
                    
                    db_id, db_main_id, db_price, db_discount, db_discount_amount, db_availability = result
                    
                    processed_count += 1
                    
                    if is_new:
                        logger.info(f"NEW_PRODUCT | id={db_id} | main_id={db_main_id} | price={db_price} | url={url[:50]}")
                    else:
                        price_changed = db_price != product.get('price', 0)
                        availability_changed = db_availability != product.get('availability', 'OutOfStock')
                        
                        if price_changed or availability_changed:
                            changes = []
                            if price_changed:
                                changes.append(f"price: {db_price}->{product.get('price', 0)}")
                            if availability_changed:
                                changes.append(f"availability: {db_availability}->{product.get('availability', 'OutOfStock')}")
                            logger.info(f"PRODUCT_UPDATED | id={db_id} | main_id={db_main_id} | changes={', '.join(changes)}")
                        else:
                            logger.debug(f"PRODUCT_UNCHANGED | id={db_id} | main_id={db_main_id}")
                    
                    if not is_new and existing:
                        self._process_product_changes(cursor, db_id, product, existing, site)
                    
                    self._update_monitoring_stats(cursor, site, db_id, is_new)
                    
                    if is_new:
                        existing_products[url] = {
                            "id": db_id,
                            "main_product_id": db_main_id,
                            "price": db_price,
                            "discount_price": db_discount,
                            "discount_amount": db_discount_amount,
                            "availability": db_availability,
                            "url": url
                        }
                    
                    upserted_count += 1
                    
                except psycopg2.IntegrityError as e:
                    conn.rollback()
                    logger.error(f"INTEGRITY_ERROR | {product.get('main_product_id')} | {e}")
                    self.log_failed_product(
                        product.get('site', 'unknown'),
                        product.get('main_product_id'),
                        f"integrity_error: {str(e)[:100]}",
                        product
                    )
                    failed_products.append(product.get('main_product_id'))
                    continue
                except Exception as e:
                    conn.rollback()
                    logger.error(f"PRODUCT_ERROR | {product.get('main_product_id')} | {e}")
                    import traceback
                    traceback.print_exc()
                    failed_products.append(product.get('main_product_id'))
                    continue

            # Commit all changes
            try:
                conn.commit()
                logger.info(f"UPSERT_COMPLETE | processed={processed_count} | upserted={upserted_count} | failed={len(failed_products)}")
                
                if failed_products:
                    self._log_failed_products(failed_products)
                
                return upserted_count
                
            except Exception as e:
                conn.rollback()
                logger.error(f"UPSERT_COMMIT_ERROR | {e}")
                import traceback
                traceback.print_exc()
                return 0
            finally:
                cursor.close()
    
    def _normalize_product_data(self, product: Dict) -> Dict:
        """Normalize product data for database insertion."""
        normalized = product.copy()
        
        if isinstance(normalized.get("short_description"), (dict, list)):
            normalized["short_description"] = Json(normalized["short_description"])
        elif normalized.get("short_description") is None:
            normalized["short_description"] = Json([])
        
        images = normalized.get("images")
        if isinstance(images, set):
            images = list(images)
        if isinstance(images, (list, dict)):
            normalized["images"] = Json(images)
        elif images is None:
            normalized["images"] = Json([])
        
        val = normalized.get("price_valid_until")
        if val in ("", " ", None):
            normalized["price_valid_until"] = None
        
        for field in ["price", "discount_price", "discount_amount"]:
            if normalized.get(field) is not None:
                normalized[field] = float(normalized[field])
            else:
                normalized[field] = 0
        
        if normalized.get("site"):
            normalized["site"] = normalized["site"].lower()
        
        return normalized
    
    def _process_product_changes(self, cursor, product_id: int, product: Dict, 
                                  existing: Dict, site: str):
        """Process and track product changes."""
        
        changes = []
        
        if existing.get('price') != product.get('price'):
            changes.append(('price', existing.get('price'), product.get('price')))
        
        if existing.get('discount_price') != product.get('discount_price'):
            changes.append(('discount_price', existing.get('discount_price'), product.get('discount_price')))
        
        if existing.get('discount_amount') != product.get('discount_amount'):
            changes.append(('discount_amount', existing.get('discount_amount'), product.get('discount_amount')))
        
        if existing.get('availability') != product.get('availability'):
            changes.append(('availability', existing.get('availability'), product.get('availability')))
        
        if existing.get('special_id') != product.get('special_id'):
            changes.append(('special_id', existing.get('special_id'), product.get('special_id')))
        
        if existing.get('name') != product.get('name'):
            changes.append(('name', existing.get('name'), product.get('name')))
        
        if existing.get('brand') != product.get('brand'):
            changes.append(('brand', existing.get('brand'), product.get('brand')))
        
        if existing.get('category') != product.get('category'):
            changes.append(('category', existing.get('category'), product.get('category')))
        
        if existing.get('price_valid_until') != product.get('price_valid_until'):
            changes.append(('price_valid_until', existing.get('price_valid_until'), product.get('price_valid_until')))
        
        if not changes:
            return
        
        logger.debug(f"PRODUCT_CHANGES_DETECTED | id={product_id} | main_id={product.get('main_product_id')} | changes={len(changes)}")
        
        price_changed = any(c[0] in ['price', 'discount_price', 'discount_amount'] for c in changes)
        availability_changed = any(c[0] == 'availability' for c in changes)
        
        if price_changed:
            cursor.execute("""
                UPDATE products 
                SET price_change_count = COALESCE(price_change_count, 0) + 1,
                    last_price_change = NOW()
                WHERE id = %s
            """, (product_id,))
        
        if availability_changed:
            cursor.execute("""
                UPDATE products 
                SET availability_change_count = COALESCE(availability_change_count, 0) + 1
                WHERE id = %s
            """, (product_id,))
        
        if price_changed:
            cursor.execute("""
                INSERT INTO price_history (
                    product_id, price, discount_price, discount_amount, availability, recorded_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (product_id, 
                  product.get('price', 0),
                  product.get('discount_price', 0),
                  product.get('discount_amount', 0),
                  product.get('availability', 'OutOfStock')))
            
            old_price = existing.get('price', 0)
            new_price = product.get('price', 0)
            
            if old_price > 0:
                if new_price < old_price * 0.9:
                    self._create_alert(cursor, product_id, site, 'price_drop', 
                                       old_price, new_price, 'high')
                elif new_price > old_price * 1.2:
                    self._create_alert(cursor, product_id, site, 'price_increase',
                                       old_price, new_price, 'medium')
        
        if availability_changed:
            old_avail = existing.get('availability', 'OutOfStock')
            new_avail = product.get('availability', 'OutOfStock')
            
            if old_avail == 'OutOfStock' and new_avail == 'InStock':
                self._create_alert(cursor, product_id, site, 'back_in_stock',
                                   old_avail, new_avail, 'high')
            elif old_avail == 'InStock' and new_avail == 'OutOfStock':
                self._create_alert(cursor, product_id, site, 'out_of_stock',
                                   old_avail, new_avail, 'medium')
        
        for field, old_val, new_val in changes:
            old_json = json.dumps(str(old_val)) if old_val is not None else None
            new_json = json.dumps(str(new_val)) if new_val is not None else None
            
            cursor.execute("""
                INSERT INTO product_changes (
                    product_id, change_type, old_value, new_value, source, created_at
                )
                VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW())
            """, (product_id, field, old_json, new_json, "scraping"))
        
        if price_changed and new_price > 0:
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE product_id = %s 
                  AND is_resolved = FALSE
                  AND alert_type IN ('price_drop', 'price_increase')
            """, (product_id,))
        
        if availability_changed and new_avail == 'InStock':
            cursor.execute("""
                UPDATE monitoring_alerts 
                SET is_resolved = TRUE, resolved_at = NOW()
                WHERE product_id = %s 
                  AND is_resolved = FALSE
                  AND alert_type = 'back_in_stock'
            """, (product_id,))
    
    def _create_alert(self, cursor, product_id: int, site: str, alert_type: str,
                      old_value, new_value, severity: str):
        """Create a monitoring alert."""
        cursor.execute("""
            INSERT INTO monitoring_alerts (
                product_id, site, alert_type, old_value, new_value, severity, created_at, is_resolved
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), FALSE)
        """, (product_id, site.lower(), alert_type, str(old_value), str(new_value), severity))
        logger.warning(f"ALERT_CREATED | {site} | {alert_type} | {old_value} -> {new_value}")
    
    def _update_monitoring_stats(self, cursor, site: str, product_id: int, is_new: bool):
        """Update monitoring statistics."""
        if is_new:
            cursor.execute("""
                INSERT INTO monitoring_stats (site, product_id, check_count, change_count, last_success, created_at, updated_at)
                VALUES (%s, %s, 1, 0, NOW(), NOW(), NOW())
            """, (site.lower(), product_id))
        else:
            cursor.execute("""
                INSERT INTO monitoring_stats (site, product_id, check_count, change_count, last_success, created_at, updated_at)
                VALUES (%s, %s, 1, 0, NOW(), NOW(), NOW())
                ON CONFLICT (site, product_id) 
                DO UPDATE SET
                    check_count = monitoring_stats.check_count + 1,
                    last_success = NOW(),
                    updated_at = NOW()
            """, (site.lower(), product_id))
    
    def _log_failed_products(self, failed_products: List[str]):
        """Log failed products to file."""
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/failed_upsert_products.log", "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                f.write(f"Failed Products: {len(failed_products)}\n")
                for pid in failed_products:
                    f.write(f"  - {pid}\n")
                f.write(f"{'='*60}\n")
        except Exception as e:
            logger.error(f"FAILED_LOG_ERROR | {e}")

    # =====================================================
    # GET PRODUCT
    # =====================================================
    
    def get_product_by_site_and_main_id(self, site: str, main_product_id: str) -> Optional[Dict]:
        """Get product by composite key."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
        TRUE BATCH UPDATE - Optimized bulk operations with executemany.
        """
        if not products_data:
            return 0, []
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            start_time = time.time()
            
            # Bulk data collections
            update_params = []
            price_history_params = []
            product_change_params = []
            alert_params = []
            monitoring_stats_params = []
            failed_products = []
            
            try:
                logger.info(f"📦 Preparing batch update for {len(products_data)} products...")
                
                for site, main_product_id, old_data, new_data, source in products_data:
                    try:
                        if not isinstance(old_data, dict) or not isinstance(new_data, dict):
                            failed_products.append({
                                "site": site,
                                "main_product_id": main_product_id,
                                "reason": "invalid_data_type"
                            })
                            continue                        
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
                        
                        price = new_data.get("price")
                        discount_price = new_data.get("discount_price")
                        discount_amount = new_data.get("discount_amount")
                        availability = new_data.get("availability")
                        special_id = new_data.get("special_id")
                        price_valid_until = new_data.get("price_valid_until")
                        
                        price = float(price) if price is not None else None
                        discount_price = float(discount_price) if discount_price is not None else None
                        discount_amount = float(discount_amount) if discount_amount is not None else None
                        
                        if price_valid_until:
                            price_valid_until = clean_date(price_valid_until)
                        if db_price_valid_until:
                            db_price_valid_until = clean_date(db_price_valid_until)
                        
                        price_changed = (db_price != price or db_discount != discount_price)
                        availability_changed = (db_availability != availability)
                        special_id_changed = (db_special_id != special_id)
                        price_valid_until_changed = (db_price_valid_until != price_valid_until)
                        
                        if not any([price_changed, availability_changed, special_id_changed, price_valid_until_changed]):
                            continue
                        
                        if price_changed and price is not None:
                            cursor.execute("""
                                SELECT price FROM price_history 
                                WHERE product_id = %s ORDER BY recorded_at DESC LIMIT 100
                            """, (product_id,))
                            price_history = [float(row[0]) for row in cursor.fetchall() if row[0] is not None]
                            all_prices = price_history + [float(price)]
                            
                            new_min_price = float(min(all_prices))
                            new_max_price = float(max(all_prices))
                            new_avg_price = round(sum(all_prices) / len(all_prices), 2)
                            
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
                        
                        new_priority = db_priority
                        if price_changed:
                            new_priority = max(1, db_priority - 1)
                        elif availability_changed and availability == "OutOfStock":
                            new_priority = min(4, db_priority + 1)
                        
                        new_group = "regular"
                        if price_changed and discount_price and discount_price < price:
                            new_group = "discounted"
                        elif availability == "InStock" and price and price > 2000:
                            new_group = "high_value"
                        elif availability == "OutOfStock":
                            new_group = "out_of_stock"
                        elif price_valid_until and price_valid_until <= (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"):
                            new_group = "flash_sale"
                        
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
                        
                        if price_changed:
                            price_history_params.append((
                                product_id, price, discount_price, discount_amount, availability
                            ))
                        
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
                        
                        if price_changed and db_price > 0:
                            if price < db_price * 0.9:
                                alert_params.append((product_id, site, "price_drop", str(db_price), str(price), "high"))
                            elif price > db_price * 1.2:
                                alert_params.append((product_id, site, "price_increase", str(db_price), str(price), "medium"))
                        
                        if availability_changed and db_availability == "OutOfStock" and availability == "InStock":
                            alert_params.append((product_id, site, "back_in_stock", db_availability, availability, "high"))
                        
                        change_count = 1 if any([price_changed, availability_changed, special_id_changed, price_valid_until_changed]) else 0
                        monitoring_stats_params.append((
                            site.lower(),
                            product_id,
                            change_count,
                            1 if price_changed else 0,
                            1 if availability_changed else 0,
                            0.5,
                            0.5
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
                
                logger.info(f"🚀 Executing batch: {len(update_params)} products to update")
                
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
                
                if price_history_params:
                    cursor.executemany("""
                        INSERT INTO price_history (product_id, price, discount_price, discount_amount, availability, recorded_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                    """, price_history_params)
                    logger.info(f"   ✅ Price history: {len(price_history_params)} records")
                
                if product_change_params:
                    cursor.executemany("""
                        INSERT INTO product_changes (product_id, change_type, old_value, new_value, source, created_at)
                        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW())
                    """, product_change_params)
                    logger.info(f"   ✅ Product changes: {len(product_change_params)} records")
                
                if alert_params:
                    cursor.executemany("""
                        INSERT INTO monitoring_alerts (product_id, site, alert_type, old_value, new_value, severity, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """, alert_params)
                    logger.info(f"   ✅ Alerts: {len(alert_params)} records")
                
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
                
                conn.commit()
                
                elapsed = round(time.time() - start_time, 2)
                logger.info(f"✅ BATCH COMPLETE: {len(update_params)} updated, {len(failed_products)} failed in {elapsed}s")
                
                return len(update_params), failed_products
                
            except Exception as e:
                conn.rollback()
                logger.error(f"❌ Batch update failed: {e}")
                import traceback
                traceback.print_exc()
                return 0, failed_products
            finally:
                cursor.close()

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
        """Update monitoring_stats table."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to increment stats: {e}")
                return False
            finally:
                cursor.close()

    # =====================================================
    # OTHER MONITORING METHODS
    # =====================================================
    
    def update_next_check_time(self, site: str, main_product_id: str, next_check_at: datetime) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE products SET next_check_at = %s, last_updated_at = NOW()
                    WHERE site = %s AND main_product_id = %s
                """, (next_check_at, site.lower(), main_product_id))
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to update next check: {e}")
                return False
            finally:
                cursor.close()

    def create_monitoring_alert(self, product_id: str, site: str, alert_type: str, old_value: str, new_value: str, severity: str = "medium") -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO monitoring_alerts (product_id, site, alert_type, old_value, new_value, severity, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """, (product_id, site, alert_type, old_value, new_value, severity))
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to create alert: {e}")
                return False
            finally:
                cursor.close()

    def get_site_config(self, site: str) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
    # ALERT MANAGEMENT METHODS
    # =====================================================

    def resolve_alert(self, alert_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE monitoring_alerts 
                    SET is_resolved = TRUE, resolved_at = NOW()
                    WHERE id = %s AND is_resolved = FALSE
                """, (alert_id,))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to resolve alert {alert_id}: {e}")
                return False
            finally:
                cursor.close()

    def resolve_price_drop_alerts(self, product_id: str, current_price: float) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
                conn.commit()
                if resolved_count > 0:
                    logger.info(f"Resolved {resolved_count} price drop alerts for product {product_id} (price recovered to {current_price})")
                return resolved_count
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to resolve price drop alerts: {e}")
                return 0
            finally:
                cursor.close()

    def resolve_price_increase_alerts(self, product_id: str, current_price: float) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
                conn.commit()
                if resolved_count > 0:
                    logger.info(f"Resolved {resolved_count} price increase alerts for product {product_id} (price dropped to {current_price})")
                return resolved_count
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to resolve price increase alerts: {e}")
                return 0
            finally:
                cursor.close()

    def resolve_back_in_stock_alerts(self, product_id: str, current_availability: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                if current_availability == "OutOfStock":
                    cursor.execute("""
                        UPDATE monitoring_alerts 
                        SET is_resolved = TRUE, resolved_at = NOW()
                        WHERE product_id = %s 
                        AND alert_type = 'back_in_stock'
                        AND is_resolved = FALSE
                    """, (product_id,))
                    resolved_count = cursor.rowcount
                    conn.commit()
                    if resolved_count > 0:
                        logger.info(f"Resolved {resolved_count} back-in-stock alerts for product {product_id} (product out of stock again)")
                    return resolved_count
                return 0
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to resolve back-in-stock alerts: {e}")
                return 0
            finally:
                cursor.close()

    def resolve_all_product_alerts(self, product_id: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE monitoring_alerts 
                    SET is_resolved = TRUE, resolved_at = NOW()
                    WHERE product_id = %s AND is_resolved = FALSE
                """, (product_id,))
                resolved_count = cursor.rowcount
                conn.commit()
                if resolved_count > 0:
                    logger.info(f"Resolved {resolved_count} alerts for product {product_id}")
                return resolved_count
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to resolve product alerts: {e}")
                return 0
            finally:
                cursor.close()

    def get_active_alerts(self, product_id: str = None, alert_type: str = None, limit: int = 100) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
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
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE monitoring_alerts 
                    SET is_resolved = TRUE, resolved_at = NOW()
                    WHERE is_resolved = FALSE 
                    AND created_at < NOW() - INTERVAL '%s days'
                """, (days,))
                cleaned_count = cursor.rowcount
                conn.commit()
                if cleaned_count > 0:
                    logger.info(f"Auto-resolved {cleaned_count} alerts older than {days} days")
                return cleaned_count
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to cleanup old alerts: {e}")
                return 0
            finally:
                cursor.close()

    # =====================================================
    # PRODUCT ENABLE/DISABLE METHODS
    # =====================================================

    def disable_product(self, product_id: str, reason: str = "consecutive_failures") -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE products 
                    SET is_enabled = FALSE, 
                        last_updated_at = NOW()
                    WHERE id = %s
                """, (product_id,))
                conn.commit()
                logger.info(f"Product disabled: {product_id} | reason={reason}")
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to disable product: {e}")
                return False
            finally:
                cursor.close()

    def enable_product(self, product_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE products 
                    SET is_enabled = TRUE, 
                        check_failures = 0,
                        last_updated_at = NOW()
                    WHERE id = %s
                """, (product_id,))
                conn.commit()
                logger.info(f"Product enabled: {product_id}")
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to enable product: {e}")
                return False
            finally:
                cursor.close()

    def increment_check_failure(self, main_product_id: str, site: str) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
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
                product_uuid = result[2]
                
                if not is_enabled or current_failures >= 3:
                    logger.debug(f"Product already disabled: {site}/{main_product_id} (failures={current_failures})")
                    return {"failure_count": current_failures, "is_disabled": True}
                
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
                
                if new_count >= 3:
                    self.disable_product(product_uuid, "consecutive_failures")
                    is_disabled = True
                    logger.warning(f"Product disabled due to {new_count} consecutive failures: {site}/{main_product_id}")
                
                conn.commit()
                return {"failure_count": new_count, "is_disabled": is_disabled}
                
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to increment check failure: {e}")
                return {"failure_count": 0, "is_disabled": False}
            finally:
                cursor.close()

    def reset_check_failures(self, main_product_id: str, site: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE products 
                    SET check_failures = 0,
                        last_updated_at = NOW()
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), main_product_id))
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to reset check failures: {e}")
                return False
            finally:
                cursor.close()

    def get_disabled_products_older_than(self, days: int = 3, limit: int = 100) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
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

    def update_price_volatility(self, site: str, main_product_id: str, new_price: float) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT price, price_volatility_score, price_change_count
                    FROM products 
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), main_product_id))
                
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"Product not found for volatility update: {site}/{main_product_id}")
                    return False
                
                old_price = float(row[0]) if row[0] is not None else 0
                current_volatility = int(row[1]) if row[1] is not None else 50
                
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
                
                if old_price > 0:
                    if new_price > old_price:
                        trend = "upward"
                    elif new_price < old_price:
                        trend = "downward"
                    else:
                        trend = "stable"
                else:
                    trend = "stable"
                
                cursor.execute("""
                    UPDATE products 
                    SET price_volatility_score = %s,
                        price_trend = %s,
                        last_updated_at = NOW()
                    WHERE site = %s AND main_product_id = %s
                """, (new_volatility, trend, site.lower(), main_product_id))
                
                conn.commit()
                logger.debug(f"Updated price volatility for {site}/{main_product_id}: {new_volatility} ({trend})")
                return True
                
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to update price volatility: {e}")
                return False
            finally:
                cursor.close()


# =====================================================
# SHUTDOWN HOOK - Close all connections on exit
# =====================================================

def shutdown_db_pool():
    """Shutdown the database connection pool."""
    try:
        Database.close_all_connections()
        logger.info("DB_POOL | Shutdown complete")
    except Exception as e:
        logger.error(f"DB_POOL | Shutdown error: {e}")


# Register shutdown hook if available
try:
    from core.shutdown_handler import register_shutdown_hook
    register_shutdown_hook(shutdown_db_pool, "db_pool_shutdown")
    logger.info("DB_POOL | Shutdown hook registered")
except Exception as e:
    logger.warning(f"DB_POOL | Shutdown hook registration failed: {e}")