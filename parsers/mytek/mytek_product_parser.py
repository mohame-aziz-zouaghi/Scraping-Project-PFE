from bs4 import BeautifulSoup
from core.clean_text_parser import clean_text
from core.logger import setup_logger
from core.price_parser import parse_price
from core.product_schema import create_product_schema

import re


logger = setup_logger("mytek", "scraper")


def parse_mytek_product_page(html, url, site, source ,product_id=None):

    soup = BeautifulSoup(html, "lxml")

    product = create_product_schema(site, url)

    # ---------------------------------
    # NAME
    # ---------------------------------
    name_tag = soup.find("h1", class_="page-title")
    if name_tag:
        product["name"] = clean_text(name_tag.text)

    # ---------------------------------
    # SKU 
    # ---------------------------------
    sku_block = soup.find("div", class_="product-info-stock-sku")

    if sku_block:
        sku_value = sku_block.find("div", class_="value")
        product["special_id"] = clean_text(sku_value.text)

    # ---------------------------------
    # AVAILABILITY (CORRECT)
    # ---------------------------------
    availability_link = soup.find("link", {"itemprop": "availability"})

    if availability_link:
        href = availability_link.get("href", "")

        if "InStock" in href:
            product["availability"] = "in_stock"
        elif "OutOfStock" in href:
            product["availability"] = "out_of_stock"

    # ---------------------------------
    # PRICE BLOCK
    # ---------------------------------
    price_box = soup.select_one(f'div.price-box.price-final_price[data-product-id="{product_id}"]'
)
    if price_box:

        final_price = None
        old_price = None

        # ---------------------------------
        # FINAL PRICE
        # ---------------------------------
        final_span = price_box.find("span", {"data-price-type": "finalPrice"})
        if final_span:
            try:
                final_price = float(final_span.get("data-price-amount", 0))
            except:
                final_price = None

        # ---------------------------------
        # OLD PRICE
        # ---------------------------------
        old_span = price_box.find("span", {"data-price-type": "oldPrice"})
        if old_span:
            try:
                old_price = float(old_span.get("data-price-amount", 0))
            except:
                old_price = None

        # ---------------------------------
        # APPLY RULES
        # ---------------------------------
        if old_price and final_price and old_price > final_price:
            # DISCOUNT CASE
            product["price"] = old_price
            product["discount_price"] = final_price
            product["discount_amount"] = round(old_price - final_price, 2)

        else:
            # NO DISCOUNT CASE
            product["price"] = final_price or 0
            product["discount_price"] = 0
            product["discount_amount"] = 0

    # ---------------------------------
    # DESCRIPTION
    # ---------------------------------
    meta_desc = soup.find("meta", {"property": "og:description"})

    if meta_desc:
        desc = meta_desc.get("content", "")

        # remove "Retrait..."
        desc = desc.split("Retrait")[0]

        product["description"] = clean_text(desc)

    # ---------------------------------
    # ATTRIBUTES TABLE PARSING
    # ---------------------------------
    product["short_description"] = {}
    product["brand"] = None
    product["category"] = None

    table = soup.find("table", class_="data table additional-attributes")

    if table:

        rows = table.find_all("tr")

        category_found = False

        for row in rows:

            th = row.find("th")
            td = row.find("td")

            if not th or not td:
                continue

            key = clean_text(th.text)
            value = clean_text(td.text)

            if not value:
                continue

            key_lower = key.lower()

            # ---------------------------------
            # STORE RAW STRUCTURE (JSON)
            # ---------------------------------
            product["short_description"][key] = value

            # ---------------------------------
            # BRAND
            # ---------------------------------
            if "marque" in key_lower and not product["brand"]:
                product["brand"] = value

            # ---------------------------------
            # CATEGORY (FIRST TYPE ONLY)
            # ---------------------------------
            if key_lower == "type" and not category_found:
                product["category"] = value
                category_found = True

        if not category_found  :
            product["category"] = source 

    # ---------------------------------
    # IMAGES
    # ---------------------------------

    meta_img = soup.find("meta", property="og:image")

    if meta_img and meta_img.get("content"):
        image_url = meta_img["content"].strip().split("?")[0]
        product["main_image"] = image_url

    # ---------------------------------
    # URL + ID
    # ---------------------------------
    product["url"] = url
    product["main_product_id"] = str(product_id) if product_id else None

    logger.info(f"{site} | PRODUCT_PARSED | {product['main_product_id']}")

    return product