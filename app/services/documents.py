"""PDF documents: quotation, invoice and receipt.

Uses ReportLab's Platypus layer so the three documents share one letterhead,
one table style and one totals block — the shop looks consistent on paper and
in WhatsApp attachments.

Public entry points all return ``bytes``:

    build_quotation_pdf(estimate) -> bytes
    build_invoice_pdf(invoice)   -> bytes
    build_receipt_pdf(payment)   -> bytes

Each raises :class:`DocumentError` if the record is not printable.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from flask import current_app

from ..constants import STAGE_LABELS, WARRANTY_TEXT
from ..models import Estimate, Invoice, Payment

# ── Brand palette (kept in step with app.css) ────────────────────────────────
NAVY = "#08142c"
NAVY_SOFT = "#16305f"
CRIMSON = "#c2102e"
GREEN = "#106b41"
AMBER = "#c8860a"
GREY = "#66748c"
LINE = "#d3dbe9"


class DocumentError(Exception):
    """Raised when a document cannot be produced."""


def _money(value) -> str:
    try:
        return f"{Decimal(str(value or 0)):,.2f}"
    except Exception:  # noqa: BLE001
        return "0.00"


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
    }


def _styles():
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "DocTitle", parent=base["Title"], fontSize=19, leading=22,
            textColor=NAVY, alignment=TA_RIGHT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "DocSub", parent=base["Normal"], fontSize=8.5, leading=11,
            textColor=GREY, alignment=TA_RIGHT,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Normal"], fontSize=7.5, leading=10,
            textColor=GREY, spaceAfter=3,
        ),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=9, leading=12.5),
        "small": ParagraphStyle("Small", parent=base["Normal"], fontSize=8, leading=11, textColor=GREY),
        "cell": ParagraphStyle("Cell", parent=base["Normal"], fontSize=8.5, leading=11),
        "cellright": ParagraphStyle(
            "CellRight", parent=base["Normal"], fontSize=8.5, leading=11, alignment=TA_RIGHT,
        ),
        "bold": ParagraphStyle("Bold", parent=base["Normal"], fontSize=9.5, leading=12,
                               textColor=NAVY, fontName="Helvetica-Bold"),
    }


def _letterhead(meta: list[tuple[str, str]], accent: str = CRIMSON, doc_title: str = ""):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    company = _company()
    styles = _styles()

    left = [
        Paragraph(f"<b>{company['name']}</b>", styles["bold"]),
        Paragraph(company["address"], styles["small"]),
        Paragraph(f"{company['tel']} &nbsp;·&nbsp; {company['email']}", styles["small"]),
        Paragraph(company["website"], styles["small"]),
    ]
    # The heading is optional. The document number and dates below already say
    # what this is, so a second "TAX INVOICE" above them is just noise.
    right = [Paragraph(doc_title, styles["title"])] if doc_title else []
    right += [Paragraph(f"<b>{label}</b> {value}", styles["subtitle"]) for label, value in meta]

    table = Table([[left, right]], colWidths=[100 * mm, 70 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 1.6, colors.HexColor(accent)),
    ]))
    return table


def _party_block(pairs: list[tuple[str, str, str]], ):
    """pairs: [(heading, label, value)] rendered as labelled mini-tables."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    cells = []
    for heading, rows in pairs:
        inner = [[Paragraph(heading.upper(), styles["h2"])]]
        for label, value in rows:
            inner.append([
                Paragraph(f"<font color='{GREY}'>{label}</font><br/><b>{value or '—'}</b>",
                          styles["cell"])
            ])
        table = Table(inner, colWidths=[80 * mm])
        table.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        cells.append(table)

    outer = Table([cells], colWidths=[85 * mm, 85 * mm])
    outer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return outer


