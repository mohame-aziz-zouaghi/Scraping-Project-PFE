# parsers/megapc/megapc_products_parser.py
from typing import Dict, List, Any, Optional

def parse_megapc_products(data: Any) -> List[Dict]:
    """
    Parse MegaPC products from API response
    
    Args:
        data: Raw API response (list or dict with 'products' key)
    
    Returns:
        List of parsed products with standardized fields
    """
    # Handle both list and dict responses
    if isinstance(data, dict):
        products_data = data.get("products", [])
    elif isinstance(data, list):
        products_data = data
    else:
        return []
    
    results = []
    
    for p in products_data:
        if not isinstance(p, dict):
            continue
        
        # Extract brand info safely
        brand = p.get("marque", {})
        brand_name = brand.get("titre") if isinstance(brand, dict) else None
        
        # Extract category info safely
        category = p.get("categorie", {})
        category_name = category.get("titre") if isinstance(category, dict) else None
        
        # Extract subcategory info safely
        subcategory = p.get("filscateg", {})
        subcategory_name = subcategory.get("titre") if isinstance(subcategory, dict) else None
        
        product = {
            "id": p.get("_id"),
            "name": p.get("title"),
            "slug": p.get("lien"),
            "price": p.get("price", 0),
            "promo_price": p.get("prixEnPromo", 0),
            "stock": p.get("stock", 0),
            "brand": brand_name,
            "category": category_name,
            "sub_category": subcategory_name,
            "images": p.get("gallerie", {}).get("urlPhoto", []) if isinstance(p.get("gallerie"), dict) else []
        }
        
        # Only add if we have minimum required data
        if product["id"] and product["slug"]:
            results.append(product)
    
    return results


def extract_product_identifier(product: Dict) -> str:
    """Extract unique identifier for product"""
    return product.get("id") or product.get("slug")


def is_valid_product(product: Dict) -> bool:
    """Check if product has minimum required data"""
    return bool(product.get("id") and product.get("slug") and product.get("name"))