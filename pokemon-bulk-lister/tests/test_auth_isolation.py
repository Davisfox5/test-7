"""Stage 1 auth: user/invite logic + per-user data isolation.

These hit webapp.db directly (no Flask), mirroring test_db_migration.py, so they
run without the web stack. The isolation tests are the security-critical part:
they assert user A can never read or mutate user B's cards.
"""
from __future__ import annotations

from webapp import db


def _seed_two_users_with_a_card_each(path):
    db.init_db(path)
    with db.connect(path) as conn:
        alice = db.create_user(conn, "alice", "password-a", role="admin")
        bob = db.create_user(conn, "bob", "password-b")

        ga = db.get_or_create_grid(conn, "u%d/page01" % alice, "in/a.jpg", user_id=alice)
        ca = db.insert_card_stub(conn, ga, "output/crops/u%d/page01_r0c0.jpg" % alice, 0, 0, user_id=alice)
        db.update_card(conn, ca, {"name": "Alice Charizard", "final_price": 100.0}, user_id=alice)

        gb = db.get_or_create_grid(conn, "u%d/page01" % bob, "in/b.jpg", user_id=bob)
        cb = db.insert_card_stub(conn, gb, "output/crops/u%d/page01_r0c0.jpg" % bob, 0, 0, user_id=bob)
        db.update_card(conn, cb, {"name": "Bob Pikachu", "final_price": 5.0}, user_id=bob)
    return alice, bob, ca, cb


def test_list_cards_is_scoped_per_user(tmp_path):
    path = str(tmp_path / "iso.sqlite")
    alice, bob, _ca, _cb = _seed_two_users_with_a_card_each(path)
    with db.connect(path) as conn:
        a_cards = db.list_cards(conn, user_id=alice)
        b_cards = db.list_cards(conn, user_id=bob)
    assert [c["name"] for c in a_cards] == ["Alice Charizard"]
    assert [c["name"] for c in b_cards] == ["Bob Pikachu"]


def test_get_card_across_users_returns_none(tmp_path):
    path = str(tmp_path / "iso.sqlite")
    alice, bob, ca, cb = _seed_two_users_with_a_card_each(path)
    with db.connect(path) as conn:
        # Bob cannot fetch Alice's card by id.
        assert db.get_card(conn, ca, user_id=bob) is None
        # Alice can fetch her own.
        assert db.get_card(conn, ca, user_id=alice)["name"] == "Alice Charizard"


def test_update_card_across_users_is_refused(tmp_path):
    path = str(tmp_path / "iso.sqlite")
    alice, bob, ca, _cb = _seed_two_users_with_a_card_each(path)
    with db.connect(path) as conn:
        # Bob tries to overwrite Alice's price — must be refused and leave it intact.
        assert db.update_card(conn, ca, {"final_price": 0.01}, user_id=bob) is None
        assert db.get_card(conn, ca, user_id=alice)["final_price"] == 100.0


def test_card_stats_are_scoped(tmp_path):
    path = str(tmp_path / "iso.sqlite")
    alice, bob, _ca, _cb = _seed_two_users_with_a_card_each(path)
    with db.connect(path) as conn:
        a_stats = db.card_stats(conn, user_id=alice)
        b_stats = db.card_stats(conn, user_id=bob)
    assert a_stats["total"] == 1 and a_stats["total_value"] == 100.0
    assert b_stats["total"] == 1 and b_stats["total_value"] == 5.0


def test_password_verification(tmp_path):
    path = str(tmp_path / "auth.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        db.create_user(conn, "carol", "s3cret-password")
        assert db.verify_login(conn, "carol", "s3cret-password")["username"] == "carol"
        assert db.verify_login(conn, "carol", "wrong") is None
        assert db.verify_login(conn, "nobody", "whatever") is None


def test_invite_single_use(tmp_path):
    path = str(tmp_path / "invite.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        admin = db.create_user(conn, "admin", "admin-password", role="admin")
        code = db.create_invite(conn, role="member", created_by=admin)

        user = db.redeem_invite(conn, code, "dave", "dave-password")
        assert user is not None and user["role"] == "member"
        # Code is now spent — a second redemption fails.
        assert db.redeem_invite(conn, code, "eve", "eve-password") is None
        # Unknown code fails.
        assert db.redeem_invite(conn, "nope", "frank", "frank-password") is None


def test_invite_rejects_duplicate_username(tmp_path):
    path = str(tmp_path / "invite2.sqlite")
    db.init_db(path)
    with db.connect(path) as conn:
        db.create_user(conn, "taken", "password-x")
        code = db.create_invite(conn)
        assert db.redeem_invite(conn, code, "taken", "password-y") is None
        # The invite must remain unredeemed after a failed attempt.
        assert db.get_invite(conn, code)["redeemed_by"] is None