def _items_table(items: list[dict], currency: str, show_markup: bool = False):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    header = ["Description", "Type", "Qty", "Rate", "Amount"]
    if show_markup:
        header.insert(4, "Markup")
    widths = [78 * mm, 22 * mm, 16 * mm, 22 * mm, 26 * mm]
    if show_markup:
        widths = [66 * mm, 20 * mm, 14 * mm, 20 * mm, 18 * mm, 26 * mm]

    rows = [[Paragraph(f"<b>{h}</b>", styles["h2"]) for h in header]]
    for item in items:
        row = [
            Paragraph(item["description"], styles["cell"]),
            Paragraph(item["kind"].title(), styles["cell"]),
            Paragraph(f"{item['quantity']:g} {item.get('unit') or ''}".strip(), styles["cellright"]),
            Paragraph(_money(item["unit_price"]), styles["cellright"]),
        ]
        if show_markup:
            row.append(Paragraph(f"{item.get('markup_pct', 0) * 100:.0f}%", styles["cellright"]))
        row.append(Paragraph(f"<b>{_money(item['line_total'])}</b>", styles["cellright"]))
        rows.append(row)

    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(NAVY)),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.HexColor(LINE)),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfe")]),
    ]))
    return table


def _totals_block(lines: list[tuple[str, str, bool]], accent: str = CRIMSON):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    rows = [
        [Paragraph(label, styles["cell"]), Paragraph(value, styles["cellright"])]
        for label, value, _strong in lines
    ]
    table = Table(rows, colWidths=[45 * mm, 35 * mm], hAlign="RIGHT")
    style = [
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]
    for index, (_label, _value, strong) in enumerate(lines):
        if strong:
            style += [
                ("LINEABOVE", (0, index), (-1, index), 0.8, colors.HexColor(accent)),
            ]
    table.setStyle(TableStyle(style))
    return table


def _footer(text: str):
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    styles = _styles()
    return [Spacer(1, 6 * mm), Paragraph(text, styles["small"])]


def _render(story: list, title: str, accent: str = CRIMSON) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    buffer = io.BytesIO()
    company = _company()

    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=16 * mm,
        title=title, author=company["name"], subject=title,
        creator=f"{company['name']} Workshop OS",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def decorate(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(LINE))
        canvas.setLineWidth(0.5)
        y = 13 * mm
        canvas.line(doc.leftMargin, y + 4 * mm, doc.leftMargin + doc.width, y + 4 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor(GREY))
        canvas.drawString(doc.leftMargin, y, f"{company['name']} · {company['tel']} · {company['email']}")
        canvas.drawRightString(
            doc.leftMargin + doc.width, y, f"Page {canvas.getPageNumber()}"
        )
        # Accent tab in the corner.
        canvas.setFillColor(colors.HexColor(accent))
        canvas.rect(0, A4[1] - 4 * mm, A4[0], 4 * mm, stroke=0, fill=1)
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
    doc.build(story)
    return buffer.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Quotation
