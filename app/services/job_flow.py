"""Job card lifecycle: intake, stage transitions, QC gating, invoicing."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from ..constants import (
    CLOSED_STAGES,
    QC_CHECKLIST,
    STAGES,
)
from ..extensions import db
from ..models import (
    Customer,
    Estimate,
    EstimateItem,
    Invoice,
    JobCard,
    JobStageEvent,
    Part,
    QcResult,
    StockMovement,
    Vehicle,
    gen_ref,
    utcnow,
)


class JobFlowError(Exception):
    """Raised when a workflow rule is violated."""


# ─────────────────────────────────────────────────────────────────────────────
# Numbering
# ─────────────────────────────────────────────────────────────────────────────
def next_job_no(year: int | None = None) -> str:
    year = year or date.today().year
    prefix = f"TC-{year}-"
    count = (
        db.session.query(func.count(JobCard.id))
        .filter(JobCard.job_no.like(f"{prefix}%"))
        .scalar()
        or 0
    )
    return f"{prefix}{count + 1:04d}"


def next_invoice_no() -> str:
    year = date.today().year
    prefix = f"INV-{year}-"
    count = (
        db.session.query(func.count(Invoice.id))
        .filter(Invoice.invoice_no.like(f"{prefix}%"))
        .scalar()
        or 0
    )
    return f"{prefix}{count + 1:04d}"


def next_receipt_no() -> str:
    """Sequential, human-quotable receipt number, e.g. RCT-2026-0007."""
    from ..models import Payment

    year = date.today().year
    prefix = f"RCT-{year}-"
    count = (
        db.session.query(func.count(Payment.id))
        .filter(Payment.receipt_no.like(f"{prefix}%"))
        .scalar()
        or 0
    )
    return f"{prefix}{count + 1:04d}"


# ─────────────────────────────────────────────────────────────────────────────
# Intake
# ─────────────────────────────────────────────────────────────────────────────
def find_or_create_customer(
    *,
    name: str,
    phone: str | None = None,
    whatsapp: str | None = None,
    email: str | None = None,
    is_fleet: bool = False,
    company: str | None = None,
) -> Customer:
    customer = None
    if phone:
        customer = Customer.query.filter(
            db.or_(Customer.phone == phone, Customer.whatsapp == phone)
        ).first()
    if not customer:
        customer = Customer(
            name=name,
            phone=phone,
            whatsapp=whatsapp or phone,
            email=email,
            is_fleet=is_fleet,
            company=company,
        )
        db.session.add(customer)
        db.session.flush()
    return customer


def find_or_create_vehicle(
    customer: Customer, *, reg_no: str, **fields
) -> Vehicle:
    reg = (reg_no or "").strip().upper().replace(" ", "")
    vehicle = Vehicle.query.filter(
        func.upper(func.replace(Vehicle.reg_no, " ", "")) == reg
    ).first()
    if vehicle:
        for key, value in fields.items():
            if value not in (None, "") and hasattr(vehicle, key):
                setattr(vehicle, key, value)
        return vehicle

    vehicle = Vehicle(customer=customer, reg_no=reg, **fields)
    db.session.add(vehicle)
    db.session.flush()
    return vehicle


def open_job_card(
    *,
    customer: Customer,
    vehicle: Vehicle,
    service: str,
    description: str | None = None,
    damage_summary: str | None = None,
    priority: str = "NORMAL",
    promised_date: date | None = None,
    bay: str | None = None,
    user_id: int | None = None,
    technician_id: int | None = None,
    fuel_level: str | None = None,
    valuables: str | None = None,
    odometer_in: int | None = None,
) -> JobCard:
    job = JobCard(
        job_no=next_job_no(),
        customer=customer,
        vehicle=vehicle,
        service=service,
        description=description,
        damage_summary=damage_summary,
        priority=priority,
        promised_date=promised_date or (date.today() + timedelta(days=7)),
        bay=bay,
        technician_id=technician_id,
        estimator_id=user_id,
        fuel_level=fuel_level,
        valuables=valuables,
        odometer_in=odometer_in or vehicle.mileage,
        stage="INTAKE",
    )
    db.session.add(job)
    db.session.flush()
    db.session.add(
        JobStageEvent(
            job_id=job.id,
            stage="INTAKE",
            note="Vehicle booked in" + (f" — {damage_summary}" if damage_summary else ""),
            user_id=user_id,
        )
    )
    db.session.commit()
    return job


# ─────────────────────────────────────────────────────────────────────────────
# Stage transitions
# ─────────────────────────────────────────────────────────────────────────────
def next_stage(stage: str) -> str | None:
    try:
        idx = STAGES.index(stage)
    except ValueError:
        return None
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


def can_advance(job: JobCard) -> tuple[bool, str]:
    """Guard rails that stop the shop jumping ahead of reality."""
    if job.stage in CLOSED_STAGES:
        return False, "Job card is already closed."

    if job.stage == "AWAITING_APPROVAL":
        est = job.latest_estimate
        if est is None or est.status != "APPROVED":
            return False, "Waiting on customer approval of the estimate."

    if job.stage == "PARTS_ORDER":
        blocking = [p for p in job.job_parts if p.is_blocking]
        if blocking:
            names = ", ".join(p.description for p in blocking[:3])
            return False, f"{len(blocking)} part(s) still outstanding: {names}"

    if job.stage == "QC":
        rows = job.qc_rows()
        if not rows:
            return False, "Run the QC checklist before releasing the vehicle."
        failed = [r for r in rows if not r.passed]
        if failed:
            return False, f"{len(failed)} QC check(s) failed. Re-work required before release."

    return True, next_stage(job.stage) or "COLLECTED"


def advance_job(job: JobCard, *, user_id: int | None = None, note: str | None = None,
                force: bool = False, target: str | None = None) -> JobCard:
    if target:
        if target not in STAGES:
            raise JobFlowError(f"Unknown stage '{target}'.")
        new_stage = target
    else:
        ok, message = can_advance(job)
        if not ok and not force:
            raise JobFlowError(message)
        new_stage = message if ok else (next_stage(job.stage) or "COLLECTED")

    job.stage = new_stage
    if new_stage == "READY":
        job.completed_at = job.completed_at or utcnow()
    if new_stage == "COLLECTED":
        job.collected_at = utcnow()

    db.session.add(JobStageEvent(job_id=job.id, stage=new_stage, note=note, user_id=user_id))

    if new_stage == "QC":
        seed_qc_checklist(job)
    if new_stage == "COLLECTED":
        ensure_invoice(job, user_id=user_id)

    db.session.commit()
    return job


def seed_qc_checklist(job: JobCard) -> list[QcResult]:
    """Create the QC checklist rows for a job (idempotent)."""
    existing = {r.item for r in QcResult.query.filter_by(job_id=job.id).all()}
    created = []
    for item in QC_CHECKLIST:
        if item in existing:
            continue
        row = QcResult(job_id=job.id, item=item, passed=False)
        db.session.add(row)
        created.append(row)
    if created:
        db.session.flush()
        db.session.expire(job, ["qc_results"])
    return created


def record_qc(job: JobCard, results: list[dict], user_id: int | None = None) -> list[QcResult]:
    """results: [{"item": str, "passed": bool, "comment": str|None}]"""
    seed_qc_checklist(job)
    rows = QcResult.query.filter_by(job_id=job.id).all()
    by_item = {r.item: r for r in rows}
    for entry in results:
        item = entry.get("item")
        if item in by_item:
            by_item[item].passed = bool(entry.get("passed"))
            by_item[item].comment = entry.get("comment")
            by_item[item].checked_by = user_id
    db.session.commit()
    db.session.expire(job, ["qc_results"])
    return job.qc_rows()


# ─────────────────────────────────────────────────────────────────────────────
# Parts
# ─────────────────────────────────────────────────────────────────────────────
def apply_stock_movement(part: Part, delta: Decimal, *, reason: str, reference: str | None = None,
                         user_id: int | None = None) -> Part:
    part.qty_on_hand = Decimal(str(part.qty_on_hand or 0)) + Decimal(str(delta))
    db.session.add(
        StockMovement(
            part_id=part.id, delta=Decimal(str(delta)), reason=reason,
            reference=reference, user_id=user_id,
        )
    )
    return part


def receive_job_part(job_part, *, user_id: int | None = None) -> None:
    job_part.status = "RECEIVED"
    job_part.received_at = utcnow()
    if job_part.part:
        apply_stock_movement(
            job_part.part, -Decimal(str(job_part.quantity or 0)),
            reason="JOB_ISSUE", reference=job_part.job.job_no if job_part.job else None,
            user_id=user_id,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Estimating bridge
# ─────────────────────────────────────────────────────────────────────────────
def save_estimate(job: JobCard, lines: list[dict], *, vat_rate: Decimal = Decimal("0.15"),
                  notes: str | None = None, mark_sent: bool = True) -> Estimate:
    """Persist a new estimate version for a job."""
    from .pricing import summarise

    existing = job.estimates or []
    estimate = Estimate(
        job_id=job.id,
        version=len(existing) + 1,
        notes=notes,
        status="SENT" if mark_sent else "DRAFT",
        sent_at=utcnow() if mark_sent else None,
    )
    db.session.add(estimate)
    db.session.flush()

    for idx, line in enumerate(lines):
        db.session.add(
            EstimateItem(
                estimate_id=estimate.id,
                kind=line.get("kind", "LABOUR"),
                panel=line.get("panel"),
                description=line.get("description") or "Item",
                operation=line.get("operation"),
                quantity=line.get("quantity", 1),
                unit=line.get("unit", "ea"),
                unit_price=line.get("unit_price", 0),
                markup_pct=line.get("markup_pct", 0),
                part_id=line.get("part_id"),
                sort_order=idx,
            )
        )
    db.session.flush()

    # Price the header from the lines we were given (single source of truth).
    summary = summarise(lines, vat_rate)
    estimate.labour_total = summary["labour_total"]
    estimate.materials_total = summary["materials_total"]
    estimate.parts_total = summary["parts_total"]
    estimate.subtotal = summary["subtotal"]
    estimate.vat = summary["vat"]
    estimate.total = summary["total"]

    if job.stage in {"ASSESSMENT", "AWAITING_APPROVAL"}:
        job.stage = "AWAITING_APPROVAL"
        db.session.add(JobStageEvent(job_id=job.id, stage="AWAITING_APPROVAL",
                                     note=f"Estimate {estimate.reference} sent for approval"))
    db.session.commit()
    return estimate


def approve_estimate(estimate: Estimate, *, approved_by: str, user_id: int | None = None) -> Estimate:
    estimate.status = "APPROVED"
    estimate.approved_by = approved_by
    estimate.approved_at = utcnow()
    job = estimate.job
    if job and job.stage == "AWAITING_APPROVAL":
        job.stage = "PARTS_ORDER" if any(p.is_blocking for p in job.job_parts) else "STRIP"
        db.session.add(JobStageEvent(job_id=job.id, stage=job.stage,
                                     note=f"Estimate approved by {approved_by}", user_id=user_id))
    db.session.commit()
    return estimate


# ─────────────────────────────────────────────────────────────────────────────
# Invoicing
# ─────────────────────────────────────────────────────────────────────────────
def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def ensure_invoice(job: JobCard, *, user_id: int | None = None) -> Invoice | None:
    if job.invoices:
        return job.invoices[0]
    estimate = job.latest_estimate
    if not estimate:
        return None

    invoice = Invoice(
        invoice_no=next_invoice_no(),
        job=job,
        customer_id=job.customer_id,
        currency=estimate.currency,
        subtotal=_dec(estimate.subtotal),
        vat=_dec(estimate.vat),
        total=_dec(estimate.total),
        status="DRAFT",
        due_date=date.today() + timedelta(days=14),
    )
    db.session.add(invoice)
    db.session.flush()
    return invoice


def record_payment(invoice: Invoice, amount: Decimal, *, method: str = "CASH",
                   reference: str | None = None, user_id: int | None = None):
    from ..models import Payment

    payment = Payment(
        invoice_id=invoice.id, amount=Decimal(str(amount)), method=method,
        reference=reference, received_by=user_id, receipt_no=next_receipt_no(),
    )
    db.session.add(payment)
    invoice.amount_paid = Decimal(str(invoice.amount_paid or 0)) + Decimal(str(amount))

    if invoice.balance <= 0:
        invoice.status = "PAID"
        invoice.paid_at = utcnow()
    elif Decimal(str(invoice.amount_paid or 0)) > 0:
        invoice.status = "PART_PAID"
    db.session.commit()
    return payment


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────
def workshop_metrics() -> dict:
    today = date.today()
    jobs = JobCard.query.all()
    open_jobs = [j for j in jobs if j.is_open]
    ready = [j for j in jobs if j.stage == "READY"]
    overdue = [j for j in open_jobs if j.is_overdue]

    closed = [j for j in jobs if j.stage == "COLLECTED" and j.collected_at and j.checked_in_at]
    tat = [j.days_in_shop for j in closed] or [0]

    invoices = Invoice.query.all()
    outstanding = sum(
        (Decimal(str(i.balance)) for i in invoices if i.status not in {"PAID", "CANCELLED"}),
        Decimal("0"),
    )
    wip_value = sum(
        (Decimal(str(j.latest_estimate.total)) for j in open_jobs if j.latest_estimate),
        Decimal("0"),
    )

    return {
        "open_jobs": len(open_jobs),
        "ready_for_collection": len(ready),
        "overdue_jobs": len(overdue),
        "in_paint": len([j for j in open_jobs if j.stage == "PAINT"]),
        "awaiting_parts": len([j for j in open_jobs if any(p.is_blocking for p in j.job_parts)]),
        "avg_turnaround_days": round(sum(tat) / len(tat), 1),
        "collected_this_month": len(
            [j for j in jobs if j.collected_at and j.collected_at.month == today.month
             and j.collected_at.year == today.year]
        ),
        "wip_value": float(wip_value.quantize(Decimal("0.01"))),
        "outstanding_receivables": float(outstanding.quantize(Decimal("0.01"))),
        "outstanding_count": len([i for i in invoices if i.status not in {"PAID", "CANCELLED"}]),
        "overdue_invoices": len([i for i in invoices if i.is_overdue]),
        "stage_breakdown": {
            stage: len([j for j in open_jobs if j.stage == stage]) for stage in STAGES
        },
    }
