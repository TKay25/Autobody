"""Outbound customer notifications (WhatsApp-first, SMS-ready).

Every notification is written to ``NotificationLog`` so the front desk can prove
what the customer was told, and so failed sends can be retried.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from flask import current_app, has_request_context, request

from ..constants import STAGE_CUSTOMER_TEXT, STAGE_LABELS, WARRANTY_TEXT
from ..extensions import db
from ..models import JobCard, NotificationLog, utcnow
from .whatsapp_client import WhatsAppClient, get_or_create_conversation, log_outbound

log = logging.getLogger(__name__)

# Meta-approved template names (register these in WhatsApp Manager).
TEMPLATE_STAGE_UPDATE = "job_stage_update"
TEMPLATE_READY = "vehicle_ready"
TEMPLATE_QUOTE_READY = "quotation_ready"
TEMPLATE_PARTS_IN = "parts_received"
TEMPLATE_PAYMENT_DUE = "payment_due"
TEMPLATE_WARRANTY = "warranty_registered"
TEMPLATE_DOCUMENT = "document_share"
# Quotations need their own template: unlike an invoice or a receipt they go out
# with Approve / Decline / Download buttons, and a template's buttons are fixed
# at approval time, so the buttoned one cannot be shared with the other two.
TEMPLATE_QUOTATION = "quotation_share"
TEMPLATE_BOOKING_REMINDER = "booking_reminder"
TEMPLATE_FEEDBACK = "job_feedback"

# The rating scale. Deliberately three options, not five: WhatsApp caps reply
# buttons at three, and the same three ids are reused as the quick-reply payloads
# on the ``job_feedback`` template — so the tap means the same thing whether the
# customer is inside the 24h window or not.
FEEDBACK_RATINGS = [
    (5, "Excellent"),
    (3, "Okay"),
    (1, "Poor"),
]


def _feedback_buttons(job) -> list[dict]:
    return [{"id": f"rate:{job.id}:{score}", "title": label}
            for score, label in FEEDBACK_RATINGS]


def public_url(path: str) -> str:
    """Absolute URL a customer (or Meta) can actually reach."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        base = request.url_root.rstrip("/") if has_request_context() else ""
    return f"{base}{path}"


def _money(value) -> str:
    return f"{Decimal(str(value or 0)):,.2f}"


