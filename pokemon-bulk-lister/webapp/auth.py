"""Flask-Login glue for the bulk-lister.

Keeps all the password/user/invite *logic* in ``webapp.db`` (which has no Flask
dependency and is unit-tested directly); this module only adapts those rows into
the ``UserMixin`` objects Flask-Login expects and wires up the login manager.
"""
from __future__ import annotations

from typing import Optional

from flask_login import LoginManager, UserMixin

from webapp import db


class User(UserMixin):
    def __init__(self, row: dict) -> None:
        self.id = row["id"]
        self.username = row["username"]
        self.role = row.get("role", "member")
        self.email = row.get("email")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def init_login_manager(app, db_path: str) -> LoginManager:
    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.login_message = "Please sign in."
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str) -> Optional[User]:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        with db.connect(db_path) as conn:
            row = db.get_user_by_id(conn, uid)
        return User(row) if row else None

    return login_manager
