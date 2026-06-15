"""SQLite layer for the Pokémon bulk-lister web UI.

One row per crop (cards table) plus a small grids table for the upload provenance.
All fields nullable — the pipeline fills them in across multiple steps.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from werkzeug.security import check_password_hash, generate_password_hash


DEFAULT_DB_PATH = "output/db.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invites (
    code         TEXT PRIMARY KEY,
    role         TEXT NOT NULL DEFAULT 'member',
    note         TEXT,
    created_by   INTEGER REFERENCES users(id),
    redeemed_by  INTEGER REFERENCES users(id),
    redeemed_at  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS grids (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    filename      TEXT UNIQUE NOT NULL,
    original_path TEXT NOT NULL,
    uploaded_at   TEXT NOT NULL DEFAULT (datetime('now')),
    crop_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cards (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER REFERENCES users(id) ON DELETE CASCADE,
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
    pricecharting_market    REAL,
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

CREATE TABLE IF NOT EXISTS card_catalog (
    id             TEXT PRIMARY KEY,          -- pokemontcg.io card id, e.g. "base1-4"
    name           TEXT,
    set_id         TEXT,
    set_name       TEXT,
    number         TEXT,
    rarity         TEXT,
    image_small    TEXT,
    image_large    TEXT,
    tcgplayer_url  TEXT,
    cardmarket_url TEXT,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS price_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id  TEXT NOT NULL REFERENCES card_catalog(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,                -- 'tcgplayer_market', 'final', ...
    price       REAL NOT NULL,
    captured_by INTEGER REFERENCES users(id),
    captured_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_price_points_catalog ON price_points(catalog_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_catalog_name         ON card_catalog(name);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    total_value REAL NOT NULL,
    card_count  INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watchlist (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    catalog_id TEXT NOT NULL REFERENCES card_catalog(id) ON DELETE CASCADE,
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, catalog_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_user ON portfolio_snapshots(user_id, captured_at);
"""

# Price sources that are licensed for internal use only and must never appear in
# a shared/cross-user view (PriceCharting's data license forbids third-party
# display). The shared catalog history serializer filters these out.
INTERNAL_ONLY_SOURCES = ("pricecharting_usd",)

