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

# Paper-only tones. Body text is INK rather than pure black — black on white
# reads as a printout, a soft navy-black reads as a document.
INK = "#1b2436"
TINT = "#f5f7fb"           # panels and zebra rows
TINT_STRONG = "#e9eef7"    # a tint that still separates from TINT
REVERSED = "#ffffff"
REVERSED_MUTED = "#b9c6e0"  # white knocked back, for text sitting on NAVY


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
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        # ── Masthead: everything here sits reversed out of NAVY ──────────
        "brand": ParagraphStyle(
            "Brand", parent=base["Normal"], fontSize=15.5, leading=18,
            textColor=REVERSED, fontName="Helvetica-Bold",
        ),
        "brandmeta": ParagraphStyle(
            "BrandMeta", parent=base["Normal"], fontSize=7.8, leading=11,
            textColor=REVERSED_MUTED,
        ),
        "doclabel": ParagraphStyle(
            "DocLabel", parent=base["Normal"], fontSize=7.5, leading=9.5,
            textColor=REVERSED_MUTED, alignment=TA_RIGHT,
        ),
        "docnum": ParagraphStyle(
            "DocNum", parent=base["Normal"], fontSize=14.5, leading=17,
            textColor=REVERSED, fontName="Helvetica-Bold", alignment=TA_RIGHT,
        ),
        "docmeta": ParagraphStyle(
            "DocMeta", parent=base["Normal"], fontSize=8, leading=11.5,
            textColor=REVERSED_MUTED, alignment=TA_RIGHT,
        ),
        # ── Kept for the optional heading; blank by default everywhere ────
        "title": ParagraphStyle(
            "DocTitle", parent=base["Title"], fontSize=19, leading=22,
            textColor=NAVY, alignment=TA_RIGHT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "DocSub", parent=base["Normal"], fontSize=8.5, leading=11,
            textColor=GREY, alignment=TA_RIGHT,
        ),
        # ── Blocks ──────────────────────────────────────────────────────
        # h2 is the micro-caps label inside a panel; section is a page-level
        # heading with a rule under it.
        "h2": ParagraphStyle(
            "H2", parent=base["Normal"], fontSize=7.5, leading=10,
            textColor=GREY, spaceAfter=3,
        ),
        "section": ParagraphStyle(
            "Section", parent=base["Normal"], fontSize=8, leading=10,
            textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=0,
        ),
        "sub": ParagraphStyle(
            "Sub", parent=base["Normal"], fontSize=8.5, leading=11,
            textColor=NAVY, fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=9, leading=12.5,
                               textColor=INK),
        "small": ParagraphStyle("Small", parent=base["Normal"], fontSize=8, leading=11, textColor=GREY),
        "cell": ParagraphStyle("Cell", parent=base["Normal"], fontSize=8.5, leading=11,
                               textColor=INK),
        "cellright": ParagraphStyle(
            "CellRight", parent=base["Normal"], fontSize=8.5, leading=11,
            alignment=TA_RIGHT, textColor=INK,
        ),
        # Column headings: white on the navy header row.
        "cellhead": ParagraphStyle(
            "CellHead", parent=base["Normal"], fontSize=7.5, leading=10,
            textColor=REVERSED, fontName="Helvetica-Bold",
        ),
        "cellheadright": ParagraphStyle(
            "CellHeadRight", parent=base["Normal"], fontSize=7.5, leading=10,
            textColor=REVERSED, fontName="Helvetica-Bold", alignment=TA_RIGHT,
        ),
        "cellmoney": ParagraphStyle(
            "CellMoney", parent=base["Normal"], fontSize=8.5, leading=11,
            alignment=TA_RIGHT, textColor=NAVY, fontName="Helvetica-Bold",
        ),
        # Totals panel.
        "rowlabel": ParagraphStyle(
            "RowLabel", parent=base["Normal"], fontSize=8.5, leading=12, textColor=GREY,
        ),
        "rowvalue": ParagraphStyle(
            "RowValue", parent=base["Normal"], fontSize=8.5, leading=12,
            alignment=TA_RIGHT, textColor=INK,
        ),
        "rowlabelstrong": ParagraphStyle(
            "RowLabelStrong", parent=base["Normal"], fontSize=9.5, leading=13,
            textColor=REVERSED, fontName="Helvetica-Bold",
        ),
        "rowvaluestrong": ParagraphStyle(
            "RowValueStrong", parent=base["Normal"], fontSize=10.5, leading=13,
            alignment=TA_RIGHT, textColor=REVERSED, fontName="Helvetica-Bold",
        ),
        "rowlabelsub": ParagraphStyle(
            "RowLabelSub", parent=base["Normal"], fontSize=9, leading=12,
            textColor=NAVY, fontName="Helvetica-Bold",
        ),
        "rowvaluesub": ParagraphStyle(
            "RowValueSub", parent=base["Normal"], fontSize=9, leading=12,
            alignment=TA_RIGHT, textColor=NAVY, fontName="Helvetica-Bold",
        ),
        # Value text inside a panel (pair, not label above).
        "label": ParagraphStyle(
            "Label", parent=base["Normal"], fontSize=6.8, leading=9,
            textColor=GREY, fontName="Helvetica-Bold",
        ),
        "value": ParagraphStyle(
            "Value", parent=base["Normal"], fontSize=8.8, leading=11.5,
            textColor=NAVY, fontName="Helvetica-Bold",
        ),
        # Status pill.
        "pill": ParagraphStyle(
            "Pill", parent=base["Normal"], fontSize=8, leading=10,
            textColor=REVERSED, fontName="Helvetica-Bold", alignment=TA_CENTER,
        ),
        "bold": ParagraphStyle("Bold", parent=base["Normal"], fontSize=9.5, leading=12,
                               textColor=NAVY, fontName="Helvetica-Bold"),
    }


