# Productization & cost review — findings for the Builder instance

Condensed from the 2026-07-19 full-repo review (Session B). Actionable items
already landed are marked ✅ with their commit.

## What landed today (Session B)

- ✅ `84bed79` — lot-bundling feature committed; rebased onto origin/main (publishing feature + CI).
- ✅ `eefbc3c` — pricing correctness: `final_price` is now **median-of-sources**
  (was effectively max), `ebay_max_30d` no longer enters the aggregate, eBay
  sold-comps no longer filter `conditions:{NEW}` (that filter returned zero
  comps for every raw single — graded slabs are now excluded by title instead),
  SQLite `busy_timeout` (silent write-drops under the parallel job), run-all
  drain-thread hang fixed, Terapeak merge no longer wipes match-warning clamps.
- ✅ `7c34ccf` — Claude-vision identification (`lib/vision_identify.py`,
  `/api/identify/run-all`, `/api/cards/<id>/identify` + UI). Page-level Haiku
  call per binder page (~$0.02/page), per-crop retry below
  `VISION_RETRY_CONFIDENCE`. Structured outputs, schema-validated.
- ✅ `971975b` — Cardmarket free daily price-guide ingest
  (`lib/cardmarket_client.py`, TOS-blessed, cached 24h) as a pokemontcg.io
  fallback; Terapeak value gate (`TERAPEAK_MIN_VALUE`, default $3).

## Notes for scheduler / portfolio / alerts (your phases)

- **Pricing semantics changed** — `final_price` values shift *down* (median,
  not max). If alerts or portfolio deltas compare against pre-2026-07-19
  prices, expect a one-time step change.
- **`ebay_median_30d` will start populating** once Marketplace Insights access
  exists (the NEW-condition filter was why it was always empty).
- **SQLAlchemy sessions need WAL + busy_timeout** against the shared SQLite
  file (`webapp/db.py:connect` now sets `busy_timeout=10000`; mirror it in
  `models.py`'s engine, e.g. `connect_args` + `PRAGMA`s on connect event), or
  the scheduler's writes will race the pricing job.
- **Tiered refresh cadence**: use `final_price` + `pricing_confidence` as the
  tier key. 96% of the current inventory is under $0.50 — those need at most a
  weekly refresh; per-card API spend is wasted below ~$1.
- **Catalog-level price dedup** (one fetch shared by all owners): key on
  `tcgplayer_product_id` (populated by `_price_card`), with a daily TTL.

## Hard blockers for any commercial release (unchanged)

1. **Terapeak scraping must be removed or stay opt-in-personal** — explicit
   TOS violation with active bot-detection evasion; account-ban + legal risk.
   The value gate reduces exposure but does not fix the contract problem.
2. **eBay Marketplace Insights is limited-release** — do not build features
   that depend on its approval.
3. **Cardmarket prices must not be displayed raw in a public UI** (internal
   aggregation is TOS-blessed; display needs written agreement).
4. Cloudinary runs on a borrowed personal account — swap to owned S3/R2
   before anything multi-user ships.

## Cost model (per 100-card binder, current design)

- Identification: ~$0.15–0.30 (Haiku page-level + retries). Batch API would
  halve it if identification ever moves server-side/async-bulk.
- Pricing APIs: $0 (pokemontcg.io free tier + Cardmarket daily download).
- Biggest future saving: cache price lookups by `tcgplayer_product_id`
  (duplicate commons dominate bulk) — fits naturally in your catalog dedup.
