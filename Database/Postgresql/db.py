import json

import psycopg2
from psycopg2.extras import Json, execute_batch
from datetime import datetime

from Database.Postgresql.config import DBConfig



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
            print("DB CONNECTED")

        except Exception as e:
            print("\n[DB CONNECTION ERROR]")
            print("TYPE:", type(e))
            print("ERROR:", repr(e))
            raise
    def close(self):
        if self.conn:
            self.conn.close()
            print("DB CLOSED")



    def upsert_products(self, products):

        if not products:
            print("No products to insert")
            return

        cursor = self.conn.cursor()

        upsert_query = """
        INSERT INTO products (
            site, main_product_id, special_id,
            name, brand, category,
            price, discount_price, discount_amount,
            availability, price_valid_until,
            url, description, short_description,
            main_image, images, scraped_at
        )
        VALUES (
            %(site)s, %(main_product_id)s, %(special_id)s,
            %(name)s, %(brand)s, %(category)s,
            %(price)s, %(discount_price)s, %(discount_amount)s,
            %(availability)s, %(price_valid_until)s,
            %(url)s, %(description)s, %(short_description)s,
            %(main_image)s, %(images)s, %(scraped_at)s
        )
        ON CONFLICT (url)
        DO UPDATE SET
            name = EXCLUDED.name,
            brand = EXCLUDED.brand,
            category = EXCLUDED.category,
            price = EXCLUDED.price,
            discount_price = EXCLUDED.discount_price,
            discount_amount = EXCLUDED.discount_amount,
            availability = EXCLUDED.availability,
            description = EXCLUDED.description,
            short_description = EXCLUDED.short_description,
            main_image = EXCLUDED.main_image,
            images = EXCLUDED.images
        RETURNING id, price, discount_price, availability;
        """

        price_history_rows = []

        try:

            for product in products:

                # ---------------------------------
                # 🔧 NORMALIZATION LAYER
                # ---------------------------------

                # short_description → JSONB
                if isinstance(product.get("short_description"), (dict, list)):
                    product["short_description"] = Json(product["short_description"])

                # images → ensure list → JSONB
                images = product.get("images")

                if isinstance(images, set):
                    images = list(images)

                if isinstance(images, (list, dict)):
                    product["images"] = Json(images)

                val = product.get("price_valid_until")

                if val in ("", " ", None):
                    product["price_valid_until"] = None

                # ---------------------------------

                cursor.execute(upsert_query, product)
                result = cursor.fetchone()

                if not result:
                    continue

                product_id, db_price, db_discount, db_availability = result

                # ---------------------------------
                # CHANGE DETECTION
                # ---------------------------------
                if (
                    db_price != product.get("price") or
                    db_discount != product.get("discount_price") or
                    db_availability != product.get("availability")
                ):
                    price_history_rows.append({
                        "product_id": product_id,
                        "price": product.get("price"),
                        "discount_price": product.get("discount_price"),
                        "discount_amount": product.get("discount_amount"),
                        "availability": product.get("availability"),
                        "recorded_at": datetime.utcnow()
                    })

            # ---------------------------------
            # INSERT PRICE HISTORY
            # ---------------------------------
            self._insert_price_history(price_history_rows, cursor)

            self.conn.commit()
            print(f"UPSERT DONE | {len(products)} products")

        except Exception as e:
            self.conn.rollback()

            print("\n[UPSERT ERROR DEBUG]")
            print("TYPE:", type(e))
            print("ERROR:", repr(e))

            raise  # <-- keep this for real debugging

        finally:
            cursor.close()

    # ---------------------------------
    # PRICE HISTORY
    # ---------------------------------
    def _insert_price_history(self, rows, cursor):

        if not rows:
            return

        query = """
        INSERT INTO price_history (
            product_id, price, discount_price,
            discount_amount, availability, recorded_at
        )
        VALUES (
            %(product_id)s, %(price)s, %(discount_price)s,
            %(discount_amount)s, %(availability)s, %(recorded_at)s
        )
        """

        execute_batch(cursor, query, rows, page_size=100)


    # ---------------------------------
    # LOAD PRODUCTS FOR MONITORING
    # ---------------------------------
    def load_products_for_monitoring(self, limit=None):

        cursor = self.conn.cursor()

        query = """
        SELECT
            id,
            site,
            url,
            special_id,
            price_valid_until,
            availability,
            price,
            discount_price,
            discount_amount
        FROM products
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)

        rows = cursor.fetchall()

        cursor.close()

        products = []

        for row in rows:

            products.append({
                "id": row[0],
                "site": row[1],
                "url": row[2],
                "special_id": row[3],
                "price_valid_until": row[4],
                "availability": row[5],
                "price": row[6],
                "discount_price": row[7],
                "discount_amount": row[8]
            })

        return products
    

    def update_monitoring_fields(
        self,
        product_id,
        old_data,
        new_data,
        source=None
    ):
        cursor = self.conn.cursor()
        
        try:
            # Tracked fields
            tracked_fields = [
                "price",
                "discount_price",
                "discount_amount",
                "availability",
                "special_id",
                "price_valid_until"
            ]
            
            changed_fields = []
            
            # Detect changes
            for field in tracked_fields:
                old_value = old_data.get(field) if old_data else None
                new_value = new_data.get(field) if new_data else None
                
                # Normalize numeric values for comparison
                if field in ["price", "discount_price", "discount_amount"]:
                    if old_value is not None:
                        old_value = float(old_value)
                    if new_value is not None:
                        new_value = float(new_value)
                
                # Normalize dates
                if field == "price_valid_until":
                    if old_value:
                        old_value = str(old_value).split("T")[0].strip()
                    if new_value:
                        new_value = str(new_value).split("T")[0].strip()
                
                print(f"[DEBUG] Field: {field} - Old: '{old_value}' - New: '{new_value}'")
                
                if old_value != new_value:
                    changed_fields.append({
                        "field": field,
                        "old": old_value,
                        "new": new_value
                    })
                    print(f"[DEBUG] CHANGE DETECTED: {field}")
            
            # No changes
            if not changed_fields:
                print(f"[DEBUG] No changes detected for product {product_id}")
                return False
            
            print(f"[DEBUG] Total changes: {len(changed_fields)}")
            
            # Update products table
            cursor.execute(
                """
                UPDATE products
                SET
                    price = %s,
                    discount_price = %s,
                    discount_amount = %s,
                    availability = %s,
                    special_id = %s,
                    price_valid_until = %s
                WHERE id = %s
                """,
                (
                    new_data.get("price"),
                    new_data.get("discount_price"),
                    new_data.get("discount_amount"),
                    new_data.get("availability"),
                    new_data.get("special_id"),
                    new_data.get("price_valid_until"),
                    product_id
                )
            )
            
            # Check if price-related fields changed
            price_related_fields = ["price", "discount_price", "discount_amount", "availability"]
            has_price_change = any(change["field"] in price_related_fields for change in changed_fields)
            
            # Save to price_history ONLY if price-related fields changed
            if has_price_change:
                cursor.execute(
                    """
                    INSERT INTO price_history (
                        product_id,
                        price,
                        discount_price,
                        discount_amount,
                        availability,
                        recorded_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        product_id,
                        new_data.get("price"),
                        new_data.get("discount_price"),
                        new_data.get("discount_amount"),
                        new_data.get("availability")
                    )
                )
                print(f"[DEBUG] Saved to price_history for product {product_id}")
            
            # ---------------------------------
            # FIXED: Save ALL changed fields to product_changes
            # ---------------------------------
            import json
            
            for change in changed_fields:
                field = change["field"]
                old_value = change["old"]
                new_value = change["new"]
                
                # Convert values to JSON strings
                old_value_json = json.dumps(str(old_value)) if old_value is not None else None
                new_value_json = json.dumps(str(new_value)) if new_value is not None else None
                
                print(f"[DEBUG] Saving to product_changes: {field} - Old: {old_value_json} - New: {new_value_json}")
                
                # FIXED: Correct number of parameters (5 values for 5 placeholders)
                cursor.execute(
                    """
                    INSERT INTO product_changes (
                        product_id,
                        change_type,
                        old_value,
                        new_value,
                        source,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        product_id,
                        field,
                        old_value_json,
                        new_value_json,
                        source
                    )
                )
                print(f"[DEBUG] Successfully saved {field} change to product_changes")
            
            self.conn.commit()
            print(f"[DEBUG] COMMITTED changes for product {product_id}")
            return True
            
        except Exception as e:
            self.conn.rollback()
            print(f"[DB UPDATE ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            cursor.close()