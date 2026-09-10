"""JSON API consumed by the single-page front end.

Everything the Workshop OS does goes through here: job cards, the WIP board,
estimating, claims, parts, invoices and the WhatsApp inbox.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from .. import reference_meta
from ..constants import (
    CLAIM_STATUSES,
    PART_STATUSES,
    PRIORITIES,
    SERVICE_NAMES,
    STAGES,
    STAGE_CUSTOMER_TEXT,
    STAGE_LABELS,
)
from ..extensions import csrf, db
from ..models import (
    Booking,
    Claim,
    Customer,
    Estimate,
    Invoice,
    JobCard,
    JobPart,
    JobPhoto,
    Part,
    Payment,
    User,
    Vehicle,
    WaConversation,
    utcnow,
)
from ..services import documents, job_flow, notifications, pricing
from ..services.activity import log_activity, recent_activity
from ..services.whatsapp_client import log_inbound, normalise_msisdn

bp = Blueprint("api", __name__, url_prefix="/api")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def payload() -> dict:
    return request.get_json(silent=True) or {}


def want(container, key, default=None):
    value = container.get(key, default)
    if isinstance(value, str):
        value = value.strip()
        return value or default
    return value


def as_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value)[:10], fmt).date()
        except ValueError:
            continue
    return None


def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_decimal(value, default=Decimal("0")):
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return default


def bad(message: str, code: int = 400):
    return jsonify({"error": "bad_request", "message": message}), code


def manager_only():
    return jsonify({"error": "forbidden",
                    "message": "Only an owner or workshop manager can do that."}), 403


# ─────────────────────────────────────────────────────────────────────────────
# Meta / session
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/meta")
@login_required
def meta():
    return jsonify(reference_meta())


@bp.get("/me")
@login_required
def me():
    return jsonify({"user": current_user.to_dict()})


@bp.get("/badges")
@login_required
def badges():
    """Live counters for the navigation rail. Cheap enough to poll."""
    today = date.today()
    open_jobs = JobCard.query.filter(JobCard.stage != "COLLECTED")
    open_claim_rows = Claim.query.filter(Claim.status.notin_(["SETTLED", "REPUDIATED"]))
    unpaid = Invoice.query.filter(Invoice.status.notin_(["PAID", "CANCELLED"]))

    unread = (
        db.session.query(db.func.coalesce(db.func.sum(WaConversation.unread), 0)).scalar() or 0
    )

    payload = {
        "jobs": open_jobs.count(),
        "jobs_overdue": open_jobs.filter(
            JobCard.promised_date.isnot(None), JobCard.promised_date < today
        ).count(),
        "jobs_ready": JobCard.query.filter_by(stage="READY").count(),
        "bookings": Booking.query.filter_by(status="REQUESTED").count(),
        "claims": open_claim_rows.count(),
        "invoices": unpaid.filter(Invoice.due_date < today).count(),
        "parts": Part.query.filter(
            Part.is_active.is_(True), Part.qty_on_hand <= Part.reorder_level
        ).count(),
        "whatsapp": int(unread),
    }
    payload["attention"] = payload["jobs_overdue"] + payload["invoices"] + payload["parts"]
    return jsonify(payload)


@bp.get("/dashboard")
@login_required
def dashboard():
    metrics = job_flow.workshop_metrics()
    board = _board_data()
    recent = (
        JobCard.query.order_by(JobCard.id.desc()).limit(8).all()
    )
    ready = (
        JobCard.query.filter_by(stage="READY").order_by(JobCard.updated_at.desc()).limit(6).all()
    )
    unread = db.session.query(db.func.coalesce(db.func.sum(WaConversation.unread), 0)).scalar() or 0
    return jsonify({
        "metrics": metrics,
        "board": board,
        "recent_jobs": [j.to_dict(brief=True) for j in recent],
        "ready_jobs": [j.to_dict(brief=True) for j in ready],
        "unread_whatsapp": int(unread),
        "generated_at": utcnow().isoformat(),
    })


def _board_data() -> dict:
    jobs = JobCard.query.filter(JobCard.stage != "COLLECTED").order_by(
        JobCard.priority.desc(), JobCard.promised_date.asc()
    ).all()
    columns = {stage: [] for stage in STAGES}
    for job in jobs:
        columns.setdefault(job.stage, []).append(job.to_dict(brief=True))
    return {
        "columns": [
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                "count": len(columns.get(stage, [])),
                "jobs": columns.get(stage, []),
            }
            for stage in STAGES
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Customers & vehicles
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/customers")
@login_required
def list_customers():
    q = want(request.args, "q")
    query = Customer.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Customer.name.ilike(like), Customer.phone.ilike(like),
            Customer.email.ilike(like), Customer.company.ilike(like),
        ))
    if request.args.get("fleet") == "1":
        query = query.filter(Customer.is_fleet.is_(True))
    customers = query.order_by(Customer.name.asc()).limit(300).all()
    return jsonify({"items": [c.to_dict() for c in customers], "count": len(customers)})


@bp.post("/customers")
@login_required
def create_customer():
    data = payload()
    name = want(data, "name")
    if not name:
        return bad("Customer name is required.")
    customer = Customer(
        name=name,
        phone=want(data, "phone"),
        whatsapp=want(data, "whatsapp") or want(data, "phone"),
        email=want(data, "email"),
        address=want(data, "address"),
        company=want(data, "company"),
        is_fleet=bool(data.get("is_fleet")),
        notes=want(data, "notes"),
    )
    db.session.add(customer)
    db.session.commit()
    return jsonify({"customer": customer.to_dict()}), 201


@bp.get("/customers/<int:cid>")
@login_required
def get_customer(cid: int):
    customer = db.session.get(Customer, cid)
    if not customer:
        return bad("Customer not found.", 404)
    return jsonify({"customer": customer.to_dict(deep=True)})


@bp.patch("/customers/<int:cid>")
@login_required
def update_customer(cid: int):
    customer = db.session.get(Customer, cid)
    if not customer:
        return bad("Customer not found.", 404)
    data = payload()
    for field in ("name", "phone", "whatsapp", "email", "address", "company", "notes"):
        if field in data:
            setattr(customer, field, want(data, field))
    if "is_fleet" in data:
        customer.is_fleet = bool(data["is_fleet"])
    if "whatsapp_opt_in" in data:
        customer.whatsapp_opt_in = bool(data["whatsapp_opt_in"])
    db.session.commit()
    return jsonify({"customer": customer.to_dict()})


@bp.get("/vehicles")
@login_required
def list_vehicles():
    q = want(request.args, "q")
    query = Vehicle.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Vehicle.reg_no.ilike(like), Vehicle.make.ilike(like),
            Vehicle.model.ilike(like), Vehicle.vin.ilike(like),
        ))
    if request.args.get("customer_id"):
        query = query.filter(Vehicle.customer_id == as_int(request.args.get("customer_id")))
    vehicles = query.order_by(Vehicle.reg_no.asc()).limit(300).all()
    return jsonify({"items": [v.to_dict() for v in vehicles], "count": len(vehicles)})


@bp.post("/vehicles")
@login_required
def create_vehicle():
    data = payload()
    customer_id = as_int(data.get("customer_id"))
    if not customer_id or not db.session.get(Customer, customer_id):
        return bad("A valid customer is required.")
    reg = want(data, "reg_no")
    if not reg:
        return bad("Registration number is required.")
    vehicle = job_flow.find_or_create_vehicle(
        db.session.get(Customer, customer_id),
        reg_no=reg,
        make=want(data, "make"),
        model=want(data, "model"),
        year=as_int(data.get("year")),
        colour=want(data, "colour"),
        vin=want(data, "vin"),
        mileage=as_int(data.get("mileage")),
    )
    db.session.commit()
    return jsonify({"vehicle": vehicle.to_dict()}), 201


# ─────────────────────────────────────────────────────────────────────────────
# Job cards
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/jobs")
@login_required
def list_jobs():
    q = want(request.args, "q")
    stage = want(request.args, "stage")
    status = want(request.args, "status")  # open | closed | overdue | ready | all
    query = JobCard.query

    if q:
        like = f"%{q}%"
        query = query.join(Vehicle, JobCard.vehicle_id == Vehicle.id).join(
            Customer, JobCard.customer_id == Customer.id
        ).filter(or_(
            JobCard.job_no.ilike(like), Vehicle.reg_no.ilike(like),
            Customer.name.ilike(like), JobCard.description.ilike(like),
        ))
    if stage and stage in STAGES:
        query = query.filter(JobCard.stage == stage)
    if request.args.get("insurance") == "1":
        query = query.filter(JobCard.is_insurance.is_(True))

    jobs = query.order_by(JobCard.id.desc()).limit(400).all()

    if status == "open":
        jobs = [j for j in jobs if j.is_open]
    elif status == "closed":
        jobs = [j for j in jobs if not j.is_open]
    elif status == "overdue":
        jobs = [j for j in jobs if j.is_overdue]
    elif status == "ready":
        jobs = [j for j in jobs if j.stage == "READY"]

    return jsonify({"items": [j.to_dict(brief=True) for j in jobs], "count": len(jobs)})


@bp.get("/jobs/<int:job_id>")
@login_required
def get_job(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    ok, message = job_flow.can_advance(job)
    return jsonify({
        "job": job.to_dict(),
        "next_stage": job_flow.next_stage(job.stage),
        "can_advance": ok,
        "advance_message": message,
        "customer_message": STAGE_CUSTOMER_TEXT.get(job.stage, ""),
    })


@bp.post("/jobs")
@login_required
def create_job():
    data = payload()

    customer_id = as_int(data.get("customer_id"))
    customer = db.session.get(Customer, customer_id) if customer_id else None
    if not customer:
        name = want(data, "customer_name")
        if not name:
            return bad("Either customer_id or customer_name is required.")
        customer = job_flow.find_or_create_customer(
            name=name, phone=want(data, "customer_phone"),
            whatsapp=want(data, "customer_whatsapp"),
            is_fleet=bool(data.get("is_fleet")),
            company=want(data, "customer_company"),
        )

    reg = want(data, "reg_no")
    if not reg:
        return bad("Registration number is required.")
    vehicle = job_flow.find_or_create_vehicle(
        customer, reg_no=reg, make=want(data, "make"), model=want(data, "model"),
        year=as_int(data.get("year")), colour=want(data, "colour"),
        mileage=as_int(data.get("mileage")),
    )

    service = want(data, "service") or SERVICE_NAMES[1]
    if service not in SERVICE_NAMES:
        return bad("Unknown service.")

    priority = want(data, "priority") or "NORMAL"
    if priority not in PRIORITIES:
        priority = "NORMAL"

    job = job_flow.open_job_card(
        customer=customer, vehicle=vehicle, service=service,        description=want(data, "description"),
        damage_summary=want(data, "damage_summary"),
        is_insurance=bool(data.get("is_insurance")),
        priority=priority,
        promised_date=as_date(data.get("promised_date")),
        bay=want(data, "bay"),
        user_id=current_user.id,
        technician_id=as_int(data.get("technician_id")),
        fuel_level=want(data, "fuel_level"),
        valuables=want(data, "valuables"),
        odometer_in=as_int(data.get("odometer_in")) or vehicle.mileage,
    )

    if data.get("source_estimate_id"):
        # Build the estimate from a quotation the customer already has, rather
        # than re-listing every panel on the job card.
        source = db.session.get(Estimate, as_int(data["source_estimate_id"]))
        if not source:
            return bad("That quotation could not be found.", 404)
        if not source.items:
            return bad("That quotation has no line items to copy.")

        origin = source.job
        note = (f"Copied from quotation {source.reference}"
                f" (job card {origin.job_no})" if origin else
                f"Copied from quotation {source.reference}")
        note += "."
        if want(data, "quotation_note"):
            note += f" {want(data, 'quotation_note')}"

        job_flow.save_estimate(
            job, [i.to_dict() for i in source.items],
            excess=as_decimal(data.get("excess")), notes=note,
        )
        db.session.refresh(job)
        copied = job.latest_estimate
        log_activity(
            "estimate.copied",
            f"{job.job_no} built from quotation {source.reference} "
            f"({len(source.items)} lines, {source.currency} {source.total:,.2f})",
            entity_type="estimate", entity_id=copied.id if copied else None,
            entity_ref=source.reference, job_id=job.id,
            meta={"source_estimate_id": source.id, "source_job_no":
                  origin.job_no if origin else None}, commit=True,
        )
    elif data.get("panels"):
        lines = pricing.build_lines(
            list(data["panels"]), is_insurance=job.is_insurance,
            parts=data.get("parts") or [],
        )
        job_flow.save_estimate(job, lines, excess=as_decimal(data.get("excess")))
        db.session.refresh(job)

    log_activity(
        "job.created",
        f"Opened job card {job.job_no} for {vehicle.reg_no} ({service})",
        entity_type="job", entity_id=job.id, entity_ref=job.job_no, job_id=job.id,
        meta={"is_insurance": job.is_insurance, "priority": job.priority},
        commit=True,
    )

    return jsonify({"job": job.to_dict(), "vehicle": vehicle.to_dict()}), 201


@bp.patch("/jobs/<int:job_id>")
@login_required
def update_job(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    data = payload()
    for field in ("description", "damage_summary", "bay", "fuel_level", "valuables"):
        if field in data:
            setattr(job, field, want(data, field))
    if "priority" in data and data["priority"] in PRIORITIES:
        job.priority = data["priority"]
    if "service" in data and data["service"] in SERVICE_NAMES:
        job.service = data["service"]
    if "promised_date" in data:
        job.promised_date = as_date(data["promised_date"])
    if "technician_id" in data:
        job.technician_id = as_int(data["technician_id"])
    if "is_insurance" in data:
        job.is_insurance = bool(data["is_insurance"])
    if "odometer_in" in data:
        job.odometer_in = as_int(data["odometer_in"])
    db.session.commit()
    return jsonify({"job": job.to_dict()})


@bp.post("/jobs/<int:job_id>/advance")
@login_required
def advance_job(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    data = payload()
    old_stage = job.stage
    try:
        job_flow.advance_job(
            job, user_id=current_user.id, note=want(data, "note"),
            force=bool(data.get("force")), target=want(data, "target"),
        )
    except job_flow.JobFlowError as exc:
        return jsonify({"error": "blocked", "message": str(exc),
                        "job": job.to_dict(brief=True)}), 409

    notified = False
    if job.stage != old_stage:
        notified = notifications.notify_stage_change(job, old_stage=old_stage)
        log_activity(
            "job.stage_changed",
            f"{job.job_no} moved {STAGE_LABELS.get(old_stage, old_stage)} → {job.stage_label}"
            + (" (overridden)" if data.get("force") else ""),
            entity_type="job", entity_id=job.id, entity_ref=job.job_no, job_id=job.id,
            meta={"from": old_stage, "to": job.stage, "notified": notified},
            commit=True,
        )
    db.session.refresh(job)
    return jsonify({"job": job.to_dict(brief=True), "notified": notified})


@bp.post("/jobs/<int:job_id>/stage")
@login_required
def move_stage(job_id: int):
    """Direct stage move used by drag-and-drop on the WIP board."""
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    data = payload()
    target = want(data, "stage")
    if not target:
        return bad("A target stage is required.")
    old_stage = job.stage
    try:
        job_flow.advance_job(job, user_id=current_user.id, target=target,
                             note=want(data, "note"), force=True)
    except job_flow.JobFlowError as exc:
        return jsonify({"error": "blocked", "message": str(exc)}), 409

    notified = job.stage != old_stage and notifications.notify_stage_change(job, old_stage=old_stage)
    if job.stage != old_stage:
        log_activity(
            "job.stage_changed",
            f"{job.job_no} dragged to {job.stage_label}",
            entity_type="job", entity_id=job.id, entity_ref=job.job_no, job_id=job.id,
            meta={"from": old_stage, "to": job.stage}, commit=True,
        )
    db.session.refresh(job)
    return jsonify({"job": job.to_dict(brief=True), "notified": notified})


@bp.post("/jobs/<int:job_id>/photos")
@login_required
def add_photo(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    data = payload()
    url = want(data, "url")
    if not url:
        return bad("A photo URL is required.")
    photo = JobPhoto(
        job_id=job.id, filename=url.rsplit("/", 1)[-1], url=url,
        kind=want(data, "kind") or "DAMAGE", caption=want(data, "caption"),
        source=want(data, "source") or "web",
    )
    db.session.add(photo)
    db.session.commit()
    return jsonify({"photo": photo.to_dict()}), 201


@bp.post("/jobs/<int:job_id>/documents")
@login_required
def upload_job_document(job_id: int):
    """Attach the paperwork — an assessor's quotation, a signed job sheet, a photo.

    Multipart upload; the file lands in ``instance/uploads/documents`` and is
    recorded against the job card so it can be reopened later.
    """
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)

    blob = request.files.get("file")
    if not blob or not blob.filename:
        return bad("Choose a file to attach.")

    ext = blob.filename.rsplit(".", 1)[-1].lower() if "." in blob.filename else ""
    allowed = current_app.config["ALLOWED_EXTENSIONS"]
    if ext not in allowed:
        return bad(f"{ext.upper() or 'That file type'} is not supported. "
                   f"Allowed: {', '.join(sorted(allowed))}.")

    original = blob.filename
    folder = Path(current_app.config["UPLOAD_DIR"]) / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex[:12]}-{secure_filename(original) or f'document.{ext}'}"
    blob.save(folder / stored)

    document = JobPhoto(
        job_id=job.id,
        filename=original,
        url=f"/uploads/documents/{stored}",
        kind=(want(request.form, "kind") or "QUOTATION").upper(),
        caption=want(request.form, "caption") or original,
        source="upload",
    )
    db.session.add(document)
    db.session.commit()
    log_activity(
        "document.attached",
        f"{document.kind.title()} attached to {job.job_no}: {original}",
        entity_type="job", entity_id=job.id, entity_ref=job.job_no, job_id=job.id,
        meta={"filename": original, "kind": document.kind}, commit=True,
    )
    return jsonify({"document": document.to_dict()}), 201


@bp.post("/jobs/<int:job_id>/qc")
@login_required
def submit_qc(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    results = payload().get("results") or []
    job_flow.record_qc(job, results, user_id=current_user.id)
    db.session.refresh(job)
    failed = [r for r in job.qc_results if not r.passed]
    return jsonify({"qc": job.qc_score(), "results": [r.to_dict() for r in job.qc_results],
                    "can_release": not failed})


# ─────────────────────────────────────────────────────────────────────────────
# Estimating
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/estimating/panels")
@login_required
def estimating_panels():
    return jsonify({
        "panels": pricing.available_panels(),
        "rates": {
            "PANEL": float(pricing.PANEL_RATE),
            "PAINT": float(pricing.PAINT_RATE),
            "METAL": float(pricing.METAL_RATE),
            "DETAIL": float(pricing.DETAIL_RATE),
        },
    })


@bp.post("/estimating/preview")
@login_required
def estimating_preview():
    """Price a set of panels without saving anything (live calculator)."""
    data = payload()
    lines = pricing.build_lines(
        list(data.get("panels") or []),
        is_insurance=bool(data.get("is_insurance")),
        include_paint=data.get("include_paint", True),
        parts=data.get("parts") or [],
        extra_labour=data.get("extra_labour") or [],
        include_consumables=data.get("include_consumables", True),
    )
    summary = pricing.summarise(
        lines, bool(data.get("is_insurance")), as_decimal(data.get("vat_rate"), Decimal("0.15"))
    )
    excess = as_decimal(data.get("excess"))
    return jsonify({
        "lines": [
            {**line, "unit_price": float(as_decimal(line["unit_price"])),
             "quantity": float(as_decimal(line["quantity"])),
             "markup_pct": float(as_decimal(line.get("markup_pct"))),
             "line_total": float(
                 as_decimal(line["quantity"]) * as_decimal(line["unit_price"])
                 * (Decimal("1") + as_decimal(line.get("markup_pct")))
             )}
            for line in lines
        ],
        "summary": {k: float(v) if isinstance(v, Decimal) else v for k, v in summary.items()},
        "split": pricing.insurer_vs_customer(summary["total"], excess),
    })


@bp.post("/jobs/<int:job_id>/estimate")
@login_required
def create_estimate(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    data = payload()

    lines = data.get("lines")
    if not lines:
        lines = pricing.build_lines(
            list(data.get("panels") or []),
            is_insurance=bool(data.get("is_insurance", job.is_insurance)),
            include_paint=data.get("include_paint", True),
            parts=data.get("parts") or [],
            extra_labour=data.get("extra_labour") or [],
            include_consumables=data.get("include_consumables", True),
        )
    if not lines:
        return bad("Nothing to estimate — choose at least one panel or part.")

    if data.get("is_insurance") is not None:
        job.is_insurance = bool(data["is_insurance"])

    estimate = job_flow.save_estimate(
        job, lines,
        is_insurance=job.is_insurance,
        vat_rate=as_decimal(data.get("vat_rate"), Decimal("0.15")),
        excess=as_decimal(data.get("excess")),
        notes=want(data, "notes"),
        mark_sent=bool(data.get("send", True)),
    )
    notified = False
    if data.get("notify", True):
        notified = notifications.notify_quote_ready(job)
    log_activity(
        "estimate.created",
        f"Estimate {estimate.reference} for {job.job_no} totalling "
        f"{estimate.currency} {estimate.total:,.2f}",
        entity_type="estimate", entity_id=estimate.id, entity_ref=estimate.reference,
        job_id=job.id, meta={"total": float(estimate.total), "insurance": job.is_insurance},
        commit=True,
    )
    return jsonify({"estimate": estimate.to_dict(), "notified": notified}), 201


@bp.get("/estimates/<int:estimate_id>")
@login_required
def get_estimate(estimate_id: int):
    estimate = db.session.get(Estimate, estimate_id)
    if not estimate:
        return bad("Estimate not found.", 404)
    return jsonify({"estimate": estimate.to_dict()})


@bp.get("/quotations")
@login_required
def list_quotations():
    """Quotations already in the system that a new job card can be built from.

    Intake filters this by the customer or registration picked on the form, so
    "the same car came back" or "the fleet sent the same job again" is one tap.
    """
    query = (
        Estimate.query
        .join(JobCard, Estimate.job_id == JobCard.id)
        .outerjoin(Vehicle, JobCard.vehicle_id == Vehicle.id)
        .outerjoin(Customer, JobCard.customer_id == Customer.id)
    )

    exclude_job = as_int(request.args.get("exclude_job"))
    if exclude_job:
        query = query.filter(Estimate.job_id != exclude_job)

    customer_id = as_int(request.args.get("customer_id"))
    if customer_id:
        query = query.filter(JobCard.customer_id == customer_id)

    reg = (want(request.args, "reg_no") or "").replace(" ", "").upper()
    if reg:
        # Registrations are stored without spaces, but tolerate legacy rows.
        query = query.filter(func.replace(Vehicle.reg_no, " ", "").like(f"%{reg}%"))

    q = want(request.args, "q")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Estimate.reference.ilike(like),
            JobCard.job_no.ilike(like),
            Customer.name.ilike(like),
            Vehicle.reg_no.ilike(like),
            func.replace(Vehicle.reg_no, " ", "").ilike(f"%{q.replace(' ', '')}%"),
        ))

    limit = as_int(request.args.get("limit"), 40) or 40
    estimates = query.order_by(Estimate.id.desc()).limit(limit).all()

    items = []
    for est in estimates:
        job = est.job
        vehicle = job.vehicle if job else None
        items.append({
            "id": est.id,
            "reference": est.reference,
            "status": est.status,
            "currency": est.currency or "USD",
            "total": float(est.total or 0),
            "is_insurance": est.is_insurance,
            "excess": float(est.excess or 0),
            "item_count": len(est.items),
            "created_at": est.created_at.isoformat() if est.created_at else None,
            "job_id": est.job_id,
            "job_no": job.job_no if job else None,
            "customer_id": job.customer_id if job else None,
            "customer_name": job.customer.name if job and job.customer else None,
            "reg_no": vehicle.reg_no if vehicle else None,
            "vehicle_title": vehicle.title if vehicle else None,
        })
    return jsonify({"items": items, "count": len(items)})


@bp.post("/estimates/<int:estimate_id>/approve")
@login_required
def approve_estimate(estimate_id: int):
    estimate = db.session.get(Estimate, estimate_id)
    if not estimate:
        return bad("Estimate not found.", 404)
    data = payload()
    approved_by = want(data, "approved_by") or current_user.full_name
    job_flow.approve_estimate(estimate, approved_by=approved_by, user_id=current_user.id)
    log_activity(
        "estimate.approved",
        f"Estimate {estimate.reference} approved by {approved_by}",
        entity_type="estimate", entity_id=estimate.id, entity_ref=estimate.reference,
        job_id=estimate.job_id, commit=True,
    )
    return jsonify({"estimate": estimate.to_dict(), "job": estimate.job.to_dict(brief=True)})


@bp.post("/estimates/<int:estimate_id>/decline")
@login_required
def decline_estimate(estimate_id: int):
    estimate = db.session.get(Estimate, estimate_id)
    if not estimate:
        return bad("Estimate not found.", 404)
    estimate.status = "DECLINED"
    notes = want(payload(), "reason")
    if notes:
        estimate.notes = (estimate.notes or "") + f"\nDeclined: {notes}"
    db.session.commit()
    log_activity(
        "estimate.declined",
        f"Estimate {estimate.reference} declined" + (f" — {notes}" if notes else ""),
        entity_type="estimate", entity_id=estimate.id, entity_ref=estimate.reference,
        job_id=estimate.job_id, commit=True,
    )
    return jsonify({"estimate": estimate.to_dict()})


# ─────────────────────────────────────────────────────────────────────────────
# Claims
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/claims")
@login_required
def list_claims():
    status = want(request.args, "status")
    query = Claim.query
    if status:
        query = query.filter(Claim.status == status)
    if request.args.get("insurer"):
        query = query.filter(Claim.insurer_code == request.args["insurer"])
    claims = query.order_by(Claim.id.desc()).limit(300).all()
    total_approved = sum((Decimal(str(c.approved_amount or 0)) for c in claims), Decimal("0"))
    return jsonify({
        "items": [c.to_dict() for c in claims],
        "count": len(claims),
        "approved_value": float(total_approved),
    })


@bp.post("/jobs/<int:job_id>/claim")
@login_required
def create_claim(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    data = payload()
    insurer = want(data, "insurer_code")
    if not insurer:
        return bad("Insurer is required.")

    claim = Claim(
        job_id=job.id,
        insurer_code=insurer,
        policy_no=want(data, "policy_no"),
        claim_no=want(data, "claim_no"),
        assessor_name=want(data, "assessor_name"),
        assessor_phone=want(data, "assessor_phone"),
        assessor_date=as_date(data.get("assessor_date")),
        status=want(data, "status") or "DRAFT",
        claimed_amount=as_decimal(data.get("claimed_amount")),
        approved_amount=as_decimal(data.get("approved_amount")),
        excess=as_decimal(data.get("excess")),
        notes=want(data, "notes"),
    )
    if claim.status == "SUBMITTED":
        claim.submitted_at = utcnow()
    db.session.add(claim)

    job.is_insurance = True
    if job.latest_estimate:
        job.latest_estimate.is_insurance = True
        job.latest_estimate.excess = claim.excess
        job.latest_estimate.recalculate(Decimal("0.15"))
    db.session.commit()
    log_activity(
        "claim.linked",
        f"Claim {claim.claim_no or claim.id} linked to {job.job_no} ({claim.insurer_name})",
        entity_type="claim", entity_id=claim.id, entity_ref=claim.claim_no,
        job_id=job.id, meta={"insurer": claim.insurer_code, "excess": float(claim.excess)},
        commit=True,
    )
    return jsonify({"claim": claim.to_dict()}), 201


@bp.patch("/claims/<int:claim_id>")
@login_required
def update_claim(claim_id: int):
    claim = db.session.get(Claim, claim_id)
    if not claim:
        return bad("Claim not found.", 404)
    data = payload()

    for field in ("policy_no", "claim_no", "assessor_name", "assessor_phone", "notes",
                  "repudiation_reason"):
        if field in data:
            setattr(claim, field, want(data, field))
    if "assessor_date" in data:
        claim.assessor_date = as_date(data["assessor_date"])
    for field in ("claimed_amount", "approved_amount", "excess"):
        if field in data:
            setattr(claim, field, as_decimal(data[field]))
    if "excess_paid" in data:
        claim.excess_paid = bool(data["excess_paid"])

    if "status" in data:
        new_status = data["status"]
        if new_status not in CLAIM_STATUSES:
            return bad("Unknown claim status.")
        claim.status = new_status
        if new_status in {"SUBMITTED", "ASSESSOR_BOOKED"} and not claim.submitted_at:
            claim.submitted_at = utcnow()
        if new_status in {"APPROVED", "PARTIAL", "REPUDIATED", "SETTLED"}:
            claim.decision_at = utcnow()

        job = claim.job
        if job:
            if new_status in {"APPROVED", "PARTIAL"} and job.stage in {"AWAITING_APPROVAL", "ASSESSMENT"}:
                target = "PARTS_ORDER" if any(p.is_blocking for p in job.job_parts) else "STRIP"
                job_flow.advance_job(job, user_id=current_user.id, target=target,
                                     note=f"Insurer {new_status.lower()}")
                notifications.notify_stage_change(job)
            elif new_status == "REPUDIATED" and job.stage == "AWAITING_APPROVAL":
                job.stage = "AWAITING_APPROVAL"  # hold for a customer decision

    db.session.commit()
    return jsonify({"claim": claim.to_dict()})


# ─────────────────────────────────────────────────────────────────────────────
# Parts
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/parts")
@login_required
def list_parts():
    q = want(request.args, "q")
    query = Part.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Part.name.ilike(like), Part.sku.ilike(like),
                                 Part.supplier.ilike(like)))
    if request.args.get("low") == "1":
        query = query.filter(Part.qty_on_hand <= Part.reorder_level)
    parts = query.order_by(Part.name.asc()).limit(400).all()
    value = sum((p.stock_value for p in parts), Decimal("0"))
    return jsonify({
        "items": [p.to_dict() for p in parts],
        "count": len(parts),
        "stock_value": float(value),
        "low_stock": len([p for p in parts if p.needs_reorder]),
    })


@bp.post("/parts")
@login_required
def create_part():
    data = payload()
    name = want(data, "name")
    if not name:
        return bad("Part name is required.")
    sku = want(data, "sku") or f"SKU-{db.session.query(Part).count() + 1:05d}"
    if Part.query.filter_by(sku=sku).first():
        return bad("That SKU already exists.")
    part = Part(
        sku=sku, name=name, category=want(data, "category") or "Body Panels",
        supplier=want(data, "supplier"), unit=want(data, "unit") or "ea",
        cost_price=as_decimal(data.get("cost_price")),
        sell_price=as_decimal(data.get("sell_price")),
        qty_on_hand=as_decimal(data.get("qty_on_hand")),
        reorder_level=as_decimal(data.get("reorder_level"), Decimal("2")),
        location=want(data, "location"),
    )
    db.session.add(part)
    db.session.commit()
    return jsonify({"part": part.to_dict()}), 201


@bp.post("/parts/<int:part_id>/movement")
@login_required
def part_movement(part_id: int):
    part = db.session.get(Part, part_id)
    if not part:
        return bad("Part not found.", 404)
    data = payload()
    delta = as_decimal(data.get("delta"))
    if delta == 0:
        return bad("A non-zero quantity is required.")
    job_flow.apply_stock_movement(
        part, delta, reason=want(data, "reason") or "ADJUSTMENT",
        reference=want(data, "reference"), user_id=current_user.id,
    )
    db.session.commit()
    return jsonify({"part": part.to_dict()})


@bp.post("/jobs/<int:job_id>/parts")
@login_required
def add_job_part(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    data = payload()
    description = want(data, "description")
    if not description:
        return bad("A description is required.")
    row = JobPart(
        job_id=job.id,
        part_id=as_int(data.get("part_id")),
        description=description,
        quantity=as_decimal(data.get("quantity"), Decimal("1")),
        unit_price=as_decimal(data.get("unit_price")),
        status=want(data, "status") or "REQUIRED",
        supplier=want(data, "supplier"),
        eta=as_date(data.get("eta")),
        notes=want(data, "notes"),
    )
    if row.status in {"ORDERED", "IN_TRANSIT"}:
        row.ordered_at = utcnow()
    if row.status == "RECEIVED":
        row.received_at = utcnow()
    db.session.add(row)
    db.session.commit()
    return jsonify({"part": row.to_dict()}), 201


@bp.patch("/job-parts/<int:job_part_id>")
@login_required
def update_job_part(job_part_id: int):
    row = db.session.get(JobPart, job_part_id)
    if not row:
        return bad("Job part not found.", 404)
    data = payload()
    if "status" in data:
        status = data["status"]
        if status not in PART_STATUSES:
            return bad("Unknown part status.")
        was_blocking = row.is_blocking
        row.status = status
        if status == "ORDERED" and not row.ordered_at:
            row.ordered_at = utcnow()
        if status == "RECEIVED" and not row.received_at:
            job_flow.receive_job_part(row, user_id=current_user.id)
        if was_blocking and not row.is_blocking:
            still = [p for p in row.job.job_parts if p.is_blocking and p.id != row.id]
            if not still:
                notifications.notify_parts_received(row.job, [row.description])
    for field in ("description", "supplier", "notes"):
        if field in data:
            setattr(row, field, want(data, field))
    if "eta" in data:
        row.eta = as_date(data["eta"])
    if "quantity" in data:
        row.quantity = as_decimal(data["quantity"])
    if "unit_price" in data:
        row.unit_price = as_decimal(data["unit_price"])
    db.session.commit()
    return jsonify({"part": row.to_dict()})


# ─────────────────────────────────────────────────────────────────────────────
# Invoices & payments
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/invoices")
@login_required
def list_invoices():
    status = want(request.args, "status")
    query = Invoice.query
    if status:
        query = query.filter(Invoice.status == status)
    invoices = query.order_by(Invoice.id.desc()).limit(300).all()
    outstanding = sum(
        (i.balance for i in invoices if i.status not in {"PAID", "CANCELLED"}), Decimal("0")
    )
    return jsonify({
        "items": [i.to_dict() for i in invoices],
        "count": len(invoices),
        "outstanding": float(outstanding),
        "overdue": len([i for i in invoices if i.is_overdue]),
    })


@bp.post("/jobs/<int:job_id>/invoice")
@login_required
def create_invoice(job_id: int):
    job = db.session.get(JobCard, job_id)
    if not job:
        return bad("Job card not found.", 404)
    if not job.latest_estimate:
        return bad("Create an estimate before invoicing this job.")
    invoice = job_flow.ensure_invoice(job, user_id=current_user.id)
    db.session.commit()
    return jsonify({"invoice": invoice.to_dict()}), 201


@bp.post("/invoices/<int:invoice_id>/issue")
@login_required
def issue_invoice(invoice_id: int):
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice:
        return bad("Invoice not found.", 404)
    invoice.status = "ISSUED"
    invoice.issued_at = utcnow()
    db.session.commit()
    notified = notifications.notify_invoice_issued(invoice.job, invoice) if invoice.job else False
    return jsonify({"invoice": invoice.to_dict(), "notified": notified})


@bp.post("/invoices/<int:invoice_id>/payment")
@login_required
def record_payment(invoice_id: int):
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice:
        return bad("Invoice not found.", 404)
    data = payload()
    amount = as_decimal(data.get("amount"))
    if amount <= 0:
        return bad("Payment amount must be greater than zero.")
    payment = job_flow.record_payment(
        invoice, amount, method=want(data, "method") or "CASH",
        reference=want(data, "reference"), user_id=current_user.id,
    )
    db.session.refresh(invoice)
    log_activity(
        "payment.recorded",
        f"{amount:,.2f} received for {invoice.invoice_no} ({invoice.status}) "
        f"— receipt {payment.receipt_no}",
        entity_type="invoice", entity_id=invoice.id, entity_ref=invoice.invoice_no,
        job_id=invoice.job_id, meta={"amount": float(amount), "method": data.get("method")},
        commit=True,
    )

    receipt_sent = None
    if data.get("send_receipt"):
        receipt_sent = notifications.send_receipt(payment)
        log_activity(
            "receipt.sent" if receipt_sent.get("sent") else "receipt.failed",
            f"Receipt {payment.receipt_no} for {invoice.invoice_no}",
            entity_type="receipt", entity_id=payment.id, entity_ref=payment.receipt_no,
            job_id=invoice.job_id, meta=receipt_sent, commit=True,
        )

    return jsonify({
        "invoice": invoice.to_dict(deep=True),
        "payment": payment.to_dict(),
        "receipt_sent": receipt_sent,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Bookings
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/bookings")
@login_required
def list_bookings():
    status = want(request.args, "status")
    query = Booking.query
    if status:
        query = query.filter(Booking.status == status)
    bookings = query.order_by(Booking.slot_date.asc(), Booking.id.desc()).limit(300).all()
    return jsonify({"items": [b.to_dict() for b in bookings], "count": len(bookings)})


@bp.post("/bookings")
@csrf.exempt
def create_booking():
    """Public endpoint — also used by the website's quick-quote widget."""
    data = payload()
    name = want(data, "name")
    phone = want(data, "phone")
    service = want(data, "service")
    slot_date = as_date(data.get("slot_date"))

    if not name or not phone or not service:
        return bad("Name, phone and service are required.")
    if service not in SERVICE_NAMES:
        return bad("Unknown service.")
    if not slot_date:
        slot_date = date.today() + timedelta(days=1)

    customer = job_flow.find_or_create_customer(name=name, phone=phone, whatsapp=phone,
                                                email=want(data, "email"))
    vehicle = None
    if want(data, "reg_no"):
        vehicle = job_flow.find_or_create_vehicle(customer, reg_no=data["reg_no"],
                                                  make=want(data, "make"),
                                                  model=want(data, "model"))

    booking = Booking(
        customer_id=customer.id,
        vehicle_id=vehicle.id if vehicle else None,
        service=service,
        slot_date=slot_date,
        slot_time=want(data, "slot_time"),
        status="REQUESTED",
        source=want(data, "source") or "web",
        notes=want(data, "notes"),
        quoted_from=as_decimal(pricing.quick_quote(service)["from_price"]),
    )
    db.session.add(booking)
    db.session.commit()
    return jsonify({"booking": booking.to_dict(),
                    "quote": pricing.quick_quote(service)}), 201