# ─────────────────────────────────────────────────────────────────────────────
def build_quotation_pdf(estimate: Estimate) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    if estimate is None:
        raise DocumentError("Quotation not found.")

    job = estimate.job
    vehicle = job.vehicle if job else None
    customer = job.customer if job else None
    styles = _styles()
    currency = estimate.currency or "USD"

    meta = [
        ("Quotation", estimate.reference),
        ("Date", (estimate.created_at or date.today()).strftime("%d %b %Y")),
        ("Valid until", estimate.expires_on.strftime("%d %b %Y")),
        ("Job card", job.job_no if job else "—"),
    ]

    items = [i.to_dict() for i in estimate.items]

    story = [
        _letterhead(meta),
        Spacer(1, 6 * mm),
        _party_block([
            ("Prepared for", [
                ("Customer", customer.name if customer else "—"),
                ("Phone", customer.phone if customer else "—"),
                ("Email", customer.email if customer else "—"),
            ]),
            ("Vehicle", [
                ("Registration", vehicle.reg_no if vehicle else "—"),
                ("Vehicle", vehicle.title if vehicle else "—"),
                ("Service", job.service if job else "—"),
            ]),
        ]),
        Spacer(1, 2 * mm),
        _items_table(items, currency),
        Spacer(1, 4 * mm),
        _totals_block([
            ("Labour", _money(estimate.labour_total), False),
            ("Materials", _money(estimate.materials_total), False),
            ("Parts", _money(estimate.parts_total), False),
            ("Subtotal", _money(estimate.subtotal), False),
            (f"VAT ({int(current_app.config['VAT_RATE'] * 100)}%)", _money(estimate.vat), False),
            (f"Total ({currency})", _money(estimate.total), True),
        ]),
    ]

    if estimate.is_insurance:
        story += [
            Spacer(1, 3 * mm),
            Paragraph(
                f"<b>Insurance repair.</b> The insurer settles "
                f"{currency} {_money(Decimal(str(estimate.total)) - Decimal(str(estimate.excess)))}. "
                f"The excess of <b>{currency} {_money(estimate.excess)}</b> is payable by the customer.",
                styles["body"],
            ),
        ]

    if estimate.is_insurance and job and job.active_claim:
        claim = job.active_claim
        story += [
            Spacer(1, 2 * mm),
            Paragraph(
                f"<font color='{GREY}'>Insurer:</font> <b>{claim.insurer_name}</b> &nbsp; "
                f"<font color='{GREY}'>Claim:</font> <b>{claim.claim_no or '—'}</b> &nbsp; "
                f"<font color='{GREY}'>Excess:</font> <b>{currency} {_money(claim.excess)}</b>",
                styles["cell"],
            ),
        ]

    story += _footer(
        "<b>Terms</b> — This quotation is valid for "
        f"{estimate.valid_days} days and assumes no hidden damage is found on strip-down. "
        "Any additional work will be re-quoted for approval before proceeding. "
        "Parts carry the manufacturer's warranty; workmanship is warranted for 12 months. "
        "<br/><br/>"
        f"<b>Acceptance</b> — Approve on WhatsApp, reply to {_company()['email']}, "
        f"or sign below. Vehicles are released on settlement of the account."
    )
    story += [
        Spacer(1, 10 * mm),
    ]

    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    signature = Table([
        [Paragraph("Customer acceptance / signature", styles["small"]),
         Paragraph("For and on behalf of the workshop", styles["small"])],
        ["", ""],
    ], colWidths=[85 * mm, 85 * mm], rowHeights=[None, 14 * mm])
    signature.setStyle(TableStyle([
        ("LINEBELOW", (0, 1), (0, 1), 0.7, colors.HexColor(NAVY)),
        ("LINEBELOW", (1, 1), (1, 1), 0.7, colors.HexColor(NAVY)),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(signature)

    return _render(story, f"Quotation {estimate.reference}")


# ─────────────────────────────────────────────────────────────────────────────
# Invoice
# ─────────────────────────────────────────────────────────────────────────────
def build_invoice_pdf(invoice: Invoice) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    if invoice is None:
        raise DocumentError("Invoice not found.")

    job = invoice.job
    customer = invoice.customer
    vehicle = job.vehicle if job else None
    styles = _styles()
    currency = invoice.currency or "USD"
    paid = Decimal(str(invoice.amount_paid or 0))
    balance = Decimal(str(invoice.balance))

    meta = [
        ("Invoice", invoice.invoice_no),
        ("Issued", (invoice.issued_at or invoice.created_at or date.today()).strftime("%d %b %Y")),
        ("Due", invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "On collection"),
        ("Job card", job.job_no if job else "—"),
    ]

    story = [
        _letterhead(meta),
        Spacer(1, 6 * mm),
        _party_block([
            ("Billed to", [
                ("Customer", customer.name if customer else "—"),
                ("Phone", customer.phone if customer else "—"),
                ("Account", "Insurance claim" if invoice.is_insurance else "Walk-in / cash"),
            ]),
            ("Vehicle", [
                ("Registration", vehicle.reg_no if vehicle else "—"),
                ("Vehicle", vehicle.title if vehicle else "—"),
                ("Stage", STAGE_LABELS.get(job.stage, job.stage) if job else "—"),
            ]),
        ]),
        Spacer(1, 2 * mm),
    ]

    estimate = job.latest_estimate if job else None
    if estimate and estimate.items:
        story += [
            _items_table([i.to_dict() for i in estimate.items], currency),
            Spacer(1, 4 * mm),
        ]
    elif invoice.notes:
        # Raised straight from the desk: there is no estimate behind it, so its own
        # wording is the only description of what is charged. Without this the
        # document is a totals block with nothing above it.
        story += [
            _items_table([{
                "description": invoice.notes,
                "kind": "Labour",
                "quantity": 1,
                "unit": "",
                "unit_price": float(invoice.subtotal or 0),
                "line_total": float(invoice.subtotal or 0),
            }], currency),
            Spacer(1, 4 * mm),
        ]

    story.append(_totals_block([
        ("Subtotal", _money(invoice.subtotal), False),
        (f"VAT ({int(current_app.config['VAT_RATE'] * 100)}%)", _money(invoice.vat), False),
        (f"Invoice total ({currency})", _money(invoice.total), True),
        ("Paid to date", _money(paid), False),
        ("Balance due", _money(balance), True),
    ], accent=GREEN if balance <= 0 else CRIMSON))

    if invoice.payments:
        story += [
            Spacer(1, 5 * mm),
            Paragraph("PAYMENTS RECEIVED", styles["h2"]),
        ]
        from reportlab.lib.units import mm as _mm
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib import colors

        rows = [[Paragraph(f"<b>{h}</b>", styles["h2"]) for h in
                 ["Receipt", "Date", "Method", "Reference", "Amount"]]]
        for payment in sorted(invoice.payments, key=lambda p: p.id):
            rows.append([
                Paragraph(payment.receipt_no or "—", styles["cell"]),
                Paragraph(payment.created_at.strftime("%d %b %Y"), styles["cell"]),
                Paragraph((payment.method or "").replace("_", " ").title(), styles["cell"]),
                Paragraph(payment.reference or "—", styles["cell"]),
                Paragraph(f"<b>{_money(payment.amount)}</b>", styles["cellright"]),
            ])
        paid_table = Table(rows, colWidths=[30 * _mm, 24 * _mm, 30 * _mm, 46 * _mm, 24 * _mm])
        paid_table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(NAVY)),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ]))
        story.append(paid_table)

    story += _footer(
        "<b>Payment</b> — Cash, EcoCash, InnBucks, bank transfer or card at reception. "
        "Please quote the invoice number with any transfer. "
        f"<br/><br/><b>Warranty</b> — {WARRANTY_TEXT}"
    )
    return _render(story, f"Invoice {invoice.invoice_no}",
                   accent=GREEN if balance <= 0 else CRIMSON)


