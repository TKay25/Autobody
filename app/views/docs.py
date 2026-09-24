"""Customer-facing documents: quotation, invoice and receipt.

Two surfaces per document:

* ``/doc/quote/<token>``            — mobile-friendly HTML the customer can open,
                                      read, print or "Save as PDF"
* ``/doc/quote/<token>.pdf``        — the same document as a real PDF, which is
                                      what we hand to Meta for WhatsApp delivery

Access is by unguessable token, so no account is needed and nothing is
enumerable. Tokens are per-document and can be rotated by regenerating them.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, current_app, render_template, send_file

from ..constants import STAGE_LABELS
from ..extensions import db
from ..models import Estimate, Invoice, Payment
from ..services import documents

bp = Blueprint("docs", __name__, url_prefix="/doc")

KINDS = {
    "quote": {
        "model": Estimate, "token": "public_token",
        "builder": documents.build_quotation_pdf, "label": "Quotation",
    },
    "invoice": {
        "model": Invoice, "token": "public_token",
        "builder": documents.build_invoice_pdf, "label": "Tax invoice",
    },
    "receipt": {
        "model": Payment, "token": "public_token",
        "builder": documents.build_receipt_pdf, "label": "Receipt",
    },
}


def _lookup(kind: str, token: str):
    spec = KINDS.get(kind)
    if not spec or not token or len(token) < 8:
        abort(404)
    record = spec["model"].query.filter_by(**{spec["token"]: token}).first()
    if not record:
        abort(404)
    return spec, record


@bp.get("/<kind>/<token>")
def view(kind: str, token: str):
    """Printable HTML version — the customer can preview or save as PDF."""
    spec, record = _lookup(kind, token)
    context = {"kind": kind, "label": spec["label"], "today": date.today(),
               "company": _company(), "currency": "USD", "token": token}

    if kind == "quote":
        estimate = record
        job = estimate.job
        context.update(
            estimate=estimate,
            job=job,
            vehicle=job.vehicle if job else None,
            customer=job.customer if job else None,
            items=estimate.items,
            expires_on=estimate.expires_on,
            is_expired=estimate.is_expired,
        )
    elif kind == "invoice":
        invoice = record
        job = invoice.job
        context.update(
            invoice=invoice,
            job=job,
            vehicle=job.vehicle if job else None,
            customer=invoice.customer,
            items=(job.latest_estimate.items if job and job.latest_estimate else []),
            payments=sorted(invoice.payments, key=lambda p: p.id),
            balance=Decimal(str(invoice.balance)),
            stage_label=STAGE_LABELS.get(job.stage, job.stage) if job else None,
        )
    else:
        payment = record
        invoice = payment.invoice
        job = invoice.job if invoice else None
        context.update(
            payment=payment,
            invoice=invoice,
            job=job,
            customer=invoice.customer if invoice else None,
            vehicle=job.vehicle if job and job.vehicle else None,
            balance_after=payment.balance_after,
            settled=payment.is_fully_settled,
        )

    return render_template("document.html", **context)


@bp.get("/<kind>/<token>.pdf")
def pdf(kind: str, token: str):
    """Real PDF — also the URL handed to Meta for WhatsApp document messages."""
    spec, record = _lookup(kind, token)
    try:
        payload, filename = documents.build_for(kind, record)
    except documents.DocumentError:
        abort(404)

    import io

    return send_file(
        io.BytesIO(payload),
        mimetype="application/pdf",
        as_attachment=False,          # inline so browsers can preview it
        download_name=filename,
        max_age=0,
    )


def _company() -> dict:
    cfg = current_app.config
    return {
        "name": cfg["COMPANY_NAME"],
        "address": cfg["COMPANY_ADDRESS"],
        "tel": cfg["COMPANY_TEL"],
        "mobile": cfg["COMPANY_MOBILE"],
        "email": cfg["COMPANY_EMAIL"],
        "hours": cfg["COMPANY_HOURS"],
        "website": cfg["COMPANY_WEBSITE"],
        "bank": cfg.get("BANK_DETAILS", ""),
        "ecocash": cfg.get("ECONET_NUMBER", ""),
        "vat": int(Decimal(str(cfg.get("VAT_RATE", "0.15"))) * 100),
    }