@bp.patch("/bookings/<int:booking_id>")
@login_required
def update_booking(booking_id: int):
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return bad("Booking not found.", 404)
    data = payload()
    if "slot_date" in data:
        booking.slot_date = as_date(data["slot_date"]) or booking.slot_date
    if "slot_time" in data:
        booking.slot_time = want(data, "slot_time")
    if "notes" in data:
        booking.notes = want(data, "notes")
    if "status" in data:
        booking.status = want(data, "status")
        if booking.status == "CONFIRMED":
            notifications.notify_booking_confirmed(booking)
    db.session.commit()
    return jsonify({"booking": booking.to_dict()})


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp inbox
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/whatsapp/conversations")
@login_required
def wa_conversations():
    query = WaConversation.query
    if request.args.get("unread") == "1":
        query = query.filter(WaConversation.unread > 0)
    if request.args.get("human") == "1":
        query = query.filter(WaConversation.human_takeover.is_(True))
    conversations = query.order_by(WaConversation.last_message_at.desc()).limit(200).all()
    return jsonify({
        "items": [c.to_dict() for c in conversations],
        "count": len(conversations),
        "unread_total": sum(c.unread or 0 for c in conversations),
    })


@bp.get("/whatsapp/conversations/<int:conversation_id>")
@login_required
def wa_conversation(conversation_id: int):
    conversation = db.session.get(WaConversation, conversation_id)
    if not conversation:
        return bad("Conversation not found.", 404)
    conversation.unread = 0
    db.session.commit()
    return jsonify({"conversation": conversation.to_dict(deep=True)})


