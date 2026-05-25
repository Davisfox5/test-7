# Pokémon Pricing Source Investigation

Investigation of additional pricing sources on top of the existing eBay + TCGPlayer (pokemontcg.io) pipeline. All claims cite real URLs; anything I could not personally fetch is flagged in the Honesty section.

## 1. Summary Table

| Source | Underlying data | Access method | Cost | Recommendation |
|---|---|---|---|---|
| **PriceCharting** | eBay sold listings + own marketplace; outlier-filtered, weighted average | Paid REST API (`/api/product`, token in `t` param) + scraping (TOS-restricted) | Paid subscription (tiers not publicly priced; "Legendary" needed for CSV) | **Defer** — duplicates eBay; useful as a sanity-check oracle |
| **Pokedata.io** | eBay + TCGPlayer + Cardmarket + auction houses, Raw/PSA/CGC splits | Official API only on Pro **Platinum** tier (documented via Postman); web UI is JS-heavy | Pro subscription, Platinum tier required for API | **Defer** — best value-add is the auction-house + grader-split signal; revisit after MVP |
| **Cardmarket** | EU marketplace listings/sales | Free **daily CSV download** of price guide + product catalogue (no API key); REST API (OAuth 1.0a) requires commercial account approval | Free for CSV; commercial account for live API | **Add now (CSV ingest only)** — cheap EU comp, no auth |
| **PWCC / Fanatics Collect** | PWCC + (per blog) eBay-sourced sales history archive going back to 2004 | Public web UI at `sales-history.fanaticscollect.com`; no published API | Free for web UI | **Defer** — scraping is ToS-restricted; high-end auction comps rarely move our bulk listings |
| **Goldin** | Their own auction lots | Public archive search at `goldin.co`; no public API | Free for web UI | **Skip for bulk** — high-end only; manual reference only |
| **Heritage Auctions** | Their own auction lots | Public web archive at `ha.com`; **TOS explicitly bans scraping** | Free for web UI; no API | **Skip** — TOS prohibits automated access |
| **Robert Edward Auctions (REA)** | Their own auction lots; collectrea.com archive | Public web archive | Free for web UI | **Skip** — REA's Pokémon volume is too small to justify a pipeline integration |
| **PSA Auction Prices Realized (APR)** (bonus) | Aggregated auction results from "leading platforms like eBay and Goldin" | Public web pages + PSA Public API (registration required) | Free tier exists | **Add later** — best free source for PSA-graded comps |
| **eBay Marketplace Insights API** | eBay sold listings, last 90 days | REST, restricted-release approval gate | Free if approved (not open to new users) | **Skip access path; keep Browse fallback** |
| **eBay Terapeak Research** | eBay sold listings, **3-year** history | Web UI inside Seller Hub, no official public API | Free for any Seller Hub seller; "Sourcing Insights" needs Basic+ Store | **Add now (manual / scrape via authenticated session)** — biggest historical win |

---

## 2. Per-Source Detail

### 2.1 PriceCharting

**Methodology** (search-snippet-sourced from `pricecharting.com/page/methodology` and the official FAQ; direct WebFetch was blocked with HTTP 403):

