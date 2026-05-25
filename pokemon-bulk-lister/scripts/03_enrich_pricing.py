"""Step 3 — enrich cards.json with prices from TCGPlayer (pokemontcg.io) + eBay.

Reads output/cards.json, looks up each card on both sources, applies the
aggregation rule, and writes output/cards_priced.json.

Skips entries whose `name` is empty (i.e. step 2 hasn't filled them in yet).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Allow running this file directly from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.ebay_client import EbayAuthError, EbayClient  # noqa: E402
from lib.pricing import aggregate  # noqa: E402
from lib.tcgplayer_client import TCGPlayerClient  # noqa: E402


def enrich_one(
    entry: dict,
    tcg: TCGPlayerClient,
    ebay: EbayClient,
) -> dict:
    name = entry.get("name", "").strip()
    if not name:
        return {**entry, "_skipped": "no name (run step 2 first)"}

    set_name = entry.get("set_name") or None
    set_code = entry.get("set_code") or None
    card_number = entry.get("card_number") or None
    is_holo = bool(entry.get("is_holo"))

    tcg_price: float | None = None
    tcg_card: dict | None = None
    try:
        tcg_price, tcg_card = tcg.lookup_price(
            name=name,
            set_name=set_name,
            set_code=set_code,
            card_number=card_number,
            is_holo=is_holo,
        )
    except Exception as exc:
        print(f"  [tcg error] {name}: {exc}", file=sys.stderr)

    ebay_stats: dict[str, Any] = {"median": None, "max": None, "count": 0}
    try:
        query = ebay.build_query(name=name, set_name=set_name, card_number=card_number)
        ebay_stats = ebay.sold_stats(query=query, days=30, condition="NEW")
    except EbayAuthError as exc:
        print(f"  [ebay auth] {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"  [ebay error] {name}: {exc}", file=sys.stderr)

    result = aggregate(
        tcgplayer_market=tcg_price,
        ebay_median_30d=ebay_stats.get("median"),
        ebay_max_30d=ebay_stats.get("max"),
    )

    enriched = dict(entry)
    enriched["price"] = result.price
    enriched["sources"] = result.sources
    enriched["confidence"] = result.confidence
    enriched["outlier_flag"] = result.outlier_flag
    enriched["needs_review"] = result.needs_review
    enriched["pricing_notes"] = result.notes
    enriched["ebay_sold_count_30d"] = ebay_stats.get("count", 0)
    if tcg_card is not None:
        enriched["tcgplayer_product_id"] = tcg_card.get("id")
        enriched["tcgplayer_url"] = (tcg_card.get("tcgplayer") or {}).get("url")
    return enriched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", default="output/cards.json")
    parser.add_argument("--out", default="output/cards_priced.json")
    args = parser.parse_args()

    load_dotenv()

    cards_path = Path(args.cards)
    out_path = Path(args.out)
    if not cards_path.exists():
        print(f"{cards_path} not found — run step 2 first", file=sys.stderr)
        return 1

    with cards_path.open() as f:
        entries = json.load(f)

    tcg = TCGPlayerClient()
    ebay = EbayClient()

    enriched: list[dict] = []
    for i, entry in enumerate(entries, 1):
        name = entry.get("name", "<unidentified>")
        print(f"[{i}/{len(entries)}] {name}")
        enriched.append(enrich_one(entry, tcg, ebay))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(enriched, f, indent=2)

    review = sum(1 for e in enriched if e.get("needs_review"))
    print(f"\nWrote {out_path}: {len(enriched)} cards, {review} flagged for review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
