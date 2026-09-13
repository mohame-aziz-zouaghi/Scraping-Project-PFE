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
    Database class with connection pooling - maximum 30 connections.
    Connections are shared across all sites, monitoring, and scraping.
    """

    # =====================================================
    # CLASS-LEVEL CONNECTION POOL
    # =====================================================
    _pool = None
    _pool_lock = threading.Lock()
    _initialized = False
    
    _pool_monitor_thread = None
    _pool_monitor_running = False
    _acquired_connections = {}
    _acquired_lock = threading.Lock()
    
    def __init__(self):
        self.conn = None
        self._acquired_connection = False
        self.failed_products_log = []
        self._connection_owner = None
        self._init_pool()
        self._start_pool_monitor()
    
    @classmethod
    def _init_pool(cls):
        if cls._initialized:
            return
        with cls._pool_lock:
            if cls._initialized:
                return
            try:
                logger.info("DB_POOL | Initializing connection pool (max=30)...")
                cls._pool = SimpleConnectionPool(
                    minconn=5, maxconn=30,
                    host=DBConfig.HOST, port=DBConfig.PORT,
                    database=DBConfig.NAME, user=DBConfig.USER,
                    password=DBConfig.PASSWORD,
                    keepalives=1, keepalives_idle=30,
                    keepalives_interval=10, keepalives_count=5
                )
                cls._initialized = True
                logger.info(f"DB_POOL | Pool initialized | min=5, max=30")
            except Exception as e:
                logger.error(f"DB_POOL | Initialization error: {e}")
                raise
    
    @classmethod
    def _start_pool_monitor(cls):
        if cls._pool_monitor_running:
            return
        with cls._pool_lock:
            if cls._pool_monitor_running:
                return
            cls._pool_monitor_running = True
            def monitor_loop():
                while cls._pool_monitor_running:
                    try:
                        cls._check_pool_status()
                    except Exception as e:
                        logger.error(f"DB_POOL_MONITOR | Error: {e}")
                    time.sleep(10)
            cls._pool_monitor_thread = threading.Thread(
                target=monitor_loop, name="db_pool_monitor", daemon=True
            )
            cls._pool_monitor_thread.start()
            logger.info("DB_POOL_MONITOR | Started (checks every 10s)")
    
    @classmethod
    def _check_pool_status(cls):
        """Check pool status. Distinguishes acquired vs free vs total."""
        if not cls._pool:
            return
        try:
            # psycopg2.pool.SimpleConnectionPool internals:
            #   _pool = list of FREE connections
            #   _used = dict of connection → key currently in use
            #   _rused = reverse map
            free_connections = len(cls._pool._pool) if hasattr(cls._pool, '_pool') else 0
            used_connections = len(cls._pool._used) if hasattr(cls._pool, '_used') else 0
            total_connections = free_connections + used_connections
            
            with cls._acquired_lock:
                acquired_count = len(cls._acquired_connections)
                acquired_details = []
                for conn_id, info in cls._acquired_connections.items():
                    acquired_time = info.get('acquired_at', datetime.now())
                    held_for = (datetime.now() - acquired_time).total_seconds()
                    acquired_details.append({
                        'conn_id': conn_id,
                        'owner': info.get('owner', 'unknown'),
                        'thread': info.get('thread_name', 'unknown'),
                        'held_for': held_for
                    })
            
            if acquired_count > 0:
                logger.warning(
                    f"DB_POOL_STATUS | free={free_connections} | used={used_connections} | "
                    f"total={total_connections} | acquired={acquired_count}"
                )
                for detail in acquired_details:
                    if detail['held_for'] > 30:
                        logger.warning(
                            f"DB_POOL_LONG_HELD | conn_id={detail['conn_id']} | "
                            f"owner={detail['owner']} | thread={detail['thread']} | "
                            f"held_for={detail['held_for']:.1f}s"
                        )
            else:
                logger.debug(
                    f"DB_POOL_STATUS | free={free_connections} | used={used_connections} | "
                    f"total={total_connections} | acquired={acquired_count}"
                )
            
            # Stuck connection detection (>120s)
            with cls._acquired_lock:
                for conn_id, info in cls._acquired_connections.items():
                    held_for = (datetime.now() - info.get('acquired_at', datetime.now())).total_seconds()
                    if held_for > 120:
                        logger.error(
                            f"DB_POOL_STUCK_CONN | conn_id={conn_id} | "
                            f"owner={info.get('owner', 'unknown')} | "
                            f"held_for={held_for:.1f}s"
                        )
        except Exception as e:
            logger.error(f"DB_POOL_MONITOR | Check error: {e}")
    
    @classmethod
    def _stop_pool_monitor(cls):
        cls._pool_monitor_running = False
        if cls._pool_monitor_thread:
            cls._pool_monitor_thread.join(timeout=2)
    
    @contextmanager
    def get_connection(self, timeout: int = 30):
        """
        Get a connection from the pool with proper transaction handling.
        Guarantees: rollback on exception, release on exit.
        """
        conn = None
        conn_id = None
        owner = self._get_owner_name()
        acquire_time = time.time()
        _ROLLBACK_MARKER = object()
        try:
            logger.debug(f"🔌 DB_ACQUIRE | owner={owner} | thread={threading.current_thread().name}")
            
            # Pool exhaustion handling
            try:
                conn = self._pool.getconn()
            except Exception as pool_err:
                logger.error(f"❌ DB_POOL_EXHAUSTED | owner={owner} | error={pool_err}")
                raise
            
            conn.autocommit = False
            conn_id = id(conn)
            with self._acquired_lock:
                self._acquired_connections[conn_id] = {
                    'owner': owner,
                    'thread_name': threading.current_thread().name,
                    'acquired_at': datetime.now()
                }
            
            free_connections = len(self._pool._pool) if hasattr(self._pool, '_pool') else '?'
            used_connections = len(self._pool._used) if hasattr(self._pool, '_used') else '?'
            logger.debug(
                f"   ✅ Connection acquired | id={conn_id} | free={free_connections} | used={used_connections}"
            )
            
            # Yield, capture exceptions from caller
            try:
                yield conn
            except Exception as caller_exc:
                # Rollback here so caller doesn't have to
                try:
                    conn.rollback()
                    logger.debug(f"🔄 DB_ROLLBACK | id={conn_id} | owner={owner} | due to exception")
                except Exception as rb_err:
                    logger.error(f"DB_ROLLBACK_ERROR | {rb_err}")
                raise caller_exc
            
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except: pass
            logger.error(f"❌ DB_ACQUIRE_FAILED | owner={owner} | error={e}")
            raise
        finally:
            if conn:
                try:
                    # If transaction is still open (idle in transaction), rollback
                    # This prevents leaking broken state to the next user
                    try:
                        if conn.get_transaction_status() != 0:  # 0 = IDLE
                            conn.rollback()
                    except: pass
                    
                    self._pool.putconn(conn)
                    if conn_id:
                        with self._acquired_lock:
                            self._acquired_connections.pop(conn_id, None)
                    
                    held_for = time.time() - acquire_time
                    if held_for > 5:
                        logger.warning(f"🔓 DB_RELEASE | id={conn_id} | owner={owner} | held_for={held_for:.1f}s")
                    else:
                        logger.debug(f"🔓 DB_RELEASE | id={conn_id} | owner={owner} | held_for={held_for:.2f}s")
                except Exception as e:
                    logger.error(f"DB_POOL | Error returning connection: {e}")
    
    def _get_owner_name(self) -> str:
        import inspect
        try:
            frame = inspect.currentframe()
            if frame and frame.f_back and frame.f_back.f_back:
                caller_frame = frame.f_back.f_back
                for _ in range(5):
                    if caller_frame is None: break
                    method_name = caller_frame.f_code.co_name
                    if method_name in ['__enter__', '__exit__', '_get_owner_name', 'get_connection']:
                        caller_frame = caller_frame.f_back
                        continue
                    class_name = ''
                    if 'self' in caller_frame.f_locals:
                        class_name = caller_frame.f_locals['self'].__class__.__name__
                    if class_name:
                        return f"{class_name}.{method_name}"
                    return method_name
        except: pass
        return self.__class__.__name__
    
    def close(self):
        """Release connection back to pool with rollback."""
        if self.conn is not None and self._acquired_connection:
            try:
                try: self.conn.rollback()
                except: pass
                self._pool.putconn(self.conn)
                self._acquired_connection = False
                self.conn = None
            except: pass
    
    @classmethod
    def close_all_connections(cls):
        cls._stop_pool_monitor()
        with cls._pool_lock:
            if cls._pool:
                try:
                    cls._pool.closeall()
                    cls._initialized = False
                except: pass
                finally: cls._pool = None
            with cls._acquired_lock:
                cls._acquired_connections.clear()

    # =====================================================
    # DEBUGGING METHODS
    # =====================================================
    
    def check_product_exists(self, site: str, main_product_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT id FROM products 
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), main_product_id))
                exists = cursor.fetchone() is not None
                if not exists:
                    logger.warning(f"DB_CHECK | Product NOT in DB: {site}/{main_product_id}")
                return exists
            except Exception as e:
                logger.error(f"DB_CHECK_ERROR | {site}/{main_product_id} | {e}")
                return False
            finally:
                cursor.close()
    
    def get_product_details(self, site: str, main_product_id: str) -> Optional[Dict]:
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
                        "id": row[0], "site": row[1], "main_product_id": row[2],
                        "url": row[3], "name": row[4], "price": row[5],
                        "discount_price": row[6], "availability": row[7],
                        "special_id": row[8], "is_enabled": row[9],
                        "price_valid_until": row[10]
                    }
                return None
            except Exception as e:
                logger.error(f"GET_PRODUCT_DETAILS_ERROR | {site}/{main_product_id} | {e}")
                return None
            finally:
                cursor.close()

    def get_products_not_in_db(self, site: str, product_ids: List[str]) -> List[str]:
        """Fixed: single query instead of N+1."""
        if not product_ids:
            return []
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT main_product_id FROM products 
                    WHERE site = %s AND main_product_id = ANY(%s)
                """, (site.lower(), product_ids))
                existing = {row[0] for row in cursor.fetchall()}
                missing = [pid for pid in product_ids if pid not in existing]
                if missing:
                    logger.warning(f"MISSING_IN_DB | site={site} | count={len(missing)}")
                return missing
            except Exception as e:
                logger.error(f"GET_MISSING_ERROR | {site} | {e}")
                return []
            finally:
                cursor.close()
    
    def log_failed_product(self, site: str, main_product_id: str, reason: str, data: Dict = None):
        self.failed_products_log.append({
            "site": site, "main_product_id": main_product_id,
            "reason": reason, "data": data, "timestamp": datetime.now().isoformat()
        })
        logger.error(f"FAILED_UPSERT | site={site} | product_id={main_product_id} | reason={reason}")
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open("logs/failed_upserts.log", "a") as f:
                f.write(f"{datetime.now().isoformat()} | {site} | {main_product_id} | {reason}\n")
        except: pass
    
    def get_failed_products(self) -> List[Dict]:
        return self.failed_products_log.copy()
    
    def clear_failed_products(self):
        self.failed_products_log = []

    # =====================================================
    # LOAD PRODUCTS FOR MONITORING
    # =====================================================
    
    def load_products_for_monitoring(self, limit: int = None, redis_db: bool = False) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT main_product_id, site, url, special_id, price_valid_until,
                    availability, price, discount_price, discount_amount,
                    is_enabled, check_failures, last_updated_at
                FROM products
            """
            if not redis_db:
                query += " WHERE is_enabled = TRUE"
            if limit:
                query += f" LIMIT {limit}"
            
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            
            products = []
            for row in rows:
                products.append({
                    "main_product_id": row[0], "site": row[1], "url": row[2],
                    "special_id": row[3], "price_valid_until": row[4],
                    "availability": row[5], "price": float(row[6]) if row[6] else 0,
                    "discount_price": float(row[7]) if row[7] else 0,
                    "discount_amount": float(row[8]) if row[8] else 0,
                    "is_enabled": row[9], "check_failures": row[10] or 0,
                    "last_updated_at": row[11]
                })
            return products

    # =====================================================
    # UPSERT PRODUCTS (From Scraping)
    # =====================================================
    
    def upsert_products(self, products: List[Dict]) -> int:
        """
        Insert or update products from scraping.
        Uses SAVEPOINT per product so one failure doesn't discard the whole batch.
        """
        if not products:
            return 0

        valid_products = []
        for p in products:
            if not p.get("main_product_id") or not p.get("url") or not p.get("name") or p.get("price") is None:
                continue
            valid_products.append(p)
        
        if not valid_products:
            return 0

        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            urls = [p.get('url') for p in valid_products]
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
                        "id": row[0], "site": row[1], "main_product_id": row[2],
                        "url": row[3], "price": float(row[4]) if row[4] else 0,
                        "discount_price": float(row[5]) if row[5] else 0,
                        "discount_amount": float(row[6]) if row[6] else 0,
                        "availability": row[7], "special_id": row[8],
                        "price_valid_until": row[9], "name": row[10],
                        "brand": row[11], "category": row[12],
                        "description": row[13], "short_description": row[14],
                        "main_image": row[15], "images": row[16],
                        "scraped_at": row[17], "is_enabled": row[18],
                        "check_failures": row[19] or 0,
                        "price_change_count": row[20] or 0,
                        "availability_change_count": row[21] or 0
                    }
            
            upsert_query = """
            INSERT INTO products (
                site, main_product_id, special_id, name, brand, category,
                price, discount_price, discount_amount, availability, price_valid_until,
                url, description, short_description, main_image, images,
                scraped_at, last_updated_at, last_checked, is_enabled,
                monitoring_priority, monitoring_group,
                check_failures, consecutive_errors,
                price_change_count, availability_change_count,
                price_volatility_score, price_trend,
                change_frequency_7d, change_frequency_30d,
                days_since_last_change, total_checks, total_errors,
                next_check_at
            )
            VALUES (
                %(site)s, %(main_product_id)s, %(special_id)s, %(name)s, %(brand)s, %(category)s,
                %(price)s, %(discount_price)s, %(discount_amount)s, %(availability)s, %(price_valid_until)s,
                %(url)s, %(description)s, %(short_description)s, %(main_image)s, %(images)s, 
                NOW(), NOW(), NOW(), TRUE,
                3, 'regular',
                0, 0,
                0, 0,
                50, 'stable',
                0, 0,
                0, 0, 0,
                NOW() + INTERVAL '3 hours'
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
                scraped_at = NOW(),
                consecutive_errors = 0,
                check_failures = 0
            RETURNING id, main_product_id, price, discount_price, discount_amount, availability
            """

            processed_count = 0
            failed_products = []
            
            for product in valid_products:
                # SAVEPOINT per product - protects prior successes
                sp_name = f"product_{processed_count}_{int(time.time()*1000)}"
                try:
                    cursor.execute(f"SAVEPOINT {sp_name}")
                    
                    main_product_id = product.get("main_product_id")
                    url = product.get("url")
                    site = product.get('site', '').lower()
                    
                    existing = existing_products.get(url)
                    is_new = existing is None
                    
                    product = self._normalize_product_data(product)
                    
                    cursor.execute(upsert_query, product)
                    result = cursor.fetchone()
                    
                    if not result:
                        cursor.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                        failed_products.append(main_product_id)
                        continue
                    
                    db_id, db_main_id, db_price, db_discount, db_discount_amount, db_availability = result
                    
                    if is_new:
                        cursor.execute("""
                            INSERT INTO price_history (
                                product_id, price, discount_price, discount_amount, 
                                availability, recorded_at, change_reason
                            )
                            VALUES (%s, %s, %s, %s, %s, NOW(), 'baseline')
                        """, (db_id, product.get('price', 0), product.get('discount_price', 0),
                              product.get('discount_amount', 0), product.get('availability', 'OutOfStock')))
                    
                    if not is_new and existing:
                        self._process_product_changes(cursor, db_id, product, existing, site)
                    
                    self._update_monitoring_stats(cursor, site, db_id, is_new)
                    
                    if is_new:
                        existing_products[url] = {
                            "id": db_id, "main_product_id": db_main_id,
                            "price": db_price, "discount_price": db_discount,
                            "availability": db_availability, "url": url
                        }
                    
                    cursor.execute(f"RELEASE SAVEPOINT {sp_name}")
                    processed_count += 1
                    
                except Exception as e:
                    # Rollback ONLY this product, keep prior successes
                    try:
                        cursor.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    except: pass
                    logger.error(f"PRODUCT_ERROR | {product.get('main_product_id')} | {e}")
                    failed_products.append(product.get('main_product_id'))
                    continue

            try:
                conn.commit()
                logger.info(f"UPSERT_COMPLETE | processed={processed_count} | failed={len(failed_products)}")
                return processed_count
            except Exception as e:
                conn.rollback()
                logger.error(f"UPSERT_COMMIT_ERROR | {e}")
                return 0
            finally:
                cursor.close()
    
    def _normalize_product_data(self, product: Dict) -> Dict:
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
        elif isinstance(val, str):
            try:
                normalized["price_valid_until"] = clean_date(val)
            except:
                normalized["price_valid_until"] = None
        
        for field in ["price", "discount_price", "discount_amount"]:
            if normalized.get(field) is not None:
                normalized[field] = float(normalized[field])
            else:
                normalized[field] = 0
        
        if normalized.get("site"):
            normalized["site"] = str(normalized["site"]).strip().lower()
        
        return normalized
    
    def _process_product_changes(self, cursor, product_id: int, product: Dict, 
                                  existing: Dict, site: str):
        """Process and track product changes during scraping."""
        changes = []
        
        field_mapping = {
            'price': ('price', existing.get('price'), product.get('price')),
            'discount_price': ('discount_price', existing.get('discount_price'), product.get('discount_price')),
            'discount_amount': ('discount_amount', existing.get('discount_amount'), product.get('discount_amount')),
            'availability': ('availability', existing.get('availability'), product.get('availability')),
            'special_id': ('special_id', existing.get('special_id'), product.get('special_id')),
            'name': ('name', existing.get('name'), product.get('name')),
            'brand': ('brand', existing.get('brand'), product.get('brand')),
            'category': ('category', existing.get('category'), product.get('category')),
        }
        
        for field, (_, old_val, new_val) in field_mapping.items():
            if old_val != new_val:
                severity = 'minor'
                if field == 'price':
                    old_p = float(old_val) if old_val else 0
                    new_p = float(new_val) if new_val else 0
                    if old_p > 0:
                        pct = abs(new_p - old_p) / old_p * 100
                        if pct > 20: severity = 'critical'
                        elif pct > 10: severity = 'major'
                        elif pct > 5: severity = 'moderate'
                changes.append((field, old_val, new_val, severity))
        
        price_changed = any(c[0] in ['price', 'discount_price', 'discount_amount'] for c in changes)
        availability_changed = any(c[0] == 'availability' for c in changes)
        
        # ─── ALWAYS increment total_checks + reset consecutive_errors ───
        cursor.execute("""
            UPDATE products 
            SET total_checks = COALESCE(total_checks, 0) + 1,
                consecutive_errors = 0,
                check_failures = 0
            WHERE id = %s
        """, (product_id,))
        
        if not changes:
            # No change - just bump days_since_last_change
            cursor.execute("""
                UPDATE products 
                SET days_since_last_change = COALESCE(days_since_last_change, 0) + 1
                WHERE id = %s
            """, (product_id,))
            return
        
        # ─── PRICE CHANGE ───
        if price_changed:
            old_price = float(existing.get('price', 0)) if existing.get('price') else 0
            new_price = float(product.get('price', 0)) if product.get('price') else 0
            
            change_pct = None
            if old_price > 0:
                change_pct = round(((new_price - old_price) / old_price) * 100, 2)
            
            cursor.execute("""
                UPDATE products 
                SET price_change_count = COALESCE(price_change_count, 0) + 1,
                    last_price_change = NOW(),
                    price_change_percent = %s,
                    change_frequency_7d = COALESCE(change_frequency_7d, 0) + 1,
                    change_frequency_30d = COALESCE(change_frequency_30d, 0) + 1,
                    days_since_last_change = 0
                WHERE id = %s
            """, (change_pct, product_id))
            
            cursor.execute("""
                INSERT INTO price_history (
                    product_id, price, discount_price, discount_amount, 
                    availability, recorded_at, change_reason
                )
                VALUES (%s, %s, %s, %s, %s, NOW(), 'scraping')
            """, (product_id, product.get('price', 0), product.get('discount_price', 0),
                  product.get('discount_amount', 0), product.get('availability', 'OutOfStock')))
        else:
            # No price change but something else changed
            cursor.execute("""
                UPDATE products 
                SET days_since_last_change = COALESCE(days_since_last_change, 0) + 1
                WHERE id = %s
            """, (product_id,))
        
        # ─── AVAILABILITY CHANGE ───
        if availability_changed:
            cursor.execute("""
                UPDATE products 
                SET availability_change_count = COALESCE(availability_change_count, 0) + 1,
                    last_availability_change = NOW()
                WHERE id = %s
            """, (product_id,))
        
        # ─── LOG CHANGES ───
        for field, old_val, new_val, severity in changes:
            old_json = json.dumps(str(old_val)) if old_val is not None else None
            new_json = json.dumps(str(new_val)) if new_val is not None else None
            
            cursor.execute("""
                INSERT INTO product_changes (
                    product_id, change_type, old_value, new_value, 
                    source, created_at, monitored_at, change_severity
                )
                VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW(), NOW(), %s)
            """, (product_id, field, old_json, new_json, "scraping", severity))
        
        # ─── ALERTS (deduplicated - only if no unresolved alert of same type) ───
        if price_changed:
            old_price = float(existing.get('price', 0)) if existing.get('price') else 0
            new_price = float(product.get('price', 0)) if product.get('price') else 0
            
            if old_price > 0:
                change_pct = round(((new_price - old_price) / old_price) * 100, 2)
                
                if new_price < old_price * 0.9:
                    self._create_alert_if_new(cursor, product_id, site, 'price_drop',
                                              old_price, new_price, 'high', change_pct)
                elif new_price > old_price * 1.2:
                    self._create_alert_if_new(cursor, product_id, site, 'price_increase',
                                              old_price, new_price, 'medium', change_pct)
    
    def _create_alert_if_new(self, cursor, product_id: int, site: str, alert_type: str,
                              old_value, new_value, severity: str, change_percent: float = None):
        """Create alert only if no active (unresolved) alert of same type exists."""
        cursor.execute("""
            SELECT id FROM monitoring_alerts 
            WHERE product_id = %s AND alert_type = %s AND is_resolved = FALSE
            LIMIT 1
        """, (product_id, alert_type))
        if cursor.fetchone():
            return  # Already have an active alert
        cursor.execute("""
            INSERT INTO monitoring_alerts (
                product_id, site, alert_type, old_value, new_value, 
                severity, change_percent, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """, (product_id, site, alert_type, str(old_value), str(new_value), 
              severity, change_percent))
    
    def _update_monitoring_stats(self, cursor, site: str, product_id: int, is_new: bool):
        """Update monitoring_stats after a scrape check."""
        if is_new:
            cursor.execute("""
                INSERT INTO monitoring_stats (
                    site, product_id, check_count, change_count,
                    price_change_count, availability_change_count,
                    consecutive_errors, total_errors, success_rate,
                    last_success, created_at, updated_at
                )
                VALUES (%s, %s, 1, 0, 0, 0, 0, 0, 100, NOW(), NOW(), NOW())
            """, (site.lower(), product_id))
        else:
            cursor.execute("""
                INSERT INTO monitoring_stats (
                    site, product_id, check_count, change_count,
                    consecutive_errors, last_success, created_at, updated_at
                )
                VALUES (%s, %s, 1, 0, 0, NOW(), NOW(), NOW())
                ON CONFLICT (site, product_id) 
                DO UPDATE SET
                    check_count = monitoring_stats.check_count + 1,
                    consecutive_errors = 0,
                    success_rate = CASE 
                        WHEN monitoring_stats.check_count + 1 > 0 THEN
                            GREATEST(0, ROUND(
                                ((monitoring_stats.check_count + 1 - COALESCE(monitoring_stats.total_errors, 0))::numeric
                                / (monitoring_stats.check_count + 1)) * 100, 2
                            ))
                        ELSE 100
                    END,
                    last_success = NOW(),
                    updated_at = NOW()
            """, (site.lower(), product_id))

    # =====================================================
    # GET PRODUCT
    # =====================================================
    
    def get_product_by_site_and_main_id(self, site: str, main_product_id: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT id, site, main_product_id, special_id, url, price,
                           discount_price, discount_amount, availability, price_valid_until,
                           monitoring_priority
                    FROM products WHERE site = %s AND main_product_id = %s LIMIT 1
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
    # BATCH UPDATE - MONITORING PHASE
    # =====================================================
    
    def batch_update_products_with_monitoring_data(
        self,
        products_data: List[Tuple[str, str, Dict, Dict, str]]
    ) -> Tuple[int, List[Dict]]:
        """
        Batch update products with monitoring data.
        
        Placeholder counts (verified):
        - update_params: 23 placeholders, 23 values
        - price_history_params: 6 placeholders, 6 values
        - product_change_params: 6 placeholders, 6 values
        - alert_params: 7 placeholders, 7 values
        - monitoring_stats_params: 8 placeholders, 8 values
        """
        if not products_data:
            return 0, []
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            start_time = time.time()
            
            update_params = []
            price_history_params = []
            product_change_params = []
            alert_params = []
            monitoring_stats_params = []
            failed_products = []
            
            try:
                for site, main_product_id, old_data, new_data, source in products_data:
                    try:
                        cursor.execute("""
                            SELECT id, price, discount_price, availability, 
                                price_change_count, avg_price, min_price, max_price,
                                monitoring_priority, special_id, price_valid_until,
                                price_volatility_score, price_trend,
                                COALESCE(change_frequency_7d, 0),
                                COALESCE(change_frequency_30d, 0),
                                COALESCE(days_since_last_change, 0)
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
                        db_price = float(result[1]) if result[1] else 0
                        db_discount = float(result[2]) if result[2] else 0
                        db_availability = result[3]
                        db_priority = result[8] or 3
                        db_special_id = result[9]
                        db_price_valid_until = result[10]
                        db_volatility = int(result[11]) if result[11] else 50
                        db_trend = result[12] or "stable"
                        db_change_7d = int(result[13]) if result[13] else 0
                        db_change_30d = int(result[14]) if result[14] else 0
                        db_days_since = int(result[15]) if result[15] else 0
                        
                        price = float(new_data.get("price", 0)) if new_data.get("price") is not None else None
                        discount_price = float(new_data.get("discount_price", 0)) if new_data.get("discount_price") is not None else None
                        discount_amount = float(new_data.get("discount_amount", 0)) if new_data.get("discount_amount") is not None else None
                        availability = new_data.get("availability")
                        special_id = new_data.get("special_id")
                        price_valid_until = new_data.get("price_valid_until")
                        
                        if price_valid_until and isinstance(price_valid_until, str):
                            try:
                                price_valid_until = clean_date(price_valid_until)
                            except:
                                price_valid_until = None
                        
                        price_changed = (db_price != price or db_discount != discount_price)
                        availability_changed = (db_availability != availability)
                        special_id_changed = (db_special_id != special_id)
                        price_valid_until_changed = (db_price_valid_until != price_valid_until)
                        
                        if not any([price_changed, availability_changed, special_id_changed, price_valid_until_changed]):
                            cursor.execute("""
                                UPDATE products 
                                SET last_checked = NOW(), last_updated_at = NOW(),
                                    days_since_last_change = COALESCE(days_since_last_change, 0) + 1,
                                    total_checks = COALESCE(total_checks, 0) + 1,
                                    consecutive_errors = 0
                                WHERE id = %s
                            """, (product_id,))
                            continue
                        
                        change_pct = None
                        if price_changed and db_price > 0 and price:
                            change_pct = round(((price - db_price) / db_price) * 100, 2)
                        
                        new_change_7d = db_change_7d + 1 if price_changed else db_change_7d
                        new_change_30d = db_change_30d + 1 if price_changed else db_change_30d
                        new_days_since = 0 if price_changed else (db_days_since + 1)
                        
                        new_volatility = db_volatility
                        if price_changed and db_price > 0 and change_pct:
                            pct = abs(change_pct)
                            if pct > 20:
                                new_volatility = min(100, db_volatility + 10)
                            elif pct > 10:
                                new_volatility = min(100, db_volatility + 5)
                            elif pct > 5:
                                new_volatility = db_volatility
                            else:
                                new_volatility = max(0, db_volatility - 2)
                        
                        new_priority = db_priority
                        if price_changed:
                            new_priority = max(1, db_priority - 1)
                        elif availability_changed and availability == "OutOfStock":
                            new_priority = min(5, db_priority + 1)
                        
                        new_group = "regular"
                        if price_changed and discount_price and discount_price < price:
                            new_group = "discounted"
                        elif availability == "InStock" and price and price > 2000:
                            new_group = "high_value"
                        elif availability == "OutOfStock":
                            new_group = "out_of_stock"
                        
                        severity = 'minor'
                        if change_pct is not None:
                            if abs(change_pct) > 20: severity = 'critical'
                            elif abs(change_pct) > 10: severity = 'major'
                            elif abs(change_pct) > 5: severity = 'moderate'
                        
                        new_trend = (
                            "upward" if price_changed and price > db_price
                            else "downward" if price_changed and price < db_price
                            else db_trend
                        )
                        
                        update_params.append((
                            price or 0,
                            discount_price or 0,
                            discount_amount or 0,
                            availability,
                            special_id,
                            price_valid_until,
                            new_priority,
                            new_group,
                            new_volatility,
                            new_trend,
                            new_change_7d,
                            new_change_30d,
                            new_days_since,
                            change_pct,
                            1 if price_changed else 0,
                            1 if availability_changed else 0,
                            price_changed,
                            new_priority == 1,
                            new_priority == 2,
                            new_priority == 3,
                            new_priority == 4,
                            new_priority == 5,
                            product_id,
                        ))
                        
                        if price_changed:
                            price_history_params.append((
                                product_id, price, discount_price, discount_amount,
                                availability, 'monitoring',
                            ))
                        
                        change_fields = []
                        if price_changed:
                            change_fields.append((product_id, "price", db_price, price, source, severity))
                        if availability_changed:
                            change_fields.append((product_id, "availability", db_availability, availability, source, severity))
                        if special_id_changed:
                            change_fields.append((product_id, "special_id", db_special_id, special_id, source, 'minor'))
                        if price_valid_until_changed:
                            change_fields.append((product_id, "price_valid_until", db_price_valid_until, price_valid_until, source, 'minor'))
                        
                        for pid, ftype, old_val, new_val, src, sev in change_fields:
                            old_json = json.dumps(str(old_val)) if old_val is not None else None
                            new_json = json.dumps(str(new_val)) if new_val is not None else None
                            product_change_params.append((pid, ftype, old_json, new_json, src, sev))
                        
                        if price_changed and db_price > 0 and price:
                            if price < db_price * 0.9:
                                alert_params.append((product_id, site, "price_drop", str(db_price), str(price), "high", change_pct))
                            elif price > db_price * 1.2:
                                alert_params.append((product_id, site, "price_increase", str(db_price), str(price), "medium", change_pct))
                        
                        if availability_changed and db_availability == "OutOfStock" and availability == "InStock":
                            alert_params.append((product_id, site, "back_in_stock", db_availability, availability, "high", None))
                        
                        monitoring_stats_params.append((
                            site.lower(),
                            product_id,
                            1 if (price_changed or availability_changed) else 0,
                            1 if price_changed else 0,
                            1 if availability_changed else 0,
                            1 if (price_changed or availability_changed) else 0,
                            1 if price_changed else 0,
                            1 if availability_changed else 0,
                        ))
                        
                    except Exception as e:
                        failed_products.append({
                            "site": site,
                            "main_product_id": main_product_id,
                            "reason": str(e)
                        })
                        logger.error(f"❌ Prep failed: {site}/{main_product_id} - {e}")
                
                if not update_params:
                    logger.debug("✅ No changes to apply")
                    return 0, failed_products
                
                # 1. UPDATE PRODUCTS (23 placeholders)
                cursor.executemany("""
                    UPDATE products SET 
                        price = %s,
                        discount_price = %s,
                        discount_amount = %s,
                        availability = %s,
                        special_id = %s,
                        price_valid_until = %s,
                        last_updated_at = NOW(),
                        last_checked = NOW(),
                        monitoring_priority = %s,
                        monitoring_group = %s,
                        price_volatility_score = %s,
                        price_trend = %s,
                        change_frequency_7d = %s,
                        change_frequency_30d = %s,
                        days_since_last_change = %s,
                        price_change_percent = %s,
                        price_change_count = COALESCE(price_change_count, 0) + %s,
                        availability_change_count = COALESCE(availability_change_count, 0) + %s,
                        last_price_change = CASE WHEN %s THEN NOW() ELSE last_price_change END,
                        check_failures = 0,
                        consecutive_errors = 0,
                        total_checks = COALESCE(total_checks, 0) + 1,
                        next_check_at = CASE 
                            WHEN %s THEN NOW() + INTERVAL '30 minutes'
                            WHEN %s THEN NOW() + INTERVAL '1 hour'
                            WHEN %s THEN NOW() + INTERVAL '3 hours'
                            WHEN %s THEN NOW() + INTERVAL '6 hours'
                            WHEN %s THEN NOW() + INTERVAL '12 hours'
                            ELSE next_check_at
                        END
                    WHERE id = %s
                """, update_params)
                
                # 2. PRICE HISTORY (6 placeholders)
                if price_history_params:
                    cursor.executemany("""
                        INSERT INTO price_history (
                            product_id, price, discount_price, discount_amount,
                            availability, recorded_at, change_reason
                        )
                        VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                    """, price_history_params)
                
                # 3. PRODUCT CHANGES (6 placeholders)
                if product_change_params:
                    cursor.executemany("""
                        INSERT INTO product_changes (
                            product_id, change_type, old_value, new_value,
                            source, created_at, monitored_at, change_severity
                        )
                        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW(), NOW(), %s)
                    """, product_change_params)
                
                # 4. ALERTS (7 placeholders)
                if alert_params:
                    cursor.executemany("""
                        INSERT INTO monitoring_alerts (
                            product_id, site, alert_type, old_value, new_value,
                            severity, change_percent, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """, alert_params)
                
                # 5. MONITORING STATS (8 placeholders)
                if monitoring_stats_params:
                    cursor.executemany("""
                        INSERT INTO monitoring_stats (
                            site, product_id, check_count, change_count,
                            price_change_count, availability_change_count,
                            consecutive_errors, last_success, created_at, updated_at
                        )
                        VALUES (%s, %s, 1, %s, %s, %s, 0, NOW(), NOW(), NOW())
                        ON CONFLICT (site, product_id) DO UPDATE SET
                            check_count = monitoring_stats.check_count + 1,
                            change_count = monitoring_stats.change_count + %s,
                            price_change_count = monitoring_stats.price_change_count + %s,
                            availability_change_count = monitoring_stats.availability_change_count + %s,
                            consecutive_errors = 0,
                            success_rate = CASE 
                                WHEN monitoring_stats.check_count + 1 > 0 THEN
                                    GREATEST(0, ROUND(
                                        ((monitoring_stats.check_count + 1 - COALESCE(monitoring_stats.total_errors, 0))::numeric
                                        / (monitoring_stats.check_count + 1)) * 100, 2
                                    ))
                                ELSE 100
                            END,
                            last_success = NOW(),
                            updated_at = NOW()
                    """, monitoring_stats_params)
                
                conn.commit()
                elapsed = round(time.time() - start_time, 2)
                logger.info(
                    f"✅ BATCH_COMPLETE | updated={len(update_params)} | "
                    f"failed={len(failed_products)} | time={elapsed}s"
                )
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
        self, site: str, main_product_id: str, has_change: bool = False,
        is_price_change: bool = False, is_availability_change: bool = False,
        duration: float = 0
    ) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT id FROM products WHERE site = %s AND main_product_id = %s", 
                              (site.lower(), main_product_id))
                result = cursor.fetchone()
                if not result:
                    return False
                
                product_uuid = result[0]
                
                cursor.execute("""
                    INSERT INTO monitoring_stats (
                        site, product_id, check_count, change_count,
                        price_change_count, availability_change_count,
                        last_check_duration, consecutive_errors, 
                        success_rate, last_success, created_at, updated_at
                    )
                    VALUES (%s, %s, 1, %s, %s, %s, %s, 0, 100, NOW(), NOW(), NOW())
                    ON CONFLICT (site, product_id) DO UPDATE SET
                        check_count = monitoring_stats.check_count + 1,
                        change_count = monitoring_stats.change_count + %s,
                        price_change_count = monitoring_stats.price_change_count + %s,
                        availability_change_count = monitoring_stats.availability_change_count + %s,
                        last_check_duration = EXCLUDED.last_check_duration,
                        avg_check_duration = CASE 
                            WHEN monitoring_stats.check_count > 0 THEN
                                ROUND(
                                    ((COALESCE(monitoring_stats.avg_check_duration, 0) * monitoring_stats.check_count) 
                                    + EXCLUDED.last_check_duration) / (monitoring_stats.check_count + 1), 3
                                )
                            ELSE EXCLUDED.last_check_duration
                        END,
                        consecutive_errors = 0,
                        success_rate = CASE 
                            WHEN monitoring_stats.check_count + 1 > 0 THEN
                                GREATEST(0, ROUND(
                                    ((monitoring_stats.check_count + 1 - COALESCE(monitoring_stats.total_errors, 0))::numeric
                                    / (monitoring_stats.check_count + 1)) * 100, 2
                                ))
                            ELSE 100
                        END,
                        last_success = NOW(),
                        updated_at = NOW()
                """, (site.lower(), product_uuid,
                      1 if has_change else 0, 1 if is_price_change else 0, 
                      1 if is_availability_change else 0, duration,
                      1 if has_change else 0, 1 if is_price_change else 0, 
                      1 if is_availability_change else 0))
                
                cursor.execute("""
                    UPDATE products 
                    SET last_checked = NOW(), last_updated_at = NOW() 
                    WHERE id = %s
                """, (product_uuid,))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to increment stats: {e}")
                return False
            finally:
                cursor.close()

    # =====================================================
    # MONITORING BATCHES - LIFECYCLE
    # =====================================================
    
    def start_monitoring_batch(self, site: str, batch_type: str = "scheduled",
                               total_products: int = 0) -> Optional[str]:
        """
        Start a new monitoring batch.
        Before creating, closes any orphaned 'running' batches for the same site
        (from previous crashed runs).
        """
        site_lower = site.lower()
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    # ─── Close orphan batches for this site ───
                    cursor.execute("""
                        UPDATE monitoring_batches SET
                            status = 'interrupted',
                            end_time = NOW(),
                            duration_seconds = EXTRACT(EPOCH FROM (NOW() - start_time))
                        WHERE site = %s
                          AND status = 'running'
                    """, (site_lower,))
                    orphans_closed = cursor.rowcount
                    if orphans_closed > 0:
                        logger.warning(
                            f"BATCH_ORPHANS_CLOSED | site={site} | count={orphans_closed}"
                        )

                    # ─── Insert new batch ───
                    cursor.execute("""
                        INSERT INTO monitoring_batches (
                            site, batch_type, status, total_products,
                            start_time, created_at
                        )
                        VALUES (%s, %s, 'running', %s, NOW(), NOW())
                        RETURNING id
                    """, (site_lower, batch_type, total_products))

                    result = cursor.fetchone()
                    conn.commit()

                    if result:
                        batch_id = str(result[0])
                        logger.info(
                            f"BATCH_START | site={site} | id={batch_id[:8]} | total={total_products}"
                        )
                        return batch_id
                    return None
                except Exception as e:
                    logger.error(f"Failed to start batch: {e}")
                    return None
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"Failed to acquire connection for start batch: {e}")
            return None


    def sweep_orphan_batches(self, older_than_seconds: int = 0) -> int:
        """
        Mark all 'running' batches as 'interrupted'.
        Called at engine startup to clean up batches from crashed processes.
        
        Args:
            older_than_seconds: only close batches running longer than this.
                               0 = close all running batches immediately.
        
        Returns: number of rows updated
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    if older_than_seconds > 0:
                        cursor.execute("""
                            UPDATE monitoring_batches SET
                                status = 'interrupted',
                                end_time = NOW(),
                                duration_seconds = EXTRACT(EPOCH FROM (NOW() - start_time))
                            WHERE status = 'running'
                              AND start_time < NOW() - (%s || ' seconds')::interval
                        """, (older_than_seconds,))
                    else:
                        cursor.execute("""
                            UPDATE monitoring_batches SET
                                status = 'interrupted',
                                end_time = NOW(),
                                duration_seconds = EXTRACT(EPOCH FROM (NOW() - start_time))
                            WHERE status = 'running'
                        """)
                    count = cursor.rowcount
                    conn.commit()
                    if count > 0:
                        logger.warning(f"BATCH_SWEEP | closed {count} orphan batches")
                    else:
                        logger.info("BATCH_SWEEP | no orphan batches found")
                    return count
                except Exception as e:
                    logger.error(f"Failed to sweep orphan batches: {e}")
                    return 0
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"Failed to acquire connection for sweep: {e}")
            return 0


    def update_batch_progress(self, batch_id: str, processed: int, 
                          changed: int, errors: int) -> bool:
        """
        Lightweight progress checkpoint.
        Called periodically from the engine (every ~30s) so that if the process
        dies, we know how far it got.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        UPDATE monitoring_batches SET
                            processed_products = %s,
                            changed_products = %s,
                            error_products = %s
                        WHERE id = %s AND status = 'running'
                    """, (processed, changed, errors, batch_id))
                    conn.commit()
                    return cursor.rowcount > 0
                except Exception as e:
                    logger.error(f"Failed to update batch progress: {e}")
                    return False
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"Failed to acquire connection for progress update: {e}")
            return False

    def update_monitoring_batch_total(self, batch_id: str, total: int) -> bool:
        """Update only the total_products field of a running batch."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        UPDATE monitoring_batches SET
                            total_products = %s
                        WHERE id = %s AND status = 'running'
                    """, (total, batch_id))
                    conn.commit()
                    return cursor.rowcount > 0
                except Exception as e:
                    logger.error(f"Failed to update batch total: {e}")
                    return False
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"Failed to acquire connection for update batch total: {e}")
            return False
    
    def update_monitoring_batch(
        self, batch_id: str, status: str = "completed",
        processed: int = 0, changed: int = 0, errors: int = 0,
        total: int = None
    ) -> bool:
        """
        Update monitoring batch when cycle completes.
        - Always fills end_time and duration_seconds
        - Optionally updates total_products (for early updates)
        - Status can be: completed, failed, partial, interrupted
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                try:
                    if total is not None:
                        cursor.execute("""
                            UPDATE monitoring_batches SET
                                status = %s,
                                total_products = %s,
                                processed_products = %s,
                                changed_products = %s,
                                error_products = %s,
                                end_time = NOW(),
                                duration_seconds = EXTRACT(EPOCH FROM (NOW() - start_time))
                            WHERE id = %s
                        """, (status, total, processed, changed, errors, batch_id))
                    else:
                        cursor.execute("""
                            UPDATE monitoring_batches SET
                                status = %s,
                                processed_products = %s,
                                changed_products = %s,
                                error_products = %s,
                                end_time = NOW(),
                                duration_seconds = EXTRACT(EPOCH FROM (NOW() - start_time))
                            WHERE id = %s
                        """, (status, processed, changed, errors, batch_id))

                    conn.commit()

                    logger.info(
                        f"BATCH_END | id={batch_id[:8]} | status={status} | "
                        f"processed={processed} | changed={changed} | errors={errors}"
                    )
                    return True
                except Exception as e:
                    logger.error(f"Failed to update batch: {e}")
                    return False
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"Failed to acquire connection for update batch: {e}")
            return False

    # =====================================================
    # SITE CONFIG
    # =====================================================
    
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
                    return {"risk_level": "medium", "base_interval": 3600, "min_interval": 1800,
                            "max_interval": 86400, "batch_size": 100, "batch_delay": 1.0,
                            "requests_per_minute": 60, "use_proxies": False, "enabled": True}
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
    # PRODUCT ENABLE/DISABLE
    # =====================================================

    def disable_product(self, product_id: str, reason: str = "consecutive_failures") -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE products SET is_enabled = FALSE, last_updated_at = NOW() 
                    WHERE id = %s
                """, (product_id,))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to disable product: {e}")
                return False
            finally:
                cursor.close()

    def enable_product(self, product_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE products SET is_enabled = TRUE, check_failures = 0, 
                        consecutive_errors = 0, last_updated_at = NOW() 
                    WHERE id = %s
                """, (product_id,))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to enable product: {e}")
                return False
            finally:
                cursor.close()

    def increment_check_failure(self, main_product_id: str, site: str) -> Dict:
        """
        Increment check failure with FIXED success_rate calculation.
        Increments check_count so success_rate never goes negative.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT check_failures, is_enabled, id FROM products 
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), main_product_id))
                
                result = cursor.fetchone()
                if not result:
                    return {"failure_count": 0, "is_disabled": False}
                
                current_failures = result[0] or 0
                is_enabled = result[1]
                product_uuid = result[2]
                
                if not is_enabled or current_failures >= 3:
                    return {"failure_count": current_failures, "is_disabled": True}
                
                new_failure_count = current_failures + 1
                error_msg = f"Fetch failed (attempt {new_failure_count})"
                
                cursor.execute("""
                    UPDATE products SET check_failures = %s, consecutive_errors = %s,
                        last_updated_at = NOW() WHERE id = %s
                """, (new_failure_count, new_failure_count, product_uuid))
                
                # Update stats: increment check_count AND total_errors so success_rate is correct
                cursor.execute("""
                    INSERT INTO monitoring_stats (
                        site, product_id, check_count, consecutive_errors, 
                        total_errors, last_error, success_rate, updated_at
                    )
                    VALUES (%s, %s, 1, 1, 1, %s, 0, NOW())
                    ON CONFLICT (site, product_id) DO UPDATE SET
                        check_count = monitoring_stats.check_count + 1,
                        consecutive_errors = monitoring_stats.consecutive_errors + 1,
                        total_errors = COALESCE(monitoring_stats.total_errors, 0) + 1,
                        last_error = %s,
                        success_rate = CASE 
                            WHEN monitoring_stats.check_count + 1 > 0 THEN
                                GREATEST(0, ROUND(
                                    ((monitoring_stats.check_count + 1 - (COALESCE(monitoring_stats.total_errors, 0) + 1))::numeric
                                    / (monitoring_stats.check_count + 1)) * 100, 2
                                ))
                            ELSE 0
                        END,
                        updated_at = NOW()
                """, (site.lower(), product_uuid, error_msg, error_msg))
                
                is_disabled = False
                if new_failure_count >= 3:
                    cursor.execute("""
                        UPDATE products SET is_enabled = FALSE, last_updated_at = NOW() 
                        WHERE id = %s
                    """, (product_uuid,))
                    is_disabled = True
                    logger.warning(f"Product disabled: {site}/{main_product_id}")
                
                conn.commit()
                return {"failure_count": new_failure_count, "is_disabled": is_disabled}
                
            except Exception as e:
                logger.error(f"Failed to increment check failure: {e}")
                return {"failure_count": 0, "is_disabled": False}
            finally:
                cursor.close()

    def reset_check_failures(self, main_product_id: str, site: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE products SET check_failures = 0, consecutive_errors = 0,
                        last_updated_at = NOW() 
                    WHERE site = %s AND main_product_id = %s
                """, (site.lower(), main_product_id))
                
                cursor.execute("""
                    UPDATE monitoring_stats 
                    SET consecutive_errors = 0, last_error = NULL, updated_at = NOW()
                    WHERE site = %s AND product_id = (
                        SELECT id FROM products WHERE site = %s AND main_product_id = %s
                    )
                """, (site.lower(), site.lower(), main_product_id))
                
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to reset check failures: {e}")
                return False
            finally:
                cursor.close()

    # =====================================================
    # ALERTS
    # =====================================================

    def get_active_alerts(self, product_id: str = None, alert_type: str = None, limit: int = 100) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    SELECT id, product_id, site, alert_type, old_value, new_value, 
                           severity, created_at, change_percent
                    FROM monitoring_alerts WHERE is_resolved = FALSE
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
                    "severity": r[6], "created_at": r[7], "change_percent": r[8]
                } for r in rows]
            except Exception as e:
                logger.error(f"Failed to get active alerts: {e}")
                return []
            finally:
                cursor.close()

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
                return False
            finally:
                cursor.close()
    
    def auto_resolve_alerts_for_product(self, product_id: str, current_price: float = None,
                                         current_availability: str = None) -> int:
        """Auto-resolve alerts when conditions are met."""
        resolved = 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                # Resolve price_drop if price recovered
                if current_price is not None:
                    cursor.execute("""
                        UPDATE monitoring_alerts 
                        SET is_resolved = TRUE, resolved_at = NOW()
                        WHERE product_id = %s 
                          AND alert_type = 'price_drop'
                          AND is_resolved = FALSE
                          AND old_value::numeric <= %s
                    """, (product_id, current_price))
                    resolved += cursor.rowcount
                    
                    cursor.execute("""
                        UPDATE monitoring_alerts 
                        SET is_resolved = TRUE, resolved_at = NOW()
                        WHERE product_id = %s 
                          AND alert_type = 'price_increase'
                          AND is_resolved = FALSE
                          AND old_value::numeric >= %s
                    """, (product_id, current_price))
                    resolved += cursor.rowcount
                
                # Resolve back_in_stock if OOS again
                if current_availability == "OutOfStock":
                    cursor.execute("""
                        UPDATE monitoring_alerts 
                        SET is_resolved = TRUE, resolved_at = NOW()
                        WHERE product_id = %s 
                          AND alert_type = 'back_in_stock'
                          AND is_resolved = FALSE
                    """, (product_id,))
                    resolved += cursor.rowcount
                
                if resolved > 0:
                    conn.commit()
                    logger.info(f"AUTO_RESOLVED_ALERTS | product={product_id} | count={resolved}")
                return resolved
            except Exception as e:
                logger.error(f"Failed to auto-resolve alerts: {e}")
                return 0
            finally:
                cursor.close()


# =====================================================
# SHUTDOWN HOOK
# =====================================================

def shutdown_db_pool():
    try:
        Database.close_all_connections()
        logger.info("DB_POOL | Shutdown complete")
    except Exception as e:
        logger.error(f"DB_POOL | Shutdown error: {e}")


try:
    from core.shutdown_handler import register_shutdown_hook
    register_shutdown_hook(shutdown_db_pool, "db_pool_shutdown")
except Exception as e:
    logger.warning(f"DB_POOL | Shutdown hook registration failed: {e}")