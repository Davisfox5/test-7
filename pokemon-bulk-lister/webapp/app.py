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
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

import cv2
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

# Allow direct python -m webapp.app from project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.ebay_client import EbayAuthError, EbayClient  # noqa: E402
from lib.pricecharting_client import PriceChartingClient  # noqa: E402
from lib.pricing import aggregate  # noqa: E402
from lib.tcgplayer_client import TCGPlayerClient  # noqa: E402
from webapp import db  # noqa: E402
from webapp.auth import User, init_login_manager  # noqa: E402


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

# Session signing key. Set FLASK_SECRET_KEY in prod; the dev fallback resets on
# restart (logging everyone out), which is fine for local use but not shared.
app.secret_key = os.getenv("FLASK_SECRET_KEY") or "dev-only-insecure-key-change-me"
if not os.getenv("FLASK_SECRET_KEY"):
    print("[webapp] WARNING: FLASK_SECRET_KEY unset — using an insecure dev key", file=sys.stderr)

# Lazy singletons
_tcg_client: Optional[TCGPlayerClient] = None
_ebay_client: Optional[EbayClient] = None
_pricecharting_client: Optional[PriceChartingClient] = None
_split_grids = None
_csv_gen = None

# Background job state (single in-process slot — fine for a one-off personal app).
_job_lock = threading.Lock()
_job_state: dict[str, Any] = {"running": False, "progress": 0, "total": 0, "message": ""}

# Separate slot for publish jobs so a publish run doesn't clobber pricing state.
_publish_lock = threading.Lock()
_publish_state: dict[str, Any] = {"running": False, "progress": 0, "total": 0, "message": "", "site": ""}


# ----------------------------------------------------------------------
# Bootstrap
# ----------------------------------------------------------------------

def _init() -> None:
    CROPS_DIR.mkdir(parents=True, exist_ok=True)
    CSVS_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db(DB_PATH)
    # Legacy JSON import is owner-aware: assign it to the lowest-id (seed admin)
    # user if one exists, else leave it unowned until the first admin is created
    # and re-runs this (auth-gated installs won't have a user on very first boot).
    with db.connect(DB_PATH) as conn:
        seed = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        seed_uid = int(seed["id"]) if seed else None
        imported = db.maybe_import_legacy_json(conn, user_id=seed_uid)
        if imported:
            print(f"[webapp] imported {imported} cards from legacy JSON (owner={seed_uid})")


_init()
init_login_manager(app, DB_PATH)


def _user_crops_dir(user_id: int) -> Path:
    d = CROPS_DIR / f"u{user_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_input_dir(user_id: int) -> Path:
    d = INPUT_DIR / f"u{user_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def get_pricecharting() -> PriceChartingClient:
    global _pricecharting_client
    if _pricecharting_client is None:
        _pricecharting_client = PriceChartingClient()
    return _pricecharting_client


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
@login_required
def index():
    return render_template("index.html", username=current_user.username, is_admin=current_user.is_admin)


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        with db.connect(DB_PATH) as conn:
            row = db.verify_login(conn, username, password)
        if row:
            login_user(User(row), remember=True)
            return redirect(request.args.get("next") or url_for("index"))
        flash("Invalid username or password.")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/invite/<code>", methods=["GET", "POST"])
def redeem_invite(code: str):
    """Single-use invite redemption — the only path to a new account."""
    with db.connect(DB_PATH) as conn:
        invite = db.get_invite(conn, code)
    valid = bool(invite) and invite["redeemed_by"] is None
    if request.method == "POST" and valid:
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if len(username) < 3 or len(password) < 8:
            flash("Username must be ≥3 chars and password ≥8 chars.")
        else:
            with db.connect(DB_PATH) as conn:
                user = db.redeem_invite(conn, code, username, password)
            if user:
                login_user(User(user), remember=True)
                return redirect(url_for("index"))
            flash("That username is taken, or the invite was just used.")
    return render_template("invite.html", code=code, valid=valid)


