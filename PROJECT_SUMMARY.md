# Scraping & Monitoring Project — Summary

A multi-site e-commerce scraping and price-monitoring platform for Tunisian
hardware retailers (MegaPC, Tunisianet, SBS, Mytek, Spacenet, Skymil). It
scrapes product catalogs into **Redis** (fast cache) and **PostgreSQL**
(source of truth), then continuously monitors the tracked products for price,
discount, and availability changes.

---

## 1. High-Level Architecture

```
                +------------------+
   CLI menu --> |     main.py      |
                +--------+---------+
                         |
          +--------------+---------------+
          |                              |
   +------v------+               +-------v--------+
   |  SCRAPING   |               |   MONITORING   |
   |  (sites/)   |               | (monitoring/)  |
   +------+------+               +-------+--------+
          |                              |
   parse pages                    load enabled products
          |                          from Redis
   save NEW products                    |
   to Redis + Postgres           re-fetch each product
                                        |
                                 ResultProcessor compares
                                 tracked fields -> updates
                                 Redis + Postgres on change
```

- **Scraping side** discovers and stores *new* products.
- **Monitoring side** re-checks *existing* products on an interval and records
  changes (price history, alerts, failure tracking).

---

## 2. Directory Layout

| Path | Responsibility |
|------|----------------|
| `main.py` | Entry point → launches `menu.cli_menu.menu()` |
| `menu/` | CLI menus (scraping, monitoring, redis management, diagnostics) |
| `sites/` | Per-site scrapers (`*_site.py`), each subclassing `BaseSite` |
| `parsers/<site>/` | HTML/JSON parsers per site (category, search, product) |
| `monitoring/extractors/` | Fast per-site field extractors (`*_fast.py`) |
| `monitoring/workers/` | Per-site monitor workers (`*_worker.py`) |
| `monitoring/engine/` | Engine, worker pool, result processor, site registry |
| `Database/Postgresql/` | `Database` class — upsert, change detection, history |
| `Database/Redis/` | `ProductCache`, redis client, cache-key helpers |
| `core/Web/` | `http_client.get()`, headers, smart requester |
| `core/utils/` | Text/date/price cleaning helpers |
| `config/` | Category maps + brand list per site |

---

## 3. Data Model — Tracked Fields

Both the scraper and the monitor speak the same 6 monitored fields:

| Field | Meaning |
|-------|---------|
| `price` | Current selling price |
| `discount_price` | Comparison/original price when discounted (else 0/None) |
| `discount_amount` | Savings amount (else 0/None) |
| `availability` | Normalized: `InStock` / `OutOfStock` / `OnOrder` |
| `special_id` | Reference / SKU / MPN |
| `price_valid_until` | Promo expiry date (`YYYY-MM-DD`) |

`ResultProcessor` compares only these fields to decide whether a change
occurred, so the extractor and scraper **must** produce identical conventions.
---

## 4. Scraping Flow (`sites/*_site.py`)

Each scraper subclasses `BaseSite` and follows the same persistence pattern
(reference implementations: `megapc_site.py`, `tunisianet_site.py`, `sbs_site.py`):

1. `scrape_site()` — resets stats, opens Redis + Postgres connections
   (`_init_connections`), iterates categories + brand fallback, closes
   connections in `finally`, prints a stats summary.
2. `scrape_category()` / `scrape_brands()` — paginate, parse product lists,
   fan out to a `ThreadPoolExecutor`.
3. `scrape_product_worker()` — per product:
   - dedup within the run (`handle_duplicate`),
   - **skip if already stored** (`_product_exists` → Redis then Postgres),
   - parse the product page,
   - **save NEW products only** (`_save_new_product` → Redis cache payload +
     Postgres upsert),
   - all wrapped in try/except with per-error stat tracking.

### Stats tracked per run
`new_products`, `skipped_duplicates`, `errors`, `redis_saved`,
`postgres_saved`, `total_products_found`.

