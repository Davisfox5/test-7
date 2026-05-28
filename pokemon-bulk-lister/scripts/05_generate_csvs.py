"""Step 5 — generate marketplace upload CSVs from cards_priced.json.

Outputs:
    output/csvs/tcgplayer_bulk.csv     — TCGPlayer staged-inventory bulk format
    output/csvs/whatnot_seller_hub.csv — Whatnot US Seller Hub inventory import
    output/csvs/ebay_bulk.csv          — eBay Seller Hub bulk listing CSV

Column orders match each platform's published template at time of writing
(2026-05). Verify against the latest template before uploading — these are
documented as moving targets.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


def _condition_full(short: str) -> str:
    return {
        "NM": "Near Mint",
        "LP": "Lightly Played",
        "MP": "Moderately Played",
        "HP": "Heavily Played",
        "DMG": "Damaged",
    }.get(short.upper(), "Near Mint")


def _title(card: dict) -> str:
    parts = [card.get("name", "")]
    number = card.get("card_number")
    set_name = card.get("set_name")
    if number:
        parts.append(f"#{number}")
    if set_name:
        parts.append(set_name)
    if card.get("is_holo"):
        parts.append("Holo")
    parts.append("Pokemon TCG")
    return " ".join(p for p in parts if p)


def tcgplayer_rows(cards: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for c in cards:
        if not c.get("price") or not c.get("tcgplayer_product_id"):
            continue
        rows.append(
            {
                "TCGplayer Id": c["tcgplayer_product_id"],
                "Product Line": "Pokemon",
                "Set Name": c.get("set_name", ""),
                "Product Name": c.get("name", ""),
                "Number": c.get("card_number", ""),
                "Rarity": c.get("rarity", ""),
                "Condition": _condition_full(c.get("condition_guess", "NM")),
                "TCG Marketplace Price": f"{c['price']:.2f}",
                "Add to Quantity": 1,
            }
        )
    return rows


def whatnot_rows(cards: list[dict]) -> list[dict]:
    """Whatnot Seller Hub inventory import template (US).

    Reference columns based on Whatnot's current bulk-inventory CSV template.
    """
    rows: list[dict] = []
    for c in cards:
        if not c.get("price"):
            continue
        rows.append(
            {
                "Category": "Trading Cards",
                "Sub Category": "Pokemon",
                "Title": _title(c),
                "Description": _description(c),
                "Quantity": 1,
                "Type": "Buy it Now",
                "Price": f"{c['price']:.2f}",
                "Shipping Profile": "0-1 oz",
                "Offerable": "TRUE",
                "Hazmat": "Not Hazmat",
                "Condition": _condition_full(c.get("condition_guess", "NM")),
                "Cost Per Item": "",
                "SKU": Path(c.get("crop_path", "")).stem,
                "Image URL 1": c.get("image_url", ""),
            }
        )
    return rows


def ebay_rows(cards: list[dict]) -> list[dict]:
    """eBay Seller Hub bulk CSV (Action / Custom label / Category ID / ...).

    Category 183454 = Collectible Card Games > Pokémon TCG > Individual Cards.
    """
    rows: list[dict] = []
    for c in cards:
        if not c.get("price"):
            continue
        rows.append(
            {
                "Action": "Add",
                "Custom label (SKU)": Path(c.get("crop_path", "")).stem,
                "Category ID": "183454",
                "Title": _title(c)[:80],
                "Condition ID": "1000",  # New / NM ungraded
                "Format": "FixedPrice",
                "Duration": "GTC",
                "Start price": f"{c['price']:.2f}",
                "Quantity": 1,
                "Item photo URL": c.get("image_url", ""),
                "Description": _description(c),
                "C:Game": "Pokémon TCG",
                "C:Card Name": c.get("name", ""),
                "C:Set": c.get("set_name", ""),
                "C:Card Number": c.get("card_number", ""),
                "C:Rarity": c.get("rarity", ""),
                "C:Features": "Holo" if c.get("is_holo") else "",
                "C:Language": "English",
                "C:Country/Region of Manufacture": "United States",
                "Shipping type": "Calculated",
                "Shipping service 1 option": "USPSGroundAdvantage",
                "Weight major": "0",
                "Weight minor": "1",
                "Package type": "PackageThickEnvelope",
                "Dispatch time max": "1",
                "Returns accepted option": "ReturnsAccepted",
                "Returns within option": "Days_30",
                "Refund option": "MoneyBack",
                "Return shipping cost paid by": "Buyer",
            }
        )
    return rows


def _description(c: dict) -> str:
    bits: list[str] = []
    name = c.get("name", "")
    if name:
        bits.append(name)
    if c.get("set_name"):
        bits.append(f"from {c['set_name']}")
    if c.get("card_number"):
        bits.append(f"(#{c['card_number']})")
    if c.get("rarity"):
        bits.append(f"— {c['rarity']}")
    if c.get("is_holo"):
        bits.append("Holo")
    bits.append(f"in {_condition_full(c.get('condition_guess', 'NM'))} condition.")
    bits.append(
        "English. Single cards ship in a hard case; "
        "combined multi-card orders ship boxed."
    )
    return " ".join(bits)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", default="output/cards_priced.json")
    parser.add_argument("--out-dir", default="output/csvs")
    args = parser.parse_args()

    cards_path = Path(args.cards)
    if not cards_path.exists():
        print(f"{cards_path} not found", file=sys.stderr)
        return 1

    with cards_path.open() as f:
        cards: list[dict[str, Any]] = json.load(f)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = [
        ("tcgplayer_bulk.csv", tcgplayer_rows(cards)),
        ("whatnot_seller_hub.csv", whatnot_rows(cards)),
        ("ebay_bulk.csv", ebay_rows(cards)),
    ]
    for filename, rows in targets:
        df = pd.DataFrame(rows)
        path = out_dir / filename
        df.to_csv(path, index=False)
        print(f"{path}: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
