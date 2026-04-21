# parsers/megapc/megapc_product_details_parser.py

def parse_megapc_product_details(data):

    product = data.get("pageProps", {}).get("product", {})

    if not product:
        return {}

    return {
        "description": product.get("description_fr", ""),
        "short_description": product.get("miniDescription_fr", ""),
        "sku": product.get("sku"),
        "images": [
            img.get("previewImageSrc")
            for img in product.get("images", [])
        ],
        "priceValidUntil":product.get("priceValidUntil","")
    }