# ----------------------------------------------------------------------
# Admin: mint invites from the UI
# ----------------------------------------------------------------------

@app.route("/api/invites", methods=["POST"])
@login_required
def api_create_invite():
    if not current_user.is_admin:
        abort(403)
    body = request.get_json(silent=True) or {}
    role = body.get("role", "member")
    if role not in ("member", "admin"):
        abort(400, "role must be 'member' or 'admin'")
    with db.connect(DB_PATH) as conn:
        code = db.create_invite(conn, role=role, created_by=current_user.id, note=body.get("note"))
    return jsonify({"code": code, "url": url_for("redeem_invite", code=code, _external=True)})


# ----------------------------------------------------------------------
# Cards
# ----------------------------------------------------------------------

@app.route("/api/cards")
@login_required
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
            user_id=current_user.id,
        )
        stats = db.card_stats(conn, user_id=current_user.id)
    return jsonify({"cards": rows, "stats": stats})


@app.route("/api/cards/<int:card_id>", methods=["GET"])
@login_required
def api_get_card(card_id: int):
    with db.connect(DB_PATH) as conn:
        card = db.get_card(conn, card_id, user_id=current_user.id)
    if not card:
        abort(404)
    return jsonify(card)


@app.route("/api/cards/<int:card_id>", methods=["PATCH"])
@login_required
def api_patch_card(card_id: int):
    patch = request.get_json(silent=True) or {}
    with db.connect(DB_PATH) as conn:
        card = db.update_card(conn, card_id, patch, user_id=current_user.id)
    if not card:
        abort(404)
    return jsonify(card)


