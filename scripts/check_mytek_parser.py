# scripts/check_mytek_parser.py
"""
Smoke test for the Mytek product parser.

Uses the pre-configured Mytek requester (basic headers, 12 req/min,
30s timeout) because mytek.tn rejects requests that carry the modern
Sec-* headers our generic requester sends.

Usage:
    python -m scripts.check_mytek_parser                # 5 known URLs
    python -m scripts.check_mytek_parser URL1 URL2 ...  # explicit URLs
    python -m scripts.check_mytek_parser --category     # one category page
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Pre-configured Mytek requester: basic headers, 12 RPM, 30s timeout.
from core.Web.smart_requester import get_mytek_requester

from parsers.mytek.mytek_product_parser import parse_mytek_product_page


# ──────────────────────────────────────────────────────────
# Default URLs — real Mytek pages seen in recent crawls.
# These exercise: name, brand, category, price, discount,
# availability, SKU, images, and the attributes table.
# ──────────────────────────────────────────────────────────
DEFAULT_URLS = [
    # simple accessory, single price, no discount
    "https://www.mytek.tn/refroidisseur-de-processeur-argus-lc-120-rgb-noir.html",
    # kitchen appliance, likely has Magento price script
    "https://www.mytek.tn/robot-multifonctions-decakila-6en1-1-5litres-blanc.html",
    # small appliance, brand in attributes table
    "https://www.mytek.tn/bouilloire-electrique-rasma-am154-12-2-3l-1500w-chrome.html",
    # appliance with model number in title
    "https://www.mytek.tn/mini-bar-montblanc-fb14-140-litres-blanc.html",
    # cheaper item, often different category depth
    "https://www.mytek.tn/gourde-maped-picnik-580-ml-rose.html",
]


def _print_summary(i: int, total: int, url: str, product: dict) -> None:
    keep = (
        "name",
        "brand",
        "category",
        "price",
        "discount_price",
        "discount_amount",
        "availability",
        "special_id",
        "main_image",
        "url",
    )
    summary = {k: product.get(k) for k in keep}
    summary["short_description_keys"] = list(
        (product.get("short_description") or {}).keys()
    )
    summary["short_description_sample"] = dict(
        list((product.get("short_description") or {}).items())[:3]
    )
    summary["images_count"] = len(product.get("images") or [])

    print(f"\n[{i}/{total}] {url}")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


def _validate(product: dict) -> list[str]:
    """
    Return a list of warnings. Empty list means every field we care
    about looks populated.
    """
    warnings: list[str] = []

    if not product.get("name"):
        warnings.append("name is empty")
    if not product.get("brand"):
        warnings.append("brand is empty")
    if not product.get("category"):
        warnings.append("category is empty")

    price = product.get("price") or 0
    discount = product.get("discount_price") or 0
    amount = product.get("discount_amount") or 0

    if price <= 0:
        warnings.append(f"price is non-positive ({price})")
    if discount < 0:
        warnings.append(f"discount_price is negative ({discount})")
    if discount > 0 and discount >= price:
        warnings.append(
            f"discount_price ({discount}) >= price ({price}) — Magento script likely wrong"
        )
    if amount != round(max(price - discount, 0), 2):
        warnings.append(
            f"discount_amount ({amount}) != price - discount ({price} - {discount})"
        )
    if not product.get("availability"):
        warnings.append("availability is empty")
    if not product.get("special_id"):
        warnings.append("special_id (SKU) is empty")
    if not product.get("main_image"):
        warnings.append("main_image is empty")
    if not product.get("short_description"):
        warnings.append("short_description (attributes table) is empty")

    return warnings


def main(argv: list[str]) -> int:
    urls = argv or DEFAULT_URLS

    # Instantiate the Mytek-specific requester. This is what the
    # real scraper uses, so it exercises the same code path.
    requester = get_mytek_requester()

    print("=" * 78)
    print(f"Mytek parser check — {len(urls)} product(s)")
    print(f"Requester: site=mytek  basic_headers={requester.use_basic_headers}")
    print(f"           rate={requester.requests_per_minute}/min  "
          f"timeout={requester.timeout}s  retries={requester.max_retries}")
    print("=" * 78)

    failures = 0
    warnings_total = 0

    for i, url in enumerate(urls, 1):
        try:
            html = requester.get_html(url, timeout=30)
        except Exception as exc:
            print(f"\n[{i}/{len(urls)}] {url}")
            print(f"  ✗ fetch raised: {exc}")
            failures += 1
            continue

        if not html:
            print(f"\n[{i}/{len(urls)}] {url}")
            print("  ✗ empty HTML (status not 2xx, or body couldn't be decoded)")
            failures += 1
            continue

        try:
            product = parse_mytek_product_page(
                html=html,
                url=url,
                site="mytek",
                source="check_mytek_parser",
            )
        except Exception as exc:
            print(f"\n[{i}/{len(urls)}] {url}")
            print(f"  ✗ parse raised: {exc}")
            failures += 1
            continue

        _print_summary(i, len(urls), url, product)

        warnings = _validate(product)
        if warnings:
            warnings_total += len(warnings)
            print("  ⚠ warnings:")
            for w in warnings:
                print(f"     - {w}")
        else:
            print("  ✓ all fields populated")

    print()
    print("=" * 78)
    print(f"Result: {len(urls) - failures}/{len(urls)} fetched and parsed")
    print(f"Warnings: {warnings_total}")
    print("=" * 78)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))