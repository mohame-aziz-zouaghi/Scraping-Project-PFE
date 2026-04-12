import json
import os

def load_existing_product_ids(site_name):

    site_name = site_name.lower()
    file_path = f"logs/{site_name}/results.txt"

    existing_ids = set()

    if not os.path.exists(file_path):
        return existing_ids

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                pid = data.get("main_product_id")

                if pid:
                    existing_ids.add(str(pid))

            except:
                continue

    return existing_ids