# user_id indexes are created after migrations add the column (a legacy DB won't
# have it when the schema script first runs).
_USER_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cards_user ON cards(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_grids_user ON grids(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_price_points_catalog ON price_points(catalog_id, captured_at)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_name ON card_catalog(name)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_user ON portfolio_snapshots(user_id, captured_at)",
)


CARD_FIELDS = (
    "id", "user_id", "grid_id", "crop_path", "row", "col",
    "name", "set_name", "set_code", "card_number", "rarity",
    "is_holo", "condition_guess", "id_confidence",
    "tcgplayer_market", "cardmarket_trend_eur", "cardmarket_trend_usd",
    "ebay_median_30d", "ebay_max_30d", "ebay_sold_count_30d",
    "terapeak_median_usd", "terapeak_sold_count_365d",
    "pricecharting_market",
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
    ("pricecharting_market", "REAL"),
    ("user_id", "INTEGER"),
)

# Tables added after the cards/grids schema shipped; created idempotently so an
# existing DB gains auth without a rebuild.
_TABLE_MIGRATIONS = (
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS invites (
        code TEXT PRIMARY KEY,
        role TEXT NOT NULL DEFAULT 'member',
        note TEXT,
        created_by INTEGER REFERENCES users(id),
        redeemed_by INTEGER REFERENCES users(id),
        redeemed_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS card_catalog (
        id TEXT PRIMARY KEY, name TEXT, set_id TEXT, set_name TEXT, number TEXT,
        rarity TEXT, image_small TEXT, image_large TEXT, tcgplayer_url TEXT,
        cardmarket_url TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS price_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        catalog_id TEXT NOT NULL REFERENCES card_catalog(id) ON DELETE CASCADE,
        source TEXT NOT NULL, price REAL NOT NULL,
        captured_by INTEGER REFERENCES users(id),
        captured_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        total_value REAL NOT NULL, card_count INTEGER NOT NULL DEFAULT 0,
        captured_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS watchlist (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        catalog_id TEXT NOT NULL REFERENCES card_catalog(id) ON DELETE CASCADE,
        added_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (user_id, catalog_id)
    )""",
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
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for stmt in _TABLE_MIGRATIONS:
        conn.execute(stmt)

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
    for column, coltype in _MIGRATIONS:
        if column not in existing:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {column} {coltype}")

    grid_cols = {row["name"] for row in conn.execute("PRAGMA table_info(grids)").fetchall()}
    if "user_id" not in grid_cols:
        conn.execute("ALTER TABLE grids ADD COLUMN user_id INTEGER")

    for stmt in _USER_INDEXES:
        conn.execute(stmt)


def get_or_create_grid(
    conn: sqlite3.Connection,
    filename: str,
    original_path: str,
    user_id: Optional[int] = None,
) -> int:
    row = conn.execute("SELECT id FROM grids WHERE filename = ?", (filename,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO grids (filename, original_path, user_id) VALUES (?, ?, ?)",
        (filename, original_path, user_id),
    )
    return int(cur.lastrowid)


def insert_card_stub(
    conn: sqlite3.Connection,
    grid_id: int,
    crop_path: str,
    row: int,
    col: int,
    user_id: Optional[int] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO cards (grid_id, crop_path, row, col, user_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (grid_id, crop_path, row, col, user_id),
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
    user_id: Optional[int] = None,
) -> list[dict]:
    where = []
    params: list[Any] = []
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
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

    rows = conn.execute(f"SELECT * FROM cards {where_sql} ORDER BY {order}", params).fetchall()
    return [dict(r) for r in rows]


def get_card(
    conn: sqlite3.Connection, card_id: int, user_id: Optional[int] = None
) -> Optional[dict]:
    if user_id is not None:
        row = conn.execute(
            "SELECT * FROM cards WHERE id = ? AND user_id = ?", (card_id, user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


def update_card(
    conn: sqlite3.Connection,
    card_id: int,
    patch: dict[str, Any],
    user_id: Optional[int] = None,
) -> Optional[dict]:
    # Ownership guard: when a user_id is supplied, refuse to touch a row that
    # isn't theirs (returns None rather than silently updating nothing).
    if user_id is not None and get_card(conn, card_id, user_id) is None:
        return None
    safe = {k: v for k, v in patch.items() if k in CARD_FIELDS and k not in ("id", "crop_path", "created_at")}
    if not safe:
        return get_card(conn, card_id, user_id)
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
    return get_card(conn, card_id, user_id)


def card_stats(conn: sqlite3.Connection, user_id: Optional[int] = None) -> dict:
    where_sql = "WHERE user_id = ?" if user_id is not None else ""
    params = (user_id,) if user_id is not None else ()
    rows = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN name IS NOT NULL AND name <> '' THEN 1 ELSE 0 END) AS identified,
            SUM(CASE WHEN final_price IS NOT NULL THEN 1 ELSE 0 END) AS priced,
            SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END) AS flagged,
            SUM(CASE WHEN image_url IS NOT NULL AND image_url <> '' THEN 1 ELSE 0 END) AS uploaded,
            SUM(CASE WHEN ebay_listing_id IS NOT NULL AND ebay_listing_id <> '' THEN 1 ELSE 0 END) AS ebay_listed,
            COALESCE(SUM(final_price), 0) AS total_value
        FROM cards
        {where_sql}
        """,
        params,
    ).fetchone()
    return dict(rows or {})


# ----------------------------------------------------------------------
# Users + invites (auth)
# ----------------------------------------------------------------------

def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    role: str = "member",
    email: Optional[str] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, email) VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), role, email),
    )
    return int(cur.lastrowid)


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def verify_login(conn: sqlite3.Connection, username: str, password: str) -> Optional[dict]:
    """Return the user dict on a correct password, else None."""
    user = get_user_by_username(conn, username)
    if not user or not check_password_hash(user["password_hash"], password):
        return None
    return user


def count_users(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])


def create_invite(
    conn: sqlite3.Connection,
    role: str = "member",
    created_by: Optional[int] = None,
    note: Optional[str] = None,
) -> str:
    """Mint a single-use invite code; returns the code string."""
    code = secrets.token_urlsafe(12)
    conn.execute(
        "INSERT INTO invites (code, role, created_by, note) VALUES (?, ?, ?, ?)",
        (code, role, created_by, note),
    )
    return code


