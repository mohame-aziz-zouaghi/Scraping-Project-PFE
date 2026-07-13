# parsers/megapc/megapc_product_details_parser.py
from typing import Dict, Any, Optional, List

from core.utils.clean_date_logic import clean_date

def parse_megapc_product_details(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse MegaPC product details from Next.js data
    
    Args:
        data: Raw JSON response from Next.js data endpoint
    
    Returns:
        Standardized product details dictionary
    """
    if not isinstance(data, dict):
        return {}
    
    # Extract from pageProps > product
    page_props = data.get("pageProps", {})
    product = page_props.get("product", {})
    
    if not product:
        return {}
    
    # Extract images safely
    images = product.get("images", [])
    image_urls = []
    for img in images:
        if isinstance(img, dict):
            img_url = img.get("previewImageSrc") or img.get("url") or img.get("src")
            if img_url:
                image_urls.append(img_url)
        elif isinstance(img, str):
            image_urls.append(img)
    
    # Extract price valid until
    price_valid_until = product.get("priceValidUntil") or product.get("price_valid_until")
    
    price_valid_until = clean_date(price_valid_until) if price_valid_until else price_valid_until
    
    return {
        "description": product.get("description_fr") or product.get("description", ""),
        "short_description": product.get("miniDescription_fr"),
        "sku": product.get("sku") or product.get("referenceInterne") or product.get("mpn"),
        "images": image_urls,
        "price_valid_until": price_valid_until,
        "meta_title": product.get("meta_title"),
        "meta_description": product.get("meta_description"),
    }


def extract_specifications(product: Dict) -> List[Dict[str, str]]:
    """
    Extract specifications from product if available
    """
    specs = product.get("specifications") or product.get("caracteristiques", [])
    
    if not specs:
        return []
    
    result = []
    for spec in specs:
        if isinstance(spec, dict):
            key = spec.get("name") or spec.get("key") or spec.get("label")
            value = spec.get("value") or spec.get("valeur")
            if key and value:
                result.append({"name": key, "value": value})
    
    return result