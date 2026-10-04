"""Outbound customer notifications (WhatsApp-first, SMS-ready).

Every notification is written to ``NotificationLog`` so the front desk can prove
what the customer was told, and so failed sends can be retried.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from flask import current_app, has_request_context, request

from ..constants import (BOOKING_EXPECTED_STATUSES, STAGE_CUSTOMER_TEXT, STAGE_LABELS,
                         WARRANTY_TEXT)
from ..extensions import db
from ..models import JobCard, NotificationLog, utcnow
from .bookings import slot_text
from . import phone as phone_numbers
from .whatsapp_client import WhatsAppClient, get_or_create_conversation, log_outbound

log = logging.getLogger(__name__)

# Meta-approved template names (register these in WhatsApp Manager).
TEMPLATE_STAGE_UPDATE = "job_stage_update"
TEMPLATE_READY = "vehicle_ready"
TEMPLATE_QUOTE_READY = "quotation_ready"
TEMPLATE_PARTS_IN = "parts_received"
TEMPLATE_PAYMENT_DUE = "payment_due"
TEMPLATE_WARRANTY = "warranty_registered"
# One template per document. Each carries a Download button, and the button has
# to *name* what it fetches — a template's buttons are frozen when Meta approves
# it, so an invoice and a receipt cannot share one template and still say
# "Download invoice" / "Download receipt". This replaced a single `document_share`.
TEMPLATE_INVOICE = "invoice_share"
TEMPLATE_RECEIPT = "receipt_share"
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

# The two things a customer can do with an appointment the day before it is due.
# Bare payloads, because these are the ids on the approved `booking_reminder`
# template and a template's payload is frozen — the same ids are sent free-form
# inside the 24-hour window so both paths run the same resolution code.
# *Move it* first and *Cancel* second: the destructive one is not the one a
# thumb lands on.
BOOKING_REMINDER_BUTTONS = [
    {"id": "b_move", "title": "Move it"},
    {"id": "b_cancel", "title": "Cancel appointment"},
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


def _dialable(customer) -> str | None:
    """The number to dial for this customer, or ``None`` if there is not one.

    ``None`` covers two cases that used to be one. The first is no number on file,
    which is obvious. The second is a number that is *present but is not a phone
    number* — "123", or a country code typed without the rest — and that one is
    dangerous: the send is attempted, Meta rejects it, and the desk sees only a
    customer who never replies. Better to refuse it here, where a caller can log
    why.
    """
    number = customer.wa_number if customer else None
    if not number or not phone_numbers.is_dialable(number):
        return None
    return number


def _undialable_reason(customer) -> str:
    """What to record when :func:`_dialable` refuses a customer's number."""
    typed = (customer.whatsapp or customer.phone) if customer else ""
    return f"Not a dialable number: {typed or 'nothing on file'}"


def _dispatch(job: JobCard | None, body: str, *, template: str, use_template: bool = False,
              params: list[str] | None = None, document: dict | None = None) -> bool:
    """Send a message (optionally with a PDF attachment) and log it."""
    customer = _customer(job) or (document or {}).get("customer")
    if not customer:
        return False
    if not customer.whatsapp_opt_in:
        _log(job, customer.wa_number, template, body, "skipped_optout")
        return False

    number = _dialable(customer)
    if not number:
        _log(job, customer.wa_number, template, body, "failed",
             _undialable_reason(customer))
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
def _remember(job: JobCard | None = None, *, customer=None, **context):
    """Record what a thread is about *before* the message goes out.

    A template's buttons carry a fixed payload — Meta cannot interpolate a
    record id into an approved template — so every tap is resolved against the
    conversation. Recording that first is what lets a delivered template answer
    its own buttons: the other order leaves the customer holding live Approve /
    Download buttons that fall through to the fallback if the process dies
    between the send and the commit.

    ``notify_feedback_request`` already worked this way; this is that pattern,
    shared.
    """
    customer = customer or _customer(job)
    number = customer.wa_number if customer else None
    if not number:
        return None
    conversation = get_or_create_conversation(number, profile_name=customer.name)
    if context:
        conversation.ctx_set(**context)
        db.session.commit()
    return conversation


