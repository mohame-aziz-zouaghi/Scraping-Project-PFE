import json
import re
from bs4 import BeautifulSoup
from core.logger import setup_logger
from core.Web.product_schema import create_product_schema

logger = setup_logger(site_name="tunisianet", log_type="scraper")


def parse_tunisianet_product_page(html, url, site, main_product_id=None):

    soup = BeautifulSoup(html, "lxml")

    # ---------------------------------
    # EXTRACT PRODUCT JSON
    # ---------------------------------
    product_div = soup.select_one("#product-details")
    data = {}

    if product_div and product_div.has_attr("data-product"):

        try:
            data = json.loads(product_div["data-product"])
        except Exception as e:
            logger.warning(f"{site} | JSON_PARSE_ERROR | {e}")

    # ---------------------------------
    # EXTRACT PRODUCT ID
    # ---------------------------------
    if not main_product_id:
        main_product_id = data.get("id") or data.get("id_product")

    # ---------------------------------
    # INIT PRODUCT
    # ---------------------------------
    product = create_product_schema(site, url, main_product_id)

    # ---------------------------------
    # BASIC DATA
    # ---------------------------------
    product["name"] = data.get("name")
    product["price"] = data.get("price_without_reduction")
    product["discount_price"] = clean_price(data.get("price"))
    product["discount_amount"] = data.get("reduction")
    product["category"] = data.get("category_name")
    product["special_id"] = data.get("reference")

    # ---------------------------------
    # 🔥 DESCRIPTION SWITCH (IMPORTANT)
    # ---------------------------------
    short_desc_html = data.get("description_short")

    if short_desc_html:
        clean_text = BeautifulSoup(short_desc_html, "lxml").text.strip()
        product["description"] = clean_text

    # ---------------------------------
    # FEATURES → SHORT DESCRIPTION LIST
    # ---------------------------------
    features = data.get("features", [])

    for f in features:

        name = f.get("name")
        value = f.get("value")

        if name and value:
            product["short_description"].append(f"{name}: {value}")

    # ---------------------------------
    # IMAGES
    # ---------------------------------
    images = data.get("images", [])

    extracted_images = []

    for img in images:
        try:
            extracted_images.append(img["bySize"]["large"]["url"])
        except:
            continue

    product["images"] = extracted_images

    # MAIN IMAGE
    cover = data.get("cover")

    if cover:
        try:
            product["main_image"] = cover["bySize"]["large"]["url"]
        except:
            pass

    # ---------------------------------
    # BRAND (HTML)
    # ---------------------------------
    brand_img = soup.select_one(".manufacturer-logo")

    if brand_img:
        product["brand"] = brand_img.get("alt")

    # ---------------------------------
    # AVAILABILITY
    # ---------------------------------
    stock = soup.select_one("#stock_availability span")

    if stock:
        product["availability"] = stock.text.strip()

    # ---------------------------------
    # LOG SUCCESS
    # ---------------------------------
    #if product["name"]:
        #logger.info(f"{site} | PRODUCT_PARSED_SUCCESSFULLY | {product['name']}")

    return product



def clean_price(price_str):
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d,\.]", "", price_str)
    # Remove thousands separator
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned) / 1000  # convert 1069000 -> 1069
    except ValueError:
        return None