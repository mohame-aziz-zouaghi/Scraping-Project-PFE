# core/utils/clean_text_parser.py
"""
Text cleaning and parsing utilities for product data extraction
Handles URL decoding, HTML unescaping, special characters, and text normalization
"""
import re
import html
import unicodedata
from urllib.parse import unquote
from typing import Optional, Dict, Any, List


# =====================================================
# CORE TEXT CLEANING
# =====================================================

def clean_and_decode_text(text: Any) -> str:
    """Clean and decode text from various encodings."""
    if not text:
        return ""
    
    text = str(text)
    
    # Decode URL encoding
    text = unquote(text)
    
    # Decode HTML entities
    text = html.unescape(text)
    
    # Replace common URL encodings
    url_encoding_map = {
        '%20': ' ', '%2F': '/', '%3A': ':', '%3F': '?',
        '%3D': '=', '%26': '&', '%40': '@', '%23': '#',
        '%25': '%', '%2B': '+', '%2C': ',', '%3B': ';',
        '%21': '!', '%22': '"', '%27': "'",
        '%28': '(', '%29': ')', '%5B': '[', '%5D': ']',
        '%7B': '{', '%7D': '}', '%7C': '|', '%5C': '\\',
    }
    for encoded, decoded in url_encoding_map.items():
        text = text.replace(encoded, decoded)
    
    # Smart quotes and special characters
    char_map = {
        '“': '"', '”': '"', '‘': "'", '’': "'",
        '«': '"', '»': '"', '‹': "'", '›': "'",
        '‐': '-', '‑': '-', '–': '-', '—': '-',
        '…': '...', '•': '*', '★': '*', '☆': '*',
        '◆': '*', '◇': '*', '○': '*', '●': '*',
        '■': '*', '□': '*', '▪': '*', '▫': '*',
        '→': '->', '←': '<-', '↑': '^', '↓': 'v',
        '™': '(TM)', '®': '(R)', '©': '(C)',
        '€': 'EUR', '£': 'GBP', '¥': 'JPY', '₹': 'INR',
    }
    for old, new in char_map.items():
        text = text.replace(old, new)
    
    # Unicode normalization
    text = unicodedata.normalize('NFKC', text)
    
    # Remove non-breaking spaces
    text = text.replace("\xa0", " ")
    
    # Remove line breaks and tabs
    text = re.sub(r"[\n\r\t]+", " ", text)
    
    # Remove invisible/control characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    # Remove duplicate quotes
    text = re.sub(r'""+', '"', text)
    text = re.sub(r"''+", "'", text)
    
    # Remove duplicate punctuation
    text = re.sub(r'([!?.]){2,}', r'\1', text)
    text = re.sub(r'([,;:]){2,}', r'\1', text)
    
    # Remove repeated dashes
    text = re.sub(r'-{2,}', '-', text)
    
    # Remove spaces before punctuation
    text = re.sub(r'\s+([,;:.!?])', r'\1', text)
    
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text)
    
    return text.strip()


def clean_product_text(text: Any) -> str:
    """Clean general product text."""
    return clean_and_decode_text(text)


def clean_product_name(text: Any) -> str:
    """Clean product name."""
    if not text:
        return ""
    
    text = clean_and_decode_text(text)
    
    # Remove common filler words
    filler_words = ['envoyé', 'expédié', 'livré', 'sous', 'délai', 'jour', 'promotion']
    for word in filler_words:
        text = re.sub(rf'\b{word}\b', '', text, flags=re.IGNORECASE)
    
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_brand_name(text: Any) -> str:
    """Clean brand name."""
    if not text:
        return ""
    
    text = clean_and_decode_text(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_category_name(text: Any) -> str:
    """Clean category name."""
    if not text:
        return ""
    
    text = clean_and_decode_text(text)
    text = text.replace('-', ' ')
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_special_id(text: Any, max_length: int = 30) -> str:
    """Clean special ID (SKU, reference, etc.)."""
    if not text:
        return ""
    
    text = clean_and_decode_text(text)
    text = re.sub(r'[^a-zA-Z0-9\-_]', '', text)
    text = re.sub(r'-{2,}', '-', text)
    text = re.sub(r'_{2,}', '_', text)
    
    if len(text) > max_length:
        text = text[:max_length]
    
    return text.strip()


# =====================================================
# AVAILABILITY NORMALIZATION
# =====================================================

def normalize_availability(text: Any) -> str:
    """Normalize availability to InStock, OutOfStock, or OnOrder."""
    if not text:
        return "OutOfStock"
    
    text = str(text).lower().strip()
    
    # Check for InStock
    stock_keywords = ['stock', 'disponible', 'en stock', 'available', 'in stock']
    for keyword in stock_keywords:
        if keyword in text:
            if 'rupture' in text:
                return "OutOfStock"
            return "InStock"
    
    # Check for OnOrder
    order_keywords = ['commande', 'précommande', 'preorder', 'backorder', 'sur commande']
    for keyword in order_keywords:
        if keyword in text:
            return "OnOrder"
    
    # Check for OutOfStock
    out_keywords = ['rupture', 'épuisé', 'indisponible', 'non disponible']
    for keyword in out_keywords:
        if keyword in text:
            return "OutOfStock"
    
    # Check quantity numbers
    numbers = re.findall(r'\d+', text)
    if numbers:
        try:
            quantity = int(numbers[0])
            if quantity > 0:
                return "InStock"
            else:
                return "OutOfStock"
        except:
            pass
    
    return "OutOfStock"


# =====================================================
# JSON CLEANING
# =====================================================

def clean_json_string(json_str: str) -> str:
    """Clean a JSON string by unescaping HTML entities."""
    if not json_str:
        return ""
    
    json_str = html.unescape(json_str)
    json_str = json_str.replace('&quot;', '"')
    return json_str


def is_empty_or_placeholder(text: Any) -> bool:
    """Check if text is empty or a placeholder value."""
    if not text:
        return True
    
    text = str(text).strip().lower()
    
    placeholders = [
        'n/a', 'na', '-', '--', '---', 'none', 'null', 'undefined',
        'unknown', 'not available', 'non disponible', 'indisponible',
        'à définir', 'a definir', 'tbd'
    ]
    
    return text in placeholders or len(text) < 2