@bp.post("/whatsapp/conversations/<int:conversation_id>/reply")
@login_required
def wa_reply(conversation_id: int):
    conversation = db.session.get(WaConversation, conversation_id)
    if not conversation:
        return bad("Conversation not found.", 404)
    body = want(payload(), "body")
    if not body:
        return bad("A message body is required.")
    delivered = notifications.send_custom(conversation, body, is_bot=False, user=current_user)
    db.session.refresh(conversation)
    return jsonify({"delivered": delivered, "conversation": conversation.to_dict(deep=True)})


@bp.post("/whatsapp/conversations/<int:conversation_id>/takeover")
@login_required
def wa_takeover(conversation_id: int):
    conversation = db.session.get(WaConversation, conversation_id)
    if not conversation:
        return bad("Conversation not found.", 404)
    data = payload()
    conversation.human_takeover = bool(data.get("human_takeover", True))
    conversation.assigned_to = current_user.id if conversation.human_takeover else None
    conversation.state = "HUMAN" if conversation.human_takeover else "MAIN_MENU"
    db.session.commit()
    return jsonify({"conversation": conversation.to_dict()})


@bp.post("/whatsapp/simulate")
@login_required
def wa_simulate():
    """Test the bot from the UI without a Meta account (simulator mode)."""
    data = payload()
    number = normalise_msisdn(want(data, "wa_id") or "+263775550555")
    body = want(data, "body")
    interactive_id = want(data, "interactive_id")
    if not body and not interactive_id:
        return bad("Provide a message body or an interactive id.")

    from ..services.intent_router import handle_inbound
    from ..services.whatsapp_client import WhatsAppClient, get_or_create_conversation

    conversation = get_or_create_conversation(number)
    log_inbound(conversation, body=body or f"[button:{interactive_id}]",
                payload={"id": interactive_id} if interactive_id else None)

    replies = handle_inbound(conversation, text_body=body, interactive_id=interactive_id)
    client = WhatsAppClient()
    for reply in replies:
        if reply["type"] == "buttons":
            client.send_buttons(number, reply["body"], reply["buttons"],
                                header=reply.get("header"), conversation=conversation)
        elif reply["type"] == "list":
            client.send_list(number, reply["body"], reply["button"], reply["sections"],
                             conversation=conversation)
        else:
            client.send_text(number, reply["body"], conversation=conversation)

    db.session.refresh(conversation)
    return jsonify({
        "conversation": conversation.to_dict(deep=True),
        "replies_sent": len(replies),
        "state": conversation.state,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Staff
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/users")
@login_required
def list_users():
    users = User.query.order_by(User.full_name.asc()).all()
    return jsonify({"items": [u.to_dict() for u in users]})


@bp.post("/users")
@login_required
def create_user():
    if not current_user.is_manager:
        return manager_only()
    data = payload()
    email = (want(data, "email") or "").lower()
    if not email or not want(data, "full_name") or not want(data, "password"):
        return bad("Full name, email and password are required.")
    if User.query.filter(db.func.lower(User.email) == email).first():
        return bad("That email is already registered.")
    user = User(
        full_name=data["full_name"],
        email=email,
        phone=want(data, "phone"),
        role=want(data, "role") or "frontdesk",
    )
    user.set_password(data["password"])
    db.session.add(user)
    db.session.commit()
    return jsonify({"user": user.to_dict()}), 201


@bp.patch("/users/<int:user_id>")
@login_required
def update_user(user_id: int):
    if not current_user.is_manager:
        return manager_only()
    user = db.session.get(User, user_id)
    if not user:
        return bad("User not found.", 404)
    data = payload()
    for field in ("full_name", "phone", "role"):
        if field in data:
            setattr(user, field, want(data, field))
    if "is_active_user" in data:
        user.is_active_user = bool(data["is_active_user"])
    if want(data, "password"):
        user.set_password(data["password"])
    db.session.commit()
    return jsonify({"user": user.to_dict()})


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/reports/overview")
@login_required
def reports_overview():
    metrics = job_flow.workshop_metrics()

    jobs = JobCard.query.all()
    by_service: dict[str, int] = {}
    by_insurer: dict[str, dict] = {}
    for job in jobs:
        by_service[job.service] = by_service.get(job.service, 0) + 1
        claim = job.active_claim
        if claim:
            bucket = by_insurer.setdefault(
                claim.insurer_name, {"jobs": 0, "claimed": 0.0, "approved": 0.0, "aging": 0}
            )
            bucket["jobs"] += 1
            bucket["claimed"] += float(claim.claimed_amount or 0)
            bucket["approved"] += float(claim.approved_amount or 0)
            bucket["aging"] += claim.aging_days

    # 14-day intake trend
    today = date.today()
    trend = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        trend.append({
            "date": day.isoformat(),
            "label": day.strftime("%d %b"),
            "intake": len([j for j in jobs if j.checked_in_at and j.checked_in_at.date() == day]),
            "collected": len([j for j in jobs if j.collected_at and j.collected_at.date() == day]),
        })

    technicians = User.query.filter(User.role.in_(["technician", "manager", "owner"])).all()
    tech_stats = []
    for tech in technicians:
        active = [j for j in jobs if j.technician_id == tech.id and j.is_open]
        done = [j for j in jobs if j.technician_id == tech.id and not j.is_open]
        tech_stats.append({
            "id": tech.id,
            "name": tech.full_name,
            "active_jobs": len(active),
            "completed_jobs": len(done),
            "avg_days": round(
                sum(j.days_in_shop for j in done) / len(done), 1
            ) if done else 0,
        })

    return jsonify({
        "metrics": metrics,
        "by_service": by_service,
        "by_insurer": by_insurer,
        "trend": trend,
        "technicians": sorted(tech_stats, key=lambda t: -t["active_jobs"]),
    })


@bp.get("/notifications")
@login_required
def list_notifications():
    from ..models import NotificationLog

    entries = NotificationLog.query.order_by(NotificationLog.id.desc()).limit(100).all()
    return jsonify({"items": [e.to_dict() for e in entries]})


# ─────────────────────────────────────────────────────────────────────────────
# Documents: quotation, invoice and receipt
# ─────────────────────────────────────────────────────────────────────────────
def _pdf_response(payload: bytes, filename: str):
    import io

    from flask import Response

    return Response(
        payload, mimetype="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@bp.get("/estimates/<int:estimate_id>/pdf")
@login_required
def estimate_pdf(estimate_id: int):
    estimate = db.session.get(Estimate, estimate_id)
    if not estimate:
        return bad("Estimate not found.", 404)
    try:
        payload, filename = documents.build_for("quote", estimate)
    except documents.DocumentError as exc:
        return bad(str(exc), 422)
    return _pdf_response(payload, filename)


@bp.get("/invoices/<int:invoice_id>/pdf")
@login_required
def invoice_pdf(invoice_id: int):
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice:
        return bad("Invoice not found.", 404)
    payload, filename = documents.build_for("invoice", invoice)
    return _pdf_response(payload, filename)


@bp.get("/payments/<int:payment_id>/pdf")
@login_required
def payment_pdf(payment_id: int):
    payment = db.session.get(Payment, payment_id)
    if not payment:
        return bad("Payment not found.", 404)
    payload, filename = documents.build_for("receipt", payment)
    return _pdf_response(payload, filename)


@bp.get("/payments")
@login_required
def list_payments():
    """Receipt register — newest first, filterable by invoice."""
    query = Payment.query
    if request.args.get("invoice_id"):
        query = query.filter(Payment.invoice_id == as_int(request.args["invoice_id"]))
    payments = query.order_by(Payment.id.desc()).limit(200).all()
    total = sum((Decimal(str(p.amount or 0)) for p in payments), Decimal("0"))
    return jsonify({
        "items": [p.to_dict() for p in payments],
        "count": len(payments),
        "total": float(total),
    })


@bp.post("/estimates/<int:estimate_id>/send")
@login_required
def send_estimate(estimate_id: int):
    """WhatsApp the quotation PDF with Approve / Decline buttons."""
    estimate = db.session.get(Estimate, estimate_id)
    if not estimate or not estimate.job:
        return bad("Estimate not found.", 404)

    result = notifications.send_quotation(estimate.job, estimate)
    log_activity(
        "estimate.sent",
        f"Quotation {estimate.reference} sent to "
        f"{estimate.job.customer.name if estimate.job.customer else 'customer'} on WhatsApp",
        entity_type="estimate", entity_id=estimate.id, entity_ref=estimate.reference,
        job_id=estimate.job_id, meta=result, commit=True,
    )
    if not result.get("sent"):
        return jsonify({
            "error": "not_sent",
            "message": {
                "no_customer": "This job has no customer record.",
                "no_number": "No WhatsApp number on file for this customer.",
            }.get(result.get("reason"), "The message could not be sent."),
            "result": result,
        }), 409
    return jsonify({"result": result, "estimate": estimate.to_dict(deep=False)})


@bp.post("/invoices/<int:invoice_id>/send")
@login_required
def send_invoice_doc(invoice_id: int):
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice or not invoice.job:
        return bad("Invoice not found, or it is not linked to a job.", 404)

    result = notifications.send_invoice(invoice.job, invoice)
    log_activity(
        "invoice.sent", f"Invoice {invoice.invoice_no} sent on WhatsApp",
        entity_type="invoice", entity_id=invoice.id, entity_ref=invoice.invoice_no,
        job_id=invoice.job_id, meta=result, commit=True,
    )
    if not result.get("sent"):
        return jsonify({"error": "not_sent",
                        "message": "The invoice could not be sent on WhatsApp.",
                        "result": result}), 409
    return jsonify({"result": result, "invoice": invoice.to_dict()})


@bp.post("/payments/<int:payment_id>/receipt/send")
@login_required
def send_receipt_doc(payment_id: int):
    payment = db.session.get(Payment, payment_id)
    if not payment:
        return bad("Payment not found.", 404)

    result = notifications.send_receipt(payment)
    log_activity(
        "receipt.sent", f"Receipt {payment.receipt_no} sent on WhatsApp",
        entity_type="receipt", entity_id=payment.id, entity_ref=payment.receipt_no,
        job_id=payment.invoice.job_id if payment.invoice else None,
        meta=result, commit=True,
    )
    if not result.get("sent"):
        return jsonify({"error": "not_sent",
                        "message": "The receipt could not be sent on WhatsApp.",
                        "result": result}), 409
    return jsonify({"result": result, "payment": payment.to_dict()})


@bp.get("/documents/links/<kind>/<int:record_id>")
@login_required
def document_links(kind: str, record_id: int):
    """Shareable links for a document (used by the 'Copy link' buttons)."""
    model = {"quote": Estimate, "invoice": Invoice, "receipt": Payment}.get(kind)
    if not model:
        return bad("Unknown document kind.", 404)
    record = db.session.get(model, record_id)
    if not record:
        return bad("Document not found.", 404)
    token = record.public_token
    return jsonify({
        "view": notifications.public_url(f"/doc/{kind}/{token}"),
        "pdf": notifications.public_url(f"/doc/{kind}/{token}.pdf"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Activity feed
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/activity")
@login_required
def list_activity():
    limit = min(as_int(request.args.get("limit"), 60) or 60, 300)
    entries = recent_activity(
        limit=limit,
        job_id=as_int(request.args.get("job_id")),
        entity_type=want(request.args, "entity_type"),
    )
    return jsonify({"items": [e.to_dict() for e in entries], "count": len(entries)})


# ─────────────────────────────────────────────────────────────────────────────
# Editing that the UI needs
# ─────────────────────────────────────────────────────────────────────────────
@bp.patch("/parts/<int:part_id>")
@login_required
def update_part(part_id: int):
    part = db.session.get(Part, part_id)
    if not part:
        return bad("Part not found.", 404)
    data = payload()
    for field in ("name", "category", "supplier", "unit", "location"):
        if field in data:
            setattr(part, field, want(data, field))
    for field in ("cost_price", "sell_price", "reorder_level"):
        if field in data:
            setattr(part, field, as_decimal(data[field]))
    if "is_active" in data:
        part.is_active = bool(data["is_active"])
    db.session.commit()
    log_activity(
        "part.updated", f"Stock item {part.sku} ({part.name}) updated",
        entity_type="part", entity_id=part.id, entity_ref=part.sku, commit=True,
    )
    return jsonify({"part": part.to_dict()})


@bp.patch("/vehicles/<int:vehicle_id>")
@login_required
def update_vehicle(vehicle_id: int):
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return bad("Vehicle not found.", 404)
    data = payload()
    for field in ("make", "model", "colour", "vin", "notes"):
        if field in data:
            setattr(vehicle, field, want(data, field))
    for field in ("year", "mileage"):
        if field in data:
            setattr(vehicle, field, as_int(data[field]))
    if want(data, "reg_no"):
        vehicle.reg_no = data["reg_no"].strip().upper().replace(" ", "")
    if "customer_id" in data and as_int(data["customer_id"]):
        vehicle.customer_id = as_int(data["customer_id"])
    db.session.commit()
    log_activity(
        "vehicle.updated", f"Vehicle {vehicle.reg_no} updated",
        entity_type="vehicle", entity_id=vehicle.id, entity_ref=vehicle.reg_no, commit=True,
    )
    return jsonify({"vehicle": vehicle.to_dict()})


# ─────────────────────────────────────────────────────────────────────────────
# Global search (powers the command palette)
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/search")
@login_required
def global_search():
    q = (want(request.args, "q") or "").strip()
    if len(q) < 2:
        return jsonify({"jobs": [], "customers": [], "vehicles": [], "query": q})

    like = f"%{q}%"
    jobs = (
        JobCard.query.join(Vehicle, JobCard.vehicle_id == Vehicle.id)
        .join(Customer, JobCard.customer_id == Customer.id)
        .filter(or_(
            JobCard.job_no.ilike(like), Vehicle.reg_no.ilike(like),
            Customer.name.ilike(like), JobCard.description.ilike(like),
        ))
        .order_by(JobCard.id.desc()).limit(8).all()
    )
    customers = Customer.query.filter(or_(
        Customer.name.ilike(like), Customer.phone.ilike(like),
        Customer.email.ilike(like), Customer.company.ilike(like),
    )).order_by(Customer.name).limit(6).all()
    vehicles = Vehicle.query.filter(or_(
        Vehicle.reg_no.ilike(like), Vehicle.make.ilike(like),
        Vehicle.model.ilike(like), Vehicle.vin.ilike(like),
    )).order_by(Vehicle.reg_no).limit(6).all()

    return jsonify({
        "query": q,
        "jobs": [{"id": j.id, "job_no": j.job_no, "reg_no": j.vehicle.reg_no if j.vehicle else "",
                  "customer": j.customer.name if j.customer else "",
                  "stage": j.stage, "stage_label": j.stage_label} for j in jobs],
        "customers": [{"id": c.id, "name": c.name, "phone": c.phone,
                       "open_jobs": c.open_jobs} for c in customers],
        "vehicles": [{"id": v.id, "reg_no": v.reg_no, "title": v.title,
                      "customer": v.customer.name if v.customer else ""} for v in vehicles],
    })


# ─────────────────────────────────────────────────────────────────────────────
# CSV export — accountants and insurers both ask for spreadsheets
# ─────────────────────────────────────────────────────────────────────────────
@bp.get("/export/<dataset>.csv")
@login_required
def export_csv(dataset: str):
    import csv
    import io

    from flask import Response

    dataset = dataset.lower()
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if dataset == "jobs":
        writer.writerow(["Job no", "Checked in", "Registration", "Vehicle", "Customer",
                         "Phone", "Service", "Stage", "Priority", "Insurance",
                         "Promised", "Days in shop", "Estimate total", "Currency"])
        for job in JobCard.query.order_by(JobCard.id.desc()).all():
            est = job.latest_estimate
            writer.writerow([
                job.job_no,
                job.checked_in_at.strftime("%Y-%m-%d") if job.checked_in_at else "",
                job.vehicle.reg_no if job.vehicle else "",
                job.vehicle.title if job.vehicle else "",
                job.customer.name if job.customer else "",
                job.customer.phone if job.customer else "",
                job.service, job.stage, job.priority,
                "yes" if job.is_insurance else "no",
                job.promised_date.isoformat() if job.promised_date else "",
                job.days_in_shop,
                f"{est.total:.2f}" if est else "",
                est.currency if est else "USD",
            ])
    elif dataset == "invoices":
        writer.writerow(["Invoice", "Job", "Customer", "Type", "Insurer", "Subtotal", "VAT",
                         "Total", "Paid", "Balance", "Status", "Due", "Issued", "Paid on"])
        for inv in Invoice.query.order_by(Invoice.id.desc()).all():
            writer.writerow([
                inv.invoice_no,
                inv.job.job_no if inv.job else "",
                inv.customer.name if inv.customer else "",
                "insurance" if inv.is_insurance else "customer",
                inv.insurer_code or "",
                f"{inv.subtotal:.2f}", f"{inv.vat:.2f}", f"{inv.total:.2f}",
                f"{inv.amount_paid:.2f}", f"{inv.balance:.2f}", inv.status,
                inv.due_date.isoformat() if inv.due_date else "",
                inv.issued_at.strftime("%Y-%m-%d") if inv.issued_at else "",
                inv.paid_at.strftime("%Y-%m-%d") if inv.paid_at else "",
            ])
    elif dataset == "claims":
        writer.writerow(["Claim no", "Job", "Insurer", "Policy", "Assessor", "Status",
                         "Claimed", "Approved", "Shortfall", "Excess", "Excess paid", "Aging days"])
        for claim in Claim.query.order_by(Claim.id.desc()).all():
            writer.writerow([
                claim.claim_no or claim.id,
                claim.job.job_no if claim.job else "",
                claim.insurer_name, claim.policy_no or "", claim.assessor_name or "",
                claim.status_label, f"{claim.claimed_amount:.2f}", f"{claim.approved_amount:.2f}",
                f"{claim.shortfall:.2f}", f"{claim.excess:.2f}",
                "yes" if claim.excess_paid else "no", claim.aging_days,
            ])
    elif dataset == "parts":
        writer.writerow(["SKU", "Name", "Category", "Supplier", "On hand", "Reorder level",
                         "Unit cost", "Sell price", "Stock value", "Location"])
        for part in Part.query.order_by(Part.name).all():
            writer.writerow([
                part.sku, part.name, part.category, part.supplier or "",
                f"{part.qty_on_hand:.2f}", f"{part.reorder_level:.2f}",
                f"{part.cost_price:.2f}", f"{part.sell_price:.2f}",
                f"{part.stock_value:.2f}", part.location or "",
            ])
    else:
        return bad("Unknown export. Use jobs, invoices, claims or parts.", 404)

    filename = f"topclass-{dataset}-{date.today().isoformat()}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
