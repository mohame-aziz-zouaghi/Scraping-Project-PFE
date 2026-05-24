# parsers/megapc/megapc_category_parser.py
from typing import Dict, List, Optional

def parse_megapc_categories_grouped(data: List[Dict]) -> Dict[str, List[str]]:
    """
    Parse MegaPC category structure into grouped format
    
    Args:
        data: Raw category data from API
    
    Returns:
        Dictionary with main category as key and list of subcategories as value
    """
    if not isinstance(data, list):
        return {}
    
    result = {}
    
    for cat in data:
        main = cat.get("title")
        if not main:
            continue
        
        subs = [
            sub.get("title")
            for sub in cat.get("children", [])
            if sub.get("title")
        ]
        
        if subs:  # Only add if there are subcategories
            result[main] = subs
    
    return result


def parse_megapc_categories_flat(data: List[Dict]) -> List[str]:
    """Return flat list of all categories"""
    grouped = parse_megapc_categories_grouped(data)
    return list(grouped.keys())