@app.route("/api/cards/bulk", methods=["POST"])
@login_required
def api_bulk_update_cards():
    """Bulk update — paste a JSON list with crop_path keys to apply identifications.

    Body:
      [{"crop_path": "output/crops/u1/page01_r0c0.jpg", "name": "Charizard", ...}, ...]
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
            # Scope by owner: only match the caller's own crop rows.
            row = conn.execute(
                "SELECT id FROM cards WHERE crop_path = ? AND user_id = ?",
                (crop_path, current_user.id),
            ).fetchone()
            if not row:
                continue
            patch = {k: v for k, v in entry.items() if k != "crop_path"}
            db.update_card(conn, int(row["id"]), patch, user_id=current_user.id)
            applied += 1
    return jsonify({"applied": applied})


# ----------------------------------------------------------------------
# Upload + split
# ----------------------------------------------------------------------

@app.route("/api/grids/upload", methods=["POST"])
@login_required
def api_upload_grid():
    files = request.files.getlist("file") or []
    if not files:
        abort(400, "no file uploaded")

    uid = current_user.id
    sg = get_split_grids()
    total_crops = 0
    grids_added = []

    for file in files:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            continue
        safe_name = _safe_filename(file.filename or "grid.jpg")
        dest = _user_input_dir(uid) / safe_name
        file.save(dest)

        n = _split_and_record(sg, dest, uid)
        total_crops += n
        grids_added.append({"filename": safe_name, "crops": n})

    return jsonify({"grids": grids_added, "total_crops": total_crops})


def _split_and_record(sg, grid_path: Path, user_id: int) -> int:
    img = cv2.imread(str(grid_path))
    if img is None:
        return 0
    img = sg.auto_orient(img)
    cells, method = sg.split_page(img)
    print(f"[split] {grid_path.name}: {sum(1 for c in cells if c is not None)} crops via {method}", file=sys.stderr)

    stem = grid_path.stem
    crops_dir = _user_crops_dir(user_id)
    # Namespace the grid identity by owner so two users can both upload "page01".
    grid_key = f"u{user_id}/{stem}"
    written = 0
    with db.connect(DB_PATH) as conn:
        grid_id = db.get_or_create_grid(conn, grid_key, str(grid_path.relative_to(ROOT)), user_id=user_id)
        for idx, cell in enumerate(cells):
            if cell is None:
                continue
            r, c = divmod(idx, 3)
            out_path = crops_dir / f"{stem}_r{r}c{c}.jpg"
            cv2.imwrite(str(out_path), cell, [cv2.IMWRITE_JPEG_QUALITY, 92])
            rel = str(out_path.relative_to(ROOT))
            db.insert_card_stub(conn, grid_id, rel, r, c, user_id=user_id)
            written += 1
        db.update_grid_count(conn, grid_id)
    return written


# ----------------------------------------------------------------------
# Pricing
# ----------------------------------------------------------------------

@app.route("/api/cards/<int:card_id>/price", methods=["POST"])
@login_required
def api_price_card(card_id: int):
    uid = current_user.id
    with db.connect(DB_PATH) as conn:
        card = db.get_card(conn, card_id, user_id=uid)
    if not card:
        abort(404)
    if not card.get("name"):
        return jsonify({"error": "card has no name — identify it first"}), 400
    try:
        result = _price_card(card)
        with db.connect(DB_PATH) as conn:
            updated = db.update_card(conn, card_id, result, user_id=uid)
        return jsonify(updated)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pricing/run-all", methods=["POST"])
@login_required
def api_run_pricing_all():
    uid = current_user.id
    use_terapeak = request.args.get("terapeak") == "1" or (request.get_json(silent=True) or {}).get("terapeak")
    with _job_lock:
        if _job_state["running"]:
            return jsonify({"error": "another job is running"}), 409
        _job_state.update(
            running=True, progress=0, total=0,
            message="starting…", terapeak=bool(use_terapeak),
        )

    n_terapeak_workers = int(os.getenv("TERAPEAK_WORKERS", "4"))
    n_tcg_workers = int(os.getenv("TCG_WORKERS", "6"))

    def worker():
        # Local copy so we can flip it on import error without touching the closure.
        terapeak_on = use_terapeak
        pool = None
        terapeak_thread = None
        # Resolved pokemontcg.io card objects, keyed by card_id. Filled during
        # Pass 1, read by Pass 2 to build Terapeak queries — saves 153
        # redundant API calls.
        resolved_cards: dict[int, dict] = {}
        resolved_lock = threading.Lock()
        pass1_progress = [0]
        pass2_progress = [0]

        try:
            with db.connect(DB_PATH) as conn:
                cards = db.list_cards(conn, sort="newest", user_id=uid)
            targets = [c for c in cards if c.get("name")]
            total_units = len(targets) * (2 if terapeak_on else 1)
            _job_state["total"] = total_units

            # ---- Pass 2 (Terapeak) starts CONCURRENTLY with Pass 1. ----
            # Pass 2 queries depend on resolved set-names from Pass 1, so we
            # gate each Terapeak submission on its card's Pass-1 completion.
            terapeak_ready = None  # unused
            terapeak_queries_ready: dict[int, str] = {}  # card_id -> query string
            queries_lock = threading.Lock()

            def update_message():
                p1 = pass1_progress[0]
                p2 = pass2_progress[0]
                if terapeak_on:
                    _job_state["progress"] = p1 + p2
                    _job_state["message"] = f"TCG/CM {p1}/{len(targets)} · Terapeak {p2}/{len(targets)}"
                else:
                    _job_state["progress"] = p1
                    _job_state["message"] = f"TCG/CM {p1}/{len(targets)}"

            def price_one_pass1(card: dict) -> None:
                """TCG + CM + eBay-MI for one card."""
                try:
                    patch, tcg_card = _price_card_pass1(card)
                    if tcg_card is not None:
                        with resolved_lock:
                            resolved_cards[int(card["id"])] = tcg_card
                    with db.connect(DB_PATH) as conn:
                        db.update_card(conn, int(card["id"]), patch, user_id=uid)
                    # Build the Terapeak query immediately so Pass 2 can drain.
                    if terapeak_on:
                        set_name = ((tcg_card or {}).get("set") or {}).get("name") or card.get("set_name") or ""
                        bits = [card.get("name") or "", set_name, card.get("card_number") or "", "pokemon"]
                        query = " ".join(b for b in bits if b)
                        with queries_lock:
                            terapeak_queries_ready[int(card["id"])] = query
                except Exception as exc:
                    print(f"[pricing pass1] {card.get('name')}: {exc}", file=sys.stderr)
                finally:
                    pass1_progress[0] += 1
                    update_message()

            # Spin Pass 1 across n_tcg_workers threads.
            pass1_executor = ThreadPoolExecutor(max_workers=n_tcg_workers)
            pass1_futures = [pass1_executor.submit(price_one_pass1, c) for c in targets]

            # ---- Pass 2: Terapeak in parallel via TerapeakPool. ----
            if terapeak_on:
                try:
                    from lib.terapeak_pool import TerapeakPool
                    from lib.terapeak_client import TerapeakNotLoggedIn
                except ImportError as exc:
                    _job_state["message"] = f"terapeak import failed: {exc}"
                    terapeak_on = False
                else:
                    pool = TerapeakPool(n_workers=n_terapeak_workers)

                    def on_terapeak(card_id: int, res):
                        if isinstance(res, Exception):
                            print(f"[terapeak] id={card_id}: {res}", file=sys.stderr)
                        else:
                            tp_med = res.get("median")
                            tp_count = res.get("count", 0)
                            try:
                                with db.connect(DB_PATH) as conn:
                                    row = db.get_card(conn, card_id, user_id=uid) or {}
                                result = aggregate(
                                    tcgplayer_market=row.get("tcgplayer_market"),
                                    ebay_median_30d=row.get("ebay_median_30d"),
                                    ebay_max_30d=row.get("ebay_max_30d"),
                                    cardmarket_trend_usd=row.get("cardmarket_trend_usd"),
                                    terapeak_median_usd=tp_med,
                                    pricecharting_usd=row.get("pricecharting_market"),
                                )
                                patch = {
                                    "terapeak_median_usd": tp_med,
                                    "terapeak_sold_count_365d": tp_count,
                                    "final_price": result.price,
                                    "pricing_confidence": result.confidence,
                                    "outlier_flag": result.outlier_flag,
                                    "needs_review": result.needs_review,
                                    "pricing_notes": result.notes or row.get("pricing_notes"),
                                }
                                with db.connect(DB_PATH) as conn:
                                    db.update_card(conn, card_id, patch, user_id=uid)
                            except Exception as exc:
                                print(f"[terapeak merge] id={card_id}: {exc}", file=sys.stderr)
                        pass2_progress[0] += 1
                        update_message()

                    def drain_terapeak():
                        """Wait for each card's Pass-1 query string, submit to pool, attach callback."""
                        submitted: set[int] = set()
                        futures = {}
                        while len(submitted) < len(targets):
                            # find ready queries that haven't been submitted yet
                            with queries_lock:
                                ready = [
                                    (cid, q) for cid, q in terapeak_queries_ready.items()
                                    if cid not in submitted
                                ]
                            for cid, q in ready:
                                submitted.add(cid)
                                fut = pool.submit(q, 365)
                                futures[fut] = cid
                            if not ready:
                                time.sleep(0.2)
                        for fut in as_completed(list(futures.keys())):
                            cid = futures[fut]
                            try:
                                res = fut.result()
                            except Exception as exc:
                                res = exc
                            on_terapeak(cid, res)

                    terapeak_thread = threading.Thread(target=drain_terapeak, daemon=True)
                    terapeak_thread.start()

            # Wait for Pass 1 to complete
            for f in pass1_futures:
                try:
                    f.result()
                except Exception:
                    pass
            pass1_executor.shutdown(wait=False)

            # Wait for Terapeak drain to finish
            if terapeak_thread is not None:
                terapeak_thread.join()

            _job_state["message"] = "done"
        finally:
            if pool is not None:
                try:
                    pool.close()
                except Exception:
                    pass
            _job_state["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/terapeak/status")
