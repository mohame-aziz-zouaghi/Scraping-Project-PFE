import json
from tqdm import tqdm
from core.load_ids import load_existing_product_ids
from core.logger import setup_logger, log_global, write_product_result
from core.performance import PerformanceTracker
from core.Web.product_schema import create_product_schema
from sites.base_site import BaseSite

logger = setup_logger("skymil", "scraper")


class SkymilSite(BaseSite):

    def __init__(self):
        self.name = "SKYMIL"
        self.performance = PerformanceTracker()

        # FILE PATHS
        self.products_file = "config/SkymilProducts.json"
        self.brands_file = "config/SkymilBrands.json"
        self.categories_file = "config/SkymilCategories.json"

    # ---------------------------------
    # LOAD DATA
    # ---------------------------------
    def load_data(self):

        logger.info(f"{self.name} | LOAD_DATA")

        with open(self.products_file, "r", encoding="utf-8") as f:
            products = json.load(f)

        with open(self.brands_file, "r", encoding="utf-8") as f:
            brands = json.load(f)

        with open(self.categories_file, "r", encoding="utf-8") as f:
            categories = json.load(f)

        # MAPS
        brand_map = {b["id"]: b["name"] for b in brands}
        category_map = {c["id"]: c["name"] for c in categories}

        logger.info(
            f"{self.name} | DATA_LOADED | products={len(products)} | brands={len(brand_map)} | categories={len(category_map)}"
        )

        return products, brand_map, category_map

    # ---------------------------------
    # TRANSFORM PRODUCT
    # ---------------------------------
    def transform_product(self, p, brand_map, category_map):

        try:
            product_id = p.get("id")
            name = p.get("name")

            if not product_id or not name:
                return None

            # URL
            slug = p.get("slug")
            url = f"https://skymil-shop.com/produit/{slug}"

            # ---------------------------------
            # INIT PRODUCT SCHEMA (CORRECT WAY)
            # ---------------------------------
            product = create_product_schema(
                self.name,
                url,
                product_id
            )

            # ---------------------------------
            # BASIC FIELDS
            # ---------------------------------
            product["name"] = name

            # ---------------------------------
            # PRICE LOGIC
            # ---------------------------------
            price = p.get("price", 0)
            old_price = p.get("old_price")

            if old_price and old_price > price:
                product["price"] = old_price
                product["discount_price"] = price
                product["discount_amount"] = round(old_price - price, 2)
            else:
                product["price"] = price
                product["discount_price"] = 0
                product["discount_amount"] = 0

            # ---------------------------------
            # BRAND / CATEGORY
            # ---------------------------------
            product["brand"] = brand_map.get(p.get("brand_id"), "Unknown")
            product["category"] = category_map.get(p.get("category_id"), "Unknown")

            # ---------------------------------
            # IMAGES
            # ---------------------------------
            main_image = p.get("image_url")
            images = p.get("images", [])

            all_images = []

            if main_image:
                all_images.append(main_image)

            if images:
                all_images.extend(images)

            product["images"] = all_images
            product["main_image"] = main_image

            # ---------------------------------
            # DESCRIPTION
            # ---------------------------------
            product["description"] = p.get("description", "")

            # ---------------------------------
            # SHORT DESCRIPTION (DICT FORMAT)
            # ---------------------------------
            specs = p.get("specs", {})

            clean_specs = {}
            seen_keys = set()

            for k, v in specs.items():
                if not k or not v:
                    continue

                key = str(k).strip().lower()   # normalize keys
                value = str(v).strip()

                if key in seen_keys:
                    continue

                seen_keys.add(key)

                clean_specs[key] = value

            product["short_description"] = clean_specs

            # ---------------------------------
            # AVAILABILITY
            # ---------------------------------
            product["availability"] = (
                "InStock" if p.get("in_stock") else "OutOfStock"
            )
            # ---------------------------------
            # SKU | Special ID
            # ---------------------------------
            product["special_id"] = p.get("reference")

            return product

        except Exception as e:
            logger.error(f"{self.name} | TRANSFORM_ERROR | {e}")
            return None
        

    # ---------------------------------
    # MAIN SCRAPER (TRANSFORMER)
    # ---------------------------------
    def scrape_site(self):

        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        products, brand_map, category_map = self.load_data()

        # 🔥 RESUME MODE
        existing_ids = load_existing_product_ids(self.name)

        logger.info(
            f"{self.name} | RESUME_MODE | existing={len(existing_ids)}"
        )

        results = []
        total = len(products)

        for p in tqdm(products, desc=f"{self.name} | Processing", unit="product"):

            product_id = str(p.get("id"))

            # ---------------------------------
            # SKIP EXISTING
            # ---------------------------------
            if product_id in existing_ids:
                continue

            # ---------------------------------
            # FILTERS (OPTIONAL BUT SMART)
            # ---------------------------------
            if not p.get("name"):
                continue

            if p.get("price", 0) <= 0:
                continue

            # ---------------------------------
            # TRANSFORM
            # ---------------------------------
            product = self.transform_product(p, brand_map, category_map)

            if not product:
                continue

            results.append(product)

            # SAVE IMMEDIATELY (SAFE MODE)
            write_product_result(product, self.name)

            # PERFORMANCE TRACKING
            self.performance.add_product()

            # LOGGING EVERY X PRODUCTS
            if self.performance.total_products % 50 == 0:
                speed = self.performance.get_speed()

                logger.info(
                    f"{self.name} | PROGRESS | "
                    f"{self.performance.total_products}/{total} | "
                    f"speed={speed:.2f} prod/s"
                )

        logger.info(f"{self.name} | SCRAPE_DONE | total={len(results)}")
        log_global(f"{self.name} | SCRAPE_DONE | total={len(results)}")

        return {
            "site": self.name,
            "total_products": len(results),
            "products": results
        }
    
    def search(self, query):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return
    
    def parse_product(self, url, product_id):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return
    def extract_categories(self):
        log_global(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        logger.warning(f"{self.name} | FEATURE_NOT_IMPLEMENTED | Yet !")
        print(f"\n {self.name} | FEATURE_NOT_IMPLEMENTED | Yet ! \n")
        return