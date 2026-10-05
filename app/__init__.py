"""Application factory for the Topclass Auto Body Workshop OS."""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from config import Config, get_config

from . import tz
from .constants import (
    BOOKING_OUTCOMES,
    BOOKING_SLOTS,
    BOOKING_STATUS_LABELS,
    BOOKING_STATUSES,
    INVOICE_STATUSES,
    PART_CATEGORIES,
    PART_STATUSES,
    PAYMENT_METHOD_LABELS,
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
    TASK_STATUS_COLOURS,
    TASK_STATUS_LABELS,
    TASK_STATUSES,
    VEHICLE_COLOURS,
    VEHICLE_MAKES,
    VEHICLE_MODELS,
    VEHICLE_MODELS_COMMON,
)
from .extensions import csrf, db, login_manager, migrate
from .services import phone as phone_numbers

__version__ = "1.0.0"


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_object or get_config())

    _configure_logging(app)
    _check_production_config(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_jinja(app)
    _register_error_handlers(app)
    _register_cli(app)
    _log_whatsapp_mode(app)

    with app.app_context():
        db.create_all()
        _ensure_schema(app)
        _bootstrap_reference_data(app)
        _bootstrap_owner(app)

    return app


# ─────────────────────────────────────────────────────────────────────────────
def _check_production_config(app: Flask) -> None:
    """Report a production deploy that is configured to be broken.

    Every check here is a failure that would otherwise be silent: the app boots,
    renders, and is quietly wide open or quietly unreachable. Starting anyway is
    a deliberate choice — a site that will not come up is its own kind of outage,
    and it is worse when the person who needs it up cannot see why. Nothing here
    fixes anything; it names what is wrong, at ERROR, on every boot.

    Read the log. Each line is a real hole, not a hardening suggestion:

      * a published SEED_PASSWORD hands out working staff logins
      * no WA_APP_SECRET leaves the webhook open to unsigned POSTs
      * a guessable SECRET_KEY makes every session cookie forgeable
    """
    if not app.config.get("IS_PRODUCTION"):
        return

    problems = []

    if app.config.get("SECRET_KEY") in (None, "", "dev-secret-change-me"):
        problems.append(
            "SECRET_KEY is unset or still the value published in this repository. "
            "Anyone who can read the source can forge a session cookie and sign in "
            "as the owner. Set SECRET_KEY to a long random string - on the host, not "
            "in .env - for example: python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\""
        )

    if (app.config.get("AUTO_SEED_STAFF")
            and app.config.get("SEED_PASSWORD") == "topclass123"):
        problems.append(
            "AUTO_SEED_STAFF is on and SEED_PASSWORD is still the published default, "
            "so an empty database gets seeded with accounts whose passwords are in "
            "the repository (owner@topclass.co.zw / topclass123). Set SEED_PASSWORD, "
            "or set OWNER_EMAIL + OWNER_PASSWORD and AUTO_SEED_STAFF=false."
        )

    if app.config.get("WA_MODE") == "live":
        if not app.config.get("WA_APP_SECRET"):
            problems.append(
                "WA_MODE=live with no WA_APP_SECRET: the webhook is public and "
                "accepts unsigned POSTs, so anyone who learns the URL can create "
                "customers, enquiries and job cards. Copy the app secret from "
                "Meta -> Settings -> Basic."
            )
        base = app.config.get("PUBLIC_BASE_URL") or ""
        if "127.0.0.1" in base or "localhost" in base:
            problems.append(
                f"WA_MODE=live with PUBLIC_BASE_URL={base!r}: quotations, invoices "
                "and receipts go to customers as links to that address, and Meta "
                "fetches them from the public internet. Set the real HTTPS URL."
            )

    if problems:
        app.logger.error(
            "PRODUCTION CONFIGURATION IS UNSAFE - starting anyway. These are not "
            "fixed by starting:\n%s",
            "\n".join(f"  - {problem}" for problem in problems),
        )

    # Survivable, but not something anyone should discover later.
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if uri.startswith("sqlite"):
        app.logger.warning(
            "Production is on SQLite (%s). On a host with an ephemeral filesystem "
            "every deploy, restart and scale-to-zero wipes the workshop's records. "
            "Attach a persistent disk, or point DATABASE_URL at Postgres.", uri,
        )
    if app.config.get("WA_VERIFY_TOKEN") == "topclass-verify-token":
        app.logger.warning(
            "WA_VERIFY_TOKEN is still the published default. It only guards the "
            "verification handshake, but set it to something private anyway."
        )


def _log_whatsapp_mode(app: Flask) -> None:
    """Say once, at boot, whether this process can actually send WhatsApp.

    A simulator-mode deployment is otherwise completely invisible: the webhook
    accepts messages, the bot answers, the inbox renders the conversation — and
    every reply is filed as delivered. One line here is the difference between a
    two-minute env fix and a day of wondering why nobody replies.
    """
    if app.config.get("TESTING"):
        return
    from .services.whatsapp_client import WhatsAppClient

    client = WhatsAppClient(app)
    if client.is_live:
        app.logger.info("WhatsApp: LIVE via phone number id %s.",
                        app.config.get("WA_PHONE_NUMBER_ID"))
    else:
        app.logger.warning(
            "WhatsApp: SIMULATOR - replies are stored but NOT sent to WhatsApp. "
            "Missing: %s. In production set these on the host, not in .env.",
            ", ".join(client.missing_live_settings) or "(unknown)",
        )


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


def _ensure_schema(app: Flask) -> None:
    """Add new columns, and retire ones the models have dropped.

    ``create_all`` builds missing tables but never alters a table that is
    already there, so a database provisioned before a model changed is left a
    step behind. Two directions are handled here:

    * new columns the model declares but the database lacks are added;
    * columns and tables the models have stopped declaring are dropped.

    The second part matters as much as the first. ``is_insurance`` was declared
    ``NOT NULL`` with only a Python-side default, so it had no server default:
    the moment SQLAlchemy stopped sending it, every ``INSERT`` on that table
    failed. Retiring a field therefore means removing the physical column too.
    """
    from .schema import drop_columns, drop_tables, ensure_columns

    try:
        # Purely additive, so this is safe against a populated database. No
        # UNIQUE/NOT NULL here on purpose: SQLite refuses to add a UNIQUE column
        # through ALTER, and a NOT NULL column with no default would fail on
        # existing rows anyway. A database created fresh gets the full
        # definition from the model instead.
        ensure_columns(db.engine, "bookings", {
            "confirmed_by_id": "INTEGER",
            "confirmed_at": "TIMESTAMP",
            "attended_by_id": "INTEGER",
            "attended_at": "TIMESTAMP",
            "booking_reference": "VARCHAR(30)",
            "outcome": "VARCHAR(20)",
            "rescheduled_count": "INTEGER DEFAULT 0",
            "reminder_sent_at": "TIMESTAMP",
            "requested_slot_date": "DATE",
            "requested_slot_time": "VARCHAR(10)",
            "requested_at": "TIMESTAMP",
            "requested_note": "TEXT",
        })

        ensure_columns(db.engine, "job_cards", {
            "feedback_requested_at": "TIMESTAMP",
            "feedback_rating": "INTEGER",
            "feedback_text": "TEXT",
            "feedback_at": "TIMESTAMP",
        })

        # An ID number is verified against the physical document when the vehicle
        # is collected, so it lives on the customer rather than the job card.
        ensure_columns(db.engine, "customers", {
            "id_number": "VARCHAR(60)",
        })

        # The navigation rail's per-account order, and the display toggles that
        # go with it. Columns added to a table that already existed, so they need
        # the guard like any other.
        ensure_columns(db.engine, "users", {
            "nav_order": "TEXT",
            "preferences": "TEXT",
        })

        # A column added to a table that already existed. `create_all()` makes a
        # missing *table* but never ALTERs one, so on any database provisioned
        # before this column existed every query touching it fails — locally and
        # on Render. Registering it here is the whole reason this guard exists.
        ensure_columns(db.engine, "outbound_queue", {
            "message_id": "INTEGER",
        })

        # Insurance claims are no longer part of the product: every job is
        # priced and paid as retail, so the claim record and the insurer
        # columns it hung off have been retired.
        drop_tables(db.engine, ["claims"])
        drop_columns(db.engine, "job_cards", ["is_insurance"])
        drop_columns(db.engine, "estimates", ["is_insurance", "excess"])
        drop_columns(db.engine, "invoices", ["is_insurance", "insurer_code"])
    except Exception:  # a schema nicety must never stop the app from booting
        db.session.rollback()
        app.logger.warning("Schema guard failed.", exc_info=True)

    # Idempotency keys only matter while a client could still be holding one, so
    # the table is trimmed on boot rather than by a scheduled job. Nothing needs
    # the cron; an app that never restarts simply accumulates rows harmlessly.
    try:
        from .services import idempotency

        removed = idempotency.prune()
        if removed:
            app.logger.info("Pruned %s stale idempotency key(s).", removed)
    except Exception:
        db.session.rollback()
        app.logger.warning("Idempotency prune failed.", exc_info=True)

    # Messages that were delivered or given up on a fortnight ago have served
    # their purpose. The pending ones are never touched.
    try:
        from .services import outbound

        stale = outbound.prune()
        if stale:
            app.logger.info("Pruned %s settled outbound queue row(s).", stale)
        waiting = outbound.pending_count()
        if waiting:
            # Worth saying at boot: these are customers who have not heard from
            # us, and the retry job is the only thing that will change that.
            app.logger.warning(
                "%s WhatsApp message(s) are still waiting to be delivered. Run "
                "'flask outbound-retry' or the /api/whatsapp/outbox/retry cron.",
                waiting)
    except Exception:
        db.session.rollback()
        app.logger.warning("Outbound queue check failed.", exc_info=True)


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


def _bootstrap_owner(app: Flask) -> None:
    """Create the account named by OWNER_EMAIL / OWNER_PASSWORD, if any.

    A no-op unless both variables are set. Existing accounts are never touched,
    so changing the password through the UI afterwards is not undone by the
    next restart. Deliberately outside the AUTO_SEED_STAFF gate: naming an
    account explicitly is a stronger signal than an empty-database heuristic.
    """
    email = app.config.get("OWNER_EMAIL")
    password = app.config.get("OWNER_PASSWORD")
    if not email or not password:
        return

    from .models import User

    try:
        if User.query.filter(db.func.lower(User.email) == email).first():
            return
        user = User(
            full_name=app.config.get("OWNER_NAME") or email.split("@")[0],
            email=email,
            role="owner",
            is_active_user=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        app.logger.info("Created the configured owner account %s.", email)
    except Exception:  # a failed bootstrap must never stop the app booting
        db.session.rollback()
        app.logger.warning("Could not create the owner account.", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
def _register_blueprints(app: Flask) -> None:
    from .views import api, auth, docs, views, whatsapp

    app.register_blueprint(views.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(docs.bp)
    app.register_blueprint(whatsapp.bp)
    # The same webhook under the shorter /webhook URL Meta users tend to type.
    app.register_blueprint(whatsapp.alias)


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
        # A stored timestamp is UTC and would print two hours early; a calendar
        # date (a promised date, a due date) carries no time and needs none.
        if isinstance(value, datetime):
            value = tz.to_local(value)
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

    @app.cli.command("booking-reminders")
    @click.option("--date", "day", default=None,
                  help="ISO date to remind for (default: tomorrow).")
    def booking_reminders(day: str | None) -> None:
        """Nudge tomorrow's bookings. Safe to run as often as you like."""
        from datetime import date as _date

        from .services.notifications import send_due_booking_reminders

        target = _date.fromisoformat(day) if day else None
        result = send_due_booking_reminders(target)
        click.echo(
            f"{result['sent']} reminder(s) sent for {result['day']} "
            f"({result['due']} due, {result['skipped']} already sent)."
        )

    @app.cli.command("feedback-requests")
    @click.option("--date", "day", default=None,
                  help="ISO date the vehicles were collected (default: yesterday).")
    def feedback_requests(day: str | None) -> None:
        """Ask yesterday's customers how we did. Safe to run repeatedly."""
        from datetime import date as _date

        from .services.notifications import send_due_feedback_requests

        target = _date.fromisoformat(day) if day else None
        result = send_due_feedback_requests(target)
        click.echo(
            f"{result['sent']} feedback request(s) sent for {result['day']} "
            f"({result['due']} due, {result['skipped']} already asked)."
        )

    @app.cli.command("outbound-retry")
    def outbound_retry() -> None:
        """Re-send WhatsApp messages that could not go out while the link was down.

        Safe to run as often as you like — each message carries its own
        next-attempt time, so a call that is too early finds nothing due.
        """
        from .services import outbound

        result = outbound.retry_due()
        click.echo(
            f"{result['sent']} message(s) delivered, {result['rescheduled']} still "
            f"trying, {result['failed']} given up on "
            f"({result['due']} were due, {result['pending']} waiting)."
        )

    @app.cli.command("purge-business-data")
    @click.option("--keep-whatsapp", is_flag=True,
                  help="Keep the WhatsApp inbox (conversations and messages).")
    def purge_business_data(yes: bool, keep_whatsapp: bool) -> None:
        """Remove every transactional record, keeping logins and the parts list.

        For going live: the demo customers, job cards, invoices and the sample
        WhatsApp thread all go, while the staff accounts you sign in with and the
        parts catalogue stay behind. It reports what it found first, and deletes
        nothing without --yes.

        Use --keep-whatsapp once real customers have started messaging, so their
        enquiries survive the cleanup.
        """
        from .models import (
            ActivityLog, Booking, BookingPhoto, Customer, Estimate, EstimateItem,
            Invoice, JobCard, JobPart, JobPhoto, JobStageEvent, NotificationLog,
            Part, Payment, PaymentProof, QcResult, StockMovement, Task, User,
            Vehicle, WaConversation, WaMessage,
        )

        # Children before parents. Postgres enforces the foreign keys, so this
        # order is not cosmetic — it is the difference between a clean delete and
        # a cascade of integrity errors.
        plan = [
            ("estimate items", EstimateItem),
            ("estimates", Estimate),
            ("job stage events", JobStageEvent),
            ("job photos", JobPhoto),
            ("QC results", QcResult),
            ("job parts", JobPart),
            ("payment proofs", PaymentProof),
            ("payments", Payment),
            ("invoices", Invoice),
            ("booking photos", BookingPhoto),
            ("bookings", Booking),
            ("tasks", Task),
            ("job cards", JobCard),
            ("vehicles", Vehicle),
            ("customers", Customer),
            ("stock movements", StockMovement),
            ("notifications", NotificationLog),
            ("activity log", ActivityLog),
        ]
        if not keep_whatsapp:
            plan += [("WhatsApp messages", WaMessage),
                     ("WhatsApp conversations", WaConversation)]

        counts = [(label, model.query.count()) for label, model in plan]
        total = sum(n for _, n in counts)

        click.echo(f"Keeping: {User.query.count()} users, {Part.query.count()} parts.")
        if keep_whatsapp:
            click.echo(f"Keeping: {WaConversation.query.count()} WhatsApp thread(s).")
        else:
            numbers = [c.wa_id for c in WaConversation.query.order_by(WaConversation.id)]
            if numbers:
                click.echo("WhatsApp threads to delete: " + ", ".join(numbers))
        click.echo("")
        for label, n in counts:
            if n:
                click.echo(f"  {n:>5}  {label}")
        click.echo(f"  {total:>5}  TOTAL rows")

        if not yes:
            click.echo("\nNothing was deleted. Re-run with --yes to apply.")
            return

        if keep_whatsapp:
            # The threads outlive their customers, so drop the links first or the
            # foreign keys block the delete.
            WaConversation.query.update({WaConversation.customer_id: None},
                                        synchronize_session=False)
            WaMessage.query.update({WaMessage.job_id: None}, synchronize_session=False)

        for _, model in plan:
            model.query.delete(synchronize_session=False)
        db.session.commit()
        click.echo(f"\nDeleted {total} rows. The database is ready for real data.")

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
        "booking_statuses": BOOKING_STATUSES,
        "booking_status_labels": BOOKING_STATUS_LABELS,
        "booking_outcomes": [{"code": code, "label": label}
                             for code, label in BOOKING_OUTCOMES.items()],
        "booking_slots": BOOKING_SLOTS,
        "task_statuses": TASK_STATUSES,
        "task_status_labels": TASK_STATUS_LABELS,
        "task_status_colours": TASK_STATUS_COLOURS,
        "invoice_statuses": INVOICE_STATUSES,
        "payment_methods": PAYMENT_METHODS,
        "payment_method_labels": PAYMENT_METHOD_LABELS,
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
        # Phone numbers: the dial-code dropdown and the one the desk's fields
        # default to. The same value the bot dials with, so the two can never
        # disagree — see app/services/phone.py.
        "countries": phone_numbers.dial_codes(),
        "default_country_code": phone_numbers.default_country_code(),
    }