def _letterhead(meta: list[tuple[str, str]], accent: str = CRIMSON, doc_title: str = "",
                width: float | None = None):
    """The masthead: a navy band, the workshop on the left, this document on the right.

    The heading is optional and blank by default. A document's number and dates
    already say what it is, so a second "TAX INVOICE" above them is noise — the
    *first* meta line is emphasised instead, because that is the thing somebody
    actually looks for when the paper is handed to them.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    company = _company()
    styles = _styles()
    content = width or _page()["width"]
    # The band has to breathe on a narrow sheet too, so the hero figure steps
    # down rather than wrapping onto a second line.
    narrow = content < 150 * mm
    docnum = ParagraphStyle("DocNumX", parent=styles["docnum"],
                            fontSize=11.5 if narrow else 14.5,
                            leading=14 if narrow else 17)
    doclabel = ParagraphStyle("DocLabelX", parent=styles["doclabel"],
                              fontSize=6.8 if narrow else 7.5)

    left = [
        Paragraph(company["name"], styles["brand"]),
        Paragraph(company["address"], styles["brandmeta"]),
        Paragraph(f"{company['tel']} &nbsp;·&nbsp; {company['mobile']}", styles["brandmeta"]),
        Paragraph(f"{company['email']} &nbsp;·&nbsp; {company['website']}", styles["brandmeta"]),
    ]

    right: list = []
    if doc_title:
        right.append(Paragraph(doc_title, styles["title"]))
    for index, (label, value) in enumerate(meta):
        if index == 0:
            right.append(Paragraph(label, doclabel))
            right.append(Paragraph(value, docnum))
        else:
            right.append(Paragraph(f"{label} &nbsp;·&nbsp; {value}", styles["docmeta"]))
    if not right:
        right = [Paragraph("", styles["docmeta"])]

    table = Table([[left, right]], colWidths=_fit([104 * mm, 70 * mm], content))
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(NAVY)),
        ("LEFTPADDING", (0, 0), (0, -1), 6 * mm),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 6 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 5.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5 * mm),
        # The accent runs the full width of the band, in the colour that belongs
        # to this kind of document.
        ("LINEBELOW", (0, 0), (-1, -1), 2.4, colors.HexColor(accent)),
    ]))
    return table


def _party_block(pairs: list[tuple[str, str, str]], width: float | None = None):
    """pairs: [(heading, label, value)] rendered as tinted panels.

    A label above its value in tiny caps, inside a tint with the brand's navy
    running down the leading edge — the same shape as a card in the console.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    outer_widths = _fit([85 * mm, 85 * mm], width)
    # Leave a gutter before the next panel, inside the outer column.
    inner_width = outer_widths[0] - 4 * mm

    cells = []
    for heading, rows in pairs:
        inner = [[Paragraph(heading.upper(), styles["h2"])]]
        for label, value in rows:
            inner.append([
                Paragraph(
                    f"<font color='{GREY}' size='6.8'><b>{label.upper()}</b></font><br/>"
                    f"<font color='{NAVY}'><b>{value or '—'}</b></font>",
                    styles["cell"],
                )
            ])
        table = Table(inner, colWidths=[inner_width])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(TINT)),
            ("LINEBEFORE", (0, 0), (0, -1), 2.2, colors.HexColor(NAVY)),
            ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        cells.append(table)

    outer = Table([cells], colWidths=outer_widths)
    outer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return outer