- "PriceCharting collects sold listing data from eBay and its own video game Marketplace and runs this information through proprietary algorithms to calculate the current market price." (per search snippet of [methodology page](https://www.pricecharting.com/page/methodology))
- The algorithm considers "most recent sale price, median price, average price, age weighted average price, and more. The algorithm takes outliers into account and the date when sales occurred." (per search snippet)
- "Junk" filtering: uses an automated language model to drop listings flagged as Reproduction / For Parts / Broken / mislabelled lots. (per snippet of FAQ)
- Prices exclude shipping.

**Upstream sources named**: eBay sold listings, plus the PriceCharting Marketplace itself. No third-party aggregators cited.

**API** (per snippets of [api-documentation](https://www.pricecharting.com/api-documentation) and [the 2014 launch blog post](https://blog.pricecharting.com/2014/03/pricecharting-api.html)):

- Auth: 40-character token passed as `t` query parameter.
- Endpoint: `GET /api/product` — returns a JSON object equivalent to one row in the CSV price guide, prices encoded as integer pennies, dates as `YYYY-MM-DD`, every response has a `status` key.
- Other endpoint family: Marketplace API (`/api/.../offer-publish`, etc.) for sellers — not relevant to comp pricing.
- **Rate limits**: 1 call / second on the product endpoint; CSV endpoints limited to 1 / 10 min (per snippet).
- **Pricing**: requires a paid subscription. Specific dollar tiers were not exposed in any cached snippet — the `pricecharting-pro` page is behind the same 403 wall. CSV/bulk download is restricted to the "Legendary" tier (per snippet).

**robots.txt / TOS**:
- `https://www.pricecharting.com/robots.txt` — I could not fetch this. WebFetch returned 403; a direct `curl` with a browser UA also returned HTTP 403. **Not verified.**
- Terms of service at `https://www.pricecharting.com/page/terms-of-service` is the public TOS URL. Per snippets, it forbids using the service in ways prohibited by the terms but I could not pull the exact scraping clause verbatim.
- A third-party Python scraper README explicitly notes "Running many workers simultaneously may result in your IP being temporarily rate-limited by pricecharting.com" and recommends "PriceCharting's official API for programmatic access at scale" ([markfoster314/Pricecharting-Scraper](https://github.com/markfoster314/Pricecharting-Scraper)).

**Does the paid API expose anything raw eBay doesn't?**
- The pricing aggregate itself (their cleaned median / weighted average) is novel — but it's derived purely from eBay + their marketplace. If we already have eBay sold data, the marginal value is (a) their outlier filter and (b) their grade-condition split. Both are reproducible from eBay's data with sufficient engineering.
- Conclusion: useful as a **sanity-check oracle**, not as a primary source.

### 2.2 Pokedata.io

**Direct fetches**: `/about`, `/api-terms`, `/pro`, `/` all returned HTTP 403 to both WebFetch and `curl` with a browser UA — the site sits behind a bot wall and likely also requires JS. **No HTML content directly observed.**

**Data sources** (per search-snippet summaries of [pokedata.io](https://www.pokedata.io/) and the Apple App Store [listing](https://apps.apple.com/us/app/pokedata-io/id6504906730)):

- "compiling current prices and price history from multiple markets, including eBay, TCGPlayer, CardMarket, and various Auction Houses" (per snippet).
- Grader splits for Raw / PSA / CGC.
- I could **not** verify the specific auction houses they pull from (Goldin? PWCC? Heritage?) — snippets only say "various Auction Houses."

**API access** (per [Postman documentation](https://documenter.getpostman.com/view/16115980/2sA3JF9iWW), which I could not WebFetch):

- API access listed as available "for personal use" to Pro **Platinum**-tier subscribers (per snippet of [/pro](https://www.pokedata.io/pro)).
- Subscription dollar amounts and API rate limits not exposed in cached snippets — both were behind 403.
- The Postman public collection URL exists; the schema would need to be inspected with an account or via a browser session.

**Third-party indicator**: [Zitronenjoghurt/pokedata-api](https://github.com/Zitronenjoghurt/pokedata-api) is a separate Rust project that compiles its own dataset and does **not** appear to call pokedata.io. So it is not a useful proxy for pokedata.io's actual endpoints.

**Unique signal vs. derivable**: The unique value is the *combination* of eBay + TCGPlayer + Cardmarket + auction houses in one normalized schema with grader splits. If we already aggregate eBay + TCGPlayer ourselves, the marginal value is Cardmarket (which we can get free elsewhere — §2.4) plus auction house comps (which they don't enumerate publicly). The API-terms page being unreadable also makes commercial redistribution risk unknown.

### 2.3 Auction Houses

#### PWCC / Fanatics Collect
- Branding migrated: PWCC was acquired by Fanatics in 2023; the sales-history tool is now hosted at [`sales-history.fanaticscollect.com`](https://sales-history.fanaticscollect.com/) (the old `sales-history.pwccmarketplace.com` URLs still resolve, per a search snippet).
- Per [the Fanatics Collect newsroom post](https://www.fanaticscollect.com/newsroom/pwcc-updates-sales-history-tool), the archive is positioned as "the world's largest archive of trading card sales… dating back to 2004 and containing over 200 million individual records… launched in partnership with eBay" (per snippet) — i.e., much of the archive is **eBay-sourced**, not PWCC-exclusive.
- **No documented public API.** A URL like `https://sales-history.fanaticscollect.com/?type=undefined` is visible in search results, implying simple query-string filtering, but the underlying XHR/JSON endpoint would need to be sniffed in a browser.
- **TOS**: Fanatics' terms of use "prohibit using any automated system or software to extract data from the Properties for commercial purposes (including 'screen scraping')" (per snippet of [fanatics.com terms](https://www.fanatics.com/fanatics-terms-of-use/x-6455+z-87173304-1397704712); I could not fetch the page directly).
- Verdict: scraping is contractually disallowed; archive is largely eBay-derived anyway.

#### Goldin (`goldin.co`)
- Completed-auction lots are publicly searchable at `https://goldin.co/buy?Sub_Category=Pokemon` (URL structure visible in search results).
- No documented public API.
- I could not fetch `goldin.co` or its `robots.txt` directly (403 / blocked by harness). TOS not verified verbatim.
- The auction volume is low-frequency (premium lots), poorly matched to a bulk-listing pipeline.

#### Heritage Auctions (`ha.com`)
- Search interface: `https://www.ha.com/c/search/results.zx?term=Pokemon&...` exposes a query-string filter that includes a `live_state` parameter for past/live auctions.
- **TOS is explicit**: per [Heritage's Website Use Agreement](https://www.ha.com/c/ref/website-use-agreement.zx) (snippet), users agree not to *"engage in any collection of data, through such practices as 'screen scraping,' 'database scraping,' 'robot,' 'spider' or other automatic means"* and *"Any unauthorized usage of the Website may subject You to civil or criminal prosecution."* Heritage has previously sued a competitor over scraping.
- Verdict: **do not scrape**. No API alternative.

#### Robert Edward Auctions
- Public archive search at [`https://collectrea.com/search`](https://collectrea.com/search) and `https://robertedwardauctions.com/archives`.
- I could not fetch either directly (403). No published API.
- REA's Pokémon-specific volume is small; not worth a dedicated integration for bulk listing.

### 2.4 Cardmarket

**Two access paths:**

1. **Free CSV download** (the important finding):
   - Per [Cardmarket news post (snippet)](https://news.cardmarket.com/en/Pokemon/were-making-the-price-guide-and-product-catalogue-available-for-download) the price guide and product catalogue are "now available for download for all games from their downloads page. Previously, these files were only available to API users."
   - Price guide updated daily; catalogue updated when a release is added.
   - File format: CSV (the API equivalent is gzipped CSV per [API_2.0:PriceGuide](https://api.cardmarket.com/ws/documentation/API_2.0:PriceGuide) snippet).
   - **No auth required for the download.** This is the cheap entry point for EU pricing.

2. **REST API** at `https://api.cardmarket.com/ws/v2.0/` (per [API auth docs snippet](https://api.cardmarket.com/ws/documentation/API:Auth_Overview)):
   - Auth: OAuth 1.0a with HMAC-SHA1, four credentials (App Token, App Secret, Access Token, Access Token Secret).
   - "Restricted to professional sellers and subject to a manual approval process. Only users with a commercial account can apply for and register 3rd Party apps." (per snippet).
   - Rate limits: 30,000 req/day general; 100,000/day for professionals (only 30k of those for marketplace endpoints) — per snippet.
   - The live `/priceguide` endpoint was deprecated June 5 2024 in favor of the public file download.

**Recommendation**: ingest the free daily CSV. Skip the OAuth API unless we end up needing live marketplace queries.

### 2.5 eBay Completeness

**Marketplace Insights API**:
- Confirmed 90-day horizon (per multiple snippets of [eBay docs](https://developer.ebay.com/api-docs/buy/static/api-insights.html) — I could not WebFetch the page itself, returns 403).
- "Limited Release" API: explicitly **not open to new users at this time** per multiple eBay Community threads (snippets of [community.ebay.com](https://community.ebay.com/t5/eBay-APIs-Talk-to-your-fellow/Marketplace-Insights-API-access/td-p/34838736)).
- Application Growth Check process exists but approval is selective.

**Terapeak Research**:
- "Terapeak Product Research is free to all Seller Hub sellers" — no store subscription required (per [eBay news archive snippet](https://export.ebay.com/en/resources/important-updates/ebay-news-archive/terapeak/) and [help page snippet](https://www.ebay.com/help/selling/selling-tools/terapeak-research?id=4853)).
- History depth: "**up to three years of sales history**" — substantially deeper than the 90-day MI API window.
- Terapeak **Sourcing Insights** (separate product) still requires a Basic+ Store subscription.
- **No official public API** for Terapeak. Programmatic access requires either:
  (a) a headless-browser session against the Seller Hub UI (terms-of-service risk), or
  (b) buying a tool that wraps it.
- Verdict: this is the single biggest missing piece for our pipeline if we want a horizon longer than 90 days. Worth a separate scoping doc on a Seller-Hub-authenticated scraper.

---

## 3. Final Recommendation

**Add now:**
- **Cardmarket free CSV download** — one HTTP GET per day, no auth, gives us EU comp baselines for every set. Lowest-effort, highest-yield addition.
- **eBay Terapeak Research (via authenticated Seller Hub session)** — best path to 3-year historical comps; needed to break the 90-day ceiling. Requires careful TOS read and a fragile scraper, but solves the biggest data gap.

**Defer:**
- **PriceCharting paid API** — adds an oracle for sanity-checking our own eBay aggregation, not new data. Revisit if our outlier filter proves unreliable.
- **Pokedata.io Pro/Platinum** — would consolidate eBay + TCGPlayer + Cardmarket + auction houses, but pricing is opaque and we'd be paying for things we already have. Revisit once we know what auction-house signal is actually unique.
- **PWCC / Fanatics Collect sales-history** — defer because the archive is largely eBay-derived and the TOS forbids scraping.

**Skip:**
- **Heritage Auctions, Goldin, REA direct** — too low volume for bulk listing, no APIs, and Heritage's TOS is explicitly hostile.

---

## 4. Legal / TOS Flags

- **Heritage Auctions** (verbatim, per search-snippet of [HA.com Website Use Agreement](https://www.ha.com/c/ref/website-use-agreement.zx)): *"engage in any collection of data, through such practices as 'screen scraping,' 'database scraping,' 'robot,' 'spider' or other automatic means"* is prohibited; violation may carry *"civil or criminal prosecution."* **Do not scrape.**
- **Fanatics Collect** (verbatim, per snippet of [fanatics.com terms](https://www.fanatics.com/fanatics-terms-of-use/x-6455+z-87173304-1397704712)): prohibits *"using any automated system or software to extract data from the Properties for commercial purposes (including 'screen scraping')."* The sales-history tool is publicly viewable but commercial scraping is contractually disallowed.
- **PriceCharting**: TOS at `https://www.pricecharting.com/page/terms-of-service` exists; I could not retrieve the verbatim scraping clause. A third-party scraper's own README routes around this by recommending the official API.
- **Cardmarket**: free CSV download is explicitly endorsed in their own news post; using it is unambiguously allowed.
- **eBay Terapeak**: the data is free for Seller Hub users via the UI, but eBay's general User Agreement disallows automated access not via approved APIs — any scraper of Terapeak's UI is ToS-grey-area and risks the seller account.

---

## 5. Honesty Section — What I Couldn't Verify

I want to be explicit about the gaps rather than fabricate:

- **PriceCharting** — every page on the apex domain (`pricecharting.com` and `blog.pricecharting.com`) returned HTTP 403 to both WebFetch and `curl` with a real browser User-Agent. I could **not** read the robots.txt, methodology, FAQ, API documentation, or pricing tier pages directly. Everything in §2.1 came from search-engine snippets of those pages. The API endpoint shapes (`/api/product`, `t` token, penny-encoded prices) are described consistently across multiple sources but I did not see a live JSON response body.
- **Pokedata.io** — same situation. `/about`, `/api-terms`, `/pro`, and `/` all returned 403. I could not view their actual API terms verbatim, could not confirm specific auction-house names they pull from, and did not load the Postman collection. The site is likely JS-rendered; even if the fetch succeeded the data probably lives in an XHR call I cannot make.
- **Fanatics Collect / sales-history.fanaticscollect.com** — could not load directly. Inferred query-string pattern (`?type=...`) from a search-result URL fragment only.
- **Cardmarket** — I did **not** retrieve the actual CSV download page URL from `cardmarket.com/en/Pokemon/Data/...`; I am relying on the news post snippet that confirms its existence and the API_2.0 doc URL. The exact download URL would need to be confirmed in a browser before wiring into the pipeline.
- **Auction houses** — could not fetch `goldin.co`, `ha.com`, or `collectrea.com` for robots.txt or sample search pages. TOS quotes for Heritage and Fanatics are from search snippets, not my own retrievals — they should be re-read verbatim before any scraping decision.
- **eBay Marketplace Insights / Terapeak** — could not load eBay developer or help pages directly. The 90-day MI horizon and 3-year Terapeak horizon are corroborated by multiple independent snippets but not by me viewing the canonical pages.
- **PriceCharting / Pokedata.io subscription pricing** — neither vendor exposes dollar amounts in cached snippets, and the pricing pages are 403-gated. A real evaluation will need a quick manual browser visit by a human to capture the price tier table.
- **No XHR/JSON endpoint sniffing was possible.** I cannot execute JavaScript or open DevTools in this environment, so for any JS-heavy site (Pokedata.io, Goldin, sales-history.fanaticscollect.com) I do not know the underlying request URLs.

The single biggest gap is the **inability to load PriceCharting and Pokedata.io directly** — a human spending 10 minutes in a browser with DevTools open would meaningfully improve §2.1 and §2.2 before any purchase decision is made.
