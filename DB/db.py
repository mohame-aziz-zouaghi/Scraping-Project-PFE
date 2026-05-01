import psycopg2
from psycopg2.extras import Json, execute_batch
from datetime import datetime

from DB.config import DBConfig



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