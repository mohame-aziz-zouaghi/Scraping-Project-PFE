# parsers/megapc/megapc_category_parser.py

def parse_megapc_categories_grouped(data):

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

        result[main] = subs

    return result