# ─────────────────────────────────────────────────────────────────────────────
# Receipt
# ─────────────────────────────────────────────────────────────────────────────
def build_receipt_pdf(payment: Payment) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    if payment is None:
        raise DocumentError("Payment not found.")

    invoice = payment.invoice
    job = invoice.job if invoice else None
    customer = invoice.customer if invoice else None
    style = _styles()
    currency = (invoice.currency if invoice else "USD") or "USD"
    settled = payment.is_fully_settled

    meta = [
        ("Official receipt", payment.receipt_no or f"#{payment.id}"),
        ("Date", payment.created_at.strftime("%d %b %Y")),
        ("Invoice", invoice.invoice_no if invoice else "—"),
        ("Received by", payment.user.full_name if payment.user else "Front desk"),
    ]

    story = [
        _letterhead(meta, accent=GREEN),
        Spacer(1, 6 * mm),
        Paragraph(
            f"Received with thanks from <b>{customer.name if customer else '—'}</b> "
            f"the sum of <b>{currency} {_money(payment.amount)}</b>.",
            style["body"],
        ),
        Spacer(1, 4 * mm),
        _party_block([
            ("Payment", [
                ("Method", (payment.method or "").replace("_", " ").title()),
                ("Reference", payment.reference or "—"),
                ("Received by", payment.user.full_name if payment.user else "Front desk"),
            ]),
            ("Against", [
                ("Invoice", invoice.invoice_no if invoice else "—"),
                ("Job card", job.job_no if job else "—"),
                ("Vehicle", job.vehicle.reg_no if job and job.vehicle else "—"),
            ]),
        ]),
        Spacer(1, 2 * mm),
        _totals_block([
            (f"Invoice total ({currency})", _money(invoice.total if invoice else 0), False),
            ("Paid to date", _money(invoice.amount_paid if invoice else 0), False),
            ("Balance remaining", _money(payment.balance_after), True),
        ], accent=GREEN if settled else AMBER),
        Spacer(1, 4 * mm),
        Paragraph(
            ("<font color='%s'><b>PAID IN FULL</b></font> — thank you for your business."
             % GREEN) if settled else
            ("<font color='%s'><b>PART PAYMENT</b></font> — the balance above remains due."
             % AMBER),
            style["body"],
        ),
    ]
    story += _footer(
        "This is a computer-generated receipt. Retain it for your records and warranty claims. "
        f"<br/>{_company()['name']} · {_company()['address']} · {_company()['tel']}"
    )
    return _render(story, f"Receipt {payment.receipt_no or payment.id}", accent=GREEN)


