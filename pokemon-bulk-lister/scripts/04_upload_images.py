"""Step 4 — upload each crop to Cloudinary and write the secure URL back into cards_priced.json.

Reuses Cloudinary creds from the First XI Fitness setup via .env.
Skips entries whose `image_url` is already set so reruns are cheap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import cloudinary_client  # noqa: E402


def public_id_for(crop_path: str) -> str:
    """Stable public_id so reuploads are idempotent."""
    stem = Path(crop_path).stem
    digest = hashlib.sha1(crop_path.encode()).hexdigest()[:8]
    return f"{stem}_{digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", default="output/cards_priced.json")
    parser.add_argument("--out", default="output/cards_priced.json")
    parser.add_argument("--force", action="store_true", help="re-upload even if image_url is set")
    args = parser.parse_args()

    load_dotenv()
    cloudinary_client.configure()

    cards_path = Path(args.cards)
    if not cards_path.exists():
        print(f"{cards_path} not found — run step 3 first", file=sys.stderr)
        return 1

    with cards_path.open() as f:
        entries = json.load(f)

    uploaded = 0
    skipped = 0
    failed = 0
    for i, entry in enumerate(entries, 1):
        crop_path = entry.get("crop_path")
        if not crop_path:
            continue
        if entry.get("image_url") and not args.force:
            skipped += 1
            continue
        if not Path(crop_path).exists():
            print(f"[{i}] missing {crop_path}", file=sys.stderr)
            failed += 1
            continue

        try:
            url = cloudinary_client.upload(crop_path, public_id=public_id_for(crop_path))
            entry["image_url"] = url
            uploaded += 1
            print(f"[{i}/{len(entries)}] {Path(crop_path).name} -> {url}")
        except Exception as exc:
            print(f"[{i}] upload failed for {crop_path}: {exc}", file=sys.stderr)
            failed += 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(entries, f, indent=2)

    print(f"\nDone. uploaded={uploaded} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