def _items_table(items: list[dict], currency: str, show_markup: bool = False,
                 accent: str = CRIMSON, width: float | None = None):
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
    widths = _fit(widths, width)

    # Solid navy header with the money columns aligned the same way as the
    # figures beneath them.
    rows = [[
        Paragraph(h, styles["cellheadright"] if index >= 2 else styles["cellhead"])
        for index, h in enumerate(header)
    ]]
    for item in items:
        quantity = item["quantity"]
        unit = item.get("unit") or ""
        # "1 hrs" on a customer's quotation reads as carelessness.
        if quantity == 1 and unit.endswith("s"):
            unit = unit[:-1]
        row = [
            Paragraph(item["description"], styles["cell"]),
            Paragraph(f"<font color='{GREY}'>{item['kind'].title()}</font>", styles["cell"]),
            Paragraph(f"{quantity:g} {unit}".strip(), styles["cellright"]),
            Paragraph(f"<font color='{GREY}'>{_money(item['unit_price'])}</font>",
                      styles["cellright"]),
        ]
        if show_markup:
            row.append(Paragraph(f"{item.get('markup_pct', 0) * 100:.0f}%", styles["cellright"]))
        row.append(Paragraph(_money(item["line_total"]), styles["cellmoney"]))
        rows.append(row)

    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.6, colors.HexColor(accent)),
        ("TOPPADDING", (0, 0), (-1, 0), 3.8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3.8),
        ("TOPPADDING", (0, 1), (-1, -1), 3.6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3.6),
        ("LINEBELOW", (0, 1), (-1, -1), 0.35, colors.HexColor(LINE)),
        ("LEFTPADDING", (0, 0), (0, -1), 2 * mm),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 2 * mm),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(TINT)]),
    ]))
    return table