@login_required
def api_terapeak_status():
    state_path = ROOT / os.getenv("TERAPEAK_STATE_PATH", "output/cache/terapeak_state.json")
    return jsonify({"logged_in": state_path.exists(), "state_path": str(state_path)})


@app.route("/api/pricing/status")
@login_required
def api_pricing_status():
    return jsonify(_job_state)


def _price_card_pass1(card: dict) -> tuple[dict, Optional[dict]]:
    """Pass-1 pricing wrapper: returns (patch, resolved_card)."""
    return _price_card(card, terapeak=None, return_card=True)


def _price_card(card: dict, terapeak=None, return_card: bool = False):
    tcg = get_tcg()
    ebay = get_ebay()

    name = (card.get("name") or "").strip()
    set_name = card.get("set_name") or None
    set_code = card.get("set_code") or None
    card_number = card.get("card_number") or None
    is_holo = bool(card.get("is_holo"))

    tcg_price: Optional[float] = None
    cm_eur: Optional[float] = None
    tcg_card: Optional[dict] = None
    tcg_error: Optional[str] = None
    match_level: Optional[str] = None  # "exact", "no_number", "no_set", "name_only"
    # Try the most specific query first; if it 404s or returns nothing,
    # progressively drop fields. pokemontcg.io has been observed 404-ing
    # narrow queries under load.
    attempts: list[tuple[dict, str]] = [
        ({"set_code": set_code, "card_number": card_number}, "exact"),
        ({"set_code": set_code, "card_number": None},        "no_number"),
        ({"set_code": None, "card_number": card_number},     "no_set"),
        ({"set_code": None, "card_number": None},            "name_only"),
    ]
    seen: set = set()
    for kwargs, level in attempts:
        key = (kwargs.get("set_code"), kwargs.get("card_number"))
        if key in seen:
            continue
        seen.add(key)
        try:
            prices = tcg.lookup_prices(
                name=name,
                set_name=set_name,
                set_code=kwargs.get("set_code"),
                card_number=kwargs.get("card_number"),
                is_holo=is_holo,
            )
            if prices.get("card") is not None:
                tcg_price = prices.get("tcgplayer_market_usd")
                cm_eur = prices.get("cardmarket_trend_eur")
                tcg_card = prices.get("card")
                match_level = level
                tcg_error = None
                break
        except Exception as exc:
            tcg_error = str(exc)
            time.sleep(0.6)
    cm_usd = round(cm_eur * EUR_USD_RATE, 2) if cm_eur else None

    # Accuracy guard: if the match came from a name-only fallback (set_code
    # dropped), flag it so the user knows the match is approximate. Same if
    # TCG and CM disagree wildly — that's a signal the matched product is
    # not the variant the seller actually has.
    match_warning: Optional[str] = None
    if tcg_card is not None:
        matched_set_id = (tcg_card.get("set") or {}).get("id", "")
        if match_level in ("no_set", "name_only"):
            match_warning = (
                f"name-only match to {matched_set_id} — set_code was not provided, "
                "match may not reflect the actual printing"
            )
        if tcg_price and cm_usd and tcg_price > 0 and cm_usd > 0:
            ratio = max(tcg_price, cm_usd) / min(tcg_price, cm_usd)
            if ratio > 3.0:
                match_warning = (
                    (match_warning + "; " if match_warning else "")
                    + f"TCG/CM disagree {ratio:.1f}x on {matched_set_id} — variant mismatch?"
                )

    ebay_stats: dict[str, Any] = {"median": None, "max": None, "count": 0}
    try:
        query = ebay.build_query(name=name, set_name=set_name, card_number=card_number)
        ebay_stats = ebay.sold_stats(query=query, days=30, condition="NEW")
    except EbayAuthError as exc:
        print(f"[ebay auth] {exc}", file=sys.stderr)

    terapeak_stats: dict[str, Any] = {"median": None, "count": 0}
    if terapeak is not None:
        try:
            tp_query = " ".join(p for p in [name, set_name, card_number, "pokemon"] if p)
            terapeak_stats = terapeak.search(tp_query, days=365)
        except Exception as exc:
            print(f"[terapeak] {name}: {exc}", file=sys.stderr)

    pc_price: Optional[float] = None
    pricecharting = get_pricecharting()
    if pricecharting.enabled:
        try:
            pc_price, _ = pricecharting.lookup_price(
                name=name, set_name=set_name, card_number=card_number,
            )
        except Exception as exc:
            print(f"[pricecharting] {name}: {exc}", file=sys.stderr)

    result = aggregate(
        tcgplayer_market=tcg_price,
        ebay_median_30d=ebay_stats.get("median"),
        ebay_max_30d=ebay_stats.get("max"),
        cardmarket_trend_usd=cm_usd,
        terapeak_median_usd=terapeak_stats.get("median"),
        pricecharting_usd=pc_price,
    )

    # Build pricing_notes from all signals so the user has full context.
    notes_parts = []
    if result.notes:
        notes_parts.append(result.notes)
    if tcg_error and tcg_card is None:
        notes_parts.append(f"tcg lookup: {tcg_error}")
    if match_warning:
        notes_parts.append(f"⚠ {match_warning}")
    if match_level and match_level != "exact":
        notes_parts.append(f"match: {match_level}")

    # A match warning forces low confidence + review regardless of source agreement,
    # because high source agreement on a wrong card is the worst case (looks reliable
    # but mis-prices the listing).
    final_conf = result.confidence
    final_needs_review = result.needs_review
    if match_warning:
        final_conf = min(final_conf, 0.2)
        final_needs_review = True

    patch: dict[str, Any] = {
        "tcgplayer_market": tcg_price,
        "cardmarket_trend_eur": cm_eur,
        "cardmarket_trend_usd": cm_usd,
        "ebay_median_30d": ebay_stats.get("median"),
        "ebay_max_30d": ebay_stats.get("max"),
        "ebay_sold_count_30d": ebay_stats.get("count", 0),
        "terapeak_median_usd": terapeak_stats.get("median"),
        "terapeak_sold_count_365d": terapeak_stats.get("count", 0),
        "pricecharting_market": pc_price,
        "final_price": result.price,
        "pricing_confidence": final_conf,
        "outlier_flag": result.outlier_flag,
        "needs_review": final_needs_review,
        "pricing_notes": "; ".join(notes_parts),
    }
    if tcg_card is not None:
        patch["tcgplayer_product_id"] = tcg_card.get("id")
        patch["tcgplayer_url"] = (tcg_card.get("tcgplayer") or {}).get("url")
        patch["cardmarket_url"] = (tcg_card.get("cardmarket") or {}).get("url")
    if return_card:
        return patch, tcg_card
    return patch


