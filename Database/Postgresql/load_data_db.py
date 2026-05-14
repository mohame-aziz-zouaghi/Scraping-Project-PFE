import json
import os

from Database.Postgresql.db import Database



LOG_FOLDERS = [
    "logs/sbs",
    # add more sites here
]


def load_products():

    all_products = []

    for folder in LOG_FOLDERS:

        file_path = os.path.join(folder, "results.txt")

        if not os.path.exists(file_path):
            print(f"Missing: {file_path}")
            continue

        with open(file_path, "r", encoding="utf-8" ,errors="replace") as f:
            for line in f:
                try:
                    all_products.append(json.loads(line))
                except:
                    continue

        print(f"{folder} loaded")

    return all_products


def run():

    db = Database()

    products = load_products()

    print(f"TOTAL LOADED: {len(products)}")

    db.upsert_products(products)

    db.close()