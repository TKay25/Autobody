"""Page blueprints: the SPA shell, login and the public portal."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, abort, current_app, render_template, request, send_from_directory
from flask_login import current_user, login_required

from ..constants import STAGE_LABELS
from ..extensions import db
from ..models import Booking, Customer, Invoice, JobCard, Vehicle

bp = Blueprint("views", __name__)


@bp.get("/")
def index():
    if current_user.is_authenticated:
        return render_template("app.html", bootstrap=_bootstrap_payload())
    return render_template("login.html")


@bp.get("/login")
def login():
    return render_template("login.html")


@bp.get("/app")
@login_required
def shell():
    """The single-page app shell. All data arrives over /api/."""
    payload = _bootstrap_payload()
    return render_template("app.html", bootstrap=payload)


@bp.get("/uploads/<path:filename>")
@login_required
def uploaded_file(filename: str):
    """Job photos and attached documents (assessor quotations, job sheets).

    Staff-only: these are customer vehicles and third-party paperwork.
    """
    root = current_app.config["UPLOAD_DIR"]
    if not (root / filename).resolve().is_relative_to(root.resolve()):
        abort(404)          # never serve outside the upload directory
    return send_from_directory(root, filename)


@bp.get("/portal/<token>")
def portal(token: str):
    """Read-only customer self-service page — no login needed."""
    customer = Customer.query.filter_by(portal_token=token).first_or_404()
    jobs = (
        JobCard.query.filter_by(customer_id=customer.id)
        .order_by(JobCard.id.desc())
        .all()
    )
    invoices = (
        Invoice.query.filter_by(customer_id=customer.id)
        .filter(Invoice.status.notin_(["PAID", "CANCELLED"]))
        .all()
    )
    bookings = (
        Booking.query.filter_by(customer_id=customer.id)
        .filter(Booking.slot_date >= date.today() - timedelta(days=30))
        .order_by(Booking.slot_date.desc())
        .all()
    )
    return render_template(
        "portal.html",
        customer=customer,
        jobs=jobs,
        invoices=invoices,
        bookings=bookings,
        stage_labels=STAGE_LABELS,
        today=date.today(),
    )


def _bootstrap_payload() -> dict:
    from flask import current_app

    from .. import __version__, reference_meta

    return {
        "user": current_user.to_dict(),
        "meta": reference_meta(),
        "version": __version__,
        "company": {
            "name": current_app.config["COMPANY_NAME"],
            "address": current_app.config["COMPANY_ADDRESS"],
            "tel": current_app.config["COMPANY_TEL"],
            "mobile": current_app.config["COMPANY_MOBILE"],
            "email": current_app.config["COMPANY_EMAIL"],
            "hours": current_app.config["COMPANY_HOURS"],
            "website": current_app.config["COMPANY_WEBSITE"],
        },
        "vehicleCount": db.session.query(Vehicle).count(),
        "customerCount": db.session.query(Customer).count(),
    }
