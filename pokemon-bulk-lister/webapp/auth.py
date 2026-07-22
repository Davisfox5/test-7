"""Session auth: register / login / logout.

Plain Flask signed-cookie sessions + werkzeug password hashing — no extra
dependencies. `login_required` guards the portfolio layer; the legacy
processing-bench routes stay open (it's a local tool).
"""
from __future__ import annotations

import functools
import os
import re
import secrets
from pathlib import Path

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from webapp.models import AlertRule, User, get_session

ROOT = Path(__file__).resolve().parent.parent

auth_bp = Blueprint("auth", __name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Default sell-signal: suggest selling when a card is up ≥25% over 30 days.
SELL_SIGNAL_PCT = float(os.getenv("SELL_SIGNAL_PCT", "25"))
SELL_SIGNAL_WINDOW_DAYS = int(os.getenv("SELL_SIGNAL_WINDOW_DAYS", "30"))


def ensure_secret_key(app) -> None:
    """Stable secret so sessions survive restarts. Env wins; else a keyfile."""
    key = os.getenv("SECRET_KEY")
    if not key:
        path = ROOT / "output" / ".secret_key"
        if path.exists():
            key = path.read_text().strip()
        else:
            key = secrets.token_hex(32)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(key)
            path.chmod(0o600)
    app.secret_key = key


def current_user_id() -> int | None:
    return session.get("uid")


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "login required"}), 401
            return redirect(url_for("auth.login_page", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def _credentials_from_request() -> tuple[str, str]:
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    return email, password


def _respond(ok_redirect: str, error: str | None = None, template: str | None = None):
    """Uniform JSON/HTML responses for form + fetch clients."""
    wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
    if error:
        if wants_json:
            return jsonify({"error": error}), 400
        return render_template(template, error=error), 400
    if wants_json:
        return jsonify({"ok": True, "redirect": ok_redirect})
    return redirect(ok_redirect)


@auth_bp.route("/register", methods=["GET"])
def register_page():
    return render_template("register.html")


@auth_bp.route("/register", methods=["POST"])
def register():
    email, password = _credentials_from_request()
    if not _EMAIL_RE.match(email):
        return _respond("", "enter a valid email address", "register.html")
    if len(password) < 8:
        return _respond("", "password must be at least 8 characters", "register.html")

    s = get_session()
    try:
        if s.query(User).filter_by(email=email).one_or_none():
            return _respond("", "an account with that email already exists", "register.html")
        # pbkdf2 explicitly: werkzeug's scrypt default needs OpenSSL scrypt,
        # which this deployment's Python (3.9) lacks.
        user = User(email=email, password_hash=generate_password_hash(password, method="pbkdf2:sha256"))
        s.add(user)
        s.flush()
        # Every account starts with the sell-signal rule; users can delete it.
        s.add(AlertRule(
            user_id=user.id,
            kind="sell_signal",
            threshold=SELL_SIGNAL_PCT,
            window_days=SELL_SIGNAL_WINDOW_DAYS,
        ))
        s.commit()
        session["uid"] = user.id
        session.permanent = True
    finally:
        s.close()
    return _respond("/portfolio")


@auth_bp.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")


@auth_bp.route("/login", methods=["POST"])
def login():
    email, password = _credentials_from_request()
    s = get_session()
    try:
        user = s.query(User).filter_by(email=email).one_or_none()
        if user is None or not check_password_hash(user.password_hash, password):
            return _respond("", "invalid email or password", "login.html")
        session["uid"] = user.id
        session.permanent = True
    finally:
        s.close()
    target = request.args.get("next") or "/portfolio"
    if not target.startswith("/"):
        target = "/portfolio"
    return _respond(target)


@auth_bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.pop("uid", None)
    return redirect("/login")
