from datetime import datetime

def create_product_schema(site, url, main_product_id=None):

    return {
        "site": site,
        "main_product_id": main_product_id,
        "special_id": None,
        "name": None,
        "brand": None,
        "category": None,
        "price": None,
        "discount_price": None,
        "discount_amount": None,
        "availability": None,
        "price_valid_until": None,
        "url": url,
        "description": None,
        "short_description": [],
        "main_image": None,
        "images": [],
        "scraped_at": datetime.utcnow().isoformat()
    }