"""Stage 3: portfolio snapshots + watchlist (per-user, catalog-aware)."""
from __future__ import annotations

from webapp import db


def _user_with_priced_cards(conn, username, values):
    uid = db.create_user(conn, username, "password-123")
    gid = db.get_or_create_grid(conn, f"u{uid}/p", "x.jpg", user_id=uid)
    for i, v in enumerate(values):
        cid = db.insert_card_stub(conn, gid, f"output/crops/u{uid}/p_r0c{i}.jpg", 0, i, user_id=uid)
        db.update_card(conn, cid, {"name": f"c{i}", "final_price": v}, user_id=uid)
    return uid


def test_snapshot_records_current_value(tmp_path):
    path = str(tmp_path / "p.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        uid = _user_with_priced_cards(conn, "alice", [10.0, 25.0, 5.0])
        snap = db.record_portfolio_snapshot(conn, uid)
        assert snap["total_value"] == 40.0 and snap["card_count"] == 3
        hist = db.portfolio_history(conn, uid)
    assert len(hist) == 1 and hist[0]["total_value"] == 40.0


def test_snapshots_are_per_user(tmp_path):
    path = str(tmp_path / "p.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        a = _user_with_priced_cards(conn, "alice", [10.0])
        b = _user_with_priced_cards(conn, "bob", [99.0])
        db.record_portfolio_snapshot(conn, a)
        db.record_portfolio_snapshot(conn, b)
        assert db.portfolio_history(conn, a)[0]["total_value"] == 10.0
        assert db.portfolio_history(conn, b)[0]["total_value"] == 99.0


def test_watchlist_add_list_remove(tmp_path):
    path = str(tmp_path / "w.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        uid = db.create_user(conn, "alice", "password-123")
        db.upsert_catalog_card(conn, {"id": "base1-4", "name": "Charizard", "set_name": "Base", "number": "4"})
        db.record_price_points(conn, "base1-4", {"tcgplayer_market": 300.0}, final=305.0)

        assert db.add_watch(conn, uid, "base1-4") is True
        # Unknown catalog id is refused.
        assert db.add_watch(conn, uid, "no-such-card") is False

        items = db.list_watch(conn, uid)
        assert len(items) == 1
        assert items[0]["name"] == "Charizard"
        assert items[0]["latest_final"] == 305.0   # latest price joined in

        db.remove_watch(conn, uid, "base1-4")
        assert db.list_watch(conn, uid) == []


def test_watchlist_is_per_user_and_dedup(tmp_path):
    path = str(tmp_path / "w.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        a = db.create_user(conn, "alice", "password-123")
        b = db.create_user(conn, "bob", "password-123")
        db.upsert_catalog_card(conn, {"id": "base1-4", "name": "Charizard"})
        db.add_watch(conn, a, "base1-4")
        db.add_watch(conn, a, "base1-4")   # idempotent
        assert len(db.list_watch(conn, a)) == 1
        assert db.list_watch(conn, b) == []