# ─────────────────────────────────────────────────────────────────────────────
# End-of-day report
# ─────────────────────────────────────────────────────────────────────────────
def _sheet_table(header: list[str], rows: list[list[str]], widths: list[float]):
    """Compact table used by the closing sheet — one style, repeated."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    data = [[Paragraph(f"<b>{h}</b>", styles["h2"]) for h in header]]
    for row in rows:
        data.append([
            Paragraph(str(cell), styles["cellright"] if index == len(row) - 1
                      else styles["cell"])
            for index, cell in enumerate(row)
        ])

    table = Table(data, colWidths=[width * mm for width in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(NAVY)),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def build_end_of_day_pdf(report: dict) -> bytes:
    """The closing sheet: job card statuses, enquiries/bookings and the money."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    styles = _styles()
    jobs = report["jobs"]
    bookings = report["bookings"]
    money = report["money"]

    story = [
        _letterhead(
            [("Report", "End of day"),
             ("For", report["date_label"]),
             ("Generated", (report["generated_at"] or "")[:16].replace("T", " "))],
        ),
        Spacer(1, 6 * mm),

        Paragraph("JOB CARDS", styles["h2"]),
        _totals_block([
            ("Open job cards", str(jobs["open"]), False),
            ("Checked in today", str(jobs["opened"]), False),
            ("Completed today", str(jobs["completed"]), False),
            ("Collected today", str(jobs["collected"]), False),
            ("Past promised date", str(jobs["overdue"]), True),
        ]),
        Spacer(1, 5 * mm),
        Paragraph("Jobs still open, by stage", styles["body"]),
        Spacer(1, 2 * mm),
        _sheet_table(
            ["Stage", "Jobs"],
            [[row["label"], row["count"]] for row in jobs["by_stage"]],
            [60, 20],
        ),
    ]

    if jobs["list"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Open job cards", styles["body"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Job card", "Customer", "Reg", "Stage", "Promised", "Days"],
                [[row["job_no"], row["customer_name"] or "—", row["reg_no"] or "—",
                  row["stage_label"], row["promised_date"] or "—", row["days_in_shop"]]
                 for row in jobs["list"][:60]],
                [26, 42, 25, 33, 24, 24],
            ),
        ]

    story += [
        Spacer(1, 7 * mm),
        Paragraph("ENQUIRIES & BOOKINGS", styles["h2"]),
        _totals_block([
            ("Raised today", str(bookings["raised"]), False),
            ("Scheduled today", str(bookings["scheduled"]), False),
            ("Handled / confirmed", str(bookings["handled"]), False),
            ("Awaiting confirmation", str(bookings["awaiting_confirmation"]), False),
            ("Jobs secured", str(bookings["secured"]), False),
            ("Walked out", str(bookings["walked_out"]), True),
        ]),
        Spacer(1, 5 * mm),
        Paragraph("Today's appointments by status", styles["body"]),
        Spacer(1, 2 * mm),
        _sheet_table(
            ["Status", "Count"],
            [[row["label"], row["count"]] for row in bookings["by_status"]],
            [60, 20],
        ),
    ]

    if bookings["pending"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Still awaiting confirmation", styles["body"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Reference", "Customer", "Service", "Slot", "Source"],
                [[row["reference"], row["customer_name"] or "—", row["service"],
                  row["slot_date"] or "—", (row["source"] or "").title() or "—"]
                 for row in bookings["pending"][:40]],
                [28, 44, 40, 26, 36],
            ),
        ]

    if bookings["scheduled_list"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Today's appointments", styles["body"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Reference", "Customer", "Service", "Time", "Status", "Handled by", "Outcome"],
                [[row["reference"], row["customer_name"] or "—", row["service"],
                  row["slot_time"] or "Any", row["status_label"],
                  row["attended_by"] or row["confirmed_by"] or "—",
                  row["outcome_label"] or "—"]
                 for row in bookings["scheduled_list"][:60]],
                [24, 33, 30, 14, 24, 28, 17],
            ),
        ]

    story += [
        Spacer(1, 7 * mm),
        Paragraph("MONEY", styles["h2"]),
        _totals_block([
            ("Invoiced today", _money(money["invoiced"]), False),
            ("Received today", _money(money["collected"]), False),
            ("Invoices settled today", str(money["paid_today_count"]), False),
            ("Outstanding on unpaid invoices", _money(money["outstanding"]), False),
            ("Unpaid invoices", str(money["unpaid_count"]), False),
            ("Overdue", f"{money['overdue_count']} · {_money(money['overdue_total'])}", True),
        ]),
    ]

    if money["by_method"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Takings by method", styles["body"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Method", "Receipts", "Total"],
                [[row["method"].replace("_", " ").title(), row["count"], _money(row["total"])]
                 for row in money["by_method"]],
                [44, 24, 28],
            ),
        ]

    if money["payments"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Receipts issued today", styles["body"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Receipt", "Invoice", "Payer", "Method", "Amount"],
                [[row["receipt_no"] or "—", row["invoice_no"] or "—",
                  row["payer_name"] or "—", (row["method"] or "").replace("_", " ").title(),
                  _money(row["amount"])]
                 for row in money["payments"][:60]],
                [30, 30, 50, 34, 26],
            ),
        ]

    if money["unsettled"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Unpaid / part-paid invoices", styles["body"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Invoice", "Customer", "Total", "Paid", "Balance", "Due"],
                [[row["invoice_no"], row["customer_name"] or "—", _money(row["total"]),
                  _money(row["amount_paid"]), _money(row["balance"]), row["due_date"] or "—"]
                 for row in money["unsettled"][:60]],
                [28, 42, 24, 24, 24, 40],
            ),
        ]

    if report["staff"]:
        story += [
            Spacer(1, 7 * mm),
            Paragraph("STAFF", styles["h2"]),
            _sheet_table(
                ["Name", "Role", "Confirmed", "Attended", "Open jobs"],
                [[row["name"], (row["role"] or "").title(), row["confirmed"],
                  row["attended"], row["open_jobs"]]
                 for row in report["staff"]],
                [46, 30, 26, 24, 24],
            ),
        ]

    story += _footer(
        f"<b>End of day</b> — {report['date_label']}. Prepared from the workshop "
        "system at the close of business."
    )
    return _render(story, f"End of day {report['date']}", accent=NAVY)


# ─────────────────────────────────────────────────────────────────────────────
def build_for(kind: str, record) -> tuple[bytes, str]:
    """Dispatch helper. Returns (pdf_bytes, filename)."""
    kind = (kind or "").lower()
    if kind in {"quote", "quotation", "estimate"}:
        pdf = build_quotation_pdf(record)
        name = f"Quotation-{record.reference}.pdf"
    elif kind == "invoice":
        pdf = build_invoice_pdf(record)
        name = f"Invoice-{record.invoice_no}.pdf"
    elif kind == "receipt":
        pdf = build_receipt_pdf(record)
        name = f"Receipt-{record.receipt_no or record.id}.pdf"
    else:
        raise DocumentError(f"Unknown document kind '{kind}'.")
    return pdf, name
