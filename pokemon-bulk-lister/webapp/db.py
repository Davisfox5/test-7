"""SQLite layer for the Pokémon bulk-lister web UI.

One row per crop (cards table) plus a small grids table for the upload provenance.
All fields nullable — the pipeline fills them in across multiple steps.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


DEFAULT_DB_PATH = "output/db.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS grids (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT UNIQUE NOT NULL,
    original_path TEXT NOT NULL,
    uploaded_at   TEXT NOT NULL DEFAULT (datetime('now')),
    crop_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cards (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_id                 INTEGER REFERENCES grids(id) ON DELETE CASCADE,
    crop_path               TEXT UNIQUE NOT NULL,
    row                     INTEGER,
    col                     INTEGER,

    -- identification (filled by you / Claude vision)
    name                    TEXT,
    set_name                TEXT,
    set_code                TEXT,
    card_number             TEXT,
    rarity                  TEXT,
    is_holo                 INTEGER NOT NULL DEFAULT 0,
    condition_guess         TEXT,
    id_confidence           REAL NOT NULL DEFAULT 0.0,

    -- prices
    tcgplayer_market        REAL,
    cardmarket_trend_eur    REAL,
    cardmarket_trend_usd    REAL,
    ebay_median_30d         REAL,
    ebay_max_30d            REAL,
    ebay_sold_count_30d     INTEGER NOT NULL DEFAULT 0,
    terapeak_median_usd     REAL,
    terapeak_sold_count_365d INTEGER NOT NULL DEFAULT 0,
    final_price             REAL,
    pricing_confidence      REAL NOT NULL DEFAULT 0.0,
    outlier_flag            INTEGER NOT NULL DEFAULT 0,
    needs_review            INTEGER NOT NULL DEFAULT 0,
    pricing_notes           TEXT,

    -- enrichment
    tcgplayer_product_id    TEXT,
    tcgplayer_url           TEXT,
    cardmarket_url          TEXT,
    image_url               TEXT,

    -- listing (eBay Sell API is per-card; TCGPlayer/Whatnot are batch CSV uploads)
    ebay_listing_id         TEXT,
    ebay_offer_id           TEXT,
    ebay_listing_url        TEXT,
    ebay_listing_status     TEXT,
    listed_at               TEXT,

    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cards_grid         ON cards(grid_id);
CREATE INDEX IF NOT EXISTS idx_cards_needs_review ON cards(needs_review);
CREATE INDEX IF NOT EXISTS idx_cards_name         ON cards(name);
"""


CARD_FIELDS = (
    "id", "grid_id", "crop_path", "row", "col",
    "name", "set_name", "set_code", "card_number", "rarity",
    "is_holo", "condition_guess", "id_confidence",
    "tcgplayer_market", "cardmarket_trend_eur", "cardmarket_trend_usd",
    "ebay_median_30d", "ebay_max_30d", "ebay_sold_count_30d",
    "terapeak_median_usd", "terapeak_sold_count_365d",
    "final_price", "pricing_confidence", "outlier_flag",
    "needs_review", "pricing_notes",
    "tcgplayer_product_id", "tcgplayer_url", "cardmarket_url", "image_url",
    "ebay_listing_id", "ebay_offer_id", "ebay_listing_url", "ebay_listing_status", "listed_at",
    "created_at", "updated_at",
)

# Columns added after the original schema shipped; applied as idempotent
# ALTER TABLEs so existing DBs migrate forward on launch.
_MIGRATIONS = (
    ("ebay_listing_id", "TEXT"),
    ("ebay_offer_id", "TEXT"),
    ("ebay_listing_url", "TEXT"),
    ("ebay_listing_status", "TEXT"),
    ("listed_at", "TEXT"),
)

EDITABLE_ID_FIELDS = (
    "name", "set_name", "set_code", "card_number", "rarity",
    "is_holo", "condition_guess", "id_confidence",
)


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    # WAL allows one writer at a time; without a busy_timeout a second concurrent
    # writer raises "database is locked" immediately, and the parallel pricing
    # job then silently drops that card's update. Wait instead.
    conn.execute("PRAGMA busy_timeout = 10000;")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
    for column, coltype in _MIGRATIONS:
        if column not in existing:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {column} {coltype}")