# ----------------------------------------------------------------------
# Export + image upload
# ----------------------------------------------------------------------

def _cards_payload(cards: list[dict]) -> list[dict]:
    """Coerce DB rows into the dict shape the CSV generator + listers expect."""
    payload = []
    for c in cards:
        payload.append({
            "id": c["id"],
            "crop_path": c["crop_path"],
            "name": c["name"] or "",
            "set_name": c["set_name"] or "",
            "set_code": c["set_code"] or "",
            "card_number": c["card_number"] or "",
            "rarity": c["rarity"] or "",
            "is_holo": bool(c["is_holo"]),
            "condition_guess": c["condition_guess"] or "",
            "price": c["final_price"],
            "final_price": c["final_price"],
            "tcgplayer_product_id": c["tcgplayer_product_id"],
            "image_url": c["image_url"] or "",
            "confidence": c["pricing_confidence"],
        })
    return payload


@app.route("/api/export/csvs", methods=["POST"])
@login_required
def api_export_csvs():
    uid = current_user.id
    gc = get_csv_gen()
    with db.connect(DB_PATH) as conn:
        cards = db.list_cards(conn, sort="newest", user_id=uid)
    payload = _cards_payload(cards)

    # CSVs are per-user so one seller's export can't overwrite another's.
    out_dir = CSVS_DIR / f"u{uid}"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, rows_fn in (
        ("tcgplayer_bulk.csv", gc.tcgplayer_rows),
        ("whatnot_seller_hub.csv", gc.whatnot_rows),
        ("ebay_bulk.csv", gc.ebay_rows),
    ):
        rows = rows_fn(payload)
        # Use pandas only when present; otherwise write naively.
        path = out_dir / filename
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
@login_required
def api_upload_card_image(card_id: int):
    uid = current_user.id
    with db.connect(DB_PATH) as conn:
        card = db.get_card(conn, card_id, user_id=uid)
    if not card:
        abort(404)

    try:
        from lib import cloudinary_client
        cloudinary_client.configure()
        crop = ROOT / card["crop_path"]
        url = cloudinary_client.upload(str(crop))
        with db.connect(DB_PATH) as conn:
            updated = db.update_card(conn, card_id, {"image_url": url}, user_id=uid)
        return jsonify(updated)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ----------------------------------------------------------------------
