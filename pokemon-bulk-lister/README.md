# pokemon-bulk-lister

A six-step pipeline that turns photos of binder pages into ready-to-upload
bulk CSVs for **TCGPlayer**, **Whatnot**, and **eBay**.

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
```

## Setup

```bash
cd pokemon-bulk-lister
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env, fill in the credentials below
```

## Credentials

### Cloudinary
Reuse the First XI Fitness account. Copy `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`,
`CLOUDINARY_API_SECRET` from that project's `.env` into this one.

### pokemontcg.io (TCGPlayer prices)
Free tier doesn't require a key for low volume, but signing up at
<https://dev.pokemontcg.io/> gets you a higher rate limit. Drop the key into
`POKEMONTCG_API_KEY` in `.env`.

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

# 4. Push crops to Cloudinary
python scripts/04_upload_images.py

# 5. Generate the three marketplace CSVs
python scripts/05_generate_csvs.py

# 6. Open output/review.html to triage low-confidence cards before upload
python scripts/06_review_report.py
```

## Pricing aggregation

For each card we collect three numbers:

| source | how |
|---|---|
| `tcgplayer_market` | pokemontcg.io `tcgplayer.prices.<variant>.market` |
| `ebay_median_30d`  | median of eBay sold listings (last 30d, NM, US, English) |
| `ebay_max_30d`     | max of same set |

Rule:

```
prices  = [tcgplayer_market, ebay_median_30d, ebay_max_30d]
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
│  └─ 06_review_report.py
├─ lib/
│  ├─ pricing.py               # aggregation + confidence
│  ├─ tcgplayer_client.py      # pokemontcg.io wrapper
│  ├─ ebay_client.py           # OAuth + Browse + Marketplace Insights
│  └─ cloudinary_client.py
├─ input/grids/
├─ output/
│  ├─ crops/
│  └─ csvs/
├─ .env.example
├─ requirements.txt
└─ README.md
```

## Out of scope (for now)

- PriceCharting and Pokedata.io integrations
- CardMarket (US-focused for the time being)
- Condition assessment beyond a rough NM/LP/MP guess
- Direct API push to marketplaces — CSV upload only