def get_or_create_grid(conn: sqlite3.Connection, filename: str, original_path: str) -> int:
    row = conn.execute("SELECT id FROM grids WHERE filename = ?", (filename,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO grids (filename, original_path) VALUES (?, ?)",
        (filename, original_path),
    )
    return int(cur.lastrowid)


def insert_card_stub(
    conn: sqlite3.Connection,
    grid_id: int,
    crop_path: str,
    row: int,
    col: int,
) -> int:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO cards (grid_id, crop_path, row, col)
        VALUES (?, ?, ?, ?)
        """,
        (grid_id, crop_path, row, col),
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    existing = conn.execute(
        "SELECT id FROM cards WHERE crop_path = ?", (crop_path,)
    ).fetchone()
    return int(existing["id"])


def update_grid_count(conn: sqlite3.Connection, grid_id: int) -> None:
    conn.execute(
        "UPDATE grids SET crop_count = (SELECT COUNT(*) FROM cards WHERE grid_id = ?) WHERE id = ?",
        (grid_id, grid_id),
    )


def list_cards(
    conn: sqlite3.Connection,
    sort: str = "confidence_asc",
    needs_review_only: bool = False,
    unidentified_only: bool = False,
) -> list[dict]:
    where = []
    if needs_review_only:
        where.append("needs_review = 1")
    if unidentified_only:
        where.append("(name IS NULL OR name = '')")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order = {
        "confidence_asc": "pricing_confidence ASC, needs_review DESC, id ASC",
        "confidence_desc": "pricing_confidence DESC, id ASC",
        "price_desc": "final_price DESC NULLS LAST, id ASC",
        "price_asc": "final_price ASC NULLS LAST, id ASC",
        "newest": "created_at DESC, id DESC",
        "oldest": "created_at ASC, id ASC",
    }.get(sort, "pricing_confidence ASC, id ASC")

    rows = conn.execute(f"SELECT * FROM cards {where_sql} ORDER BY {order}").fetchall()
    return [dict(r) for r in rows]


def get_card(conn: sqlite3.Connection, card_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


def update_card(conn: sqlite3.Connection, card_id: int, patch: dict[str, Any]) -> Optional[dict]:
    safe = {k: v for k, v in patch.items() if k in CARD_FIELDS and k not in ("id", "crop_path", "created_at")}
    if not safe:
        return get_card(conn, card_id)
    # Coerce booleans.
    for bool_field in ("is_holo", "outlier_flag", "needs_review"):
        if bool_field in safe:
            safe[bool_field] = 1 if safe[bool_field] else 0
    cols = ", ".join(f"{k} = ?" for k in safe.keys())
    values = list(safe.values()) + [card_id]
    conn.execute(
        f"UPDATE cards SET {cols}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    return get_card(conn, card_id)


def card_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN name IS NOT NULL AND name <> '' THEN 1 ELSE 0 END) AS identified,
            SUM(CASE WHEN final_price IS NOT NULL THEN 1 ELSE 0 END) AS priced,
            SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END) AS flagged,
            SUM(CASE WHEN image_url IS NOT NULL AND image_url <> '' THEN 1 ELSE 0 END) AS uploaded,
            SUM(CASE WHEN ebay_listing_id IS NOT NULL AND ebay_listing_id <> '' THEN 1 ELSE 0 END) AS ebay_listed,
            COALESCE(SUM(final_price), 0) AS total_value
        FROM cards
        """
    ).fetchone()
    return dict(rows or {})


def maybe_import_legacy_json(
    conn: sqlite3.Connection,
    cards_json: str = "output/cards.json",
    cards_priced_json: str = "output/cards_priced.json",
) -> int:
    """If the DB is empty and legacy JSON files exist, import them."""
    count = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()
    if count and int(count["n"]) > 0:
        return 0

    legacy_path = Path(cards_priced_json) if Path(cards_priced_json).exists() else Path(cards_json)
    if not legacy_path.exists():
        return 0
    try:
        entries = json.loads(legacy_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    imported = 0
    for entry in entries:
        crop_path = entry.get("crop_path")
        if not crop_path:
            continue
        # Fake grid record from the filename stem.
        grid_filename = Path(crop_path).stem.split("_r")[0] or Path(crop_path).stem
        grid_id = get_or_create_grid(conn, grid_filename, crop_path)
        row, col = _parse_row_col(crop_path)
        card_id = insert_card_stub(conn, grid_id, crop_path, row, col)

        patch: dict[str, Any] = {
            k: entry.get(k)
            for k in (
                "name", "set_name", "set_code", "card_number", "rarity",
                "is_holo", "condition_guess",
                "tcgplayer_market", "cardmarket_trend_eur",
                "ebay_median_30d", "ebay_max_30d", "ebay_sold_count_30d",
                "terapeak_median_usd", "terapeak_sold_count_365d",
                "outlier_flag", "needs_review", "pricing_notes",
                "tcgplayer_product_id", "tcgplayer_url", "cardmarket_url", "image_url",
            )
            if k in entry
        }
        patch["id_confidence"] = float(entry.get("confidence", 0.0) or 0.0)
        patch["final_price"] = entry.get("price")
        patch["pricing_confidence"] = float(entry.get("confidence", 0.0) or 0.0)
        sources = entry.get("sources") or {}
        if isinstance(sources, dict):
            patch.setdefault("tcgplayer_market", sources.get("tcgplayer_market"))
            patch.setdefault("ebay_median_30d", sources.get("ebay_median_30d"))
            patch.setdefault("ebay_max_30d", sources.get("ebay_max_30d"))
            patch.setdefault("cardmarket_trend_usd", sources.get("cardmarket_trend_usd"))
            patch.setdefault("terapeak_median_usd", sources.get("terapeak_median_usd"))

        update_card(conn, card_id, patch)
        imported += 1

    for grid_id_row in conn.execute("SELECT id FROM grids").fetchall():
        update_grid_count(conn, int(grid_id_row["id"]))

    return imported


def _parse_row_col(crop_path: str) -> tuple[Optional[int], Optional[int]]:
    """Pull row/col from a path like '..._r0c2.jpg'."""
    stem = Path(crop_path).stem
    if "_r" not in stem or "c" not in stem:
        return None, None
    try:
        tail = stem.split("_r")[-1]
        r, c = tail.split("c")
        return int(r), int(c)
    except (ValueError, IndexError):
        return None, None
