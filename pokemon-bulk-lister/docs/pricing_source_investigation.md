# Pokémon Pricing Source Investigation

Updated investigation following deep research by five focused subagents. Most claims are now backed by **pages actually retrieved** rather than by search-engine snippets; the few snippet-only claims are flagged inline.

## 1. Summary Table

| Source | Underlying data | Access method | Cost | Recommendation |
|---|---|---|---|---|
| **PriceCharting** | eBay sold + own marketplace; median over 2 weeks, dynamic 1/3/7/14-day windows for high-volume items, outlier-filtered | Paid REST API (`/api/product?t=<40-char-token>`, prices in pennies); HTML scrape technically possible but data-license forbids public redistribution | Premium $4.99/mo or $39.99/yr; **Legendary** tier (required for API + CSV) — price unknown without a logged-in page load | **Defer** — duplicates eBay; data-license forbids public re-display |
| **Pokedata.io** | eBay + TCGPlayer + Cardmarket; "Auction Houses" is marketing copy with **no specific house named** anywhere on site or in JS bundle | Bearer-token REST at `https://www.pokedata.io/api/…`; `/api/sets` and `/api/products` are **anonymous + free** (latter includes `market_value` for sealed); per-card price history is Platinum-only | Gold $8/mo (~$60/yr); Platinum $20/mo (~$200/yr); API access **Platinum only** | **Defer** — API terms forbid commercial/redistributive use, which is our use case |
| **Cardmarket free price guide** | EU marketplace listings/sales | Anonymous JSON download at `https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json` (idGame=6 = Pokémon) | **Free**, no auth | **Add now** — explicit TOS carve-out for app use, daily updates, EN + JP coverage |
| **Cardmarket REST API** | Same data + live marketplace | OAuth 1.0a HMAC-SHA1; commercial accounts only; new applications **currently paused** | Commercial Cardmarket account | **Skip** — public download supersedes it |
| **PWCC / Fanatics Collect** | Their + eBay-sourced sales archive back to 2004 | Public SPA at `sales-history.fanaticscollect.com`; backend API is bearer-token-gated (`sales-history-api.services.fanaticscollect.com`, 401 anonymous); Cloudflare bot challenge active | Free web UI; no public API tier | **Skip** — TOS §3.5/§9.3.7/§9.3.8 triple-bans scrapers + auth wall + Cloudflare |
| **Goldin** | Their own auction lots | Public web UI at `goldin.co/buy`; robots allows `/buy/` `/item/` `/auction/`; **TOS bans automated access + commercial use of data** | Free web UI | **Skip** — TOS forbids commercial-purpose scraping |
| **Heritage Auctions** | Their own auction lots | Public web archive at `ha.com` with 15s crawl-delay; **TOS explicitly bans screen scraping, database scraping, robots, spiders** | Free web UI; no API | **Skip** — most aggressive TOS of the four houses |
| **Robert Edward Auctions** | REA archive (sports-card-first house) | Permissive robots.txt; **TOS limits to "personal use" only**, "Use of REA Content on any other site or in a networked environment is prohibited" | Free | **Skip** — Pokémon volume too low + personal-use license |
| **PSA Auction Prices Realized** *(bonus)* | Aggregated from "leading platforms like eBay and Goldin" | Public web pages + PSA Public API (registration required) | Free tier exists | **Add later** — best free PSA-graded comp source |
| **eBay Marketplace Insights API** | eBay sold listings, **last 90 days** only | Restricted-release REST; production access requires Application Growth Check approval and is "only for certain verticals" — realistically closed to a hobbyist Pokémon project | Free if approved | **Apply but don't block on it** |
| **eBay Browse API** | Active listings only — **no sold-data filter exists**; legacy `findCompletedItems` deprecated | Standard OAuth | Free | **Keep for active comps only** |
| **eBay Terapeak Research** | eBay sold listings, **3-year** history, includes Best Offer accepted prices | Seller Hub web UI; **no official API**; Terapeak Subscription Terms explicitly ban "robot, spider, scraper or other automated means" | Free for any Seller Hub seller; Sourcing Insights needs Basic Store (~$22/mo) | **Use manually for calibration** — automation is contractually prohibited |
| **Algopix** *(bonus, only viable wrapper)* | eBay + Amazon + Walmart | REST API on paid tiers | From ~$29/mo with API | **Add later if 90-day MI window proves insufficient** |

