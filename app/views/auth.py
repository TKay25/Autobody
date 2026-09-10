"""Authentication routes."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User, utcnow

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _wants_json() -> bool:
    return request.path.startswith("/api/") or request.is_json


def _safe_next() -> str:
    """Where to land after signing in.

    Flask-Login bounces anonymous visitors to `/login?next=<path>`, so the value
    is attacker-controllable. Only ever honour a same-site path — anything with a
    scheme, a host or a protocol-relative `//` prefix is dropped, otherwise the
    sign-in form becomes an open redirect.
    """
    target = request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("views.shell")


@bp.post("/login")
def do_login():
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    remember = str(data.get("remember", "")).lower() in {"1", "true", "on", "yes"}

    user = User.query.filter(db.func.lower(User.email) == email).first()
    if not user or not user.check_password(password) or not user.is_active_user:
        if _wants_json():
            return jsonify({"error": "invalid_credentials",
                            "message": "Incorrect email or password."}), 401
        return render_template("login.html", error="Incorrect email or password.",
                               email=email), 401

    login_user(user, remember=remember)
    user.last_login_at = utcnow()
    db.session.commit()

    if _wants_json():
        return jsonify({"ok": True, "user": user.to_dict()})
    return redirect(_safe_next())


@bp.post("/logout")
@login_required
def do_logout():
    logout_user()
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(url_for("views.login"))


@bp.get("/me")
@login_required
def me():
    return jsonify({"user": current_user.to_dict()})