# Publish to marketplaces
# ----------------------------------------------------------------------

@app.route("/api/publish/status")
@login_required
def api_publish_status():
    """Readiness probe for each site + the current publish job state."""
    # eBay: official API — are we authorized?
    try:
        from lib.ebay_lister import preflight
        ebay = preflight()
    except Exception as exc:  # pragma: no cover - import/runtime guard
        ebay = {"ready": False, "reason": str(exc)}

    # TCGPlayer / Whatnot: headless portal upload — is a session saved?
    def _portal_ready(env_key: str, default: str) -> dict:
        path = ROOT / os.getenv(env_key, default)
        return {
            "ready": path.exists(),
            "reason": "" if path.exists() else "no saved session — run python -m webapp.setup_portal",
        }

    return jsonify({
        "ebay": ebay,
        "tcgplayer": _portal_ready("TCGPLAYER_STATE_PATH", "output/cache/tcgplayer_state.json"),
        "whatnot": _portal_ready("WHATNOT_STATE_PATH", "output/cache/whatnot_state.json"),
        "job": _publish_state,
    })


@app.route("/api/publish/job-status")
@login_required
def api_publish_job_status():
    return jsonify(_publish_state)


@app.route("/api/publish/ebay", methods=["POST"])
@login_required
def api_publish_ebay():
    """Publish every eligible card as a live eBay listing (background job)."""
    uid = current_user.id
    with _publish_lock:
        if _publish_state["running"]:
            return jsonify({"error": "a publish job is already running"}), 409
        _publish_state.update(running=True, progress=0, total=0, message="starting…", site="ebay")

    def worker():
        try:
            from lib.ebay_lister import EbayLister, EbayListingError
            from lib.ebay_oauth import EbayUserNotAuthorized
            try:
                lister = EbayLister()
            except Exception as exc:
                _publish_state["message"] = f"cannot start: {exc}"
                return

            with db.connect(DB_PATH) as conn:
                cards = _cards_payload(db.list_cards(conn, sort="newest", user_id=uid))
            eligible = [c for c in cards if c.get("final_price") and c.get("image_url")]
            _publish_state["total"] = len(eligible)

            done = 0
            for card in eligible:
                title = card.get("name") or Path(card.get("crop_path", "")).stem
                try:
                    result = lister.publish_card(card)
                    patch = {
                        "ebay_listing_id": result.get("listing_id"),
                        "ebay_offer_id": result.get("offer_id"),
                        "ebay_listing_url": result.get("url"),
                        "ebay_listing_status": "listed",
                        "listed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    with db.connect(DB_PATH) as conn:
                        db.update_card(conn, int(card["id"]), patch, user_id=uid)
                except EbayUserNotAuthorized as exc:
                    _publish_state["message"] = str(exc)
                    return
                except EbayListingError as exc:
                    print(f"[publish ebay] FAILED {title}: {exc}", file=sys.stderr)
                    with db.connect(DB_PATH) as conn:
                        db.update_card(conn, int(card["id"]), {"ebay_listing_status": f"error: {exc}"[:200]}, user_id=uid)
                except Exception as exc:  # pragma: no cover
                    print(f"[publish ebay] error {title}: {exc}", file=sys.stderr)
                finally:
                    done += 1
                    _publish_state["progress"] = done
                    _publish_state["message"] = f"listed {done}/{len(eligible)}"
            _publish_state["message"] = f"done — {done} processed"
        finally:
            _publish_state["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/publish/portal", methods=["POST"])
@login_required
def api_publish_portal():
    """Generate the site's CSV from the DB and upload it via headless browser."""
    uid = current_user.id
    body = request.get_json(silent=True) or {}
    site = body.get("site") or request.args.get("site")
    if site not in ("tcgplayer", "whatnot"):
        return jsonify({"error": "site must be 'tcgplayer' or 'whatnot'"}), 400

    with _publish_lock:
        if _publish_state["running"]:
            return jsonify({"error": "a publish job is already running"}), 409
        _publish_state.update(running=True, progress=0, total=1, message="preparing CSV…", site=site)

    def worker():
        try:
            gc = get_csv_gen()
            with db.connect(DB_PATH) as conn:
                payload = _cards_payload(db.list_cards(conn, sort="newest", user_id=uid))
            out_dir = CSVS_DIR / f"u{uid}"
            out_dir.mkdir(parents=True, exist_ok=True)
            if site == "tcgplayer":
                filename, rows_fn = "tcgplayer_bulk.csv", gc.tcgplayer_rows
                from lib.tcgplayer_lister import TCGPlayerLister as Lister
            else:
                filename, rows_fn = "whatnot_seller_hub.csv", gc.whatnot_rows
                from lib.whatnot_lister import WhatnotLister as Lister
            path = out_dir / filename
            _write_csv(path, rows_fn(payload))

            _publish_state["message"] = f"uploading {filename} to {site}…"
            with Lister() as lister:
                result = lister.upload_csv(str(path))
            _publish_state["progress"] = 1
            _publish_state["message"] = (
                f"upload {'ok' if result.get('ok') else 'uncertain'}: {result.get('detail')}"
            )
        except Exception as exc:
            traceback.print_exc()
            _publish_state["message"] = f"failed: {exc}"
        finally:
            _publish_state["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"started": True})


# ----------------------------------------------------------------------
# Static crops + CSV downloads
# ----------------------------------------------------------------------

def _authorize_user_path(filename: str) -> None:
    """Crops/CSVs live under a per-user ``u<id>/`` prefix. Only let a user fetch
    their own (admins may fetch anyone's). 403 otherwise."""
    if getattr(current_user, "is_admin", False):
        return
    expected = f"u{current_user.id}/"
    # Normalise leading "./" and any stray separators.
    norm = filename.lstrip("/")
    if not norm.startswith(expected):
        abort(403)


@app.route("/crops/<path:filename>")
@app.route("/output/crops/<path:filename>")
@login_required
def serve_crop(filename: str):
    _authorize_user_path(filename)
    return send_from_directory(CROPS_DIR, filename)


@app.route("/csvs/<path:filename>")
@app.route("/output/csvs/<path:filename>")
@login_required
def download_csv(filename: str):
    _authorize_user_path(filename)
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