def get_invite(conn: sqlite3.Connection, code: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
    return dict(row) if row else None


def redeem_invite(
    conn: sqlite3.Connection, code: str, username: str, password: str
) -> Optional[dict]:
    """Atomically consume an unredeemed invite and create its user.

    Returns the new user dict, or None if the code is unknown/already used or the
    username is taken.
    """
    invite = get_invite(conn, code)
    if not invite or invite["redeemed_by"] is not None:
        return None
    if get_user_by_username(conn, username) is not None:
        return None
    user_id = create_user(conn, username, password, role=invite["role"])
    conn.execute(
        "UPDATE invites SET redeemed_by = ?, redeemed_at = datetime('now') WHERE code = ?",
        (user_id, code),
    )
    return get_user_by_id(conn, user_id)


# ----------------------------------------------------------------------
# Catalog + price history (Stage 2)
# ----------------------------------------------------------------------

_CATALOG_FIELDS = (
    "id", "name", "set_id", "set_name", "number", "rarity",
    "image_small", "image_large", "tcgplayer_url", "cardmarket_url",
)


def upsert_catalog_card(conn: sqlite3.Connection, cat: dict) -> Optional[str]:
    """Insert/refresh a canonical catalog row keyed by pokemontcg.io id.

    Returns the catalog id, or None if the dict has no id.
    """
    cid = cat.get("id")
    if not cid:
        return None
    cols = [c for c in _CATALOG_FIELDS if c in cat]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    conn.execute(
        f"""
        INSERT INTO card_catalog ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {updates}, updated_at = datetime('now')
        """,
        [cat.get(c) for c in cols],
    )
    return cid


def record_price_points(
    conn: sqlite3.Connection,
    catalog_id: str,
    sources: dict[str, Optional[float]],
    final: Optional[float] = None,
    captured_by: Optional[int] = None,
) -> int:
    """Append a timestamped point per non-null source (plus 'final').

    Every pricing run appends rather than overwriting — this is what gives the
    catalog card a price history to chart.
    """
    rows: list[tuple] = []
    for source, price in sources.items():
        if price is not None and price > 0:
            rows.append((catalog_id, source, float(price), captured_by))
    if final is not None and final > 0:
        rows.append((catalog_id, "final", float(final), captured_by))
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO price_points (catalog_id, source, price, captured_by) VALUES (?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def get_catalog_card(conn: sqlite3.Connection, catalog_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM card_catalog WHERE id = ?", (catalog_id,)).fetchone()
    return dict(row) if row else None


def search_catalog(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    rows = conn.execute(
        """
        SELECT * FROM card_catalog
        WHERE name LIKE ? OR set_name LIKE ?
        ORDER BY name, set_name
        LIMIT ?
        """,
        (f"%{q}%", f"%{q}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def price_history(
    conn: sqlite3.Connection,
    catalog_id: str,
    exclude_sources: tuple[str, ...] = (),
) -> list[dict]:
    """Chronological price points for a catalog card.

    ``exclude_sources`` drops licensed-internal sources (e.g. PriceCharting) from
    any shared/cross-user view.
    """
    params: list[Any] = [catalog_id]
    where = "catalog_id = ?"
    if exclude_sources:
        placeholders = ", ".join("?" for _ in exclude_sources)
        where += f" AND source NOT IN ({placeholders})"
        params.extend(exclude_sources)
    rows = conn.execute(
        f"SELECT source, price, captured_at FROM price_points WHERE {where} ORDER BY captured_at",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# Portfolio snapshots + watchlist (Stage 3)
# ----------------------------------------------------------------------

def record_portfolio_snapshot(conn: sqlite3.Connection, user_id: int) -> dict:
    """Snapshot the user's current total value + card count for the value chart."""
    stats = card_stats(conn, user_id=user_id)
    total = float(stats.get("total_value") or 0.0)
    count = int(stats.get("priced") or 0)
    conn.execute(
        "INSERT INTO portfolio_snapshots (user_id, total_value, card_count) VALUES (?, ?, ?)",
        (user_id, total, count),
    )
    return {"total_value": total, "card_count": count}


def portfolio_history(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT total_value, card_count, captured_at FROM portfolio_snapshots "
        "WHERE user_id = ? ORDER BY captured_at",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def add_watch(conn: sqlite3.Connection, user_id: int, catalog_id: str) -> bool:
    """Watch a catalog card. Returns False if the catalog card doesn't exist."""
    if get_catalog_card(conn, catalog_id) is None:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (user_id, catalog_id) VALUES (?, ?)",
        (user_id, catalog_id),
    )
    return True


def remove_watch(conn: sqlite3.Connection, user_id: int, catalog_id: str) -> None:
    conn.execute(
        "DELETE FROM watchlist WHERE user_id = ? AND catalog_id = ?",
        (user_id, catalog_id),
    )


def list_watch(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """The user's watched catalog cards, each with its latest final / TCG price."""
    rows = conn.execute(
        """
        SELECT c.*, w.added_at,
            (SELECT price FROM price_points p
               WHERE p.catalog_id = c.id AND p.source = 'final'
               ORDER BY captured_at DESC LIMIT 1) AS latest_final,
            (SELECT price FROM price_points p
               WHERE p.catalog_id = c.id AND p.source = 'tcgplayer_market'
               ORDER BY captured_at DESC LIMIT 1) AS latest_tcg
        FROM watchlist w
        JOIN card_catalog c ON c.id = w.catalog_id
        WHERE w.user_id = ?
        ORDER BY w.added_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def maybe_import_legacy_json(
    conn: sqlite3.Connection,
    cards_json: str = "output/cards.json",
    cards_priced_json: str = "output/cards_priced.json",
    user_id: Optional[int] = None,
) -> int:
    """If the DB is empty and legacy JSON files exist, import them.

    Imported rows are assigned to ``user_id`` (the seed admin) so they're owned
    once auth is on.
    """
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
        grid_id = get_or_create_grid(conn, grid_filename, crop_path, user_id=user_id)
        row, col = _parse_row_col(crop_path)
        card_id = insert_card_stub(conn, grid_id, crop_path, row, col, user_id=user_id)

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
        patch["user_id"] = user_id
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