def _totals_block(lines: list[tuple[str, str, bool]], accent: str = CRIMSON,
                  width: float | None = None, band: bool = True):
    """The money summary, as a panel that closes with the figure that matters.

    Only the *last* strong line is emphasised. An invoice carries two of them —
    the total and the balance due — and banding both makes the page read as
    though there were two grand totals.

    ``band=False`` keeps the emphasis as a tint instead of a solid accent fill.
    A closing sheet is full of figures that are not badges, and "Overdue 0" in a
    crimson band reads as an alarm when nothing is actually wrong.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    final = max((index for index, line in enumerate(lines) if line[2]), default=None)

    rows = []
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
    ]
    for index, (label, value, strong) in enumerate(lines):
        if index == final and band:
            label_style, value_style = "rowlabelstrong", "rowvaluestrong"
            style += [
                ("BACKGROUND", (0, index), (-1, index), colors.HexColor(accent)),
                ("TOPPADDING", (0, index), (-1, index), 5.5),
                ("BOTTOMPADDING", (0, index), (-1, index), 5.5),
            ]
        elif strong:
            label_style, value_style = "rowlabelsub", "rowvaluesub"
            style += [("BACKGROUND", (0, index), (-1, index), colors.HexColor(TINT_STRONG))]
        else:
            label_style, value_style = "rowlabel", "rowvalue"
        rows.append([
            Paragraph(label, styles[label_style]),
            Paragraph(value, styles[value_style]),
        ])

    table = Table(rows, colWidths=_fit([48 * mm, 38 * mm], width), hAlign="RIGHT")
    table.setStyle(TableStyle(style))
    return table


def _pill(text: str, colour: str, align: str = "LEFT", width: float | None = None):
    """A filled status stamp — PAID IN FULL, PART PAYMENT, BALANCE DUE.

    Sized to its own text. Measuring it is fiddly: ``Paragraph.wrap`` hands back
    the width it was *given* once the text fits, not the width it *needs*, so
    asking a Paragraph for its natural size and passing that as a column width
    produced a 1000mm stamp that stretched across the sheet like a second banner.
    ``stringWidth`` against the style's own font is the real measurement.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    style = styles["pill"]
    inset = 4.5 * mm
    frame = (width or _page()["width"]) - 2 * inset
    natural = stringWidth(text, style.fontName, style.fontSize)

    table = Table([[Paragraph(text, style)]], colWidths=[min(natural, frame) + 2 * inset],
                  hAlign=align)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(colour)),
        ("LEFTPADDING", (0, 0), (-1, -1), inset),
        ("RIGHTPADDING", (0, 0), (-1, -1), inset),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2 * mm),
    ]))
    return table


def _section_heading(title: str, accent: str = NAVY, width: float | None = None):
    """A page-level section label with a hairline under it.

    Used on the closing sheet, where a bare grey paragraph disappears into the
    body copy and the eye has no way to find the next block.
    """
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    table = Table([[Paragraph(title, styles["section"])]],
                  colWidths=[width] if width else None)
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.8, colors.HexColor(accent)),
    ]))
    return table


def _footer(text: str, width: float | None = None):
    """Terms, above a hairline that separates them from the document proper."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    styles = _styles()
    rule = Table([[" "]], colWidths=[width or _page()["width"]], rowHeights=[0.1])
    rule.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.HexColor(LINE)),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [Spacer(1, 5 * mm), rule, Spacer(1, 3 * mm), Paragraph(text, styles["small"])]


def _fit(widths: list[float], width: float | None) -> list[float]:
    """Scale fixed column widths to ``width``.

    The blocks below were drawn against A4 and carry literal millimetre widths,
    which is fine until a document is rendered on a smaller sheet — an A5 receipt
    is 124mm of body against A4's 174mm, so anything that assumes the wider frame
    runs off the paper. Passing the frame width in rescales the columns
    proportionally; passing ``None`` leaves the A4 originals untouched.
    """
    if not width:
        return widths
    used = sum(widths)
    if not used:
        return widths
    factor = width / used
    return [value * factor for value in widths]


def _page(pagesize=None) -> dict:
    """Page size plus the insets and chrome sizes that suit it.

    A5 is a bit over a third of A4's area, so the A4 margins would swallow the
    body. Along with tighter edges the footer rule and the corner tab come in a
    little, which is what makes an A5 receipt read like a till slip rather than
    a shrunk A4 page.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    size = pagesize or A4
    narrow = size[0] < 160 * mm
    side = 12 * mm if narrow else 18 * mm
    return {
        "size": size,
        "narrow": narrow,
        "side": side,
        "width": size[0] - 2 * side,
        "top": 11 * mm if narrow else 15 * mm,
        "bottom": 12 * mm if narrow else 16 * mm,
        "foot_y": 9 * mm if narrow else 13 * mm,
        "tab": 3 * mm if narrow else 4 * mm,
    }


