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

## Image identification experiment (2026-07-22)

Goal: identify crops without AI. What failed and what shipped, so nobody
re-treads this ground:

- **Perceptual bit-hashes (dHash-256 / pHash-64) do not work on binder
  photos.** Median same-card distance ~99/256 vs. ~random 128 — card layouts
  are dominated by flat regions where sensor noise flips gradient signs, and
  framing offsets (binder margins in the crop) shift everything. Grayscale
  loses most of the remaining signal: all card frames look alike.
- **What works:** (1) segment the card from the dark pocket background
  (saturation+value mask → open → close → minAreaRect, aspect-gated) and
  perspective-warp to a canonical 200x280 frame; (2) describe it as a
  blurred 12x12 **color** thumbnail, contrast-normalized; (3) query with
  jittered reframings, cosine distance, and a double gate: best distance
  < 0.15 AND ≥ 0.05 ahead of the nearest *different* card (name+number).
- **Validation (153 real through-sleeve crops, 2,511-card index):** 64
  accepts, 64 correct, 0 wrong — 42% of AI calls eliminated at zero quality
  cost. The 8 "disagreements" with stored AI answers were all *AI* errors
  (page-level row/col mix-ups on two pages), confirmed visually and fixed.
- Same-art reprints across sets tie under the margin gate and intentionally
  fall through to AI (it reads the collector number). Reverse-holo vs.
  regular cannot be distinguished from art; rarity heuristic only.
- pokemontcg.io's REST API 500s frequently (deep pagination always, shallow
  queries in bad weather). The official static export at
  github.com/PokemonTCG/pokemon-tcg-data is the reliable source; the builder
  uses it first and falls back to the API.
