"""Job card lifecycle: intake, stage transitions, QC gating, invoicing."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from .. import tz
from ..constants import (
    CLOSED_STAGES,
    QC_CHECKLIST,
    SERVICE_NAMES,
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
    normalise_id_number,
    utcnow,
)
from . import phone as phone_numbers


class JobFlowError(Exception):
    """Raised when a workflow rule is violated."""


# ─────────────────────────────────────────────────────────────────────────────
# Numbering
# ─────────────────────────────────────────────────────────────────────────────
def next_job_no(year: int | None = None) -> str:
    year = year or tz.today().year
    prefix = f"TC-{year}-"
    count = (
        db.session.query(func.count(JobCard.id))
        .filter(JobCard.job_no.like(f"{prefix}%"))
        .scalar()
        or 0
    )
    return f"{prefix}{count + 1:04d}"


def next_invoice_no() -> str:
    year = tz.today().year
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

    year = tz.today().year
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
    id_number: str | None = None,
) -> Customer:
    """The customer with this number, or a new one.

    The number is stored in one canonical form (``+263775550555``) whichever way
    it was typed, because the WhatsApp bot dials by normalising it — a number
    saved as ``0775550555`` used to be a customer the bot could not reach.

    The lookup deliberately accepts *any* spelling of the same number. Rows saved
    before this canonicalisation existed still hold ``0775550555`` and
    ``+263 77 555 0555``, and matching only the canonical form would quietly
    create a second customer for somebody already on file.
    """
    phone = phone_numbers.format_msisdn(phone) or None
    whatsapp = phone_numbers.format_msisdn(whatsapp) or phone
    id_number = normalise_id_number(id_number)

    customer = None
    if phone:
        variants = {phone, phone_numbers.normalise_msisdn(phone)}
        variants.discard("")
        customer = Customer.query.filter(
            db.or_(Customer.phone.in_(variants), Customer.whatsapp.in_(variants))
        ).first()
        if not customer:
            # Fall back to a normalised comparison for rows stored in a form we
            # cannot enumerate ("00263...", "+263 77 555 0555" with punctuation).
            wanted = phone_numbers.normalise_msisdn(phone)
            customer = next(
                (c for c in Customer.query.all()
                 if c.wa_number and c.wa_number == wanted),
                None,
            )
    if not customer:
        customer = Customer(
            name=name,
            id_number=id_number,
            phone=phone,
            whatsapp=whatsapp,
            email=email,
            is_fleet=is_fleet,
            company=company,
        )
        db.session.add(customer)
        db.session.flush()
    elif id_number and not customer.id_number:
        # Fill a blank ID, but never overwrite one already on file: the number
        # recorded when somebody collected the car is the one that was checked
        # against the document, and a later form must not quietly replace it.
        customer.id_number = id_number
    return customer


def apply_customer_phone(customer: Customer, phone: str | None) -> bool:
    """Record a number the desk corrected during intake.

    Only a real change is written: an empty box is never read as "delete the
    number", because the operator may simply not have touched it. The corrected
    number becomes the WhatsApp number too — it is the one we would ring — but
    only when the customer has no separate WhatsApp number of their own on file,
    which the registry lets them keep.
    """
    corrected = phone_numbers.format_msisdn(phone) or None
    if not corrected or corrected == customer.phone:
        return False
    previous = customer.phone
    customer.phone = corrected
    if not customer.whatsapp or customer.whatsapp == previous:
        customer.whatsapp = corrected
    return True


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
        promised_date=promised_date or (tz.today() + timedelta(days=7)),
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
def save_estimate(job: JobCard | None, lines: list[dict], *,
                  booking=None, vat_rate: Decimal = Decimal("0.15"),
                  notes: str | None = None, mark_sent: bool = False) -> Estimate:
    """Persist a new estimate version.

    Attaches to a ``job`` (in-house work: the desk prices what is already on the
    ramp) or to a ``booking`` (the enquiry path, where quoting happens *before*
    any card exists and the card is only opened once the customer accepts).

    ``mark_sent`` defaults to **False**, and that is deliberate: an estimate is
    not sent because somebody saved it. The only thing that sends it is
    :func:`notifications.send_quotation`, and that is where the status flips.
    Saving used to mark it SENT while the customer had received nothing but a
    notice, so the desk read "sent" on a quotation the customer never held.
    """
    from .pricing import summarise

    if job is None and booking is None:
        raise JobFlowError("A quotation needs an enquiry or a job card behind it.")

    # Versions are numbered per record, so the second quotation on one enquiry is
    # v2 whether or not somebody else's card sits next to it in the table.
    siblings = job.estimates if job is not None else booking.estimates
    estimate = Estimate(
        job_id=job.id if job else None,
        booking_id=booking.id if booking else None,
        version=len(siblings or []) + 1,
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

    db.session.commit()
    return estimate


def attach_quotation(estimate: Estimate, job: JobCard) -> Estimate:
    """Move an enquiry's quotation onto the job card it was booked in on.

    Moved, not copied. The customer is already holding a PDF and a reference from
    the enquiry; re-issuing those under a new number at the moment they say yes
    reads as a second quotation, and leaves two documents to keep in step.

    The enquiry link is kept as well: the quotation was priced there, and the
    paperwork should stay findable from the enquiry it came out of.
    """
    estimate.job_id = job.id
    if estimate.booking_id is None and job.booking_id is not None:
        estimate.booking_id = job.booking_id
    return estimate


def latest_quotation(booking) -> Estimate | None:
    """The quotation on an enquiry the desk would act on.

    An accepted one if there is one — that is the price the customer agreed to —
    otherwise the most recent, which is whatever the desk is waiting on an
    answer for.
    """
    quotes = list(booking.estimates or [])
    if not quotes:
        return None
    approved = [e for e in quotes if e.status == "APPROVED"]
    return approved[-1] if approved else quotes[-1]


def book_in_estimate(estimate: Estimate, *, user_id: int | None = None,
                     vehicle: Vehicle | None = None, **fields) -> JobCard:
    """Open the job card for an accepted quotation.

    This is what turns a quotation into work, and it is the only way a card is
    meant to be born: the customer has accepted a price, so the shop has
    something to build. Opening a card before that would mean holding a ramp slot
    for work nobody has agreed to.

    The approved quotation *moves* onto the new card rather than being copied.
    The reference, the totals and the public link the customer already holds all
    stay valid, and there is one document to keep in step with reality instead of
    a quotation and a near-identical card copy that can drift apart.

    ``fields`` carries whatever the desk captured when booking the car in —
    mileage, fuel, bay, technician — because none of that is known at quoting
    time.
    """
    if estimate.job is not None:
        return estimate.job
    if estimate.status != "APPROVED":
        raise JobFlowError(
            "The customer has to accept the quotation before the car is booked in."
        )

    customer = estimate.customer
    if customer is None:
        raise JobFlowError("This quotation has nobody to book in.")

    booking = estimate.booking
    vehicle = vehicle or estimate.vehicle
    if vehicle is None:
        raise JobFlowError(
            "Capture the vehicle's registration before booking this quotation in."
        )

    job = open_job_card(
        customer=customer,
        vehicle=vehicle,
        service=estimate.service_name or SERVICE_NAMES[0],
        description=fields.get("description"),
        # The enquiry notes are what the customer described on the way in, and
        # the card should carry them even when the desk types nothing at intake.
        damage_summary=fields.get("damage_summary") or (booking.notes if booking else None),
        priority=fields.get("priority") or "NORMAL",
        promised_date=fields.get("promised_date") or (booking.slot_date if booking else None),
        bay=fields.get("bay"),
        user_id=user_id,
        technician_id=fields.get("technician_id"),
        fuel_level=fields.get("fuel_level"),
        valuables=fields.get("valuables"),
        odometer_in=fields.get("odometer_in"),
    )
    # Both links are kept: the card knows which enquiry produced it, and the
    # quotation stays reachable from the enquiry it was priced on.
    job.booking_id = booking.id if booking else None
    attach_quotation(estimate, job)
    db.session.commit()
    return job


def approve_estimate(estimate: Estimate, *, approved_by: str, user_id: int | None = None) -> Estimate:
    """The customer has accepted the quotation.

    Acceptance is the commitment, so it is also the moment the car is booked in —
    but only when we already know which car it is. Enquiries the bot takes often
    have no registration on them, and a job card cannot exist without a vehicle,
    so those wait for the desk to book the car in. The quotation is carried onto
    the card then; it is never re-priced and never re-typed.

    An estimate that already hangs off a job card — the in-house path, where the
    car is on the ramp before anyone prices it — simply becomes approved.
    """
    estimate.status = "APPROVED"
    estimate.approved_by = approved_by
    estimate.approved_at = utcnow()
    db.session.commit()

    if estimate.job is None and estimate.vehicle is not None:
        book_in_estimate(estimate, user_id=user_id)
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
        due_date=tz.today() + timedelta(days=14),
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
    today = tz.today()
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