def _render(story: list, title: str, accent: str = CRIMSON, pagesize=None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    buffer = io.BytesIO()
    company = _company()
    page = _page(pagesize)

    doc = BaseDocTemplate(
        buffer, pagesize=page["size"],
        leftMargin=page["side"], rightMargin=page["side"],
        topMargin=page["top"], bottomMargin=page["bottom"],
        title=title, author=company["name"], subject=title,
        creator=f"{company['name']} Workshop OS",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def decorate(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(LINE))
        canvas.setLineWidth(0.5)
        y = page["foot_y"]
        canvas.line(doc.leftMargin, y + 4 * mm, doc.leftMargin + doc.width, y + 4 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor(GREY))
        canvas.drawString(doc.leftMargin, y, f"{company['name']} · {company['tel']} · {company['email']}")
        canvas.drawRightString(
            doc.leftMargin + doc.width, y, f"Page {canvas.getPageNumber()}"
        )
        # Accent tab in the corner.
        canvas.setFillColor(colors.HexColor(accent))
        canvas.rect(0, page["size"][1] - page["tab"], page["size"][0], page["tab"],
                    stroke=0, fill=1)
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
        Spacer(1, 6 * mm),
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
    # A settled account is good news and takes the green; anything outstanding
    # takes the brand crimson so it is the first thing seen on the page.
    accent = GREEN if balance <= 0 else CRIMSON

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
                ("Account", "Walk-in / cash"),
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
            _items_table([i.to_dict() for i in estimate.items], currency, accent=accent),
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
            }], currency, accent=accent),
            Spacer(1, 4 * mm),
        ]

    # The state of the account is stamped above the summary, not under it: below,
    # the stamp landed directly against the colour-banded balance.
    if balance <= 0:
        story += [_pill("PAID IN FULL", GREEN, align="RIGHT"), Spacer(1, 3 * mm)]
    elif paid > 0:
        story += [_pill(f"PART PAID · {currency} {_money(balance)} OUTSTANDING", AMBER,
                        align="RIGHT"), Spacer(1, 3 * mm)]
    else:
        story += [_pill(f"{currency} {_money(balance)} DUE", CRIMSON, align="RIGHT"),
                  Spacer(1, 3 * mm)]

    story.append(_totals_block([
        ("Subtotal", _money(invoice.subtotal), False),
        (f"VAT ({int(current_app.config['VAT_RATE'] * 100)}%)", _money(invoice.vat), False),
        (f"Invoice total ({currency})", _money(invoice.total), True),
        ("Paid to date", _money(paid), False),
        ("Balance due", _money(balance), True),
    ], accent=accent))

    if invoice.payments:
        story += [
            Spacer(1, 6 * mm),
            _section_heading("PAYMENTS RECEIVED"),
            Spacer(1, 2 * mm),
        ]
        from reportlab.lib.units import mm as _mm
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib import colors

        rows = [[Paragraph(h, styles["cellheadright"] if index == 4 else styles["cellhead"])
                 for index, h in enumerate(
                     ["Receipt", "Date", "Method", "Reference", "Amount"])]]
        for payment in sorted(invoice.payments, key=lambda p: p.id):
            rows.append([
                Paragraph(payment.receipt_no or "—", styles["cell"]),
                Paragraph(payment.created_at.strftime("%d %b %Y"), styles["cell"]),
                Paragraph((payment.method or "").replace("_", " ").title(), styles["cell"]),
                Paragraph(payment.reference or "—", styles["cell"]),
                Paragraph(_money(payment.amount), styles["cellmoney"]),
            ])
        paid_table = Table(rows, colWidths=[30 * _mm, 24 * _mm, 30 * _mm, 46 * _mm, 24 * _mm])
        paid_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
            ("LINEBELOW", (0, 0), (-1, 0), 1.4, colors.HexColor(accent)),
            ("TOPPADDING", (0, 0), (-1, 0), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
            ("TOPPADDING", (0, 1), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 3.5),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(LINE)),
            ("LEFTPADDING", (0, 0), (0, -1), 2 * _mm),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 2 * _mm),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(TINT)]),
        ]))
        story.append(paid_table)

    story += _footer(
        "<b>Payment</b> — Cash, EcoCash, InnBucks, bank transfer or card at reception. "
        "Please quote the invoice number with any transfer. "
        f"<br/><br/><b>Warranty</b> — {WARRANTY_TEXT}"
    )
    return _render(story, f"Invoice {invoice.invoice_no}", accent=accent)


