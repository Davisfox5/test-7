"""Account management CLI — bootstrap the first admin and mint invite codes.

    python -m webapp.manage create-admin <username>          # prompts for password
    python -m webapp.manage invite [--role member|admin] [--note "..."]
    python -m webapp.manage list-users

Invite codes are single-use. Hand the printed code to a new user; they redeem it
at /invite/<code> to pick a password. There is intentionally no open sign-up.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp import db  # noqa: E402

load_dotenv(ROOT / ".env")
DB_PATH = str(ROOT / os.getenv("OUTPUT_DIR", "output") / Path("db.sqlite"))


def _prompt_password() -> str:
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Confirm: "):
        print("passwords did not match", file=sys.stderr)
        raise SystemExit(1)
    if len(pw) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        raise SystemExit(1)
    return pw


def cmd_create_admin(args: argparse.Namespace) -> int:
    db.init_db(DB_PATH)
    with db.connect(DB_PATH) as conn:
        if db.get_user_by_username(conn, args.username):
            print(f"user {args.username!r} already exists", file=sys.stderr)
            return 1
        uid = db.create_user(conn, args.username, _prompt_password(), role="admin")
    print(f"created admin {args.username!r} (id={uid})")
    return 0


def cmd_invite(args: argparse.Namespace) -> int:
    db.init_db(DB_PATH)
    with db.connect(DB_PATH) as conn:
        if db.count_users(conn) == 0:
            print("no users yet — run `create-admin` first", file=sys.stderr)
            return 1
        code = db.create_invite(conn, role=args.role, note=args.note)
    print("invite code (single use):")
    print(f"  {code}")
    print(f"  redeem at /invite/{code}")
    return 0


def cmd_list_users(_args: argparse.Namespace) -> int:
    db.init_db(DB_PATH)
    with db.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id"
        ).fetchall()
    for r in rows:
        print(f"  {r['id']:>3}  {r['username']:<20} {r['role']:<8} {r['created_at']}")
    if not rows:
        print("  (no users)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_admin = sub.add_parser("create-admin", help="create the first admin user")
    p_admin.add_argument("username")
    p_admin.set_defaults(func=cmd_create_admin)

    p_invite = sub.add_parser("invite", help="mint a single-use invite code")
    p_invite.add_argument("--role", choices=("member", "admin"), default="member")
    p_invite.add_argument("--note", default=None)
    p_invite.set_defaults(func=cmd_invite)

    p_list = sub.add_parser("list-users", help="list accounts")
    p_list.set_defaults(func=cmd_list_users)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
