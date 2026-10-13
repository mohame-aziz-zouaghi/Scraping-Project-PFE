# scripts/check_all_parsers.py
"""
Cross-site parser smoke test.

For each site, fetches one known-good product URL through the
site's pre-configured SmartRequester, runs the site's parser, and
prints a JSON summary plus a per-field validation.

Usage:
    python -m scripts.check_all_parsers                    # all sites, one URL each
    python -m scripts.check_all_parsers --site mytek       # one site only
    python -m scripts.check_all_parsers --url <url>        # override the URL
    python -m scripts.check_all_parsers --site mytek --url <url>

Exit code: 0 if every site fetched and parsed without warnings;
1 if any site failed or produced warnings.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# ── Requester factories (one per site; the same instances the
#    scraper workers use) ──────────────────────────────────────
from core.Web.smart_requester import (
    get_megapc_requester,
    get_mytek_requester,
    get_spacenet_requester,
    get_tunisianet_requester,
    SmartRequester,
)

# ── Parsers (public entry points) ─────────────────────────────
from parsers.mytek.mytek_product_parser import parse_mytek_product_page
from parsers.sbs.sbs_product_parser import parse_sbs_product_page
from parsers.spacenet.spacenet_product_parser import (
    parse_spacenet_product_page,
)
from parsers.tunisianet.tunisianet_product_parser import (
    parse_tunisianet_product_page,
)
from parsers.megapc.megapc_product_details_parser import (
    parse_megapc_product_details,
)


# ═══════════════════════════════════════════════════════════
# Site table
# ═══════════════════════════════════════════════════════════
SITE_URLS: dict[str, str] = {
    "mytek": (
        "https://www.mytek.tn/"
        "pc-portable-hp-ai-15-fd2007nk-ultra-5-225u-24go-512go-ssd-silver.html"
    ),
    "tunisianet": (
        "https://www.tunisianet.com.tn/"
        "pc-portable-tunisie/95062-pc-portable-hp-15-fd0079nk-i5-1334u-8-go-512-go-ssd-silver.html"
    ),
    "sbs": (
        "https://www.sbsinformatique.com/"
        "pc-portable-tunisie/hp-250-g9-i5-1235u-8go-512go-ssd-noir.html"
    ),
    "spacenet": (
        "https://spacenet.tn/pc-portable-tunisie/"
        "70967-pc-portable-hp-15-fd0079nk-i5-1334u-8go-512go-ssd-silver.html"
    ),
    "megapc": (
        "https://megapc.tn/shop/product/ORDINATEURS/PC%20PORTABLE/"
        "PC-Portable-HP-15-fd0401nk"
    ),
    "skymil": (
        "https://www.skymil.com/"
        "pc-portable-hp-15-fd0401nk-i3-1315u-8go-256go-ssd-noir.html"
    ),
}


# ═══════════════════════════════════════════════════════════
# Requester factory dispatch
# ═══════════════════════════════════════════════════════════
def _requester_for(site: str) -> SmartRequester:
    if site == "mytek":
        return get_mytek_requester()
    if site == "tunisianet":
        return get_tunisianet_requester()
    if site == "spacenet":
        return get_spacenet_requester()
    if site == "megapc":
        return get_megapc_requester()
    # No pre-configured factory for sbs / skymil — use the generic
    # default with an explicit site name so referers/logs are right.
    return SmartRequester(site_name=site)


# ═══════════════════════════════════════════════════════════
# Parser dispatch
# ═══════════════════════════════════════════════════════════
_PARSERS: dict[str, Callable[[str, str, Optional[str]], Optional[dict]]] = {
    "mytek": lambda html, url, pid: parse_mytek_product_page(
        html, url, "mytek", "check_all_parsers", product_id=pid
    ),
    "tunisianet": lambda html, url, pid: parse_tunisianet_product_page(
        html, url, "tunisianet", pid
    ),
    "sbs": lambda html, url, pid: parse_sbs_product_page(
        html, url, "sbs", pid
    ),
    "spacenet": lambda html, url, pid: parse_spacenet_product_page(
        html, url, "spacenet", pid
    ),
    "megapc": lambda html, url, pid: parse_megapc_product_details(
        html, url, "megapc", pid
    ),
}


# ═══════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════
def _validate(site: str, product: dict) -> list[str]:
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
    if discount > 0 and price > 0 and discount >= price:
        warnings.append(
            f"discount_price ({discount}) >= price ({price})"
        )
    if price > 0 and amount:
        expected = round(max(price - discount, 0), 2)
        if abs(amount - expected) > 0.05:
            warnings.append(
                f"discount_amount ({amount}) != price - discount ({expected})"
            )
    if not product.get("availability"):
        warnings.append("availability is empty")
    if not product.get("special_id"):
        warnings.append("special_id (SKU) is empty")
    if not product.get("main_image"):
        warnings.append("main_image is empty")

    if site in {"tunisianet", "sbs", "spacenet", "megapc"} and not product.get("brand"):
        warnings.append(f"[{site}] brand extraction failed")

    return warnings


# ═══════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════
def _summary(product: dict) -> dict:
    keep = (
        "name", "brand", "category",
        "price", "discount_price", "discount_amount",
        "availability", "special_id", "main_image", "url",
    )
    out = {k: product.get(k) for k in keep}
    sd = product.get("short_description")
    if isinstance(sd, dict):
        out["short_description_keys"] = list(sd.keys())
        out["short_description_sample"] = dict(list(sd.items())[:3])
    elif isinstance(sd, list):
        out["short_description_count"] = len(sd)
        out["short_description_sample"] = sd[:3]
    else:
        out["short_description_keys"] = []
    out["images_count"] = len(product.get("images") or [])
    return out


def _check_one(site: str, url: str, *, timeout: int = 30) -> tuple[bool, int]:
    """Returns (ok, warning_count)."""
    print(f"\n{'=' * 78}")
    print(f"[{site.upper()}] {url}")
    print("=" * 78)

    requester = _requester_for(site)
    parser = _PARSERS.get(site)
    if parser is None:
        print(f"  ✗ no parser wired for site={site}")
        return False, 0

    try:
        html = requester.get_html(url, timeout=timeout)
    except Exception as exc:
        print(f"  ✗ fetch raised: {exc!r}")
        traceback.print_exc()
        return False, 0

    if not html:
        print("  ✗ empty HTML (status not 2xx, or body couldn't be decoded)")
        return False, 0

    print(f"  fetched {len(html):,} chars")

    try:
        product = parser(html, url, None)
    except Exception as exc:
        print(f"  ✗ parser raised: {exc!r}")
        traceback.print_exc()
        return False, 0

    if product is None:
        print("  ✗ parser returned None (product treated as invalid/redirected)")
        return False, 0

    if not isinstance(product, dict):
        print(f"  ✗ parser returned {type(product).__name__}, expected dict")
        return False, 0

    print()
    print(json.dumps(_summary(product), indent=2, ensure_ascii=False, default=str))

    warnings = _validate(site, product)
    if warnings:
        print(f"  ⚠ {len(warnings)} warning(s):")
        for w in warnings:
            print(f"     - {w}")
    else:
        print("  ✓ all fields populated")

    return True, len(warnings)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--site", "-s", choices=sorted(SITE_URLS.keys()),
        help="Run only this site (default: all)",
    )
    ap.add_argument(
        "--url", "-u",
        help="Override the URL for the selected site (requires --site)",
    )
    ap.add_argument(
        "--timeout", "-t", type=int, default=30,
        help="Per-request timeout in seconds (default: 30)",
    )
    args = ap.parse_args(argv)

    if args.url and not args.site:
        print("--url requires --site")
        return 2

    if args.site:
        sites = {args.site: args.url or SITE_URLS[args.site]}
    else:
        sites = dict(SITE_URLS)

    print("=" * 78)
    print(f"Parser smoke test — {len(sites)} site(s)")
    for site, url in sites.items():
        print(f"  {site:<12} {url}")
    print("=" * 78)

    ok_count = 0
    warn_total = 0
    failures: list[str] = []

    for site, url in sites.items():
        ok, warns = _check_one(site, url, timeout=args.timeout)
        if ok:
            ok_count += 1
            warn_total += warns
        else:
            failures.append(site)

    print()
    print("=" * 78)
    print(f"Sites OK:      {ok_count}/{len(sites)}")
    print(f"Warnings:      {warn_total}")
    if failures:
        print(f"Failures:      {', '.join(failures)}")
    print("=" * 78)

    return 0 if not failures and warn_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))