import json
from bs4 import BeautifulSoup
from core.utils.clean_date_logic import clean_date
from core.logger import setup_logger

logger = setup_logger(
    "megapc_fast",
    "monitor"
)

# ---------------------------------
# CLEAN PRICE
# ---------------------------------
def clean_price(value):
    if value is None:
        return None
    
    try:
        value = str(value)
        value = (
            value
            .replace("TND", "")
            .replace("DT", "")
            .replace(",", ".")
            .strip()
        )
        return float(value)
    except:
        return None

# ---------------------------------
# FAST EXTRACTION
# ---------------------------------
def extract_megapc_fast(html):
    soup = BeautifulSoup(html, "lxml")
    
    script = soup.find("script", id="__NEXT_DATA__")
    
    if not script:
        logger.warning("MEGAPC_FAST | NO_NEXT_DATA")
        return None
    
    try:
        data = json.loads(script.string)
        
        product = (
            data
            .get("props", {})
            .get("pageProps", {})
            .get("product", {})
        )
        
        if not product:
            return None
        
        normal_price = clean_price(product.get("price"))
        promo_price = clean_price(product.get("prixEnPromo"))
        
        final_price = normal_price
        discount_price = promo_price if promo_price else 0
        discount_amount = 0
        
        if promo_price and normal_price:
            discount_amount = round(normal_price - promo_price, 2)
        
        stock = product.get("stock", 0)
        availability = "InStock" if stock > 0 else "OutOfStock"
        
        # Get and clean the date at extraction time
        raw_date = product.get("priceValidUntil")
        cleaned_date = clean_date(raw_date) if raw_date else None
        
        return {
            "price": final_price,
            "discount_price": discount_price,
            "discount_amount": discount_amount,
            "availability": availability,
            "special_id": (
                product.get("sku") or product.get("mpn")
            ),
            "price_valid_until": cleaned_date  # Now always YYYY-MM-DD format
        }
    
    except Exception as e:
        logger.error(f"MEGAPC_FAST_PARSE_ERROR | {e}")
        return None