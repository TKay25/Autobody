"""Application factory for the Topclass Auto Body Workshop OS."""
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from config import Config, get_config

from .constants import (
    CLAIM_STATUSES,
    CLAIM_STATUS_LABELS,
    INSURERS,
    INVOICE_STATUSES,
    PART_CATEGORIES,
    PART_STATUSES,
    PAYMENT_METHODS,
    PRIORITIES,
    PRIORITY_COLOURS,
    QC_CHECKLIST,
    ROLE_LABELS,
    ROLES,
    SERVICE_NAMES,
    SERVICES,
    STAGE_COLOURS,
    STAGE_LABELS,
    STAGE_PROGRESS,
    STAGES,
    SUPPLIERS,
    VEHICLE_COLOURS,
    VEHICLE_MAKES,
    VEHICLE_MODELS,
    VEHICLE_MODELS_COMMON,
)
from .extensions import csrf, db, login_manager, migrate

__version__ = "1.0.0"


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_object or get_config())

    _configure_logging(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_jinja(app)
    _register_error_handlers(app)
    _register_cli(app)

    with app.app_context():
        db.create_all()
        _bootstrap_reference_data(app)

    return app


# ─────────────────────────────────────────────────────────────────────────────
def _configure_logging(app: Flask) -> None:
    if not app.debug:
        logging.basicConfig(level=logging.INFO)


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # The WhatsApp webhook is called by Meta (no CSRF token available).
    csrf.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication_required"}), 401
        from flask import redirect, url_for

        return redirect(url_for("views.login", next=request.path))

    app.config.setdefault("UPLOAD_DIR", Config.UPLOAD_DIR)
    app.config["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)


def _bootstrap_reference_data(app: Flask) -> None:
    """Give a database with no accounts something to sign in with.

    Render's filesystem is ephemeral, so a SQLite deployment comes back with
    empty tables after every release, restart or idle spin-down. The sign-in
    page still renders in that state — but no credentials can ever match, which
    looks exactly like a broken login rather than an empty database.

    Seeding is idempotent and only fires when the user table is genuinely
    empty, so it can never overwrite a live install. Opt out with
    AUTO_SEED_STAFF=false (the default outside production).
    """
    if not app.config.get("AUTO_SEED_STAFF"):
        return

    from .models import User
    from .seed import seed_reference_data

    try:
        if User.query.count():
            return
        app.logger.info("User table is empty — seeding staff accounts and stock.")
        seed_reference_data()
        db.session.commit()
    except Exception:  # a failed seed must never stop the app from booting
        db.session.rollback()
        app.logger.warning("Auto-seed of reference data failed.", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
def _register_blueprints(app: Flask) -> None:
    from .views import api, auth, docs, views, whatsapp

    app.register_blueprint(views.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(docs.bp)
    app.register_blueprint(whatsapp.bp)


def _register_jinja(app: Flask) -> None:
    @app.context_processor
    def inject_globals() -> dict:
        return {
            "COMPANY": {
                "name": app.config["COMPANY_NAME"],
                "address": app.config["COMPANY_ADDRESS"],
                "tel": app.config["COMPANY_TEL"],
                "mobile": app.config["COMPANY_MOBILE"],
                "email": app.config["COMPANY_EMAIL"],
                "hours": app.config["COMPANY_HOURS"],
                "website": app.config["COMPANY_WEBSITE"],
            },
            "APP_VERSION": __version__,
            "DEMO_ACCOUNTS": _demo_accounts(app),
            "DEMO_PASSWORD": (
                app.config["SEED_PASSWORD"] if app.config.get("SHOW_DEMO_ACCOUNTS") else None
            ),
        }

    def _demo_accounts(flask_app: Flask) -> list[dict]:
        """Sign-in shortcuts generated from the table the seeder uses.

        Deriving them from `seed.STAFF` means the buttons can never advertise an
        account that does not exist. Off by default in production, because a
        public sign-in page should not list working credentials.
        """
        if not flask_app.config.get("SHOW_DEMO_ACCOUNTS"):
            return []
        from .seed import STAFF

        order = ["owner", "manager", "estimator", "frontdesk"]
        by_role = {role: (name, email) for name, email, role, _phone in STAFF}
        accounts = []
        for role in order:
            if role not in by_role:
                continue
            name, email = by_role[role]
            accounts.append({
                "name": name,
                "email": email,
                "label": ROLE_LABELS.get(role, role),
                "initials": "".join(p[0].upper() for p in name.split()[:2]),
            })
        return accounts

    @app.template_global("static_url")
    def static_url(filename: str) -> str:
        """Cache-busting static URL based on the file's mtime.

        Without this, shops on slow links keep serving a stale stylesheet or
        bundle after a deploy until someone hard-refreshes.
        """
        from flask import url_for

        path = Path(app.static_folder or "") / filename
        try:
            stamp = int(path.stat().st_mtime)
        except OSError:
            stamp = 1
        return url_for("static", filename=filename, v=stamp)

    @app.template_filter("money")
    def money(value) -> str:
        try:
            return f"{Decimal(str(value or 0)):,.2f}"
        except Exception:  # noqa: BLE001
            return "0.00"

    @app.template_filter("nice_date")
    def nice_date(value) -> str:
        if not value:
            return "—"
        try:
            return value.strftime("%d %b %Y")
        except AttributeError:
            return str(value)


def _register_error_handlers(app: Flask) -> None:
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def csrf_failed(e):
        """CSRF failures must be legible to the SPA, not an HTML 400 page."""
        app.logger.warning("CSRF failure on %s: %s", request.path, e.description)
        if request.path.startswith("/api/"):
            return jsonify({
                "error": "csrf_failed",
                "message": "Your session expired. Refresh the page and try again.",
            }), 400
        return render_template(
            "error.html", code=400,
            message="Your session expired. Please sign in again.",
        ), 400

    @app.errorhandler(403)
    def forbidden(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "forbidden"}), 403
        return render_template("error.html", code=403, message="You do not have access to that."), 403

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found"}), 404
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(e):  # pragma: no cover
        db.session.rollback()
        app.logger.exception("Unhandled error: %s", e)
        if request.path.startswith("/api/"):
            return jsonify({"error": "server_error"}), 500
        return render_template("error.html", code=500, message="Something went wrong."), 500


def _register_cli(app: Flask) -> None:
    import click

    @app.cli.command("init-db")
    def init_db() -> None:
        """Create all tables."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("seed")
    @click.option("--demo/--no-demo", default=True, help="Include demo customers and jobs.")
    def seed_cmd(demo: bool) -> None:
        """Load reference data (and optional demo records)."""
        from .seed import run_seed

        run_seed(with_demo=demo)
        click.echo("Seed complete.")

    @app.cli.command("reset-db")
    def reset_db() -> None:
        """Drop and recreate all tables, then seed."""
        from .seed import run_seed

        db.drop_all()
        db.create_all()
        run_seed(with_demo=True)
        click.echo("Database reset and seeded.")


# Re-exported so blueprints can `from .. import meta` without importing the world.
def reference_meta() -> dict:
    return {
        "stages": [{"code": s, "label": STAGE_LABELS[s], "colour": STAGE_COLOURS.get(s, "secondary")}
                   for s in STAGES],
        "stage_progress": STAGE_PROGRESS,
        "services": SERVICES,
        "service_names": SERVICE_NAMES,
        "insurers": INSURERS,
        "claim_statuses": [{"code": c, "label": CLAIM_STATUS_LABELS[c]} for c in CLAIM_STATUSES],
        "invoice_statuses": INVOICE_STATUSES,
        "payment_methods": PAYMENT_METHODS,
        "part_categories": PART_CATEGORIES,
        "part_statuses": PART_STATUSES,
        "suppliers": SUPPLIERS,
        "priorities": PRIORITIES,
        "priority_colours": PRIORITY_COLOURS,
        "roles": [{"code": r, "label": ROLE_LABELS[r]} for r in ROLES],
        "qc_checklist": QC_CHECKLIST,
        "vehicle_makes": VEHICLE_MAKES,
        "vehicle_models": VEHICLE_MODELS,
        "vehicle_models_common": VEHICLE_MODELS_COMMON,
        "vehicle_colours": VEHICLE_COLOURS,
    }
