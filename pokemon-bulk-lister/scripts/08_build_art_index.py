"""Step 8 (one-time) — build the perceptual-hash art index from pokemontcg.io.

Downloads every card's official image, hashes it (lib/art_match), and saves
the index that lets the webapp identify crops WITHOUT an AI call. Resumable:
re-running skips cards already in the index, so an interrupted build just
continues where it left off.

Usage:
    python scripts/08_build_art_index.py                 # full catalog (~20k cards)
    python scripts/08_build_art_index.py --sets sv7,sv8  # only these set ids

Notes:
    - Card *data* comes from pokemontcg.io's official static data repo on
      GitHub (reliable CDN), falling back to api.pokemontcg.io when a set is
      missing there (set POKEMONTCG_API_KEY in .env for higher API limits).
      Images come from images.pokemontcg.io (uncapped).
    - Full build downloads ~20k small images (~300 MB transfer, nothing kept
      on disk) and takes on the order of 20-40 minutes.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from lib.art_match import ArtIndex, index_descriptor  # noqa: E402

API_URL = "https://api.pokemontcg.io/v2/cards"
# Official static export of the same database — far more reliable than the API.
GITHUB_DATA = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master"
DEFAULT_OUT = ROOT / "output" / "cache" / "art_index" / "catalog"
PAGE_SIZE = 250

PLAIN_RARITIES = {"common", "uncommon", "rare", "promo", "classic collection"}
HOLO_RARITY_WORDS = (
    "holo", "ex", "gx", "v", "vmax", "vstar", "illustration", "ultra",
    "secret", "hyper", "shiny", "radiant", "amazing", "prism", "rainbow",
    "gold", "ace", "break", "prime", "legend",
)


def _guess_holo(rarity: str) -> bool:
    """Best-effort foil guess from the catalog rarity string.

    Reverse holos share art (and rarity) with their plain versions, so a hash
    match can't see them — the AI fallback and manual review still can.
    """
    r = (rarity or "").lower()
    if not r or r in PLAIN_RARITIES:
        return False
    return any(w in r.replace("-", " ").split() for w in HOLO_RARITY_WORDS) or any(
        w in r for w in ("holo", "illustration", "ultra", "secret", "hyper", "shiny")
    )


def _api_get(session: requests.Session, url: str, params: dict) -> dict:
    headers = {}
    api_key = os.getenv("POKEMONTCG_API_KEY")
    if api_key:
        headers["X-Api-Key"] = api_key
    for attempt in range(4):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=60)
            if resp.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            print(f"  {url} {params.get('q', '')} p{params.get('page')}: {exc} — retrying",
                  file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _sets_catalog(session: requests.Session) -> dict[str, str]:
    """{set_id: set_name} for the whole catalog (GitHub first, API fallback)."""
    try:
        resp = session.get(f"{GITHUB_DATA}/sets/en.json", timeout=60)
        resp.raise_for_status()
        return {s["id"]: s.get("name") or "" for s in resp.json()}
    except (requests.RequestException, ValueError) as exc:
        print(f"GitHub sets list failed ({exc}) — trying the API", file=sys.stderr)
    data = _api_get(session, "https://api.pokemontcg.io/v2/sets",
                    {"pageSize": 250, "select": "id,name,releaseDate", "orderBy": "releaseDate"})
    return {s["id"]: s.get("name") or "" for s in data.get("data") or []}


def _set_cards(session: requests.Session, set_id: str, set_name: str) -> list[dict]:
    """All cards of one set — GitHub static data first, API fallback.

    Per-set requests keep API pagination shallow when the fallback is needed;
    pokemontcg.io reliably 500s on deep pages of big OR-queries.
    """
    try:
        resp = session.get(f"{GITHUB_DATA}/cards/en/{set_id}.json", timeout=60)
        if resp.status_code == 200:
            cards = resp.json()
            # GitHub entries carry no `set` object — synthesize what we use.
            for c in cards:
                c.setdefault("set", {"id": set_id, "name": set_name})
            return cards
    except (requests.RequestException, ValueError):
        pass
    cards: list[dict] = []
    page = 1
    while True:
        data = _api_get(session, API_URL, {
            "page": page,
            "pageSize": PAGE_SIZE,
            "select": "id,name,number,rarity,set,images",
            "q": f"set.id:{set_id}",
        })
        batch = data.get("data") or []
        cards.extend(batch)
        if len(batch) < PAGE_SIZE:
            return cards
        page += 1


def _hash_card(session: requests.Session, card: dict) -> tuple[dict, np.ndarray] | None:
    images = card.get("images") or {}
    url = images.get("small") or images.get("large")
    if not url:
        return None
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(2 * (attempt + 1))
    img = cv2.imdecode(np.frombuffer(resp.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    desc = index_descriptor(img)
    if desc is None:
        return None
    card_set = card.get("set") or {}
    meta = {
        "tcg_id": card.get("id"),
        "name": card.get("name") or "",
        "set_code": card_set.get("id") or "",
        "set_name": card_set.get("name") or "",
        "card_number": (card.get("number") or "").lstrip("0") or (card.get("number") or ""),
        "rarity": card.get("rarity") or "",
        "is_holo": _guess_holo(card.get("rarity") or ""),
    }
    return meta, desc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sets", help="comma-separated pokemontcg.io set ids (default: all)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="index output directory")
    ap.add_argument("--workers", type=int, default=12, help="parallel image downloads")
    args = ap.parse_args()

    session = requests.Session()
    catalog = _sets_catalog(session)
    if args.sets:
        set_ids = [s.strip() for s in args.sets.split(",") if s.strip()]
    else:
        set_ids = list(catalog)
    print(f"building over {len(set_ids)} sets")

    index = ArtIndex.load(args.out)
    seen = {m.get("tcg_id") for m in index.meta}
    print(f"index at {args.out}: {len(index)} cards already hashed")

    added = 0
    failed_sets: list[str] = []
    for n, set_id in enumerate(set_ids, 1):
        try:
            cards = _set_cards(session, set_id, catalog.get(set_id, ""))
        except Exception as exc:
            print(f"[{n}/{len(set_ids)}] {set_id}: FAILED ({exc}) — skipping", file=sys.stderr)
            failed_sets.append(set_id)
            continue
        todo = [c for c in cards if c.get("id") not in seen]
        if todo:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for result in pool.map(lambda c: _hash_card(session, c), todo):
                    if result is None:
                        continue
                    meta, desc = result
                    index.add(desc, meta)
                    seen.add(meta["tcg_id"])
                    added += 1
            index.save(args.out)
        print(f"[{n}/{len(set_ids)}] {set_id}: {len(cards)} cards "
              f"({len(todo)} new) — index at {len(index)}")

    index.save(args.out)
    print(f"done — {len(index)} cards in index ({added} added this run)")
    if failed_sets:
        print(f"FAILED sets (re-run to retry): {','.join(failed_sets)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
