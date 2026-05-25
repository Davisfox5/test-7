"""Step 6 — render output/review.html for human triage.

One row per card with the crop thumbnail, identified data, the three
source prices, the final price, and the confidence score. Sorted by
confidence ascending so the suspect cards float to the top.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

TEMPLATE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Pokemon bulk listing — review</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; margin: 24px; color: #222; }
  h1 { font-size: 22px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { border: 1px solid #ddd; padding: 6px 8px; vertical-align: top; }
  th { background: #f5f5f5; text-align: left; }
  tr.flag { background: #fff5f5; }
  tr.flag td.conf { color: #b00; font-weight: 600; }
  img { max-width: 140px; max-height: 200px; display: block; }
  .src { font-variant-numeric: tabular-nums; }
  .meta { color: #666; font-size: 12px; }
</style>
</head>
<body>
<h1>Pokemon bulk listing — review ({n} cards, {flagged} flagged)</h1>
<table>
<thead>
<tr>
  <th>Crop</th>
  <th>Card</th>
  <th>Set / # / Rarity</th>
  <th>Cond.</th>
  <th>TCG market</th>
  <th>eBay median 30d</th>
  <th>eBay max 30d</th>
  <th>Final</th>
  <th>Confidence</th>
  <th>Notes</th>
</tr>
</thead>
<tbody>
"""

TEMPLATE_FOOT = """</tbody></table></body></html>
"""


def _fmt(v: object) -> str:
    if v is None or v == "":
        return "&mdash;"
    if isinstance(v, (int, float)):
        return f"${v:.2f}"
    return html.escape(str(v))


def render_row(card: dict) -> str:
    sources = card.get("sources") or {}
    needs_review = bool(card.get("needs_review"))
    klass = "flag" if needs_review else ""
    crop = card.get("crop_path", "")
    image_src = card.get("image_url") or crop
    confidence = card.get("confidence", 0.0)
    notes = card.get("pricing_notes", "")
    if card.get("outlier_flag"):
        notes = (notes + " [outlier]").strip()

    return (
        f'<tr class="{klass}">'
        f'<td><img src="{html.escape(image_src)}" alt=""></td>'
        f'<td><strong>{html.escape(card.get("name") or "(unidentified)")}</strong>'
        f'<div class="meta">{html.escape(Path(crop).name)}</div></td>'
        f'<td>{html.escape(card.get("set_name") or "")}<br>'
        f'#{html.escape(str(card.get("card_number") or ""))}'
        f' &middot; {html.escape(card.get("rarity") or "")}'
        f'{" &middot; Holo" if card.get("is_holo") else ""}</td>'
        f'<td>{html.escape(card.get("condition_guess") or "")}</td>'
        f'<td class="src">{_fmt(sources.get("tcgplayer_market"))}</td>'
        f'<td class="src">{_fmt(sources.get("ebay_median_30d"))}</td>'
        f'<td class="src">{_fmt(sources.get("ebay_max_30d"))}</td>'
        f'<td class="src"><strong>{_fmt(card.get("price"))}</strong></td>'
        f'<td class="conf">{confidence:.2f}</td>'
        f'<td>{html.escape(notes)}</td>'
        f'</tr>\n'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", default="output/cards_priced.json")
    parser.add_argument("--out", default="output/review.html")
    args = parser.parse_args()

    cards_path = Path(args.cards)
    if not cards_path.exists():
        print(f"{cards_path} not found", file=sys.stderr)
        return 1

    with cards_path.open() as f:
        cards = json.load(f)

    cards_sorted = sorted(cards, key=lambda c: c.get("confidence") or 0.0)
    flagged = sum(1 for c in cards if c.get("needs_review"))

    out_path = Path(args.out)
    with out_path.open("w") as f:
        f.write(TEMPLATE_HEAD.format(n=len(cards), flagged=flagged))
        for c in cards_sorted:
            f.write(render_row(c))
        f.write(TEMPLATE_FOOT)

    print(f"Wrote {out_path} ({len(cards)} cards, {flagged} flagged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
