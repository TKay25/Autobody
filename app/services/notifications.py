"""Outbound customer notifications (WhatsApp-first, SMS-ready).

Every notification is written to ``NotificationLog`` so the front desk can prove
what the customer was told, and so failed sends can be retried.
"""
from __future__ import annotations

import logging
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
        f"📋 Quotation {estimate.reference}\n"
        f"{job.vehicle.reg_no if job.vehicle else ''} — {job.service}\n"
        f"Total: {currency} {_money(estimate.total)}\n"
        f"Valid until {estimate.expires_on.strftime('%d %b %Y')}"
    )
    if estimate.is_insurance:
        caption += f"\nExcess payable by you: {currency} {_money(estimate.excess)}"

    delivered = _dispatch(
        job, caption, template=TEMPLATE_DOCUMENT, use_template=True,
        document={"link": pdf_link, "filename": f"Quotation-{estimate.reference}.pdf"},
    )

    # Approve / decline buttons in their own message so they are tappable.
    if delivered and with_buttons:
        number = customer.wa_number
        if number:
            client = WhatsAppClient()
            conversation = get_or_create_conversation(number, profile_name=customer.name)
            try:
                client.send_buttons(
                    number,
                    "Shall we go ahead with this quotation?",
                    [
                        {"id": f"a_approve:{estimate.id}", "title": "Approve"},
                        {"id": f"a_decline:{estimate.id}", "title": "Decline"},
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
        f"🧾 Invoice {invoice.invoice_no}\n"
        f"Job card {job.job_no}\n"
        f"Total: {currency} {_money(invoice.total)}\n"
        f"Balance: {currency} {_money(invoice.balance)}"
    )
    if invoice.balance > 0:
        caption += "\n\nPayment: Cash, EcoCash, InnBucks, bank transfer or card at reception."
    else:
        caption += "\n\n✅ Settled in full — thank you."

    delivered = _dispatch(
        job, caption, template=TEMPLATE_DOCUMENT, use_template=True,
        document={"link": public_url(f"/doc/invoice/{invoice.public_token}.pdf"),
                  "filename": f"Invoice-{invoice.invoice_no}.pdf"},
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
        f"🧾 Receipt {payment.receipt_no}\n"
        f"{currency} {_money(payment.amount)} received — "
        f"{(payment.method or '').replace('_', ' ').title()}\n"
        + ("Account settled in full. Thank you! 🙏"
           if settled else
           f"Balance remaining: {currency} {_money(payment.balance_after)}")
    )

    client = WhatsAppClient()
    conversation = get_or_create_conversation(customer.wa_number, profile_name=customer.name)
    link = public_url(f"/doc/receipt/{payment.public_token}.pdf")
    try:
        client.send_document(
            customer.wa_number, link, f"Receipt-{payment.receipt_no or payment.id}.pdf",
            caption=caption, conversation=conversation,
            job_id=job.id if job else None,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Receipt send failed: %s", exc)
        _log(job, customer.wa_number, TEMPLATE_DOCUMENT, caption, "failed", str(exc)[:250])
        return {"sent": False, "reason": "send_failed"}

    _log(job, customer.wa_number, TEMPLATE_DOCUMENT, caption, "sent")
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
        body += f"\n\n📅 Promised date: {job.promised_date.strftime('%d %b %Y')}"
    blocking = [p for p in job.job_parts if p.is_blocking]
    if blocking:
        body += f"\n\n⚠️ Waiting on {len(blocking)} part(s). We will update you as soon as they land."
    body += "\n\nReply *track* to check progress any time."

    return _dispatch(
        job, body, template=TEMPLATE_STAGE_UPDATE, use_template=True,
        params=[job.customer.name, label, line],
    )


def notify_ready_for_collection(job: JobCard) -> bool:
    invoice = job.outstanding_invoice
    body = (
        f"🎉 *Your vehicle is ready!*\n\n"
        f"{job.vehicle.title if job.vehicle else 'Vehicle'} ({job.vehicle.reg_no if job.vehicle else '-'})\n"
        f"Job card number: *{job.job_no}*"
    )
    if invoice and invoice.balance > 0:
        body += f"\n\n💰 Balance due: *{invoice.currency} {_money(invoice.balance)}*"
        body += "\nPay by EcoCash, InnBucks or bank transfer — reply *pay* for details."
    body += (
        f"\n\n📍 Collect at {current_app.config['COMPANY_ADDRESS']}"
        f"\n🕐 {current_app.config['COMPANY_HOURS']}"
        f"\n\nPlease bring your ID and collection slip."
    )
    body += f"\n\n🛡️ {WARRANTY_TEXT[:120]}…"

    return _dispatch(
        job, body, template=TEMPLATE_READY, use_template=True,
        params=[job.customer.name, job.vehicle.reg_no if job.vehicle else "-"],
    )


def notify_quote_ready(job: JobCard) -> bool:
    estimate = job.latest_estimate
    if not estimate:
        return False
    body = (
        f"📋 *Quotation ready* — job card {job.job_no}\n\n"
        f"Vehicle: {job.vehicle.reg_no if job.vehicle else '-'}\n"
        f"Reference: {estimate.reference}\n"
        f"Total: *{estimate.currency} {_money(estimate.total)}*"
    )
    if estimate.is_insurance:
        body += f"\nExcess payable by you: *USD {_money(estimate.excess)}*"
    body += (
        "\n\nReply *approve* to authorise the repair, or *decline* and our team will call you."
    )
    return _dispatch(
        job, body, template=TEMPLATE_QUOTE_READY, use_template=True,
        params=[job.customer.name, estimate.reference, _money(estimate.total)],
    )


def notify_parts_received(job: JobCard, parts: list[str]) -> bool:
    body = (
        f"📦 Good news — parts received for job {job.job_no}.\n\n"
        + "\n".join(f"• {p}" for p in parts[:8])
        + "\n\nWork continues and we will update you at the next stage."
    )
    return _dispatch(job, body, template=TEMPLATE_PARTS_IN, use_template=True,
                     params=[job.customer.name, ", ".join(parts[:3])])


def notify_invoice_issued(job: JobCard, invoice) -> bool:
    body = (
        f"🧾 *Invoice {invoice.invoice_no}*\n\n"
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
        f"🛡️ *Warranty registered* — job {job.job_no}\n\n{WARRANTY_TEXT}\n\n"
        "Keep this message as your warranty reference. Reply *menu* for anything else."
    )
    return _dispatch(job, body, template=TEMPLATE_WARRANTY, use_template=True,
                     params=[job.customer.name, job.job_no])


def notify_booking_confirmed(booking) -> bool:
    customer = booking.customer
    if not customer or not customer.wa_number:
        return False
    body = (
        f"✅ *Booking confirmed*\n\n"
        f"Reference: {booking.reference}\n"
        f"Service: {booking.service}\n"
        f"Date: {booking.slot_date.strftime('%a %d %b %Y')}"
        + (f" at {booking.slot_time}" if booking.slot_time else "")
        + f"\n\n📍 {current_app.config['COMPANY_ADDRESS']}"
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


def send_custom(conversation, body: str, *, is_bot: bool = False, user=None):
    """Used by the web inbox when a staff member replies manually."""
    client = WhatsAppClient()
    delivered = True
    try:
        client.send_text(conversation.wa_id, body, conversation=conversation, is_bot=is_bot)
    except Exception as exc:  # noqa: BLE001
        log.error("Manual reply failed: %s", exc)
        delivered = False
        log_outbound(conversation, body=body, is_bot=is_bot, status="failed",
                     payload={"error": str(exc)[:200]})
    return delivered