def _action_prompt(number: str, conversation, *, body: str, buttons: list[dict],
                   job_id: int | None, intent: str) -> bool:
    """A short prompt with buttons, under a message that has just gone out.

    Stays quiet outside the service window: Meta refuses a free-form interactive
    message there, and a templated delivery already carried its own buttons.
    A failure here must not fail the delivery that already succeeded, so this
    swallows its own errors and reports back instead.
    """
    if conversation is not None and not conversation.is_session_open:
        return False
    try:
        WhatsAppClient().send_buttons(
            number, body, buttons,
            footer="Topclass Auto Body", conversation=conversation,
            intent=intent, job_id=job_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Button prompt failed: %s", exc)
        return False


def _download_prompt(number: str, conversation, *, body: str, doc_id: str,
                     job_id: int | None, intent: str, title: str = "Download") -> bool:
    """A lone Download button under a document that has just been delivered.

    The customer may have scrolled past the attachment, opened it once and lost
    it, or been on a phone that quietly saved it nowhere. Asking for the file on
    demand beats sending the same PDF twice: the tap rebuilds it from the record,
    so a re-issued invoice is never handed out from a stale attachment, and the
    customer can come back for it a week later.

    ``title`` names the document ("Download invoice"), matching the button on the
    approved template so the two paths offer the customer the same thing.
    """
    return _action_prompt(
        number, conversation, body=body,
        buttons=[{"id": doc_id, "title": title}],
        job_id=job_id, intent=intent,
    )


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

    conversation = _remember(job, last_estimate_id=estimate.id)
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
            if not conversation.is_session_open:
                # The approved template carried its own Approve / Decline /
                # Download buttons, and Meta refuses a free-form interactive
                # message outside the service window anyway.
                log.info("Quotation %s sent as a template; buttons ride on it",
                         estimate.reference)
            else:
                _action_prompt(
                    number, conversation,
                    body="Shall we go ahead with this quotation?",
                    buttons=[
                        # Bare payloads, identical to the ones on the approved
                        # `quotation_share` template. Both paths then run the
                        # same resolution code, so a bug in it cannot hide
                        # behind the branch that only fires in production.
                        {"id": "a_approve", "title": "Approve"},
                        {"id": "a_decline", "title": "Decline"},
                        {"id": "doc_quote", "title": "Download quotation"},
                    ],
                    job_id=job.id, intent="quotation_decision",
                )

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

    conversation = _remember(job, last_invoice_id=invoice.id)
    delivered = _dispatch(
        job, caption, template=TEMPLATE_INVOICE, use_template=True,
        params=[customer.name],
        document={"link": public_url(f"/doc/invoice/{invoice.public_token}.pdf"),
                  "filename": f"Invoice-{invoice.invoice_no}.pdf"},
    )

    if delivered:
        number = customer.wa_number
        if number:
            _download_prompt(
                number, conversation,
                body="Keep a copy of this invoice for your records.",
                doc_id="doc_invoice", title="Download invoice",
                job_id=job.id, intent="invoice_download",
            )

    return {"sent": delivered, "link": public_url(f"/doc/invoice/{invoice.public_token}")}


def send_receipt(payment) -> dict:
    """Send the receipt PDF for a recorded payment."""
    invoice = payment.invoice
    job = invoice.job if invoice else None
    customer = invoice.customer if invoice else None
    if not _dialable(customer):
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
    # Which receipt this thread is about, recorded BEFORE the receipt goes out so
    # the template's bare `doc_receipt` button always has something to resolve
    # against.
    conversation = _remember(customer=customer, last_receipt_id=payment.id)
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
                customer.wa_number, TEMPLATE_RECEIPT, [customer.name],
                document={"link": link, "filename": filename},
                conversation=conversation,
            )
    except Exception as exc:  # noqa: BLE001
        log.error("Receipt send failed: %s", exc)
        _log(job, customer.wa_number, TEMPLATE_RECEIPT, caption, "failed", str(exc)[:250])
        return {"sent": False, "reason": "send_failed"}

    _log(job, customer.wa_number, TEMPLATE_RECEIPT, caption, "sent")
    _download_prompt(
        customer.wa_number, conversation,
        body="Keep a copy of this receipt for your records.",
        doc_id="doc_receipt", title="Download receipt",
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
    """A quotation has been raised — approve it, decline it, or download it.

    All three buttons carry **bare** payloads (`a_approve`, `a_decline`,
    `doc_quote`), not this estimate's id: a template's payload is fixed when Meta
    approves it and cannot interpolate one. So sending this message records the
    estimate against the conversation, and every tap resolves against that — as
    does a customer who types *approve* instead of tapping at all.
    """
    estimate = job.latest_estimate
    if not estimate:
        return False

    customer = _customer(job)
    first = (customer.name.split(" ")[0] if customer and customer.name else "there")
    currency = estimate.currency or "USD"

    body = (
        f"Hello {first}, your quotation *{estimate.reference}* is ready.\n\n"
        f"Total: {currency} {_money(estimate.total)}\n\n"
        "Tap *Approve* to authorise the repair, *Decline* if you would like to "
        "discuss it, or *Download quotation* to keep a copy."
    )

    # Record which quotation this thread is about BEFORE the notice goes out.
    # The bare payloads and a typed *approve* both resolve against this, and the
    # conversation is the only record that survives a Meta redelivery. Writing it
    # first is what stops the out-of-window notice arriving with Approve /
    # Decline / Download buttons that resolve to nothing.
    conversation = _remember(job, last_estimate_id=estimate.id)
    delivered = _dispatch(
        job, body, template=TEMPLATE_QUOTE_READY, use_template=True,
        params=[first, estimate.reference, _money(estimate.total)],
    )

    if delivered:
        number = customer.wa_number if customer else None
        if number:
            # Only in the window: outside it the approved template carries the
            # same three buttons, and a free-form message is refused outright.
            _action_prompt(
                number, conversation,
                body="What would you like to do?",
                buttons=[
                    {"id": "a_approve", "title": "Approve"},
                    {"id": "a_decline", "title": "Decline"},
                    {"id": "doc_quote", "title": "Download quotation"},
                ],
                job_id=job.id, intent="quotation_decision",
            )

    return delivered


def notify_parts_received(job: JobCard, parts: list[str]) -> bool:
    body = (
        f"Good news — parts received for job card {job.job_no}.\n\n"
        + "\n".join(f"- {p}" for p in parts[:8])
        + "\n\nWork continues and we will update you at the next stage."
    )
    return _dispatch(job, body, template=TEMPLATE_PARTS_IN, use_template=True,
                     params=[job.customer.name, ", ".join(parts[:3])])


def notify_invoice_issued(job: JobCard, invoice) -> bool:
    """Invoice raised — the notice, with *Download invoice* and *Pay via EcoCash*.

    Neither button carries an id: a template's payload is frozen when Meta
    approves it, so `doc_invoice` and `m_pay` resolve against the invoice
    recorded on the thread. That record is written before this goes out, so the
    buttons on a delivered notice can always be resolved.

    The balance is in the body because the desk has just issued the invoice and
    the customer needs the figure to pay against. A balance that moves later is
    answered by *Check balance* / the menu rather than by this snapshot.
    """
    customer = _customer(job)
    first = (customer.name.split(" ")[0] if customer and customer.name else "there")
    currency = invoice.currency or "USD"

    body = (
        f"Hello {first}, invoice {invoice.invoice_no} has been raised.\n\n"
        f"Balance due: {currency} {_money(invoice.balance)}\n\n"
        "Payment: Cash, EcoCash, InnBucks, bank transfer or card at reception. "
        "Please quote the invoice number with any transfer."
    )

    conversation = _remember(job, last_invoice_id=invoice.id)
    delivered = _dispatch(job, body, template=TEMPLATE_PAYMENT_DUE, use_template=True,
                          params=[first, invoice.invoice_no, _money(invoice.balance)])

    if delivered:
        number = customer.wa_number if customer else None
        if number:
            # The same two buttons the approved template carries, so a tap means
            # the identical thing either side of the service window.
            _action_prompt(
                number, conversation,
                body="Download the invoice, or get the payment details.",
                buttons=[
                    # Titles match the approved template word for word, so the
                    # customer sees the same pair either side of the window.
                    {"id": "doc_invoice", "title": "Download Invoice"},
                    {"id": "m_pay", "title": "Pay via EcoCash"},
                ],
                job_id=job.id, intent="invoice_actions",
            )

    return delivered


def notify_warranty(job: JobCard) -> bool:
    body = (
        f"*Warranty registered* — job card {job.job_no}\n\n{WARRANTY_TEXT}\n\n"
        "Keep this message as your warranty reference. Reply *menu* for anything else."
    )
    return _dispatch(job, body, template=TEMPLATE_WARRANTY, use_template=True,
                     params=[job.customer.name, job.job_no])


def _slot_text(booking) -> str:
    """'Mon 23 Sep 2026 at 08:00', or just the day when no time was booked."""
    return slot_text(booking)


def notify_booking_confirmed(booking) -> bool:
    customer = booking.customer
    if not _dialable(customer):
        return False
    body = (
        f"*Booking confirmed*\n\n"
        f"Reference: {booking.display_reference}\n"
        f"Service: {booking.service}\n"
        f"Date: {_slot_text(booking)}"
        f"\n\n {current_app.config['COMPANY_ADDRESS']}"
        # Not "reply menu": the menu deliberately carries no booking row, so
        # pointing at it sent the customer to a screen with no way to move.
        "\n\nReply *move* if that time no longer works."
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
    if not _dialable(customer):
        return False
    body = (
        f"*Booking moved*\n\n"
        f"Reference: {booking.display_reference}\n"
        f"Service: {booking.service}\n"
        f"Was: {previous_slot}\n"
        f"Now: {_slot_text(booking)}"
        f"\n\n {current_app.config['COMPANY_ADDRESS']}"
        "\n\nReply *move* if that no longer works."
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


def notify_reschedule_declined(booking, held_slot: str,
                               *, reason: str | None = None) -> bool:
    """Say no, and tell the customer what still stands.

    A refusal that names the reason is an answer; one that does not is a wall.
    The original appointment is restated, because that is the only thing the
    customer now has to work with.
    """
    customer = booking.customer
    if not _dialable(customer):
        return False
    why = f"\n\n{reason.strip()}" if (reason or "").strip() else ""
    body = (
        f"*About your appointment*\n\n"
        f"Reference: {booking.display_reference}\n"
        f"Service: {booking.service}\n"
        f"Still booked for: {held_slot}"
        f"{why}"
        "\n\nReply *move* if another day or time would suit better and we will "
        "take a look."
    )
    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, customer.name)
    try:
        client.send_text(customer.wa_number, body, conversation=conversation,
                         intent="reschedule_declined")
    except Exception as exc:  # noqa: BLE001
        log.error("Reschedule decline notice failed: %s", exc)
        return False
    _log(None, customer.wa_number, "reschedule_declined", body, "sent")
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

    # Counted BEFORE the loop. Counting afterwards counted the messages this run
    # had just sent, so a run that did everything reported "1 due, 1 already
    # asked" — the exact opposite of what happened. ``skipped`` means "already
    # dealt with before this run".
    already = JobCard.query.filter(
        JobCard.stage == "COLLECTED",
        JobCard.feedback_requested_at.isnot(None),
        JobCard.collected_at >= start,
        JobCard.collected_at < end,
    ).count()

    sent = 0
    for job in pending:
        if notify_feedback_request(job):
            job.feedback_requested_at = utcnow()
            sent += 1

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
    if not _dialable(customer):
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
    """The day-before nudge, with the two buttons a customer actually needs.

    Unlike the other booking notices this one is *designed* to land outside the
    24-hour service window — the customer booked days ago and has said nothing
    since. Free-form text would be rejected outright by Meta, so when the window
    is shut this goes out as the approved template instead, buttons and all.

    The buttons carry ``b_move`` and ``b_cancel`` — the same ids free-form inside
    the window, so both paths resolve against the same code. *Cancel* asks first:
    an appointment is worth more than the tap it takes to lose it.
    """
    customer = booking.customer
    if not _dialable(customer):
        return False
    if not customer.whatsapp_opt_in:
        _log(None, customer.wa_number, TEMPLATE_BOOKING_REMINDER, "", "skipped_optout")
        return False

    when = slot_text(booking)
    body = (
        f"*Reminder — your appointment*\n\n"
        f"Reference: {booking.display_reference}\n"
        f"Service: {booking.service}\n"
        f"When: {when}"
        f"\n\n {current_app.config['COMPANY_ADDRESS']}"
        "\n\nLet us know if anything has changed."
    )
    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, customer.name)
    try:
        if conversation.is_session_open:
            client.send_text(customer.wa_number, body, conversation=conversation,
                             intent=TEMPLATE_BOOKING_REMINDER)
            # Only in the window. Outside it the approved template carries the
            # same two buttons, and a free-form interactive would be a 400.
            _action_prompt(
                customer.wa_number, conversation,
                body="Has anything changed?",
                buttons=BOOKING_REMINDER_BUTTONS,
                job_id=None, intent="booking_reminder_actions",
            )
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
        Booking.status.in_(BOOKING_EXPECTED_STATUSES),
        Booking.reminder_sent_at.is_(None),
    ).order_by(Booking.slot_time.asc()).all()

    # Counted BEFORE the loop, for the same reason as the feedback run: counted
    # afterwards, ``skipped`` included the reminders this run had just sent.
    already = Booking.query.filter(
        Booking.slot_date == day, Booking.reminder_sent_at.isnot(None),
    ).count()

    sent = 0
    for booking in pending:
        if notify_booking_reminder(booking):
            booking.reminder_sent_at = utcnow()
            sent += 1

    db.session.commit()
    return {"day": day.isoformat(), "due": len(pending), "sent": sent,
            "skipped": already}