---

## 2. Per-Source Detail

### 2.1 PriceCharting

**robots.txt** — retrieved verbatim from `https://www.pricecharting.com/robots.txt`:
```
User-agent: *
Disallow: /stripe-connect
Disallow: /publish-offer
Disallow: /buy
```
No path-level block on `/api/`, `/game/`, `/console/`, `/category/`, or `/offers`. No `Crawl-delay`. Bot UAs are nonetheless 403'd at the edge on the apex domain (likely Cloudflare WAF), while `blog.pricecharting.com` responds normally.

**Methodology** — confirmed from the (accessible) blog subdomain:

- [2010 algorithm post](https://blog.pricecharting.com/2010/09/new-pricing-algorithms.html):
  - Default (~87% used / 71% new): "the median sales price during the last 2 weeks"; if none, "the most recent sale"; then median across "lowest listing prices from Amazon and Half.com" and the eBay value.
  - High-volume games (~1%): algorithm "dynamically adjusts between one, three, seven and fourteen days worth of sales".
  - Rare games (~4% used / 7% new): "the most recent sale" used directly.
  - "JJGames prices received dramatically reduced weight."
- [2008 median-vs-mean post](https://blog.pricecharting.com/2008/12/average-or-median-which-to-choose.html): rationale for median — outliers like Nintendo World Championships were skewing averages by $5–$6.
- [2022 update](https://blog.pricecharting.com/2022/01/update-to-how-new-prices-are-calculated.html): "if the sealed condition sale is more than one year old we will look to see if the CIB price for that same game has changed and adjust our estimate of the sealed price." Affects ~20% of NES/SNES/N64 sealed.

The algorithm is video-game-first; the company does not publicly document a separate Pokémon variant.

**API** (compiled from blog post + [dlthub source spec](https://dlthub.com/context/source/pricecharting); the canonical `/api-documentation` page itself 403'd):
- Base URL: `https://www.pricecharting.com` (sister `https://www.sportscardspro.com`, identical schema).
- Auth: 40-character token in `?t=…` query parameter. Requires paid (Legendary) subscription.
- Endpoints: `GET /api/product`, `/api/products`, `/api/offers`, `/api/offer-details`, `/api/game`, `/api/sales`; `POST /api/offer-publish`, `/api/offer-feedback`, `/api/offer-ship`, `/api/offer-end`, `/api/offer-refund`.
- Sample `/api/product` response (recovered via search-snippet):
  ```json
  {"status":"success","id":"6910","product-name":"EarthBound","console-name":"Super Nintendo",
   "release-date":"1995-06-05","loose-price":17244,"cib-price":42995,"new-price":53000}
  ```
  Prices are integer pennies (`17244` = `$172.44`). Graded cards expose PSA/BGS/CGC/SGC grades 1–9.5 plus the four "10" tiers (per [June 2024 CGC/SGC support post](https://blog.pricecharting.com/2024/06/support-for-cgc-and-sgc-graded-cards.html)).
- Rate limits: not surfaced in any retrieved page. Third-party scrapers warn that running multiple workers risks IP throttling.

**Pricing tiers** (snippet-sourced; `/pricecharting-pro` itself 403'd):
- **Premium**: $4.99/mo or $39.99/yr — collection tracking, bulk import/export, sales history.
- **Legendary**: gates CSV downloads, API access, GameStop buy/sell prices, retail integrations. **Dollar amount not retrievable without a browser session.**

**TOS — data license** (verbatim from snippets of `/page/guide-terms-of-service`):
> "Price Data can be used for your Internal Business Purposes if you maintain a valid and current Legendary subscription."
> "Internal business purposes" = "usage by the subscriber and their authorized employees or contractors, strictly within the organization, and not for external display or redistribution."
> "Price Data cannot be used in any software, application, or system that is accessible to third parties, including customers, clients, or the general public, without express written permission."

The data license — not the scraping clause — is what blocks us. A pipeline that surfaces PriceCharting prices to listing buyers, even via an integration, is the precise scenario the language prohibits.

**Live page inspection** — direct fetches of `/game/pokemon-base-set/charizard-4` returned 403 on every attempt. Indirect inspection of third-party scrapers reveals stable element IDs (`<td id="used_price">`, `<td id="complete_price">`, `<td id="new_price">`, each containing `<span class="price">`). Pokémon set lists appear to be partially JS-loaded — third-party reports note "the data wasn't exposed in an accessible JSON format" in DevTools.

**Verdict**: **Defer**. Methodology is confirmed and aligned with what our own aggregation would want, but the data-license language explicitly bars our use case, and the API requires the Legendary tier whose price we cannot read without a manual browser visit.

### 2.2 Pokedata.io

**Direct access works** — `/pro`, `/api-terms`, `/termsandconditions`, and `/api/sets` all returned HTTP 200 to subagent fetches (the original WebFetch failures were environment-specific). Marketing pages `/about` and `/contact-us` 404 — they don't exist.

**Data sources** — the answer is genuinely "eBay + TCGPlayer + Cardmarket; nothing else specific."

Grepping the production JS bundle (`_next/static/chunks/pages/_app-4077891d40aec37b.js`) for `Goldin`, `PWCC`, `Fanatics`, `Heritage`, `Sotheby`, `auction` (word-boundary): **zero hits**. The bundle's internal price-category enum is:

```
Raw: range[0,0]         // eBay raw
PSA 1–10
TCGPlayer: range[11,11]
CardMkt:   range[11.1,11.1]   // EUR
eBay:      range[12,12]
CGC 1–10
BGS 1–10
```

There is no "Auction" / "Goldin" / "PWCC" category in the displayed model. The "Auction Houses, etc." marketing line is unsupported by any concrete name on their site, in their terms, in their app listings, or in their front-end code. Confirmed sources: eBay (Raw + a distinct "eBay" series), TCGPlayer (USD), CardMarket (EUR).

**API** — backend URL is the site domain itself, `https://www.pokedata.io`:

- Auth: `Authorization: Bearer <JWT>` issued by `/auth/login` and refreshed by `/auth/refresh` (or `apple_login` / `google_login` / `fb_login` variants).
- **Anonymous (free) endpoints**:
  - `GET /api/sets` — 414 KB JSON, 689 sets with `id`, `code`, `name`, `series`, `tcg`, `language`, `release_date`, image URLs.
  - `GET /api/products` — 3.4 MB JSON, every sealed product **including `market_value`**.
- **Authenticated endpoints**: `/api/chart`, `/api/list`, `/api/list/items`, `/api/portfolio[/holdings|/transactions|/value]`, `/api/pricealert`, `/api/notification`, `/api/marketplace/cart[/items|/claim]`, `/api/affiliate/visit`, `/api/currency`, `/user/info`, `/user/changecurrency`, etc.
- Search backend: Elastic App Search at `https://pokedata-01.ent.us-east1.gcp.elastic-cloud.com`, indexes `pokedata-card-03` / `pokedata-product-01-2`, engine key `search-3vnnhp2jkqsqgfm5wbqhii72` — called directly from the client.
- Postman documenter URL (`documenter.getpostman.com/view/16115980/2sA3JF9iWW`) is JS-rendered; the gateway endpoint returns only the collection `info` block (name `pokedata_api`, owner 16115980, published 2024-05-01) without the `item` array, so the full endpoint spec needs an authenticated Postman session.
- Rate limits: not documented anywhere.

**Pricing** (verbatim from `/pro`):
- Free: 6 months pricing data, 1 portfolio, card scanning, PSA pop report, PSA & CGC pricing.
- **Gold: $8/month**; yearly plan saves $36 ⇒ ~$60/yr.
- **Platinum: $20/month**; yearly plan saves $40 ⇒ ~$200/yr.
- "Personal API Access (see docs)" is listed **exclusively under Platinum**.

**API Terms — verbatim** (`https://www.pokedata.io/api-terms`):
> "The current offering of Pokedata Platinum is limited to non-commercial and personal use."
> "You cannot sell, lease, or sublicense the APIs or access thereto or derive revenues from the use or provision of the APIs, whether for monetary gain or not unless there is express written approval from Pokedata (Browse LLC)."
> "You cannot use the APIs in any manner that is competitive to Pokedata (Browse LLC) or its affiliates."
> "You cannot redistribute Data from the Pokedata API, website, or application for any reason."
> "You agree to only access (or attempt to access) APIs described in the Documentation."

**Methodology**: not published anywhere. Searched the homepage, `/pro`, `/api-terms`, `/termsandconditions`, App Store / Google Play listings, the Postman doc metadata, and the entire `_app.js` bundle — no mention of `median`, `weighted`, `aggregate`, `recency`, or any computation description.

**Live page inspection**: card detail pages (e.g. `/card/Chaos+Rising/Mega+Greninja+116`) are Next.js SPA shells; static HTML contains only `<script id="__NEXT_DATA__">{"props":{"pageProps":{}}}</script>` with no inlined pricing. Anonymous visitors see a "Log In For The Latest Data" wall in place of the price view.

**Verdict**: **Defer** — and the defer is now stronger, not weaker:
1. No evidence of unique auction-house signal — their internal price model only enumerates eBay/TCGPlayer/CardMkt + grader columns.
2. API Terms explicitly forbid our use case ("limited to non-commercial and personal use", "cannot redistribute Data… for any reason", "in any manner that is competitive").
3. No documented methodology — paying for an undocumented black box with redistribution barred has no upside vs. hitting eBay/TCGPlayer/CardMarket directly.

The one minor upside: `/api/sets` and `/api/products` are anonymously readable and contain reasonable catalog metadata + sealed-product `market_value`. Redistribution is still TOS-prohibited, but for **internal calibration** it's a free reference. Worth bookmarking, not building on.

### 2.3 Auction Houses

#### Goldin (`goldin.co`)

**robots.txt** (verbatim):
```
User-agent: *
Disallow: /account/
Disallow: /static/
Disallow: /api/
Disallow: /assets/
Disallow: /static/js/client.\w+.js
Disallow: /versions.txt
Allow: /buy/
Allow: /fixed-price/item/
Allow: /item/
Allow: /auction/
Allow: /about/

Sitemap: https://www.goldin.co/sitemap.xml
```
Public lot pages are technically allowed; the internal API is blocked.

**URL structure**: `https://goldin.co/buy?Sub_Category=Pokemon&page=N&sort=Highest_Bids` etc. — `Sub_Category`, `Item_Type`, `Certification`, `search`, `page`, `sort`, `number_of_lots` are the visible filters. Detail pages at `/item/<slug>`. Sold-items filter is a UI toggle on `/buy`. The page itself is a React Native Web SSR shell — values render client-side.

**TOS — User Agreement** (verbatim from rendered React content at `https://goldin.co/useragreement`):
> "use any robot, spider, scraper, data mining tools, data gathering and extraction tools, or other automated means to access our Services for any purposes, except with the prior express permission of Goldin;"
> "commercialize any Goldin application or any information, data, or software associated with such application, except with the prior express permission of Goldin;"
> "harvest or otherwise collect information about users without their consent."

**Verdict**: TOS bans both the act of scraping and the commercial use of any Goldin data without express permission. Both clauses target our exact use case. **Skip.**

#### PWCC / Fanatics Collect

**Visible XHR endpoints** — extracted from `window.env` in `https://sales-history.fanaticscollect.com/` page source:
- `REACT_APP_API_URL`: `https://sales-history-api.services.fanaticscollect.com`
- `AUTH_URL`: `https://token.api.fanaticscollect.com`
- `SEARCH_URL`: `https://universal-search.api.fanaticscollect.com`
- `GRAPHQL_API_URL`: `https://app.fanaticscollect.com/graphql`
- `AUCTIONS_URL`: `https://premier-auction-api.fanaticscollect.com`
- `MEMBERS_API_URL`: `https://members.api.fanaticscollect.com`
- `CHECKOUT_API_URL`: `https://checkout.api.fanaticscollect.com`
- `MOBILE_URL`: `https://mobile.api.fanaticscollect.com`

Anonymous probes: `https://sales-history-api.services.fanaticscollect.com/` and `/api/v1/items?title=charizard` both return **HTTP 401 Unauthorized**. `/search` returns 202 with an empty body. Token URL is `https://token.api.fanaticscollect.com`. Cloudflare bot challenge (`/cdn-cgi/challenge-platform/scripts/jsd/main.js`) is injected into the SPA.

**robots.txt** at `https://www.fanaticscollect.com/robots.txt`:
```
User-agent: *
Disallow: /login
Disallow: /join
Disallow: /i-am-not-a-robot
Disallow: /share/
Disallow: /email/verify/*
Disallow: */.well-known/assetlinks.json

Sitemap: https://www.fanaticscollect.com/sitemap.xml
```

**TOS — verbatim from `https://support.fanaticscollect.com/en_us/terms-of-use-r11C70QTge`** (last updated September 11, 2025):
> §3.5: "use any manual or automated software, devices or other processes (including but not limited to spiders, robots, scrapers, crawlers, avatars, data mining tools or the like) to 'scrape' or download data from any portion of the Platform and/or Services;"
> §9.3.7: "Make any automated use of the Platform and/or any Services… or take any action that imposes or may impose… an unreasonable or disproportionately large load on the infrastructure of the Platform and/or any Services;"
> §9.3.8: "Bypass any robot exclusion headers or other measures Fanatics Collect takes to restrict access to the Platform and/or Services, or use any software, technology, or device to send content or messages, scrape, spider or crawl on the Platform, or harvest or manipulate data;"

**Verdict**: triple-redundant TOS prohibition + 401-gated API + Cloudflare bot challenge. Highest-value Pokémon sales archive in the hobby is the most locked down of any source we examined. **Skip.**

#### Heritage Auctions (`ha.com`)

**robots.txt** (summarised — Heritage's file is long): 15-second `Crawl-delay` for `*`; aggressive named-bot blocks (SemrushBot, PetalBot, Amazonbot, etc. fully blocked). Allows generic `/search/` paths but with the crawl-delay throttle.

**TOS — Website Use Agreement** (verbatim from `https://www.ha.com/c/ref/website-use-agreement.zx`, retrieved via Wayback after direct fetches 403'd):
> "engage in any collection of data, through such practices as 'screen scraping,' 'database scraping,' 'robot,' 'spider' or other automatic means;"
> "attempt to copy any aspect of this Website's content, services, descriptions, images, or code into any other Website for commercial or malicious purposes, or to distribute any such content for commercial gain;"
> "Any unauthorized usage of the Website may subject You to civil or criminal prosecution."

**Verdict**: most aggressive TOS of any source examined. Anonymous requests are 403'd at the edge. Heritage has sued competitors over scraping in the past. **Skip — do not consider scraping under any circumstances.**

#### Robert Edward Auctions

**robots.txt** at `https://robertedwardauctions.com/robots.txt`:
```
User-agent: *
Disallow:
```
Permissive. Same on `https://collectrea.com/robots.txt`.

**TOS — verbatim from `https://collectrea.com/terms`** (last updated February 17, 2026):
> "REA hereby grants you a limited, revocable, non-sublicensable license to reproduce and display the REA Content solely for your personal use in connection with viewing our Website and using our services. You may not sell or modify REA Content or reproduce, display, distribute, or otherwise use REA Content in any way for any public or commercial purpose. Use of REA Content on any other site or in a networked environment is prohibited."

No explicit anti-bot clause, but the content license is personal-use-only and forbids any networked-environment use — directly incompatible with a pricing pipeline. Pokémon volume is also low (REA is sports-card-first). **Skip.**

#### Auction-house aggregate verdict

Every house bars our use case either by TOS, by technical wall, or by both. Even though Fanatics Collect's sales-history archive is the single best Pokémon-sales dataset in the hobby (back to 2004, eBay-sourced), the right move for premium-tier cards is to **request a partnership / data license** directly from Goldin or Fanatics — every TOS examined leaves a "with the prior express permission of [company]" door open. We stay with eBay + TCGPlayer + CardMarket for the bulk pipeline.

### 2.4 CardMarket

**Free anonymous JSON download** — the key actionable finding.

**URL**: `https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json` (Pokémon, `idGame=6`).
**Format**: JSON (not CSV, despite the historical naming).
**Auth**: **none** — anonymous HTTPS GET.
**Size**: >10 MB (subagent's WebFetch capped out; file exists and is large).
**Frequency**: daily ("The price guide is updated once daily and the product catalogue is updated whenever a new product is added").
**Fields** (per `https://api.cardmarket.com/ws/documentation/API_2.0:PriceGuide`): `idProduct`, `Avg. Sell Price`, `Low Price`, `Trend Price`, `German Pro Low`, `Suggested Price`, `Foil Sell`, `Foil Low`, `Foil Trend`, `Low Price Ex+`, `AVG1`, `AVG7`, `AVG30`, plus foil-AVG variants. Prices in EUR.
**Coverage**: EN and JP Pokémon — per the [Sep 2024 announcement](https://news.cardmarket.com/en/Pokemon/japanese-pokemon-singles-are-up-to-date-on-cardmarket), JP cards live under separate `idProduct`s with "JP" on the title banner. They will not auto-merge with EN entries — we'll need a join table to TCGplayer/Scryfall IDs.

**Product catalogue companion file**: `https://downloads.s3.cardmarket.com/productCatalog/productList/products_6.json` — same path pattern, contains expansion + name metadata for the join.

**TOS** — verbatim, the relevant carve-out language:
> "Anyone can import and incorporate Cardmarket's product and price data into their own applications with no extra permission or access point necessary through their publicly available product catalog and price data exports."
> "The presentation of the trading cards and their respective prices require prior written agreement."
> "The API may only be used for managing your own contents, and the use of the API and the transfer and use of data for any other purpose is prohibited."

The public download is **explicitly endorsed** for application use. Re-displaying raw Cardmarket prices in a public UI is the one thing that would still need written agreement.

**REST API**: still exists at `https://api.cardmarket.com/ws/v2.0/`, OAuth 1.0a HMAC-SHA1, but:
- 3rd-party apps are restricted to commercial accounts.
- **Cardmarket is currently not accepting new API applications.**
- The live `/priceguide` endpoint was deprecated on 2024-06-05 in favor of the public download.
- Rate limits (when usable): Dedicated/private 5k req/day, commercial 100k, powerseller 1M.

**Verdict**: **Add now (CSV/JSON ingest)**. Gotchas to plan for:
1. The file is **JSON not CSV** — `requests.get(url).json()`.
2. >10 MB payload — stream if memory-constrained.
3. Prices are EUR — needs FX conversion step.
4. Cardmarket `idProduct` keyspace needs a join to TCGplayer/Scryfall IDs (use `products_6.json`).
5. Do not surface raw Cardmarket prices in a public UI without written permission — internal/derived use is fine.

### 2.5 eBay Completeness

**Marketplace Insights API**:
- Time horizon: 90 days, confirmed.
- "Limited Release API available only to select developers approved by business units."
- Required scope: `https://api.ebay.com/oauth/api_scope/buy.marketplace.insights` (Client Credentials).
- Application path: Application Growth Check at `https://developer.ebay.com/my/support/tickets?tab=app-check`.
- As of late 2025 / early 2026, approvals are "very difficult … only for certain verticals/meta-categories" — multiple eBay Community denials documented in 2025. The Findings API (the legacy free path for sold data) was discontinued in early 2025 and redirected users to MI, but eBay did **not** open MI to general developers as part of that migration.
- Sandbox is open immediately for development; production realistically unreachable for a hobbyist Pokémon project.

**Browse API as a fallback**: **does not return sold data**. The historical `findCompletedItems` + `soldItemsOnly` filter from the legacy Finding API are deprecated/removed. Browse filters cover active inventory only (listing format, condition, price range, UPC, end date, location, seller).

**Terapeak Research**:
- Free for **all Seller Hub sellers** — no Store subscription required (per [eBay help id=4853](https://www.ebay.com/help/selling/selling-tools/terapeak-research?id=4853)).
- **3-year sold-history** depth, including the actual price on Best Offer accepted listings.
- Terapeak Sourcing Insights (separate product) needs Basic Store (~$22/mo) — adds category-level trend discovery, not per-SKU lookups.
- **No official API.** Multiple eBay Community threads explicitly confirm this. The closest official path remains Marketplace Insights (90 days, gated).

**Sell Analytics API**: exposes only the authenticated seller's own metrics — `customer_service_metric`, `traffic_report`, `seller_standards_profile`. Not a Terapeak substitute.

**TOS / scraping clauses**:
- eBay User Agreement (effective Feb 20 2026): bans "any robot, spider, scraper, data mining tools, data gathering and extraction tools, or other automated means (including, without limitation buy-for-me agents, LLM-driven bots, or any end-to-end flow that attempts to place orders without human review) to access our Services for any purpose, except with the prior express permission of eBay."
- Terapeak Subscription Terms (`https://pages.ebay.com/terapeak/subscriptionterms.html`): bans "any robot, spider, scraper or other automated means to access the Terapeak Service for any purpose." **Browser-automating Terapeak is named-and-banned, not a grey area.**

**Third-party paid wrappers**:
- **Algopix** — free plan (10 daily searches); paid from $29/mo with API access for bulk queries. Multi-marketplace. Closest thing to a clean programmatic Terapeak-style alternative.
- **ZIK Analytics** — $19.90 month 1, $39.90/mo ongoing. UI-driven; no real API.
- Apify "eBay Sold Listings" actor — pay-per-result scraper, same TOS risk as any scrape.
- SerpApi eBay Search API — search-engine snapshot, primarily active listings, ~$50/mo+.

**Verdict**: design the pipeline around the **90-day MI window** as the upper bound. Submit an Application Growth Check (free, low cost to try). Use Terapeak manually for spot calibration; do not automate it. If multi-year history becomes load-bearing, **Algopix** is the only defensible paid wrapper.

---

## 3. Final Recommendation

**Add now (concrete next step):**
- **Cardmarket free price guide JSON** — single anonymous GET to `https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json`, daily updates, EN + JP coverage, TOS-blessed for app use. **Highest yield, lowest effort change to the pipeline.**

**Apply (low-cost speculative):**
- Submit an eBay **Application Growth Check** request for Marketplace Insights production access. Free; approval is a long shot but worth the 30 minutes.

**Use manually (no automation):**
- **eBay Terapeak Research** for low-confidence-card spot calibration. 3-year history, free for any Seller Hub seller, but contractually un-automatable.
- **Pokedata.io `/api/sets` + `/api/products`** (the anonymous endpoints) as a free internal-reference catalog with sealed `market_value` — don't redistribute.

**Defer:**
- **PriceCharting Legendary tier** — needs a manual browser visit to read the price tag before any buy decision. Even at a reasonable price, data-license forbids public re-display.
- **Pokedata.io Pro/Platinum** — API terms explicitly bar our commercial/redistributive use; methodology unpublished; no auction-house signal actually exists in their data model.
- **Algopix** — revisit only if the 90-day MI window proves load-bearing-too-short in practice.

**Skip:**
- **PWCC / Fanatics Collect, Goldin, Heritage, REA** — all four bar our use case via TOS, technical walls, or both. For premium-tier cards specifically, the right path is a **direct partnership / data-license request** to Goldin or Fanatics, not scraping.
- **Cardmarket REST API** — superseded by the free public file download.

---

## 4. Legal / TOS Flags

Strict TOS bans on automated access (do not scrape):

- **Fanatics Collect** (verbatim, [terms of use last updated 2025-09-11](https://support.fanaticscollect.com/en_us/terms-of-use-r11C70QTge)): §3.5 bans "spiders, robots, scrapers, crawlers, avatars, data mining tools or the like" to "'scrape' or download data from any portion of the Platform"; §9.3.8 bans "bypass any robot exclusion headers or other measures Fanatics Collect takes to restrict access".
- **Goldin** (verbatim, `https://goldin.co/useragreement`): bans "robot, spider, scraper, data mining tools, data gathering and extraction tools, or other automated means" and "commercialize any Goldin application or any information, data, or software associated with such application, except with the prior express permission of Goldin."
- **Heritage Auctions** (verbatim, `https://www.ha.com/c/ref/website-use-agreement.zx`): bans "screen scraping, database scraping, robot, spider or other automatic means" and "to copy any aspect of this Website's content… for commercial gain"; threatens "civil or criminal prosecution."
- **eBay User Agreement** (Feb 2026): bans "any robot, spider, scraper, data mining tools… buy-for-me agents, LLM-driven bots, or any end-to-end flow that attempts to place orders without human review."
- **eBay Terapeak Subscription Terms**: bans "any robot, spider, scraper or other automated means to access the Terapeak Service for any purpose." Named-and-banned.
- **Robert Edward Auctions** (verbatim, `https://collectrea.com/terms`, last updated 2026-02-17): "limited, revocable, non-sublicensable license to reproduce and display the REA Content solely for your personal use… Use of REA Content on any other site or in a networked environment is prohibited."

Use-restricted but TOS-blessed for our case:

- **Cardmarket** (verbatim): "Anyone can import and incorporate Cardmarket's product and price data into their own applications with no extra permission or access point necessary through their publicly available product catalog and price data exports." — but "The presentation of the trading cards and their respective prices require prior written agreement", so internal/derived use only without explicit permission.

Restrictive data license even if access is paid-and-clean:

- **PriceCharting Legendary** (snippet-verbatim): "Price Data cannot be used in any software, application, or system that is accessible to third parties, including customers, clients, or the general public, without express written permission."
- **Pokedata.io Platinum** (verbatim, [api-terms](https://www.pokedata.io/api-terms)): "limited to non-commercial and personal use"; "You cannot redistribute Data from the Pokedata API, website, or application for any reason"; "You cannot use the APIs in any manner that is competitive to Pokedata (Browse LLC) or its affiliates."

---

## 5. Honesty Section — Remaining Gaps

What's now genuinely verified (page actually retrieved with content read):
- PriceCharting `robots.txt`, all blog posts on the methodology, dlthub spec for endpoint list.
- Pokedata.io `/pro`, `/api-terms`, `/termsandconditions`, `/api/sets`, `/api/products`, and the production JS bundle (grepped for auction-house names).
- Cardmarket public price-guide JSON URL (file confirmed live, >10 MB), `API_2.0:PriceGuide`, `API:Auth_Overview`, `API_2.0:Response_Codes`.
- Goldin `robots.txt`, user agreement (via SPA renderer), live `/buy` page.
- Fanatics Collect sales-history env vars, `www.fanaticscollect.com/robots.txt`, full TOS at `support.fanaticscollect.com`. API confirmed 401-gated.
- Heritage `robots.txt`; Website Use Agreement quoted from Wayback after live 403s.
- REA `robots.txt` (both domains) and full terms at `collectrea.com/terms`.
- eBay User Agreement (Feb 2026), Terapeak Subscription Terms, `robots.txt` — all quoted from snippets that mirror canonical pages (every direct fetch of `developer.ebay.com` and `community.ebay.com` 403'd or timed out, consistent with the anti-bot clauses themselves).

What I still **could not** verify (a human with a logged-in browser is needed):
- **PriceCharting Legendary tier dollar amount** — the only number that gates the buy/no-buy decision for that source.
- **PriceCharting full `/api/product` field list for Pokémon TCG** — the dlthub spec confirms the endpoint shape but the Pokémon-specific field set (graded variants, language) wasn't extractable.
- **Pokedata.io Postman collection `item` array** — needs an authenticated Postman session.
- **Cardmarket `idProduct` → TCGplayer/Scryfall mapping** — the `products_6.json` companion file exists at the documented path but I didn't open it to confirm field shape.
- **Marketplace Insights production approval outcomes for hobbyist Pokémon-focused apps in 2026** — second-hand only; the only way to know is to file the Application Growth Check.

The "no specific auction houses cited by Pokedata.io" finding — confirmed by grepping their production JS bundle — is the single most counter-intuitive update from this round. It substantially weakens the case for paying for Pokedata.io Platinum.