# ─────────────────────────────────────────────────────────────────────────────
# Receipt
# ─────────────────────────────────────────────────────────────────────────────
def build_receipt_pdf(payment: Payment) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A5
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

    # A receipt is handed to the customer, not filed — so it prints on A5, which
    # slips into a glovebox or a jacket pocket. Every block that was drawn to A4's
    # 174mm body has to be told the narrower frame or it runs off the sheet.
    page = _page(A5)
    width = page["width"]

    meta = [
        ("Official receipt", payment.receipt_no or f"#{payment.id}"),
        ("Date", payment.created_at.strftime("%d %b %Y")),
        ("Invoice", invoice.invoice_no if invoice else "—"),
        ("Received by", payment.user.full_name if payment.user else "Front desk"),
    ]

    story = [
        _letterhead(meta, accent=GREEN, width=width),
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
        ], width=width),
        Spacer(1, 3 * mm),
        _pill("PAID IN FULL" if settled else "PART PAYMENT",
              GREEN if settled else AMBER, align="RIGHT", width=width),
        Spacer(1, 3 * mm),
        _totals_block([
            (f"Invoice total ({currency})", _money(invoice.total if invoice else 0), False),
            ("Paid to date", _money(invoice.amount_paid if invoice else 0), False),
            ("Balance remaining", _money(payment.balance_after), True),
        ], accent=GREEN if settled else AMBER),
        Spacer(1, 4 * mm),
        Paragraph(
            "Thank you for your business." if settled
            else "The balance shown above remains due.",
            style["body"],
        ),
    ]
    story += _footer(
        "This is a computer-generated receipt. Retain it for your records and warranty claims. "
        f"<br/>{_company()['name']} · {_company()['address']} · {_company()['tel']}",
        width=width,
    )
    return _render(story, f"Receipt {payment.receipt_no or payment.id}", accent=GREEN,
                   pagesize=A5)


