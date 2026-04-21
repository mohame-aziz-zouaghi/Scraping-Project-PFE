# parsers/megapc/megapc_products_parser.py

def parse_megapc_products(data):

    products = data if isinstance(data, list) else data.get("products", [])

    results = []

    for p in products:

        results.append({
            "id": p.get("_id"),
            "name": p.get("title"),
            "slug": p.get("lien"),
            "price": p.get("price"),
            "promo_price": p.get("prixEnPromo"),
            "stock": p.get("stock", 0),
            "brand": p.get("marque", {}).get("titre"),
            "category": p.get("categorie", {}).get("titre"),
            "sub_category": p.get("filscateg", {}).get("titre"),
            "images": p.get("gallerie", {}).get("urlPhoto", [])
        })

    return results