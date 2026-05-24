# parsers/megapc/megapc_product_details_parser.py
from typing import Dict, Any, Optional, List

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
    
    # Clean and normalize date
    if price_valid_until:
        # Remove any whitespace
        price_valid_until = str(price_valid_until).strip()
        # Replace slashes with hyphens for consistency
        price_valid_until = price_valid_until.replace("/", "-")
        # Remove time part if exists
        if "T" in price_valid_until:
            price_valid_until = price_valid_until.split("T")[0]
    
    return {
        "description": product.get("description_fr") or product.get("description", ""),
        "short_description": product.get("miniDescription_fr") or product.get("miniDescription", "") or product.get("short_description", ""),
        "sku": product.get("sku") or product.get("reference"),
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