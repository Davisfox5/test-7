# pokemon-bulk-lister

[![CI](https://github.com/Davisfox5/test-7/actions/workflows/ci.yml/badge.svg)](https://github.com/Davisfox5/test-7/actions/workflows/ci.yml)

A seven-step pipeline that turns photos of binder pages into ready-to-upload
bulk CSVs for **TCGPlayer**, **Whatnot**, and **eBay** — then publishes them
(eBay live via the Sell API; TCGPlayer/Whatnot via CSV upload).

```
input/grids/*.jpg
        │
        ▼  scripts/01_split_grids.py
output/crops/*.jpg
        │
        ▼  scripts/02_identify_cards.py  ◄── you fill this in via Claude Code vision
output/cards.json
        │
        ▼  scripts/03_enrich_pricing.py
output/cards_priced.json
        │
        ▼  scripts/04_upload_images.py
output/cards_priced.json  (with image_url)
        │
        ▼  scripts/05_generate_csvs.py
output/csvs/{tcgplayer,whatnot,ebay}_*.csv
        │
        ▼  scripts/06_review_report.py
output/review.html
        │
        ▼  scripts/07_publish_listings.py
eBay live listings (Sell API) + TCGPlayer/Whatnot CSV uploads
```

## Setup

```bash
cd pokemon-bulk-lister
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env, fill in the credentials below
```

## Web UI (recommended)

A single-page Flask app on top of a local SQLite DB. Upload grids, edit
identifications, run pricing, and export CSVs from one browser tab.

```bash
python -m webapp.app
# http://localhost:5050
```

Data lives at `output/db.sqlite` (auto-created; auto-imports any existing
`output/cards.json` / `output/cards_priced.json` on first launch). Drop binder
photos into the drop-zone — they're split into crops automatically and inserted
as empty card rows. Edit identification fields inline or paste a JSON list of
identifications in bulk (handy for dropping in the answers Claude gives you in
chat). Per-card "Price" runs the eBay + TCGplayer + Cardmarket pipeline; the
"Run pricing (all)" button kicks it off as a background job with a progress bar.

The CLI scripts below still work — they read/write the same `output/` files —
so you can mix and match.

## Credentials

### Cloudinary
Reuse the First XI Fitness account. Copy `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`,
`CLOUDINARY_API_SECRET` from that project's `.env` into this one.

### pokemontcg.io (TCGPlayer prices)
Free tier doesn't require a key for low volume, but signing up at
<https://dev.pokemontcg.io/> gets you a higher rate limit. Drop the key into
`POKEMONTCG_API_KEY` in `.env`.

### PriceCharting (optional pricing source)
Adds an eBay-sold-median price plus graded (PSA/BGS/CGC/SGC) tiers. Requires a
paid **Legendary**-tier subscription at <https://www.pricecharting.com/> — that
tier is what gates API access. Copy the 40-character API token from your account
page into `PRICECHARTING_API_TOKEN`. With no token the source is simply skipped,
so the rest of the pipeline runs unchanged.

> ⚠️ **Data license.** PriceCharting permits *internal business use* of its price
> data but forbids displaying those prices to third parties or the public without
> express written permission. Keep it to internal/derived use (our aggregation
> does exactly this); do not surface raw PriceCharting numbers in a shared or
> public UI without a written agreement from them.

### eBay developer API
The eBay client uses **OAuth client-credentials**, the Browse API, and the
Marketplace Insights API (sold-listing data).

1. Sign up at <https://developer.ebay.com/> (free).
2. Go to **My Account → Application Keysets** and create a *Production*
   keyset. Copy:
   - **App ID (Client ID)** → `EBAY_CLIENT_ID`
   - **Cert ID (Client Secret)** → `EBAY_CLIENT_SECRET`
3. **Marketplace Insights API is gated.** Apply for access via the same
   developer portal (under *User Access Tokens* / *API release notes →
   Marketplace Insights*). Approval is usually a few business days.
4. Leave `EBAY_MARKETPLACE_ID=EBAY_US` and `EBAY_ENV=production` unless you
   have a reason to change them.

If the script logs `403 — your app probably isn't approved for the
buy.marketplace.insights scope`, you're still waiting on access.

## Running the pipeline

```bash
# 1. Photograph binder pages, drop them in input/grids/
python scripts/01_split_grids.py

# 2. Open output/cards.json in this IDE; with Claude Code, view each crop
#    and fill in name / set_name / set_code / card_number / rarity / is_holo
#    / condition_guess / confidence. Re-run the script anytime to merge in
#    new crops without losing your edits.
python scripts/02_identify_cards.py

# 3. Look up prices and apply the aggregation rule
python scripts/03_enrich_pricing.py
# Optionally enable Terapeak headless scraping for 365-day eBay sold history:
python scripts/03_enrich_pricing.py --terapeak

# 4. Push crops to Cloudinary
python scripts/04_upload_images.py

# 5. Generate the three marketplace CSVs
python scripts/05_generate_csvs.py

# 6. Open output/review.html to triage low-confidence cards before upload
python scripts/06_review_report.py

# 7. Publish. eBay goes live via the official Sell API; TCGPlayer + Whatnot
#    upload the generated CSVs through the seller portal (headless browser).
python scripts/07_publish_listings.py --site ebay
python scripts/07_publish_listings.py --site tcgplayer --site whatnot
# Dry-run first to build payloads/CSVs without pushing anything:
python scripts/07_publish_listings.py --all --dry-run
```

## Publishing listings

The three sites are not equal — only eBay exposes a usable listing API for a
typical seller, so the publish step uses two mechanisms:

### eBay — official Sell API (recommended)
Each priced + image-uploaded card becomes a live fixed-price listing via the
Inventory API (`inventory_item` → `offer` → `publishOffer`). No scraping.

One-time setup:
1. Create a **RuName** under developer.ebay.com → *User Tokens → Get a Token
   from eBay via Your Application*; set its accepted redirect URL. Put the
   RuName in `EBAY_REDIRECT_URI`.
2. Authorize once:
   ```bash
   python -m webapp.setup_ebay
   ```
   Sign in, approve the `sell.inventory` / `sell.account` scopes, and paste the
   redirected URL back. The refresh token is cached at `EBAY_USER_TOKEN_PATH`
   and listing then runs headlessly until it expires (~18 months).
3. Publishing needs payment/return/fulfillment **business policies** and an
   inventory location. We auto-pick the account's first of each; pin specific
   IDs with `EBAY_*_POLICY_ID` / `EBAY_MERCHANT_LOCATION_KEY` if you have
   several. Opt into Business Policies in eBay account settings if you haven't.

Listing IDs are written back to `cards_priced.json` (CLI) and the SQLite DB
(web UI), so re-runs update existing offers rather than duplicating them.

### TCGPlayer & Whatnot — headless CSV upload (⚠️ TOS-grey)
Neither offers a public listing API to typical sellers. The supported path is
the bulk-inventory CSV this app already generates; this step just drives the
seller portal in a headless browser to submit it for you — the same Playwright
pattern as the Terapeak scraper. **This is against their Terms, is fragile (DOM
changes break it), and use is at your own risk** — keep it to a
personal/secondary account, or just upload the CSVs by hand.

```bash
pip install playwright && playwright install chromium
python -m webapp.setup_portal --site tcgplayer   # sign in once; session saved
python -m webapp.setup_portal --site whatnot
```

On parse failure the uploader dumps a screenshot + HTML to
`output/cache/<site>_debug/` and the upload URL/selectors can be overridden via
`TCGPLAYER_UPLOAD_URL` / `WHATNOT_UPLOAD_URL` in `.env`.

All of this is also wired into the web UI's **Publish** panel.

## Pricing aggregation

For each card we collect up to five numbers:

| source | how |
|---|---|
| `tcgplayer_market`     | pokemontcg.io `tcgplayer.prices.<variant>.market` (USD) |
| `cardmarket_trend_usd` | pokemontcg.io `cardmarket.prices.trendPrice` (EUR → USD via `EUR_USD_RATE`) |
| `ebay_median_30d`      | median of eBay sold listings (last 30d, NM, US, English) |
| `ebay_max_30d`         | max of same set |
| `terapeak_median_usd`  | optional — median of Terapeak Research sold listings (last 365d) via Playwright headless scrape |
| `pricecharting_usd`    | optional — PriceCharting ungraded ("loose") price via paid REST API (`PRICECHARTING_API_TOKEN`) |

Rule:

```
prices  = [tcg, cardmarket_usd, ebay_median_30d, ebay_max_30d, terapeak_median_usd, pricecharting_usd]
median  = statistics.median(non_null(prices))
candidate = max(prices)

if candidate > 2.5 * median:
    final  = second_highest(prices)
    outlier_flag = True
else:
    final  = candidate
```

`confidence` is `1.0` if the non-null prices are all within 20% of each
other, then decays linearly to `0.0` at 120% spread. Single-source falls
back to `0.5`. Anything below `0.6` (or any outlier) is marked
`needs_review = true` and bubbles to the top of `review.html`.

## Repo layout

```
pokemon-bulk-lister/
├─ scripts/
│  ├─ 01_split_grids.py
│  ├─ 02_identify_cards.py     # schema-only stub
│  ├─ 03_enrich_pricing.py
│  ├─ 04_upload_images.py
│  ├─ 05_generate_csvs.py
│  ├─ 06_review_report.py
│  └─ 07_publish_listings.py   # eBay Sell API + TCGPlayer/Whatnot CSV upload
├─ lib/
│  ├─ pricing.py               # aggregation + confidence
│  ├─ tcgplayer_client.py      # pokemontcg.io wrapper
│  ├─ ebay_client.py           # OAuth client-credentials + Browse + Marketplace Insights
│  ├─ ebay_oauth.py            # user-token OAuth (authorization-code + refresh)
│  ├─ ebay_lister.py           # Sell Inventory API: create + publish listings
│  ├─ portal_uploader.py       # headless-browser CSV upload base
│  ├─ tcgplayer_lister.py      # TCGPlayer seller-portal CSV upload
│  ├─ whatnot_lister.py        # Whatnot Seller Hub CSV upload
│  └─ cloudinary_client.py
├─ input/grids/
├─ output/
│  ├─ crops/
│  └─ csvs/
├─ .env.example
├─ requirements.txt
└─ README.md
```

## Terapeak headless scraping (optional)

eBay's Marketplace Insights API is closed to new applicants and capped at 90 days.
The Terapeak Research UI inside Seller Hub has the same data going back ~365 days
and is free for any Seller Hub seller — but Terapeak's Subscription Terms ban
automated access. This is a personal one-off, so we use it anyway.

```bash
pip install playwright
playwright install chromium
# First run is headful — log in to eBay once; the session is saved.
python scripts/03_enrich_pricing.py --terapeak
```

Tunables in `.env`:
- `TERAPEAK_HEADLESS=1` — flip to `0` if you want to watch.
- `TERAPEAK_MIN_DELAY` / `TERAPEAK_MAX_JITTER` — pacing between searches (default 4s + 0–4s).
- `TERAPEAK_PROXIES` — comma-separated list. Round-robined; a new browser context is created per search.
- `TERAPEAK_STATE_PATH` — where the logged-in session is cached (default `output/cache/terapeak_state.json`). Delete it to re-login.

The scraper writes screenshots + HTML to `output/cache/terapeak_debug/` whenever parsing fails so selectors can be tuned without re-running the full pipeline.

## Out of scope (for now)

- Pokedata.io paid tiers (data-license / commercial-use restrictions)
- PWCC / Fanatics Collect, Goldin, Heritage, REA scraping (TOS-prohibited)
- Condition assessment beyond a rough NM/LP/MP guess
- Direct *API* push for TCGPlayer & Whatnot — no public seller listing API, so
  those remain CSV-based (optionally auto-submitted via headless browser, see
  "Publishing listings"). eBay does publish directly via its Sell API.