# ─────────────────────────────────────────────────────────────────────────────
# End-of-day report
# ─────────────────────────────────────────────────────────────────────────────
def _sheet_table(header: list[str], rows: list[list[str]], widths: list[float]):
    """Compact table used by the closing sheet — one style, repeated."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    data = [[
        Paragraph(h, styles["cellhead"]) for h in header
    ]]
    for row in rows:
        data.append([
            Paragraph(str(cell), styles["cellmoney"] if index == len(row) - 1
                      else styles["cell"])
            for index, cell in enumerate(row)
        ])

    table = Table(data, colWidths=[width * mm for width in widths], repeatRows=1,
                  hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, colors.HexColor(CRIMSON)),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 1), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(LINE)),
        ("LEFTPADDING", (0, 0), (0, -1), 2 * mm),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 2 * mm),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(TINT)]),
    ]))
    return table


def build_end_of_day_pdf(report: dict) -> bytes:
    """The closing sheet: job card statuses, bookings, the day book and the money."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    styles = _styles()
    width = _page()["width"]
    jobs = report["jobs"]
    bookings = report["bookings"]
    tasks = report["tasks"]
    money = report["money"]

    story = [
        _letterhead(
            [("Report", "End of day"),
             ("For", report["date_label"]),
             ("Generated", (report["generated_at"] or "")[:16].replace("T", " "))],
        ),
        Spacer(1, 6 * mm),

        _section_heading("JOB CARDS", width=width),
        _totals_block([
            ("Open job cards", str(jobs["open"]), False),
            ("Checked in today", str(jobs["opened"]), False),
            ("Completed today", str(jobs["completed"]), False),
            ("Collected today", str(jobs["collected"]), False),
            ("Past promised date", str(jobs["overdue"]), True),
        ], width=width, band=False),
        Spacer(1, 5 * mm),
        Paragraph("Jobs still open, by stage", styles["sub"]),
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
            Paragraph("Open job cards", styles["sub"]),
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
        _section_heading("ENQUIRIES & BOOKINGS", width=width),
        _totals_block([
            ("Raised today", str(bookings["raised"]), False),
            ("Scheduled today", str(bookings["scheduled"]), False),
            ("Handled / confirmed", str(bookings["handled"]), False),
            ("Awaiting confirmation", str(bookings["awaiting_confirmation"]), False),
            ("Jobs secured", str(bookings["secured"]), False),
            ("Walked out", str(bookings["walked_out"]), True),
        ], width=width, band=False),
        Spacer(1, 5 * mm),
        Paragraph("Today's appointments by status", styles["sub"]),
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
            Paragraph("Still awaiting confirmation", styles["sub"]),
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
            Paragraph("Today's appointments", styles["sub"]),
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
        _section_heading("TO-DO · THE DAY BOOK", width=width),
        _totals_block([
            ("Open", str(tasks["open"]), False),
            ("In progress", str(tasks["doing"]), False),
            ("Blocked", str(tasks["blocked"]), False),
            ("Due today", str(tasks["due_today"]), False),
            ("Past due", str(tasks["overdue"]), True),
        ], width=width, band=False),
        Spacer(1, 5 * mm),
        Paragraph("Open work by status", styles["sub"]),
        Spacer(1, 2 * mm),
        _sheet_table(
            ["Status", "Tasks"],
            [[row["label"], row["count"]] for row in tasks["by_status"]],
            [60, 20],
        ),
    ]

    if tasks["by_custodian"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Who is carrying what", styles["sub"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Custodian", "Open", "Done today", "Past due"],
                [[row["name"], row["open"], row["done"], row["overdue"]]
                 for row in tasks["by_custodian"][:40]],
                [86, 22, 28, 24],
            ),
        ]

    if tasks["list"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Open to-do list", styles["sub"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Activity", "Area", "Custodian", "Status", "Due", "Job card"],
                [[row["title"], row["category"] or "—", row["custodian"] or "Unassigned",
                  row["status_label"],
                  # Short marker, long column: "Past due · 2026-09-24" fits on one
                  # line at this width where "Past due — 2026-09-24" wrapped.
                  (f"Past due · {row['due_date']}") if row["is_overdue"]
                  else (row["due_date"] or "No date"),
                  row["job_no"] or "—"]
                 for row in tasks["list"][:60]],
                [44, 18, 26, 22, 34, 22],
            ),
        ]

    if tasks["done"]:
        story += [
            Spacer(1, 6 * mm),
            # Deliberately not just "Completed today" — the job card block above
            # already uses that wording for vehicles finished.
            Paragraph("To-do completed today", styles["sub"]),
            Spacer(1, 2 * mm),
            _sheet_table(
                ["Activity", "Custodian", "Area"],
                [[row["title"], row["custodian"] or "Unassigned", row["category"] or "—"]
                 for row in tasks["done"][:60]],
                [90, 40, 40],
            ),
        ]

    story += [
        Spacer(1, 7 * mm),
        _section_heading("MONEY", width=width),
        _totals_block([
            ("Invoiced today", _money(money["invoiced"]), False),
            ("Received today", _money(money["collected"]), False),
            ("Invoices settled today", str(money["paid_today_count"]), False),
            ("Outstanding on unpaid invoices", _money(money["outstanding"]), False),
            ("Unpaid invoices", str(money["unpaid_count"]), False),
            ("Overdue", f"{money['overdue_count']} · {_money(money['overdue_total'])}", True),
        ], width=width, band=False),
    ]

    if money["by_method"]:
        story += [
            Spacer(1, 6 * mm),
            Paragraph("Takings by method", styles["sub"]),
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
            Paragraph("Receipts issued today", styles["sub"]),
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
            Paragraph("Unpaid / part-paid invoices", styles["sub"]),
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
            _section_heading("STAFF", width=width),
            _sheet_table(
                ["Name", "Role", "Confirmed", "Attended", "Open jobs", "To-do", "Done today"],
                [[row["name"], (row["role"] or "").title(), row["confirmed"],
                  row["attended"], row["open_jobs"], row["open_tasks"], row["done_tasks"]]
                 for row in report["staff"]],
                [40, 22, 22, 20, 22, 22, 22],
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