### Key conventions
- **Site keys are lowercased** for storage (`self.name.lower()`) because the
  monitoring side lowercases site names — e.g. class `SBS` stores under `sbs`.
- **None-safe numerics**: SBS's parser can return `None` for price fields, so
  saves use `float(x or 0)` to avoid `TypeError`.

---

## 5. Monitoring Flow (`monitoring/`)

### Components
- **`site_registry.py`** — registers each site's worker + config via
  `register_site(name, worker_func, config)`. Also `auto_discover_workers()`
  auto-registers any `monitoring/workers/*_worker.py`.
- **`monitor_engine.py`** (`MonitorEngine`) — loads *enabled* products from
  Redis, processes them in batches through a `ThreadPoolExecutor`, supports
  pause/stop events, multi-site progress dashboard, and flushes pending
  Postgres batches at cycle end.
- **`result_processor.py`** (`ResultProcessor`) — `process(site, id, new_data)`
  compares tracked fields, updates Redis immediately and batches Postgres
  writes; `handle_failed_product()` increments failure counts (disables a
  product after repeated failures).
- **`monitor_config.py`** — `MONITOR_SITES` list of `{site, workers, interval}`.

### Worker contract
```python
def <site>_worker(product: dict, processor) -> bool:
    # product = {main_product_id, site, url, ...}
    # 1. fetch HTML via core.Web.http_client.get(...)
    # 2. extract 6 fields via extractors/<site>_fast.py
    # 3. processor.process(...) -> True if a change was saved
    # on any failure: processor.handle_failed_product(site, id); return False
```

### Extractor contract (`extractors/<site>_fast.py`)
Returns the 6 tracked fields or `None` if unpriced. To guarantee identical
field conventions between scrape-save and monitor-read, extractors **reuse the
scraper's product parser** (Tunisianet reuses `parse_prices`; SBS reuses
`parse_sbs_product_page`).

---

## 6. Per-Site Notes

| Site | Data source | Extractor | Worker | Registered |
|------|-------------|-----------|--------|:----------:|
| MegaPC | Next.js `__NEXT_DATA__` JSON | `megapc_fast.py` | `megapc_worker.py` | ✅ |
| Tunisianet | PrestaShop `#product-details` `data-product` | `tunisianet_fast.py` | `tunisianet_worker.py` | ✅ |
| SBS | schema.org `ld+json` + HTML discount block | `sbs_fast.py` | `sbs_worker.py` | ✅ |
| Mytek / Spacenet / Skymil | scraper present | — | — | ❌ (monitoring not yet wired) |

### SBS specifics
- Price convention from `parse_sbs_product_page`: when discounted,
  `price` = discounted, `discount_price` = original,
  `discount_amount` = discounted − original (negative — opposite of MegaPC);
  when not discounted, discount fields are `None`.
- `sbs_fast.py` coerces `None → 0.0` and returns `None` if nothing is priced.

---

## 7. Storage Layer

- **Redis (`ProductCache`)** — fast lookup cache keyed by `site:product_id`,
  stores minimal payload incl. `is_enabled`, `last_checked`.
- **PostgreSQL (`Database`)** — `upsert_products()` handles change detection,
  `price_history`, alerts, and `monitoring_stats`;
  `get_product_by_site_and_main_id()` for existence checks;
  `increment_check_failure()` disables a product after 3 failures.

---

## 8. Cross-Cutting

- **Logging** — `setup_logger(name, category)` → `logs/<name>/<category>.log`.
  Categories: `scraper`, `monitor`, `duplication`, `error`, `database`, `cache`.
- **HTTP** — `core.Web.http_client.get(url, max_retries, timeout,
  requests_per_minute, use_referer, verify_ssl)` with anti-detection headers.
- **Error handling** — every worker/scraper path is wrapped in try/except,
  logs a prefixed message, and increments an error/failure counter rather than
  crashing the run.

---

## 9. Running

```bash
python main.py     # opens the CLI menu (scraping + monitoring options)
```

Monitoring cycles run on each site's configured interval until stopped
(Ctrl+C), using the multi-site progress dashboard.

