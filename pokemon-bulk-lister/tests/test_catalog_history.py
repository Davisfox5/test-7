"""Stage 2 catalog + price history: upsert, recording, search, and the
internal-source (PriceCharting) exclusion from shared history."""
from __future__ import annotations

from webapp import db


CARD = {
    "id": "base1-4",
    "name": "Charizard",
    "set_id": "base1",
    "set_name": "Base",
    "number": "4",
    "rarity": "Rare Holo",
    "image_small": "http://img/small.png",
    "image_large": "http://img/large.png",
    "tcgplayer_url": "http://tcg/charizard",
    "cardmarket_url": "http://cm/charizard",
}


def test_upsert_is_idempotent_and_updates(tmp_path):
    path = str(tmp_path / "cat.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        cid = db.upsert_catalog_card(conn, CARD)
        assert cid == "base1-4"
        # Second upsert with a changed field updates in place (no duplicate row).
        db.upsert_catalog_card(conn, {**CARD, "rarity": "Promo"})
        n = conn.execute("SELECT COUNT(*) AS n FROM card_catalog").fetchone()["n"]
        assert n == 1
        assert db.get_catalog_card(conn, "base1-4")["rarity"] == "Promo"


def test_upsert_without_id_is_noop(tmp_path):
    path = str(tmp_path / "cat.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        assert db.upsert_catalog_card(conn, {"name": "x"}) is None


def test_record_price_points_appends_history(tmp_path):
    path = str(tmp_path / "cat.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        db.upsert_catalog_card(conn, CARD)
        # Two pricing runs -> appended, not overwritten.
        db.record_price_points(conn, "base1-4", {"tcgplayer_market": 300.0, "ebay_median_30d": None}, final=305.0)
        db.record_price_points(conn, "base1-4", {"tcgplayer_market": 320.0}, final=318.0)
        history = db.price_history(conn, "base1-4")
    finals = [p["price"] for p in history if p["source"] == "final"]
    tcg = [p["price"] for p in history if p["source"] == "tcgplayer_market"]
    assert finals == [305.0, 318.0]          # both runs retained, in order
    assert tcg == [300.0, 320.0]
    # Null/zero sources are not recorded.
    assert all(p["source"] != "ebay_median_30d" for p in history)


def test_pricecharting_excluded_from_shared_history(tmp_path):
    path = str(tmp_path / "cat.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        db.upsert_catalog_card(conn, CARD)
        db.record_price_points(
            conn, "base1-4",
            {"tcgplayer_market": 300.0, "pricecharting_usd": 290.0}, final=300.0,
        )
        shared = db.price_history(conn, "base1-4", exclude_sources=db.INTERNAL_ONLY_SOURCES)
        full = db.price_history(conn, "base1-4")
    assert any(p["source"] == "pricecharting_usd" for p in full)        # stored
    assert all(p["source"] != "pricecharting_usd" for p in shared)      # but filtered from shared view


def test_search_catalog_matches_name_and_set(tmp_path):
    path = str(tmp_path / "cat.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        db.upsert_catalog_card(conn, CARD)
        db.upsert_catalog_card(conn, {**CARD, "id": "base1-58", "name": "Pikachu", "number": "58"})
        assert {r["id"] for r in db.search_catalog(conn, "char")} == {"base1-4"}
        assert {r["id"] for r in db.search_catalog(conn, "Base")} == {"base1-4", "base1-58"}
        assert db.search_catalog(conn, "") == []