def _log(job: JobCard | None, recipient: str | None, template: str, body: str, status: str,
         error: str | None = None) -> NotificationLog:
    entry = NotificationLog(
        channel="whatsapp", recipient=recipient, template=template, body=body,
        job_id=job.id if job else None, status=status, error=error,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _customer(job: JobCard):
    return job.customer if job else None


def _dispatch(job: JobCard | None, body: str, *, template: str, use_template: bool = False,
              params: list[str] | None = None, document: dict | None = None) -> bool:
    """Send a message (optionally with a PDF attachment) and log it."""
    customer = _customer(job) or (document or {}).get("customer")
    if not customer:
        return False
    if not customer.whatsapp_opt_in:
        _log(job, customer.wa_number, template, body, "skipped_optout")
        return False

    number = customer.wa_number
    if not number:
        _log(job, None, template, body, "failed", "No WhatsApp number on file")
        return False

    client = WhatsAppClient()
    conversation = get_or_create_conversation(number, profile_name=customer.name)

    try:
        if document and document.get("link"):
            if use_template and not conversation.is_session_open:
                # Outside the 24h service window Meta refuses a free-form
                # document message outright, so the PDF rides on the approved
                # template's document header instead. Dropping it (or sending
                # it anyway and getting a 400) would silently lose the quotation.
                client.send_template(
                    number, template, params or [customer.name],
                    document=document, conversation=conversation,
                    job_id=job.id if job else None,
                )
            else:
                client.send_document(
                    number, document["link"], document.get("filename") or "document.pdf",
                    caption=body, conversation=conversation, job_id=job.id if job else None,
                )
        elif use_template and not conversation.is_session_open:
            # Outside the 24h service window Meta requires an approved template.
            client.send_template(
                number, template, params or [customer.name], conversation=conversation,
                job_id=job.id if job else None,
            )
        else:
            client.send_text(number, body, is_bot=True, intent=template,
                             job_id=job.id if job else None, conversation=conversation)
    except Exception as exc:  # noqa: BLE001 - never let a send break the workflow
        log.error("Notification failed for %s: %s", number, exc)
        _log(job, number, template, body, "failed", str(exc)[:250])
        return False

    _log(job, number, template, body, "sent")
    return True


# ── Document delivery ────────────────────────────────────────────────────────
def _download_prompt(number: str, conversation, *, body: str, doc_id: str,
                     job_id: int | None, intent: str) -> bool:
    """A lone Download button under a document that has just been delivered.

    The customer may have scrolled past the attachment, opened it once and lost
    it, or been on a phone that quietly saved it nowhere. Asking for the file on
    demand beats sending the same PDF twice: the tap rebuilds it from the record,
    so a re-issued invoice is never handed out from a stale attachment, and the
    customer can come back for it a week later.

    A failure here must not fail the delivery that already succeeded, so this
    swallows its own errors and reports back instead.
    """
    if conversation is not None and not conversation.is_session_open:
        # Two reasons to stay quiet. A templated delivery already carried the PDF
        # as its document header, so there is nothing to re-fetch; and Meta
        # refuses a free-form interactive message outside the service window, so
        # sending one is a guaranteed 400 on every notification.
        return False
    try:
        WhatsAppClient().send_buttons(
            number, body, [{"id": doc_id, "title": "Download"}],
            footer="Topclass Auto Body", conversation=conversation,
            intent=intent, job_id=job_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Download prompt failed: %s", exc)
        return False


def send_quotation(job: JobCard, estimate, *, with_buttons: bool = True) -> dict:
    """Send the quotation PDF plus an approve/decline prompt.

    Returns a small result dict so the UI can report what actually happened.
    """
    customer = _customer(job)
    if not customer:
        return {"sent": False, "reason": "no_customer"}

    link = public_url(f"/doc/quote/{estimate.public_token}")
    pdf_link = public_url(f"/doc/quote/{estimate.public_token}.pdf")
    currency = estimate.currency or "USD"

    caption = (
        f"Quotation {estimate.reference}\n"
        f"{job.vehicle.reg_no if job.vehicle else ''} — {job.service}\n"
        f"Total: {currency} {_money(estimate.total)}\n"
        f"Valid until {estimate.expires_on.strftime('%d %b %Y')}"
    )

    delivered = _dispatch(
        job, caption, template=TEMPLATE_QUOTATION, use_template=True,
        params=[customer.name, estimate.reference, f"{currency} {_money(estimate.total)}"],
        document={"link": pdf_link, "filename": f"Quotation-{estimate.reference}.pdf"},
    )

    # Approve / decline / download in their own message so they are tappable.
    # Three is Meta's ceiling for an interactive button message, so the download
    # has to ride along with the decision rather than follow it.
    if delivered and with_buttons:
        number = customer.wa_number
        if number:
            client = WhatsAppClient()
            conversation = get_or_create_conversation(number, profile_name=customer.name)
            # Record which quotation this thread is about before any tap can
            # arrive. A template's quick-reply buttons carry a *fixed* payload —
            # Meta cannot interpolate the estimate id into them — so the bare ids
            # on `quotation_share` resolve against this.
            conversation.ctx_set(last_estimate_id=estimate.id)
            db.session.commit()
            if not conversation.is_session_open:
                # The approved template carried its own Approve / Decline /
                # Download buttons, and Meta refuses a free-form interactive
                # message outside the service window anyway.
                log.info("Quotation %s sent as a template; buttons ride on it",
                         estimate.reference)
            else:
                try:
                    client.send_buttons(
                        number,
                        "Shall we go ahead with this quotation?",
                        [
                            {"id": f"a_approve:{estimate.id}", "title": "Approve"},
                            {"id": f"a_decline:{estimate.id}", "title": "Decline"},
                            {"id": f"doc:quote:{estimate.id}", "title": "Download"},
                        ],
                        footer="Topclass Auto Body",
                        conversation=conversation, intent="quotation_decision",
                        job_id=job.id,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error("Quotation buttons failed: %s", exc)

    if delivered:
        estimate.status = "SENT"
        estimate.sent_at = estimate.sent_at or utcnow()
        db.session.commit()

    return {"sent": delivered, "link": link, "pdf": pdf_link}


def send_invoice(job: JobCard, invoice) -> dict:
    customer = _customer(job)
    if not customer:
        return {"sent": False, "reason": "no_customer"}

    currency = invoice.currency or "USD"
    caption = (
        f"Invoice {invoice.invoice_no}\n"
        f"Job card {job.job_no}\n"
        f"Total: {currency} {_money(invoice.total)}\n"
        f"Balance: {currency} {_money(invoice.balance)}"
    )
    if invoice.balance > 0:
        caption += "\n\nPayment: Cash, EcoCash, InnBucks, bank transfer or card at reception."
    else:
        caption += "\n\n Settled in full — thank you."

    delivered = _dispatch(
        job, caption, template=TEMPLATE_DOCUMENT, use_template=True,
        document={"link": public_url(f"/doc/invoice/{invoice.public_token}.pdf"),
                  "filename": f"Invoice-{invoice.invoice_no}.pdf"},
    )

    if delivered:
        number = customer.wa_number
        if number:
            conversation = get_or_create_conversation(number, profile_name=customer.name)
            _download_prompt(
                number, conversation,
                body="Keep a copy of this invoice for your records.",
                doc_id=f"doc:invoice:{invoice.id}",
                job_id=job.id, intent="invoice_download",
            )

    return {"sent": delivered, "link": public_url(f"/doc/invoice/{invoice.public_token}")}


def send_receipt(payment) -> dict:
    """Send the receipt PDF for a recorded payment."""
    invoice = payment.invoice
    job = invoice.job if invoice else None
    customer = invoice.customer if invoice else None
    if not customer or not customer.wa_number:
        return {"sent": False, "reason": "no_number"}

    currency = (invoice.currency if invoice else "USD") or "USD"
    settled = payment.is_fully_settled
    caption = (
        f"Receipt {payment.receipt_no}\n"
        f"{currency} {_money(payment.amount)} received — "
        f"{(payment.method or '').replace('_', ' ').title()}\n"
        + ("Account settled in full. Thank you! "
           if settled else
           f"Balance remaining: {currency} {_money(payment.balance_after)}")
    )

    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, profile_name=customer.name)
    link = public_url(f"/doc/receipt/{payment.public_token}.pdf")
    filename = f"Receipt-{payment.receipt_no or payment.id}.pdf"
    try:
        if conversation.is_session_open:
            client.send_document(
                customer.wa_number, link, filename,
                caption=caption, conversation=conversation,
                job_id=job.id if job else None,
            )
        else:
            # Outside the 24h window Meta refuses a free-form document, so the
            # receipt rides on the approved template's document header instead.
            # Sending it anyway lost the receipt silently.
            client.send_template(
                customer.wa_number, TEMPLATE_DOCUMENT, [customer.name],
                document={"link": link, "filename": filename},
                conversation=conversation,
            )
    except Exception as exc:  # noqa: BLE001
        log.error("Receipt send failed: %s", exc)
        _log(job, customer.wa_number, TEMPLATE_DOCUMENT, caption, "failed", str(exc)[:250])
        return {"sent": False, "reason": "send_failed"}

    _log(job, customer.wa_number, TEMPLATE_DOCUMENT, caption, "sent")
    _download_prompt(
        customer.wa_number, conversation,
        body="Keep a copy of this receipt for your records.",
        doc_id=f"doc:receipt:{payment.id}",
        job_id=job.id if job else None, intent="receipt_download",
    )
    return {"sent": True, "link": public_url(f"/doc/receipt/{payment.public_token}")}



# ── Triggers ─────────────────────────────────────────────────────────────────
def notify_stage_change(job: JobCard, *, old_stage: str | None = None) -> bool:
    if not current_app.config.get("NOTIFY_ON_STAGE_CHANGE", True):
        return False

    label = STAGE_LABELS.get(job.stage, job.stage)
    line = STAGE_CUSTOMER_TEXT.get(job.stage, "")

    if job.stage == "READY":
        return notify_ready_for_collection(job)

    body = (
        f"*{current_app.config['COMPANY_NAME']}* — job card {job.job_no}\n\n"
        f"Update: {label}\n{line}"
    )
    if job.promised_date:
        body += f"\n\n Promised date: {job.promised_date.strftime('%d %b %Y')}"
    blocking = [p for p in job.job_parts if p.is_blocking]
    if blocking:
        body += f"\n\n Waiting on {len(blocking)} part(s). We will update you as soon as they land."
    body += "\n\nReply *track* to check progress any time."

    return _dispatch(
        job, body, template=TEMPLATE_STAGE_UPDATE, use_template=True,
        params=[job.customer.name, label, line],
    )


def notify_ready_for_collection(job: JobCard) -> bool:
    """The collection notice, with a *Check balance* button.

    The money is **not** in the message, and that is deliberate. A Meta
    template's body is frozen when it is approved, so a balance written into the
    template is a snapshot taken the moment it was sent — the customer would be
    reading a figure that could be a fortnight stale. Behind the button,
    ``_menu_pay`` answers from the invoice as it stands right now.

    The button carries the menu id ``m_pay`` rather than a job id, because a
    template's payload is fixed at approval and cannot interpolate one. Inside
    the 24-hour window the same button is sent free-form, so a tap means the
    identical thing either side of the window.
    """
    customer = _customer(job)
    first = (customer.name.split(" ")[0] if customer and customer.name else "there")
    reg = job.vehicle.reg_no if job.vehicle else "—"

    body = (
        f"Good news {first} — your vehicle ({reg}) is ready for collection.\n\n"
        "Please bring your ID and collection slip. If a balance is due, tap "
        "*Check balance* for the payment details."
    )

    delivered = _dispatch(
        job, body, template=TEMPLATE_READY, use_template=True,
        params=[first, reg],
    )

    # Only in the window: outside it the approved template carries this button
    # itself, and a second free-form message would be refused outright.
    if delivered:
        number = customer.wa_number if customer else None
        if number:
            conversation = get_or_create_conversation(number, profile_name=customer.name)
            if conversation.is_session_open:
                try:
                    WhatsAppClient().send_buttons(
                        number, "Payment details and the balance outstanding.",
                        [{"id": "m_pay", "title": "Check balance"}],
                        footer="Topclass Auto Body", conversation=conversation,
                        intent="vehicle_ready_balance", job_id=job.id,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error("Collection balance button failed: %s", exc)

    return delivered


def notify_quote_ready(job: JobCard) -> bool:
    estimate = job.latest_estimate
    if not estimate:
        return False
    body = (
        f"*Quotation ready* — job card {job.job_no}\n\n"
        f"Vehicle: {job.vehicle.reg_no if job.vehicle else '-'}\n"
        f"Reference: {estimate.reference}\n"
        f"Total: *{estimate.currency} {_money(estimate.total)}*"
    )
    body += (
        "\n\nReply *approve* to authorise the repair, or *decline* and our team will call you."
    )
    return _dispatch(
        job, body, template=TEMPLATE_QUOTE_READY, use_template=True,
        params=[job.customer.name, estimate.reference, _money(estimate.total)],
    )


def notify_parts_received(job: JobCard, parts: list[str]) -> bool:
    body = (
        f"Good news — parts received for job card {job.job_no}.\n\n"
        + "\n".join(f"- {p}" for p in parts[:8])
        + "\n\nWork continues and we will update you at the next stage."
    )
    return _dispatch(job, body, template=TEMPLATE_PARTS_IN, use_template=True,
                     params=[job.customer.name, ", ".join(parts[:3])])


def notify_invoice_issued(job: JobCard, invoice) -> bool:
    body = (
        f"*Invoice {invoice.invoice_no}*\n\n"
        f"Job: {job.job_no}\n"
        f"Total: *{invoice.currency} {_money(invoice.total)}*"
        f"\nBalance: *{invoice.currency} {_money(invoice.balance)}*"
        f"\nDue: {invoice.due_date.strftime('%d %b %Y') if invoice.due_date else 'on collection'}"
        "\n\nPayment methods: Cash, EcoCash, InnBucks, Bank Transfer, Card."
    )
    return _dispatch(job, body, template=TEMPLATE_PAYMENT_DUE, use_template=True,
                     params=[job.customer.name, invoice.invoice_no, _money(invoice.balance)])


def notify_warranty(job: JobCard) -> bool:
    body = (
        f"*Warranty registered* — job card {job.job_no}\n\n{WARRANTY_TEXT}\n\n"
        "Keep this message as your warranty reference. Reply *menu* for anything else."
    )
    return _dispatch(job, body, template=TEMPLATE_WARRANTY, use_template=True,
                     params=[job.customer.name, job.job_no])


def _slot_text(booking) -> str:
    """'Mon 23 Sep 2026 at 08:00', or just the day when no time was booked."""
    if not booking.slot_date:
        return "to be advised"
    when = booking.slot_date.strftime("%a %d %b %Y")
    return f"{when} at {booking.slot_time}" if booking.slot_time else when


def notify_booking_confirmed(booking) -> bool:
    customer = booking.customer
    if not customer or not customer.wa_number:
        return False
    body = (
        f"*Booking confirmed*\n\n"
        f"Reference: {booking.display_reference}\n"
        f"Service: {booking.service}\n"
        f"Date: {_slot_text(booking)}"
        f"\n\n {current_app.config['COMPANY_ADDRESS']}"
        "\n\nReply *menu* to change or cancel."
    )
    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, customer.name)
    try:
        client.send_text(customer.wa_number, body, conversation=conversation,
                         intent="booking_confirmed")
    except Exception as exc:  # noqa: BLE001
        log.error("Booking confirmation failed: %s", exc)
        return False
    _log(None, customer.wa_number, "booking_confirmed", body, "sent")
    return True


def notify_booking_rescheduled(booking, previous_slot: str) -> bool:
    """Tell the customer their appointment has moved.

    Deliberately says what it was before as well as what it is now — a bare new
    date gives the customer nothing to check against.
    """
    customer = booking.customer
    if not customer or not customer.wa_number:
        return False
    body = (
        f"*Booking moved*\n\n"
        f"Reference: {booking.display_reference}\n"
        f"Service: {booking.service}\n"
        f"Was: {previous_slot}\n"
        f"Now: {_slot_text(booking)}"
        f"\n\n {current_app.config['COMPANY_ADDRESS']}"
        "\n\nReply *menu* if that no longer works."
    )
    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, customer.name)
    try:
        client.send_text(customer.wa_number, body, conversation=conversation,
                         intent="booking_rescheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("Booking reschedule notice failed: %s", exc)
        return False
    _log(None, customer.wa_number, "booking_rescheduled", body, "sent")
    return True


def send_due_feedback_requests(day: date | None = None) -> dict:
    """Ask the customers whose vehicles were collected on ``day`` how we did.

    Defaults to yesterday, so the ask lands after they have had the car back for
    a night rather than while they are still on the forecourt. Only jobs that
    actually reached COLLECTED are asked about, and ``feedback_requested_at``
    makes the run idempotent.
    """
    from ..models import JobCard

    day = day or (date.today() - timedelta(days=1))
    # A half-open range rather than ``date(collected_at) = day``: function-wrapping
    # the column is not portable to Postgres and cannot use an index. The day is
    # read on the server's clock, the same clock that wrote ``collected_at``.
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    pending = JobCard.query.filter(
        JobCard.stage == "COLLECTED",
        JobCard.feedback_requested_at.is_(None),
        JobCard.collected_at >= start,
        JobCard.collected_at < end,
    ).order_by(JobCard.id.asc()).all()

    sent = 0
    for job in pending:
        if notify_feedback_request(job):
            job.feedback_requested_at = utcnow()
            sent += 1

    already = JobCard.query.filter(
        JobCard.stage == "COLLECTED",
        JobCard.feedback_requested_at.isnot(None),
        JobCard.collected_at >= start,
        JobCard.collected_at < end,
    ).count()
    db.session.commit()
    return {"day": day.isoformat(), "due": len(pending), "sent": sent,
            "skipped": already}


def notify_feedback_request(job: JobCard) -> bool:
    """Ask for a rating on a finished job.

    Inside the service window this is three reply buttons; outside it, the
    approved template carrying the same three quick-reply payloads, so the tap
    is handled identically either way.
    """
    customer = job.customer
    if not customer or not customer.wa_number:
        return False
    if not customer.whatsapp_opt_in:
        _log(job, customer.wa_number, TEMPLATE_FEEDBACK, "", "skipped_optout")
        return False

    name = (customer.name or "there").split(" ")[0]
    body = (
        f"How did we do on *{job.job_no}*, {name}?\n\n"
        "One tap tells the workshop owner. If anything was not right, pick "
        "*Poor* and tell us what happened — we would rather hear it from you."
    )
    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, customer.name)
    # Record the job before asking. The approved template's rating buttons carry
    # a fixed payload (`rate:5`), so the tap has to be resolved back to a job —
    # and this thread is the only place that says which one we asked about.
    conversation.ctx_set(last_job_no=job.job_no)
    db.session.commit()
    try:
        if conversation.is_session_open:
            client.send_buttons(customer.wa_number, body, _feedback_buttons(job),
                                header="Topclass Auto Body", conversation=conversation,
                                intent=TEMPLATE_FEEDBACK, job_id=job.id)
        else:
            client.send_template(customer.wa_number, TEMPLATE_FEEDBACK,
                                 [name, job.job_no], conversation=conversation,
                                 job_id=job.id)
    except Exception as exc:  # noqa: BLE001
        log.error("Feedback request failed: %s", exc)
        _log(job, customer.wa_number, TEMPLATE_FEEDBACK, body, "failed", str(exc)[:250])
        return False
    _log(job, customer.wa_number, TEMPLATE_FEEDBACK, body, "sent")
    return True


def send_custom(conversation, body: str, *, is_bot: bool = False, user=None):
    """Used by the web inbox when a staff member replies manually.

    Returns ``False`` when the message did **not** reach WhatsApp. A manual reply
    that silently goes nowhere is worse than one that visibly fails: the operator
    ticks the customer off as answered and stops expecting a response.
    """
    client = WhatsAppClient()
    try:
        message = client.send_text(conversation.wa_id, body,
                                   conversation=conversation, is_bot=is_bot)
    except Exception as exc:  # noqa: BLE001
        log.error("Manual reply failed: %s", exc)
        log_outbound(conversation, body=body, is_bot=is_bot, status="failed",
                     payload={"error": str(exc)[:200]})
        return False
    if message is None:
        return False
    # ``_dispatch`` records "simulated" when nothing left the machine.
    return getattr(message, "status", "delivered") != "simulated"


def notify_booking_reminder(booking) -> bool:
    """The day-before nudge.

    Unlike the other booking notices this one is *designed* to land outside the
    24-hour service window — the customer booked days ago and has said nothing
    since. Free-form text would be rejected outright by Meta, so when the window
    is shut this goes out as the approved template instead.
    """
    customer = booking.customer
    if not customer or not customer.wa_number:
        return False
    if not customer.whatsapp_opt_in:
        _log(None, customer.wa_number, TEMPLATE_BOOKING_REMINDER, "", "skipped_optout")
        return False

    when = _slot_text(booking)
    body = (
        f"*Reminder — your appointment*\n\n"
        f"Reference: {booking.display_reference}\n"
        f"Service: {booking.service}\n"
        f"When: {when}"
        f"\n\n {current_app.config['COMPANY_ADDRESS']}"
        "\n\nReply *menu* if you need to move it."
    )
    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, customer.name)
    try:
        if conversation.is_session_open:
            client.send_text(customer.wa_number, body, conversation=conversation,
                             intent=TEMPLATE_BOOKING_REMINDER)
        else:
            client.send_template(
                customer.wa_number, TEMPLATE_BOOKING_REMINDER,
                [customer.name, when, booking.display_reference],
                conversation=conversation,
            )
    except Exception as exc:  # noqa: BLE001
        log.error("Booking reminder failed: %s", exc)
        _log(None, customer.wa_number, TEMPLATE_BOOKING_REMINDER, body, "failed",
             str(exc)[:250])
        return False
    _log(None, customer.wa_number, TEMPLATE_BOOKING_REMINDER, body, "sent")
    return True


def send_due_booking_reminders(day: date | None = None) -> dict:
    """Remind everyone booked in for ``day`` (default: tomorrow), once each.

    ``Booking.reminder_sent_at`` is the guard, so this is safe to run as often as
    you like — hourly, twice a day, or as a single daily cron. Only bookings that
    are still expected are reminded: a cancelled or no-show appointment must not
    be resurrected by a nudge.
    """
    from ..models import Booking

    day = day or (date.today() + timedelta(days=1))
    pending = Booking.query.filter(
        Booking.slot_date == day,
        Booking.status.in_(("REQUESTED", "CONFIRMED", "ATTENDED")),
        Booking.reminder_sent_at.is_(None),
    ).order_by(Booking.slot_time.asc()).all()

    sent = 0
    for booking in pending:
        if notify_booking_reminder(booking):
            booking.reminder_sent_at = utcnow()
            sent += 1

    already = Booking.query.filter(
        Booking.slot_date == day, Booking.reminder_sent_at.isnot(None),
    ).count()
    db.session.commit()
    return {"day": day.isoformat(), "due": len(pending), "sent": sent,
            "skipped": already}
