# sites/megapc.py

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import threading
from bs4 import BeautifulSoup
import requests
from tqdm import tqdm

from core.utils.Dclean_html import clean_html
from core.utils.clean_parse_specs import parse_specs
from core.logger import setup_logger, write_product_result, log_global
from core.Web.product_schema import create_product_schema
from core.performance import PerformanceTracker

from parsers.megapc.megapc_products_parser import parse_megapc_products
from parsers.megapc.megapc_product_details_parser import parse_megapc_product_details

from config.categoriesMegaPC import CATEGORIES
from sites.base_site import BaseSite


logger = setup_logger("megapc", "scraper")
dup_logger = setup_logger("megapc", "duplication")

class MegaPCSite(BaseSite):

    def __init__(self):
        self.name = "MEGAPC"
        self.performance = PerformanceTracker()

        self.api_products = "https://apiclt.gi-ga.tech/produit/byPaginationNew"
        self.base_next = "https://megapc.tn/_next/data/build-1757602359699"

    # ---------------------------------
    # API PRODUCTS
    # ---------------------------------
    def get_products(self, category, subcategory):

        payload = {
            "recordByPage": 1000,
            "pageNumber": "1",
            "categorie": {"titre": category},
            "filscateg": {"titre": subcategory},
            "page": 1,
            "price": {"$gte": 0, "$lte": 999999},
            "query": None,
            "brand": [],
            "valeurAttribute1": []
        }

        try:
            res = requests.post(self.api_products, json=payload, timeout=15)

            if res.status_code != 200:
                logger.warning(f"{self.name} | API_FAIL | {category} > {subcategory} | status={res.status_code}")
                return []

            # 🔍 DEBUG RAW RESPONSE
            try:
                data = res.json()
            except Exception:
                logger.error(f"{self.name} | INVALID_JSON | {category} > {subcategory}")
                print("RAW RESPONSE:", res.text[:500])  # show first 500 chars
                return []

            # 🛑 SAFETY CHECK
            if not isinstance(data, dict):
                logger.error(f"{self.name} | UNEXPECTED_RESPONSE_TYPE | {type(data)}")
                return []

            raw_products = data.get("products", [])

            if not isinstance(raw_products, list):
                logger.error(f"{self.name} | PRODUCTS_NOT_LIST | type={type(raw_products)}")
                return []

            logger.info(
                f"{self.name} | RAW_PRODUCTS | {category} > {subcategory} | count={len(raw_products)}"
            )

            products = parse_megapc_products(raw_products)

            logger.info(
                f"{self.name} | PARSED_PRODUCTS | {category} > {subcategory} | count={len(products)}"
            )

            return products

        except Exception as e:
            logger.error(f"{self.name} | API_ERROR | {category} > {subcategory} | {e}")
            return []

    # ---------------------------------
    # PRODUCT DETAILS
    # ---------------------------------
    def get_product_details(self, category, subcategory, slug):

        if not slug:
            return {}

        cat_param = category.replace(" ", "+")
        sub_param = subcategory.replace(" ", "+")

        cat_path = category.replace(" ", "%20")
        sub_path = subcategory.replace(" ", "%20")

        url = (
            f"{self.base_next}/shop/product/"
            f"{cat_path}/{sub_path}/{slug}.json"
            f"?lien={cat_param}&lien={sub_param}&lien={slug}"
        )

        try:
            res = requests.get(url, timeout=10)

            if res.status_code != 200:
                logger.warning(f"{self.name} | DETAILS_FAIL | {slug}")
                return {}

            return parse_megapc_product_details(res.json())

        except Exception as e:
            logger.error(f"{self.name} | DETAILS_ERROR | {slug} | {e}")
            return {}

    # ---------------------------------
    # BUILD PRODUCT
    # ---------------------------------
    def build_product(self, base, details, url):

        product = create_product_schema(self.name, url, base.get("id"))

        product["name"] = base.get("name")
        product["brand"] = base.get("brand")
        product["category"] = base.get("category")

        price = base.get("price") or 0
        promo = base.get("prixEnPromo") or 0

        product["price"] = price
        product["discount_price"] = promo if promo < price else 0
        product["discount_amount"] = (price - promo) if promo and promo < price else 0

        product["availability"] = "InStock" if base.get("stock", 0) > 0 else "OutOfStock"
        product["special_id"] = details.get("sku")

        if details:

            # -------------------------
            # DESCRIPTION → CLEAN TEXT ONLY
            # -------------------------
            product["description"] = clean_html(
                details.get("description")
            )

            # -------------------------
            # SHORT DESCRIPTION → KEY/VALUE STRUCTURE
            # -------------------------
            product["short_description"] = parse_specs(
                details.get("short_description", [])
            )

            # -------------------------
            # IMAGES
            # -------------------------
            images = details.get("images", [])
            product["images"] = [
                img for img in images if isinstance(img, str)
            ]

            if product["images"]:
                product["main_image"] = product["images"][0]

        product["price_valid_until"] = details.get("priceValidUntil")

        return product

    # ---------------------------------
    # MAIN SCRAPER
    # ---------------------------------



    def scrape_site(self):

        logger.info(f"{self.name} | SCRAPE_START")
        log_global(f"{self.name} | SCRAPE_START")

        results = []
        count = 0

        seen_products = {}
        lock = threading.Lock()

        total_categories = len(CATEGORIES)

        # =========================
        # CATEGORY LOOP PROGRESS
        # =========================
        for i, (cat, subs) in enumerate(CATEGORIES.items(), start=1):

            print(f"\n📦 CATEGORY {i}/{total_categories} -> {cat}\n")

            for sub in subs:

                logger.info(f"{self.name} | CATEGORY | {cat} > {sub}")

                products = self.get_products(cat, sub)

                if not products:
                    logger.warning(f"{self.name} | NO_PRODUCTS | {cat} > {sub}")
                    continue

                # =========================
                # WORKER FUNCTION
                # =========================
                def process_product(p):

                    nonlocal seen_products, lock

                    try:
                        slug = p.get("slug")

                        if not slug:
                            logger.warning(f"{self.name} | SKIP_NO_SLUG")
                            return None

                        # -------------------------
                        # DUPLICATION CHECK (THREAD SAFE)
                        # -------------------------
                        with lock:
                            if slug in seen_products:
                                dup_logger.warning(
                                    f"{self.name} | DUPLICATE | {slug}"
                                )
                                return None

                            seen_products[slug] = True

                        url = f"https://megapc.tn/shop/product/{cat}/{sub}/{slug}"

                        details = self.get_product_details(cat, sub, slug)

                        final = self.build_product(p, details, url)

                        if not final.get("name"):
                            logger.warning(f"{self.name} | INVALID_PRODUCT | {slug}")
                            return None

                        return final

                    except Exception as e:
                        logger.error(f"{self.name} | PRODUCT_ERROR | {e}")
                        return None

                # =========================
                # THREADPOOL EXECUTION
                # =========================
                with ThreadPoolExecutor(max_workers=5) as executor:

                    futures = [
                        executor.submit(process_product, p)
                        for p in products
                    ]

                    for future in tqdm(
                        as_completed(futures),
                        total=len(futures),
                        desc=f"{cat} | {sub}"
                    ):

                        final = future.result()

                        if not final:
                            continue

                        write_product_result(final, self.name)
                        results.append(final)

                        count += 1
                        self.performance.add_product()

        logger.info(f"{self.name} | SCRAPE_DONE | total={len(results)}")
        log_global(f"{self.name} | SCRAPE_DONE | total={len(results)}")

        return results