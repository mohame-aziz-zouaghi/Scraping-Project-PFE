# parsers/mytek/mytek_product_parser.py

from bs4 import BeautifulSoup
from core.logger import setup_logger
from core.utils.clean_text_parser import clean_and_decode_text, normalize_availability
from core.utils.price_cleaner import clean_price
from core.Web.product_schema import create_product_schema
from core.utils.Dclean_html import clean_html
from core.utils.cleaning_url_logic import normalize_url

import re
import json

logger = setup_logger("mytek", "scraper")


def parse_mytek_product_page(html, url, site, source, product_id=None):
    """
    Parse Mytek product page with proper cleaning using utility functions.
    
    Args:
        html: HTML content of the product page
        url: Product URL
        site: Site name (MYTEK)
        source: Category source
        product_id: Product ID from listing
    
    Returns:
        Dict: Parsed product data in canonical schema
    """
    soup = BeautifulSoup(html, "lxml")
    
    # Create base product schema
    product = create_product_schema(site, url, product_id)
    
    # =====================================================
    # NAME
    # =====================================================
    name_tag = soup.find("h1", class_="page-title")
    if name_tag:
        product["name"] = clean_and_decode_text(name_tag.text)
    
    # =====================================================
    # PRICE BLOCK - Using price_cleaner utility
    # =====================================================
    price_box = None
    
    # Try to find by data-product-id first
    if product_id:
        price_box = soup.select_one(f'div.price-box.price-final_price[data-product-id="{product_id}"]')
    
    # Fallback: find any price box
    if not price_box:
        price_box = soup.find("div", class_="price-box", attrs={"data-role": "priceBox"})
    
    if price_box:
        final_price = None
        old_price = None
        
        # Final price
        final_span = price_box.find("span", {"data-price-type": "finalPrice"})
        if final_span:
            final_price = clean_price(final_span.get("data-price-amount"))
        
        # Old price (for discounts)
        old_span = price_box.find("span", {"data-price-type": "oldPrice"})
        if old_span:
            old_price = clean_price(old_span.get("data-price-amount"))
        
        # Special Mytek price format: handle price text like "169,000 DT"
        if final_price is None:
            price_span = price_box.find("span", class_="price")
            if price_span:
                price_text = price_span.text.strip()
                # Clean the price text
                final_price = clean_price(price_text)
        
        # Apply price rules
        if old_price and final_price and old_price > final_price:
            # Discount case: original price is higher than final price
            product["price"] = old_price
            product["discount_price"] = final_price
            product["discount_amount"] = round(old_price - final_price, 2)
        else:
            # No discount
            product["price"] = final_price or 0
            product["discount_price"] = 0
            product["discount_amount"] = 0
    
    # =====================================================
    # SKU / SPECIAL ID
    # =====================================================
    sku_block = soup.find("div", class_="product-info-stock-sku")
    if sku_block:
        sku_value = sku_block.find("div", class_="value")
        if sku_value:
            product["special_id"] = clean_and_decode_text(sku_value.text)
    
    # If SKU not found, try from meta
    if not product["special_id"]:
        sku_meta = soup.find("meta", {"property": "product:retailer_item_id"})
        if sku_meta:
            product["special_id"] = clean_and_decode_text(sku_meta.get("content", ""))
    
    # =====================================================
    # AVAILABILITY - Using normalize_availability utility
    # =====================================================
    availability_text = None
    
    # Method 1: Schema.org availability link
    availability_link = soup.find("link", {"itemprop": "availability"})
    if availability_link:
        href = availability_link.get("href", "")
        if "InStock" in href:
            availability_text = "InStock"
        elif "OutOfStock" in href:
            availability_text = "OutOfStock"
        elif "PreOrder" in href:
            availability_text = "OnOrder"
    
    # Method 2: Stock status div
    if not availability_text:
        stock_div = soup.find("div", class_="stock")
        if stock_div:
            stock_text = stock_div.text.strip()
            availability_text = normalize_availability(stock_text)
    
    # Method 3: From product data attributes
    if not availability_text:
        product_data = soup.find("div", {"data-product": True})
        if product_data:
            try:
                data_json = product_data.get("data-product")
                if data_json:
                    data = json.loads(data_json)
                    stock_status = data.get("stock_status", "")
                    availability_text = normalize_availability(stock_status)
            except:
                pass
    
    # Method 4: From availability table row
    if not availability_text:
        table = soup.find("table", class_="data table additional-attributes")
        if table:
            rows = table.find_all("tr")
            for row in rows:
                th = row.find("th")
                td = row.find("td")
                if th and td:
                    if "disponibilit" in th.text.lower():
                        availability_text = normalize_availability(td.text)
                        break
    
    product["availability"] = availability_text or "OutOfStock"
    
    # =====================================================
    # BRAND
    # =====================================================
    brand_meta = soup.find("meta", {"property": "product:brand"})
    if brand_meta:
        product["brand"] = clean_and_decode_text(brand_meta.get("content", ""))
    
    # Fallback: from product data
    if not product["brand"]:
        product_data = soup.find("div", {"data-product": True})
        if product_data:
            try:
                data_json = product_data.get("data-product")
                if data_json:
                    data = json.loads(data_json)
                    product["brand"] = clean_and_decode_text(data.get("brand", ""))
            except:
                pass
    
    # Fallback: from attributes table
    if not product["brand"]:
        product = _parse_attributes_table(soup, product, source)
    
    # =====================================================
    # DESCRIPTION - Using clean_html utility
    # =====================================================
    # Method 1: Meta description
    meta_desc = soup.find("meta", {"property": "og:description"})
    if meta_desc:
        desc = meta_desc.get("content", "")
        # Remove "Retrait..." and other unwanted text
        desc = desc.split("Retrait")[0]
        desc = desc.split("En savoir")[0]
        desc = desc.strip()
        product["description"] = clean_html(desc)
    
    # Method 2: Product description div
    if not product["description"]:
        desc_div = soup.find("div", class_="product-description")
        if desc_div:
            product["description"] = clean_html(desc_div.text)
    
    # Method 3: Description from product data
    if not product["description"]:
        product_data = soup.find("div", {"data-product": True})
        if product_data:
            try:
                data_json = product_data.get("data-product")
                if data_json:
                    data = json.loads(data_json)
                    product["description"] = clean_html(data.get("description", ""))
            except:
                pass
    
    # =====================================================
    # SHORT DESCRIPTION / SPECS - From attributes table
    # =====================================================
    product = _parse_attributes_table(soup, product, source)
    
    # =====================================================
    # IMAGES
    # =====================================================
    images = []
    main_image = None
    
    # Method 1: Meta image
    meta_img = soup.find("meta", property="og:image")
    if meta_img and meta_img.get("content"):
        main_image = normalize_url(meta_img["content"].split("?")[0])
    
    # Method 2: Product image gallery
    gallery_images = soup.select(".product-image-gallery img")
    for img in gallery_images:
        img_url = img.get("src") or img.get("data-src")
        if img_url:
            img_url = normalize_url(img_url.split("?")[0])
            if img_url and img_url not in images:
                images.append(img_url)
    
    # Method 3: Product image from container
    if not images:
        product_img = soup.select_one(".product-image-container img")
        if product_img:
            img_url = product_img.get("src") or product_img.get("data-src")
            if img_url:
                img_url = normalize_url(img_url.split("?")[0])
                if img_url and img_url not in images:
                    images.append(img_url)
    
    # Set main image if not set
    if not main_image and images:
        main_image = images[0]
    
    product["main_image"] = main_image
    product["images"] = images
    
    # =====================================================
    # PRICE VALID UNTIL
    # =====================================================
    valid_until = soup.find("meta", {"property": "product:price:valid_until"})
    if valid_until:
        product["price_valid_until"] = valid_until.get("content")
    
    # =====================================================
    # CATEGORY - From breadcrumbs or attributes
    # =====================================================
    if not product.get("category"):
        # Try from breadcrumbs
        breadcrumbs = soup.select(".breadcrumbs .items .item")
        if breadcrumbs:
            # Get the last breadcrumb (usually the category)
            last_crumb = breadcrumbs[-1]
            category_text = last_crumb.text.strip()
            if category_text:
                product["category"] = clean_and_decode_text(category_text)
    
    if not product.get("category"):
        product["category"] = source
    
    # =====================================================
    # MAIN_PRODUCT_ID - Ensure it's set
    # =====================================================
    if not product["main_product_id"] and product_id:
        product["main_product_id"] = str(product_id)
    
    # =====================================================
    # URL - Normalize
    # =====================================================
    product["url"] = normalize_url(url)
    
    logger.info(f"{site} | PRODUCT_PARSED | {product['main_product_id']}")
    
    return product


