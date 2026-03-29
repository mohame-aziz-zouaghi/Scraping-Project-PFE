import json
from bs4 import BeautifulSoup
from datetime import datetime
from core.logger import setup_logger
from core.product_schema import create_product_schema

logger = setup_logger(site_name="sbs", log_type="scraper")

def parse_sbs_product_page(html, url, site, main_product_id=None):

    soup = BeautifulSoup(html, "lxml")

    # ---------------------------------
    # Extract product id if not provided
    # ---------------------------------

    if not main_product_id:

        body = soup.find("body")

        if body:

            classes = body.get("class", [])

            for c in classes:

                if "product-id-" in c:

                    main_product_id = c.split("product-id-")[-1]

                    break

    product = create_product_schema(site, url, main_product_id)

    scripts = soup.find_all("script", type="application/ld+json")

    for script in scripts:

        try:

            data = json.loads(script.string)

            if data.get("@type") == "Product":

                product["name"] = data.get("name")
                product["description"] = data.get("description")
                product["category"] = data.get("category")

                product["brand"] = data.get("brand", {}).get("name")

                product["special_id"] = data.get("mpn") or data.get("sku")

                offers = data.get("offers", {})

                product["price"] = offers.get("price")
                product["price_valid_until"] = offers.get("priceValidUntil")

                availability = offers.get("availability")

                if availability:
                    product["availability"] = availability.split("/")[-1]

                # -----------------------
                # FIX IMAGE FORMAT
                # -----------------------

                images = data.get("image", [])

                if isinstance(images, str):
                    images = [images]

                product["images"] = images

                if images:
                    product["main_image"] = images[0]

        except Exception as e:

            logger.warning(f"{site} | json parse error | {e}")

    # ---------------------------------
    # SHORT DESCRIPTION
    # ---------------------------------

    category = product.get("category")

    if category and "pc" in category.lower() and main_product_id:

        rows = soup.select(f"#product-description-short-{main_product_id} tr")

        for row in rows:

            cols = row.find_all("td")

            if len(cols) >= 2:

                value = cols[1].text.strip()

                if value:
                    product["short_description"].append(value)

    else:

        desc = soup.select("#product-description-short li")

        for li in desc:

            product["short_description"].append(li.text.strip())

    # ---------------------------------
    # DISCOUNT
    # ---------------------------------

    discount_block = soup.select_one(".product-price.has-discount")

    if discount_block:

        discount_price = soup.select_one(".product-discount .regular-price")

        discount_amount = soup.select_one(".discount-amount")

        if discount_price:
            product["discount_price"] = discount_price.text.strip()

        if discount_amount:
            product["discount_amount"] = discount_amount.text.strip()

    # ---------------------------------
    # LOG SUCCESS
    # ---------------------------------
    if product["name"]:
        logger.info(f"{site} | PRODUCT_PARSED_SUCCESSFULLY | {product['name']}")

    return product