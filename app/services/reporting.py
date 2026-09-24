"""End-of-day reporting.

The workshop closes the day out from a single sheet, so the numbers are
assembled here once and shared by both the JSON API and the printable PDF.
That way the paper on the wall and the screen in the office can never tell
two different stories about the same day.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from ..constants import (
    BOOKING_OUTCOMES,
    BOOKING_STATUS_LABELS,
    BOOKING_STATUSES,
    STAGE_LABELS,
    STAGES,
    TASK_STATUSES,
    TASK_STATUS_LABELS,
)
from ..models import Booking, Invoice, JobCard, Payment, Task, User, utcnow


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def _money(value) -> float:
    return float(_dec(value).quantize(Decimal("0.01")))


def _day_window(day: date) -> tuple[datetime, datetime]:
    """Half-open [start, end) window covering one local calendar day."""
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def _in_window(value, start: datetime, end: datetime) -> bool:
    return bool(value and start <= value < end)


def _booking_row(booking: Booking) -> dict:
    return {
        "id": booking.id,
        "reference": booking.display_reference,
        "enquiry_reference": booking.reference,
        "customer_name": booking.customer.name if booking.customer else None,
        "customer_phone": booking.customer.phone if booking.customer else None,
        "reg_no": booking.vehicle.reg_no if booking.vehicle else None,
        "service": booking.service,
        "slot_date": booking.slot_date.isoformat() if booking.slot_date else None,
        "slot_time": booking.slot_time,
        "status": booking.status,
        "status_label": BOOKING_STATUS_LABELS.get(booking.status, booking.status),
        "source": booking.source,
        "confirmed_by": booking.confirmed_by.full_name if booking.confirmed_by else None,
        "attended_by": booking.attended_by.full_name if booking.attended_by else None,
        "outcome": booking.outcome,
        "outcome_label": BOOKING_OUTCOMES.get(booking.outcome or ""),
    }


def _invoice_row(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_no": invoice.invoice_no,
        "customer_name": invoice.customer.name if invoice.customer else None,
        "job_no": invoice.job.job_no if invoice.job else None,
        "total": _money(invoice.total),
        "amount_paid": _money(invoice.amount_paid),
        "balance": _money(invoice.balance),
        "status": invoice.status,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "is_overdue": invoice.is_overdue,
    }


def _payment_row(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "receipt_no": payment.receipt_no,
        "invoice_no": payment.invoice.invoice_no if payment.invoice else None,
        "payer_name": payment.payer_name,
        "amount": _money(payment.amount),
        "method": payment.method,
        "received_by": payment.user.full_name if payment.user else None,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
    }


def _task_row(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "detail": task.detail,
        "category": task.category,
        "status": task.status,
        "status_label": TASK_STATUS_LABELS.get(task.status, task.status),
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "custodian": task.custodian.full_name if task.custodian else None,
        "job_no": task.job.job_no if task.job else None,
        "is_overdue": task.is_overdue,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def end_of_day(day: date | None = None) -> dict:
    """Everything that happened on ``day``, plus what is still open."""
    day = day or date.today()
    start, end = _day_window(day)

    jobs = JobCard.query.all()
    bookings = Booking.query.all()
    invoices = Invoice.query.all()
    payments = (Payment.query
                .filter(Payment.created_at >= start, Payment.created_at < end)
                .order_by(Payment.id.asc())
                .all())

    # ── Job cards ────────────────────────────────────────────────────────
    open_jobs = [j for j in jobs if j.is_open]
    counts_by_stage: dict[str, int] = {}
    for job in open_jobs:
        counts_by_stage[job.stage] = counts_by_stage.get(job.stage, 0) + 1

    job_rows = sorted(open_jobs, key=lambda j: (j.promised_date or date.max, j.id))
    jobs_section = {
        "opened": len([j for j in jobs if _in_window(j.checked_in_at, start, end)]),
        "completed": len([j for j in jobs if _in_window(j.completed_at, start, end)]),
        "collected": len([j for j in jobs if _in_window(j.collected_at, start, end)]),
        "open": len(open_jobs),
        "overdue": len([j for j in open_jobs if j.is_overdue]),
        "by_stage": [
            {"code": code, "label": STAGE_LABELS.get(code, code),
             "count": counts_by_stage.get(code, 0)}
            for code in STAGES
        ],
        "list": [
            {
                "job_no": job.job_no,
                "customer_name": job.customer.name if job.customer else None,
                "reg_no": job.vehicle.reg_no if job.vehicle else None,
                "service": job.service,
                "stage": job.stage,
                "stage_label": job.stage_label,
                "priority": job.priority,
                "technician": job.technician.full_name if job.technician else None,
                "promised_date": job.promised_date.isoformat() if job.promised_date else None,
                "days_in_shop": job.days_in_shop,
                "is_overdue": job.is_overdue,
            }
            for job in job_rows
        ],
    }

    # ── Enquiries & bookings (the same record at different statuses) ─────
    scheduled = [b for b in bookings if b.slot_date == day]
    raised = [b for b in bookings if _in_window(b.created_at, start, end)]
    awaiting = [b for b in bookings if b.status == "REQUESTED"]
    handled = [b for b in bookings if b.status in ("CONFIRMED", "ATTENDED")]

    status_counts = {code: 0 for code in BOOKING_STATUSES}
    for booking in scheduled:
        if booking.status in status_counts:
            status_counts[booking.status] += 1

    bookings_section = {
        "raised": len(raised),
        "scheduled": len(scheduled),
        "awaiting_confirmation": len(awaiting),
        "handled": len(handled),
        # Whether the customers who turned up actually committed to the work.
        "secured": len([b for b in bookings if b.outcome == "SECURED"]),
        "walked_out": len([b for b in bookings if b.outcome == "WALKED_OUT"]),
        "by_status": [
            {"code": code, "label": BOOKING_STATUS_LABELS.get(code, code),
             "count": status_counts[code]}
            for code in BOOKING_STATUSES
        ],
        "pending": [_booking_row(b) for b in awaiting],
        "scheduled_list": [_booking_row(b) for b in scheduled],
    }

    # ── Money ────────────────────────────────────────────────────────────
    unsettled = [i for i in invoices
                 if i.status not in ("PAID", "CANCELLED") and _dec(i.total) > 0]
    settled_today = [i for i in invoices if _in_window(i.paid_at, start, end)]
    issued_today = [i for i in invoices if _in_window(i.issued_at, start, end)]
    overdue = [i for i in unsettled if i.is_overdue]

    by_method: dict[str, dict] = {}
    for payment in payments:
        bucket = by_method.setdefault(payment.method or "CASH", {"count": 0, "total": 0.0})
        bucket["count"] += 1
        bucket["total"] = _money(_dec(bucket["total"]) + _dec(payment.amount))

    money_section = {
        "collected": _money(sum((_dec(p.amount) for p in payments), Decimal("0"))),
        "invoiced": _money(sum((_dec(i.total) for i in issued_today), Decimal("0"))),
        "outstanding": _money(sum((i.balance for i in unsettled), Decimal("0"))),
        "unpaid_count": len(unsettled),
        "paid_today_count": len(settled_today),
        "overdue_count": len(overdue),
        "overdue_total": _money(sum((i.balance for i in overdue), Decimal("0"))),
        "by_method": [
            {"method": method, **values}
            for method, values in sorted(by_method.items(),
                                         key=lambda kv: kv[1]["total"], reverse=True)
        ],
        "payments": [_payment_row(p) for p in payments],
        "unsettled": [_invoice_row(i) for i in sorted(unsettled, key=lambda i: i.invoice_no)],
        "settled_today": [_invoice_row(i) for i in settled_today],
    }

    # ── The day book (tasks) ─────────────────────────────────────────────
    # The sheet is a handover document, so the work nobody has done yet matters
    # as much as the money. `DONE` is counted for the day being reported on, not
    # for all time — otherwise the board's fourth status always reads as the
    # lifetime total and tells the foreman nothing.
    #
    # `open` is as-at-now, matching the job card and booking figures on this same
    # sheet: the report is the day's events plus what is still outstanding, not a
    # historical reconstruction. Asking for a past date therefore shows that day's
    # completions and today's backlog, which is the useful reading for a review.
    tasks = Task.query.all()
    open_tasks = [t for t in tasks if t.is_open]
    done_today = [t for t in tasks if _in_window(t.completed_at, start, end)]

    task_status_counts = {code: 0 for code in TASK_STATUSES}
    for task in open_tasks:
        if task.status in task_status_counts:
            task_status_counts[task.status] += 1
    task_status_counts["DONE"] = len(done_today)

    # Who is carrying what, so the sheet can be handed over by name.
    custodian_counts: dict[str, dict] = {}
    for task in open_tasks + done_today:
        name = task.custodian.full_name if task.custodian else "Unassigned"
        bucket = custodian_counts.setdefault(name, {"open": 0, "done": 0, "overdue": 0})
        if task.is_open:
            bucket["open"] += 1
            if task.is_overdue:
                bucket["overdue"] += 1
        else:
            bucket["done"] += 1

    task_rows = sorted(open_tasks, key=lambda t: (t.due_date or date.max, t.id))
    tasks_section = {
        "open": len(open_tasks),
        "doing": task_status_counts.get("DOING", 0),
        "blocked": task_status_counts.get("BLOCKED", 0),
        "overdue": len([t for t in open_tasks if t.is_overdue]),
        "due_today": len([t for t in open_tasks if t.due_date == day]),
        "undated": len([t for t in open_tasks if not t.due_date]),
        "done_today": len(done_today),
        "by_status": [
            {"code": code, "label": TASK_STATUS_LABELS.get(code, code),
             "count": task_status_counts.get(code, 0)}
            for code in TASK_STATUSES
        ],
        "by_custodian": [
            {"name": name, **values}
            for name, values in sorted(custodian_counts.items(),
                                       key=lambda kv: (-kv[1]["open"], kv[0]))
        ],
        "list": [_task_row(t) for t in task_rows],
        "done": [_task_row(t) for t in sorted(done_today, key=lambda t: t.id)],
    }

    # ── Staff ────────────────────────────────────────────────────────────
    staff_section = []
    for user in User.query.order_by(User.id.asc()).all():
        confirmed = len([b for b in bookings if b.confirmed_by_id == user.id])
        attended = len([b for b in bookings if b.attended_by_id == user.id])
        owned = len([j for j in open_jobs if j.technician_id == user.id])
        carried = len([t for t in open_tasks if t.custodian_id == user.id])
        finished = len([t for t in done_today if t.custodian_id == user.id])
        # Somebody who only ever appears in the day book still belongs on the
        # sheet — a storeman with three parts to chase has done a day's work.
        if not (confirmed or attended or owned or carried or finished):
            continue
        staff_section.append({
            "name": user.full_name,
            "role": user.role,
            "confirmed": confirmed,
            "attended": attended,
            "open_jobs": owned,
            "open_tasks": len([t for t in open_tasks if t.custodian_id == user.id]),
            "done_tasks": len([t for t in done_today if t.custodian_id == user.id]),
        })

    return {
        "date": day.isoformat(),
        "date_label": day.strftime("%A, %d %B %Y"),
        "generated_at": utcnow().isoformat(),
        "jobs": jobs_section,
        "bookings": bookings_section,
        "tasks": tasks_section,
        "money": money_section,
        "staff": staff_section,
    }
