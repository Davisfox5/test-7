"""Step 2 — STUB. Schema for human-in-the-loop card identification.

This step is intentionally not automated. The user identifies each crop
interactively in Claude Code using vision, then populates the fields here.

What this script does:
- Walks output/crops/ and emits a stub entry per crop into output/cards.json
  with empty identification fields.
- If output/cards.json already exists, merges new crops in without clobbering
  any fields you've already filled.

Schema (one entry per crop):
    {
        "crop_path": "output/crops/page01_r0c0.jpg",   # relative path
        "name": "",            # e.g. "Charizard"
        "set_name": "",        # e.g. "Base Set"
        "set_code": "",        # pokemontcg.io set id, e.g. "base1"
        "card_number": "",     # printed number, e.g. "4" or "4/102"
        "rarity": "",          # "Common" | "Uncommon" | "Rare" | "Holo Rare" | ...
        "is_holo": false,
        "condition_guess": "", # "NM" | "LP" | "MP"
        "confidence": 0.0      # 0.0-1.0, your subjective ID confidence
    }
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EMPTY_ENTRY: dict[str, Any] = {
    "crop_path": "",
    "name": "",
    "set_name": "",
    "set_code": "",
    "card_number": "",
    "rarity": "",
    "is_holo": False,
    "condition_guess": "",
    "confidence": 0.0,
}


def load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    return {entry["crop_path"]: entry for entry in data}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crops", default="output/crops")
    parser.add_argument("--out", default="output/cards.json")
    args = parser.parse_args()

    crops_dir = Path(args.crops)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing = load_existing(out_path)
    entries: list[dict] = []

    crops = sorted(p for p in crops_dir.glob("*.jpg"))
    for crop in crops:
        rel = str(crop.as_posix())
        if rel in existing:
            entries.append(existing[rel])
            continue
        entry = dict(EMPTY_ENTRY)
        entry["crop_path"] = rel
        entries.append(entry)

    with out_path.open("w") as f:
        json.dump(entries, f, indent=2)

    filled = sum(1 for e in entries if e.get("name"))
    print(f"Wrote {out_path}: {len(entries)} entries ({filled} filled, {len(entries) - filled} empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