def _parse_attributes_table(soup, product, source):
    """
    Parse the attributes table and extract brand, category, and short_description.
    """
    table = soup.find("table", class_="data table additional-attributes")
    
    if not table:
        # If no table, set default short_description
        if not product.get("short_description"):
            product["short_description"] = {}
        return product
    
    # Initialize short_description if not exists
    if not product.get("short_description"):
        product["short_description"] = {}
    
    rows = table.find_all("tr")
    category_found = False
    
    for row in rows:
        th = row.find("th")
        td = row.find("td")
        
        if not th or not td:
            continue
        
        key = clean_and_decode_text(th.text)
        value = clean_and_decode_text(td.text)
        
        if not value:
            continue
        
        key_lower = key.lower()
        
        # Store raw structure in short_description
        product["short_description"][key] = value
        
        # Extract brand
        if "marque" in key_lower and not product.get("brand"):
            product["brand"] = value
        
        # Extract category (first "type" row only)
        if key_lower == "type" and not category_found:
            product["category"] = value
            category_found = True
    
    # If category not found in table, use source
    if not product.get("category"):
        product["category"] = source
    
    return product


def fast_check_mytek_product(html, url=None, main_product_id=None):
    """
    Fast product checker for monitoring - extracts only essential fields.
    """
    soup = BeautifulSoup(html, "lxml")
    
    result = {}
    
    # Extract price
    price_box = None
    if main_product_id:
        price_box = soup.select_one(f'div.price-box.price-final_price[data-product-id="{main_product_id}"]')
    
    if not price_box:
        price_box = soup.find("div", class_="price-box", attrs={"data-role": "priceBox"})
    
    if price_box:
        final_price = None
        final_span = price_box.find("span", {"data-price-type": "finalPrice"})
        if final_span:
            final_price = clean_price(final_span.get("data-price-amount"))
        
        if final_price is None:
            price_span = price_box.find("span", class_="price")
            if price_span:
                final_price = clean_price(price_span.text)
        
        result["price"] = final_price or 0
        
        # Check for discount
        old_span = price_box.find("span", {"data-price-type": "oldPrice"})
        if old_span:
            old_price = clean_price(old_span.get("data-price-amount"))
            if old_price and old_price > result["price"]:
                result["discount_price"] = result["price"]
                result["price"] = old_price
                result["discount_amount"] = round(old_price - result["discount_price"], 2)
            else:
                result["discount_price"] = 0
                result["discount_amount"] = 0
        else:
            result["discount_price"] = 0
            result["discount_amount"] = 0
    
    # Extract availability
    availability_text = None
    availability_link = soup.find("link", {"itemprop": "availability"})
    if availability_link:
        href = availability_link.get("href", "")
        if "InStock" in href:
            availability_text = "InStock"
        elif "OutOfStock" in href:
            availability_text = "OutOfStock"
    
    if not availability_text:
        stock_div = soup.find("div", class_="stock")
        if stock_div:
            availability_text = normalize_availability(stock_div.text)
    
    result["availability"] = availability_text or "OutOfStock"
    
    # Extract special_id (SKU)
    sku_block = soup.find("div", class_="product-info-stock-sku")
    if sku_block:
        sku_value = sku_block.find("div", class_="value")
        if sku_value:
            result["special_id"] = clean_and_decode_text(sku_value.text)
    
    if not result.get("special_id"):
        sku_meta = soup.find("meta", {"property": "product:retailer_item_id"})
        if sku_meta:
            result["special_id"] = clean_and_decode_text(sku_meta.get("content", ""))
    
    # Extract price_valid_until
    valid_until = soup.find("meta", {"property": "product:price:valid_until"})
    if valid_until:
        result["price_valid_until"] = valid_until.get("content")
    
    return result