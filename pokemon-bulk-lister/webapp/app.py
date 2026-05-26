"""Flask web app for the Pokémon bulk-lister pipeline.

Single-page UI backed by SQLite. Wires existing pipeline scripts:
  - 01_split_grids.process_image() for upload-time cropping
  - lib.tcgplayer_client + lib.ebay_client + lib.pricing for per-card pricing
  - lib.cloudinary_client for image upload
  - scripts/05_generate_csvs for CSV export

Run:
    pip install -r requirements.txt
    python -m webapp.app           # http://localhost:5050

Or:
    flask --app webapp.app run --port 5050
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

import cv2
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)

# Allow direct python -m webapp.app from project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.ebay_client import EbayAuthError, EbayClient  # noqa: E402
from lib.pricing import aggregate  # noqa: E402
from lib.tcgplayer_client import TCGPlayerClient  # noqa: E402
from webapp import db  # noqa: E402


def _load_split_grids():
    """Load the numbered split-grids script as a module."""
    path = ROOT / "scripts" / "01_split_grids.py"
    spec = importlib.util.spec_from_file_location("split_grids", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_csv_generator():
    path = ROOT / "scripts" / "05_generate_csvs.py"
    spec = importlib.util.spec_from_file_location("generate_csvs", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


load_dotenv(ROOT / ".env")

INPUT_DIR = ROOT / os.getenv("INPUT_DIR", "input/grids")
OUTPUT_DIR = ROOT / os.getenv("OUTPUT_DIR", "output")
CROPS_DIR = OUTPUT_DIR / "crops"
CSVS_DIR = OUTPUT_DIR / "csvs"
DB_PATH = str(OUTPUT_DIR / "db.sqlite")

EUR_USD_RATE = float(os.getenv("EUR_USD_RATE", "1.08"))

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB

# Lazy singletons
_tcg_client: Optional[TCGPlayerClient] = None
_ebay_client: Optional[EbayClient] = None
_split_grids = None
_csv_gen = None

# Background job state (single in-process slot — fine for a one-off personal app).
_job_lock = threading.Lock()
_job_state: dict[str, Any] = {"running": False, "progress": 0, "total": 0, "message": ""}


# ----------------------------------------------------------------------
# Bootstrap
# ----------------------------------------------------------------------

def _init() -> None:
    CROPS_DIR.mkdir(parents=True, exist_ok=True)
    CSVS_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db(DB_PATH)
    with db.connect(DB_PATH) as conn:
        imported = db.maybe_import_legacy_json(conn)
        if imported:
            print(f"[webapp] imported {imported} cards from legacy JSON")


_init()


def get_tcg() -> TCGPlayerClient:
    global _tcg_client
    if _tcg_client is None:
        _tcg_client = TCGPlayerClient()
    return _tcg_client


def get_ebay() -> EbayClient:
    global _ebay_client
    if _ebay_client is None:
        _ebay_client = EbayClient()
    return _ebay_client


def get_split_grids():
    global _split_grids
    if _split_grids is None:
        _split_grids = _load_split_grids()
    return _split_grids


def get_csv_gen():
    global _csv_gen
    if _csv_gen is None:
        _csv_gen = _load_csv_generator()
    return _csv_gen


# ----------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ----------------------------------------------------------------------
# Cards
# ----------------------------------------------------------------------

@app.route("/api/cards")
def api_list_cards():
    sort = request.args.get("sort", "confidence_asc")
    needs_review_only = request.args.get("needs_review") == "1"
    unidentified_only = request.args.get("unidentified") == "1"
    with db.connect(DB_PATH) as conn:
        rows = db.list_cards(
            conn,
            sort=sort,
            needs_review_only=needs_review_only,
            unidentified_only=unidentified_only,
        )
        stats = db.card_stats(conn)
    return jsonify({"cards": rows, "stats": stats})


@app.route("/api/cards/<int:card_id>", methods=["GET"])
def api_get_card(card_id: int):
    with db.connect(DB_PATH) as conn:
        card = db.get_card(conn, card_id)
    if not card:
        abort(404)
    return jsonify(card)


@app.route("/api/cards/<int:card_id>", methods=["PATCH"])
def api_patch_card(card_id: int):
    patch = request.get_json(silent=True) or {}
    with db.connect(DB_PATH) as conn:
        card = db.update_card(conn, card_id, patch)
    if not card:
        abort(404)
    return jsonify(card)


@app.route("/api/cards/bulk", methods=["POST"])
def api_bulk_update_cards():
    """Bulk update — paste a JSON list with crop_path keys to apply identifications.

    Body:
      [{"crop_path": "output/crops/page01_r0c0.jpg", "name": "Charizard", ...}, ...]
    """
    body = request.get_json(silent=True) or []
    if not isinstance(body, list):
        abort(400, "expected a JSON list")

    applied = 0
    with db.connect(DB_PATH) as conn:
        for entry in body:
            crop_path = entry.get("crop_path")
            if not crop_path:
                continue
            row = conn.execute("SELECT id FROM cards WHERE crop_path = ?", (crop_path,)).fetchone()
            if not row:
                continue
            patch = {k: v for k, v in entry.items() if k != "crop_path"}
            db.update_card(conn, int(row["id"]), patch)
            applied += 1
    return jsonify({"applied": applied})


# ----------------------------------------------------------------------
# Upload + split
# ----------------------------------------------------------------------

@app.route("/api/grids/upload", methods=["POST"])
def api_upload_grid():
    files = request.files.getlist("file") or []
    if not files:
        abort(400, "no file uploaded")

    sg = get_split_grids()
    total_crops = 0
    grids_added = []

    for file in files:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            continue
        safe_name = _safe_filename(file.filename or "grid.jpg")
        dest = INPUT_DIR / safe_name
        file.save(dest)

        n = _split_and_record(sg, dest)
        total_crops += n
        grids_added.append({"filename": safe_name, "crops": n})

    return jsonify({"grids": grids_added, "total_crops": total_crops})


def _split_and_record(sg, grid_path: Path) -> int:
    img = cv2.imread(str(grid_path))
    if img is None:
        return 0
    img = sg.auto_orient(img)
    cells = sg.split_grid(img)

    stem = grid_path.stem
    written = 0
    with db.connect(DB_PATH) as conn:
        grid_id = db.get_or_create_grid(conn, stem, str(grid_path.relative_to(ROOT)))
        for idx, cell in enumerate(cells):
            if cell is None:
                continue
            r, c = divmod(idx, 3)
            out_path = CROPS_DIR / f"{stem}_r{r}c{c}.jpg"
            cv2.imwrite(str(out_path), cell, [cv2.IMWRITE_JPEG_QUALITY, 92])
            rel = str(out_path.relative_to(ROOT))
            db.insert_card_stub(conn, grid_id, rel, r, c)
            written += 1
        db.update_grid_count(conn, grid_id)
    return written


# ----------------------------------------------------------------------
# Pricing
# ----------------------------------------------------------------------

@app.route("/api/cards/<int:card_id>/price", methods=["POST"])
def api_price_card(card_id: int):
    with db.connect(DB_PATH) as conn:
        card = db.get_card(conn, card_id)
    if not card:
        abort(404)
    if not card.get("name"):
        return jsonify({"error": "card has no name — identify it first"}), 400
    try:
        result = _price_card(card)
        with db.connect(DB_PATH) as conn:
            updated = db.update_card(conn, card_id, result)
        return jsonify(updated)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pricing/run-all", methods=["POST"])
def api_run_pricing_all():
    with _job_lock:
        if _job_state["running"]:
            return jsonify({"error": "another job is running"}), 409
        _job_state.update(running=True, progress=0, total=0, message="starting…")

    def worker():
        try:
            with db.connect(DB_PATH) as conn:
                cards = db.list_cards(conn, sort="newest")
            targets = [c for c in cards if c.get("name")]
            _job_state["total"] = len(targets)
            for i, card in enumerate(targets, 1):
                _job_state["progress"] = i
                _job_state["message"] = f"{card.get('name')} ({i}/{len(targets)})"
                try:
                    patch = _price_card(card)
                    with db.connect(DB_PATH) as conn:
                        db.update_card(conn, int(card["id"]), patch)
                except Exception as exc:
                    print(f"[pricing] {card.get('name')}: {exc}", file=sys.stderr)
            _job_state["message"] = "done"
        finally:
            _job_state["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/pricing/status")
def api_pricing_status():
    return jsonify(_job_state)


def _price_card(card: dict) -> dict:
    tcg = get_tcg()
    ebay = get_ebay()

    name = (card.get("name") or "").strip()
    set_name = card.get("set_name") or None
    set_code = card.get("set_code") or None
    card_number = card.get("card_number") or None
    is_holo = bool(card.get("is_holo"))

    prices = tcg.lookup_prices(
        name=name, set_name=set_name, set_code=set_code,
        card_number=card_number, is_holo=is_holo,
    )
    tcg_price = prices.get("tcgplayer_market_usd")
    cm_eur = prices.get("cardmarket_trend_eur")
    tcg_card = prices.get("card")
    cm_usd = round(cm_eur * EUR_USD_RATE, 2) if cm_eur else None

    ebay_stats: dict[str, Any] = {"median": None, "max": None, "count": 0}
    try:
        query = ebay.build_query(name=name, set_name=set_name, card_number=card_number)
        ebay_stats = ebay.sold_stats(query=query, days=30, condition="NEW")
    except EbayAuthError as exc:
        print(f"[ebay auth] {exc}", file=sys.stderr)

    result = aggregate(
        tcgplayer_market=tcg_price,
        ebay_median_30d=ebay_stats.get("median"),
        ebay_max_30d=ebay_stats.get("max"),
        cardmarket_trend_usd=cm_usd,
    )

    patch: dict[str, Any] = {
        "tcgplayer_market": tcg_price,
        "cardmarket_trend_eur": cm_eur,
        "cardmarket_trend_usd": cm_usd,
        "ebay_median_30d": ebay_stats.get("median"),
        "ebay_max_30d": ebay_stats.get("max"),
        "ebay_sold_count_30d": ebay_stats.get("count", 0),
        "final_price": result.price,
        "pricing_confidence": result.confidence,
        "outlier_flag": result.outlier_flag,
        "needs_review": result.needs_review,
        "pricing_notes": result.notes,
    }
    if tcg_card is not None:
        patch["tcgplayer_product_id"] = tcg_card.get("id")
        patch["tcgplayer_url"] = (tcg_card.get("tcgplayer") or {}).get("url")
        patch["cardmarket_url"] = (tcg_card.get("cardmarket") or {}).get("url")
    return patch


# ----------------------------------------------------------------------
# Export + image upload
# ----------------------------------------------------------------------

@app.route("/api/export/csvs", methods=["POST"])
def api_export_csvs():
    gc = get_csv_gen()
    with db.connect(DB_PATH) as conn:
        cards = db.list_cards(conn, sort="newest")
    # Coerce DB rows into the dict shape the CSV generator expects.
    payload = []
    for c in cards:
        payload.append({
            "crop_path": c["crop_path"],
            "name": c["name"] or "",
            "set_name": c["set_name"] or "",
            "set_code": c["set_code"] or "",
            "card_number": c["card_number"] or "",
            "rarity": c["rarity"] or "",
            "is_holo": bool(c["is_holo"]),
            "condition_guess": c["condition_guess"] or "",
            "price": c["final_price"],
            "tcgplayer_product_id": c["tcgplayer_product_id"],
            "image_url": c["image_url"] or "",
            "confidence": c["pricing_confidence"],
        })

    CSVS_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, rows_fn in (
        ("tcgplayer_bulk.csv", gc.tcgplayer_rows),
        ("whatnot_seller_hub.csv", gc.whatnot_rows),
        ("ebay_bulk.csv", gc.ebay_rows),
    ):
        rows = rows_fn(payload)
        # Use pandas only when present; otherwise write naively.
        path = CSVS_DIR / filename
        _write_csv(path, rows)
        written.append({"file": str(path.relative_to(ROOT)), "rows": len(rows)})
    return jsonify({"written": written})


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@app.route("/api/cards/<int:card_id>/upload-image", methods=["POST"])
def api_upload_card_image(card_id: int):
    with db.connect(DB_PATH) as conn:
        card = db.get_card(conn, card_id)
    if not card:
        abort(404)

    try:
        from lib import cloudinary_client
        cloudinary_client.configure()
        crop = ROOT / card["crop_path"]
        url = cloudinary_client.upload(str(crop))
        with db.connect(DB_PATH) as conn:
            updated = db.update_card(conn, card_id, {"image_url": url})
        return jsonify(updated)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ----------------------------------------------------------------------
# Static crops + CSV downloads
# ----------------------------------------------------------------------

@app.route("/crops/<path:filename>")
def serve_crop(filename: str):
    return send_from_directory(CROPS_DIR, filename)


@app.route("/csvs/<path:filename>")
def download_csv(filename: str):
    path = CSVS_DIR / filename
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    name = Path(name).name
    # Allow letters, numbers, dot, dash, underscore.
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in name)
    return safe or "grid.jpg"


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5050"))
    app.run(host="127.0.0.1", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
