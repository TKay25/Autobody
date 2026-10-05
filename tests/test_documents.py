"""Quotation / invoice / receipt documents and WhatsApp delivery."""
from __future__ import annotations

import base64
import re
import zlib
from datetime import date
from pathlib import Path

import pytest

from app.extensions import db
from app.models import (
    ActivityLog,
    Customer,
    Estimate,
    Invoice,
    JobCard,
    NotificationLog,
    Payment,
    Vehicle,
    WaMessage,
)
from app.services import documents, intent_router, notifications, reporting
from app.services.whatsapp_client import get_or_create_conversation

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _simulator(monkeypatch):
    """Delivery must never hit the network during tests."""
    monkeypatch.setenv("WA_MODE", "simulator")


# ── helpers ──────────────────────────────────────────────────────────────────
def _job_with_estimate(client, *, reg="DOC111"):
    res = client.post("/api/jobs", json={
        "customer_name": "Doc Tester",
        "customer_phone": "+263771234567",
        "reg_no": reg,
        "panels": ["Front Bumper", "Bonnet"],
    })
    assert res.status_code == 201, res.get_json()
    return res.get_json()["job"]


def _ensure_invoice(client, job_id):
    res = client.post(f"/api/jobs/{job_id}/invoice", json={})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["invoice"]


# ── model behaviour ──────────────────────────────────────────────────────────
def test_new_estimates_get_a_public_token_and_expiry(app):
    client = app.test_client()
    client.post("/auth/login", json={"email": "owner@topclass.co.zw", "password": "topclass123"})
    _job_with_estimate(client)
    with app.app_context():
        estimate = Estimate.query.first()
        assert estimate.public_token and len(estimate.public_token) >= 20
        assert estimate.valid_days == 14
        assert estimate.expires_on == (estimate.created_at.date()
                                       + __import__("datetime").timedelta(days=14))
        assert estimate.is_expired is False


def test_receipt_numbers_are_sequential(app, auth_client):
    job = _job_with_estimate(auth_client, reg="RCT111")
    invoice = _ensure_invoice(auth_client, job["id"])
    a = auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 10})
    b = auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 10})
    assert a.status_code == 200 and b.status_code == 200
    first = a.get_json()["payment"]["receipt_no"]
    second = b.get_json()["payment"]["receipt_no"]
    assert first.startswith("RCT-") and second.startswith("RCT-")
    assert first != second
    assert int(second.rsplit("-", 1)[1]) == int(first.rsplit("-", 1)[1]) + 1


def test_payment_reports_payer_and_balance_after(app, auth_client):
    job = _job_with_estimate(auth_client, reg="BAL222")
    invoice = _ensure_invoice(auth_client, job["id"])
    res = auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 25})
    payment = res.get_json()["payment"]
    assert payment["payer_name"] == "Doc Tester"
    assert payment["is_fully_settled"] is False
    assert payment["balance_after"] == round(invoice["total"] - 25, 2)


# ── PDF building ─────────────────────────────────────────────────────────────
def test_pdf_builder_returns_real_pdfs(app, auth_client):
    job = _job_with_estimate(auth_client, reg="PDF333")
    invoice = _ensure_invoice(auth_client, job["id"])
    auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 5})

    with app.app_context():
        from app.models import Invoice
        est = Estimate.query.first()
        inv = db.session.get(Invoice, invoice["id"])
        pay = Payment.query.first()
        for kind, record in (("quote", est), ("invoice", inv), ("receipt", pay)):
            payload, filename = documents.build_for(kind, record)
            assert payload[:4] == b"%PDF", kind
            assert len(payload) > 1500, kind
            assert filename.endswith(".pdf"), kind


def test_pdf_endpoints_are_protected_and_typed(auth_client):
    job = _job_with_estimate(auth_client, reg="PDF444")
    invoice = _ensure_invoice(auth_client, job["id"])
    est_id = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]["estimate"]["id"]

    for url in (f"/api/estimates/{est_id}/pdf",
                f"/api/invoices/{invoice['id']}/pdf"):
        res = auth_client.get(url)
        assert res.status_code == 200, url
        assert res.headers["Content-Type"] == "application/pdf"
        assert res.data[:4] == b"%PDF"
        # Served inline so the browser previews it; the filename is still set.
        assert "filename=" in res.headers.get("Content-Disposition", "")

    assert auth_client.get("/api/estimates/999999/pdf").status_code == 404


# ── paper size ───────────────────────────────────────────────────────────────
def _page_size(pdf: bytes) -> tuple[float, float]:
    match = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf)
    assert match, "the PDF declares no MediaBox"
    return float(match.group(1)), float(match.group(2))


def _pdf_text(pdf: bytes) -> str:
    """Readable text out of a Platypus PDF.

    ReportLab writes each page stream as ASCII85 *around* Flate, so the strings
    are not greppable in the raw bytes — both layers have to come off first.
    Without this a test can only assert that "a PDF came back", which would pass
    just as happily on an empty page.
    """
    out = []
    for chunk in re.findall(rb"stream(.*?)endstream", pdf, re.S):
        body = re.sub(rb"\s", b"", chunk.strip(b"\r\n")).rstrip(b"~>")
        try:
            out.append(zlib.decompress(base64.a85decode(body, adobe=False)))
        except Exception:      # a stream that is not encoded this way
            out.append(chunk)
    return b"\n".join(out).decode("latin-1", "replace")


def _receipt_payment(auth_client, *, reg="A5REC"):
    job = _job_with_estimate(auth_client, reg=reg)
    invoice = _ensure_invoice(auth_client, job["id"])
    auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 5})


def test_the_receipt_prints_on_a5(app, auth_client):
    """A receipt is handed to the customer, so it is cut down to A5.

    An A4 receipt is mostly white space and does not fit a glovebox or a pocket.
    """
    from reportlab.lib.pagesizes import A5

    _receipt_payment(auth_client)
    with app.app_context():
        receipt = documents.build_receipt_pdf(Payment.query.first())

    width, height = _page_size(receipt)
    assert abs(width - A5[0]) < 1, (width, height)
    assert abs(height - A5[1]) < 1, (width, height)


def test_the_receipt_fits_its_narrower_sheet(app, auth_client):
    """The shared blocks were drawn to A4's 174mm body.

    A5 leaves 124mm, so anything that assumed the wider frame used to run off
    the paper. Every column must now add up to the frame or less.
    """
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.units import mm

    geometry = documents._page(A5)
    assert geometry["width"] < 130 * mm

    # The wide blocks are told the frame width and must scale into it.
    letterhead = documents._letterhead([("Receipt", "RCT-1")], width=geometry["width"])
    assert abs(sum(letterhead._argW) - geometry["width"]) < 0.5
    # Two pairs, as the real receipt passes — a single cell collapses the table
    # to one column and would prove nothing about the split.
    parties = documents._party_block(
        [("Payment", [("Method", "Cash")]), ("Against", [("Invoice", "INV-1")])],
        width=geometry["width"],
    )
    assert abs(sum(parties._argW) - geometry["width"]) < 0.5


def _ink_extent(pdf: bytes) -> tuple[float, float]:
    """Left- and right-most x that anything is drawn at, in points.

    The failure this exists to catch: a block laid out against A4's 174mm body
    keeps drawing past the edge of a narrower sheet. ReportLab does not complain
    about that — it just puts ink on the paper.
    """
    low, high = float("inf"), -float("inf")
    for chunk in re.findall(rb"stream(.*?)endstream", pdf, re.S):
        body = re.sub(rb"\s", b"", chunk.strip(b"\r\n")).rstrip(b"~>")
        try:
            src = zlib.decompress(base64.a85decode(body, adobe=False)).decode("latin-1")
        except Exception:
            continue
        for pattern in (r"([\d.]+)\s+([\d.]+)\s+(?:l|m)\b",
                        r"1 0 0 1 ([\d.]+) ([\d.]+) (?:cm|Tm)"):
            for match in re.finditer(pattern, src):
                x = float(match.group(1))
                low, high = min(low, x), max(high, x)
    return low, high


def test_the_receipt_draws_nothing_past_the_edge_of_the_sheet(app, auth_client):
    """The A5 body is 124mm against A4's 174mm, so the shared blocks had to learn
    to scale. Anything that did not would still draw, just off the paper.
    """
    from reportlab.lib.units import mm

    _receipt_payment(auth_client, reg="EDGE1")
    with app.app_context():
        receipt = documents.build_receipt_pdf(Payment.query.first())

    width, _height = _page_size(receipt)
    low, high = _ink_extent(receipt)
    assert low >= 0, f"ink starts off the left edge at {low}"
    assert high <= width, f"ink runs to {high} on a {width:.1f}pt page"
    # And it actually uses the sheet rather than collapsing into a narrow column.
    assert high > width - 30 * mm, f"the receipt only draws out to {high}"


def _page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/MediaBox", pdf))


def _documents(app, auth_client, *, reg="A5ALL"):
    """A quotation, invoice and receipt built from one real job."""
    from app.models import Invoice

    job = _job_with_estimate(auth_client, reg=reg)
    invoice = _ensure_invoice(auth_client, job["id"])
    auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 5})
    with app.app_context():
        return {
            "quote": documents.build_quotation_pdf(Estimate.query.first()),
            "invoice": documents.build_invoice_pdf(Invoice.query.first()),
            "receipt": documents.build_receipt_pdf(Payment.query.first()),
        }


def test_all_three_customer_documents_print_on_a5(app, auth_client):
    """Quotation, invoice and receipt are all handed to the customer.

    A5 is a sheet that fits a glovebox or a folder pocket, which is where these
    actually end up. The three must agree on the paper or the set looks improvised.
    """
    from reportlab.lib.pagesizes import A5

    for kind, pdf in _documents(app, auth_client).items():
        width, height = _page_size(pdf)
        assert abs(width - A5[0]) < 1, (kind, width, height)
        assert abs(height - A5[1]) < 1, (kind, width, height)


def test_quotations_and_invoices_draw_nothing_past_the_edge_of_the_sheet(app, auth_client):
    """The wide blocks are drawn to A4's 174mm body, and A5 leaves 124mm.

    ReportLab does not object to a block that overruns the sheet — it just puts
    ink past the paper — so the only way to catch it is to measure where the ink
    actually lands. The receipt has its own test; the other two need the same
    proof, because they carry the item table and the payments ledger.
    """
    from reportlab.lib.units import mm

    for kind, pdf in _documents(app, auth_client, reg="A5EDGE").items():
        width, _height = _page_size(pdf)
        low, high = _ink_extent(pdf)
        assert low >= 0, f"{kind} ink starts off the left edge at {low}"
        assert high <= width, f"{kind} ink runs to {high} on a {width:.1f}pt page"
        # And it uses the sheet rather than collapsing into a narrow column.
        assert high > width - 30 * mm, f"{kind} only draws out to {high}"


def test_a_short_quotation_stays_on_one_sheet(app, auth_client):
    """A5 holds 186mm of body against A4's 266mm, so the shared blocks had to
    give up some of their air. A three-line quotation is the common case and it
    has to be one sheet — a second sheet carrying only the signature lines is
    worse than a slightly tighter first one.
    """
    from app.models import EstimateItem

    job = _job_with_estimate(auth_client, reg="A5ONE")
    with app.app_context():
        estimate = Estimate.query.first()
        # The panels on the job already raised lines; this test is about three.
        estimate.items.clear()
        db.session.flush()
        for index in range(3):
            estimate.items.append(EstimateItem(
                kind="LABOUR", description=f"Panel beating - panel {index + 1}",
                quantity=3.5, unit="hrs", unit_price=45,
            ))
        db.session.commit()

        assert len(estimate.items) == 3
        assert _page_count(documents.build_quotation_pdf(estimate)) == 1


def test_the_totals_panel_keeps_its_half_width(app):
    """_fit scales a block to fill the frame, which is right for the letterhead
    and the item table and wrong for this panel.

    The totals are a deliberate 86mm half-block set to the right. Handing it the
    A5 frame stretched it to the full 124mm and made every document's summary
    span the sheet like a second masthead.
    """
    from reportlab.lib.units import mm

    with app.app_context():
        panel = documents._totals_block(
            [("Subtotal", "1.00", False), ("Total (USD)", "1.00", True)],
        )
        assert abs(sum(panel._argW) - 86 * mm) < 0.5


def test_the_totals_panel_is_tighter_on_a_narrow_sheet(app):
    """The compact metrics are the only reason a short document fits one sheet."""
    lines = [(f"Line {index}", "1.00", index == 5) for index in range(6)]
    with app.app_context():
        roomy = documents._totals_block(lines)
        tight = documents._totals_block(lines, narrow=True)

        assert tight.wrap(351.5, 527.2)[1] < roomy.wrap(351.5, 527.2)[1]


def test_the_letterhead_still_spans_the_a5_frame(app):
    """The counterpart to the totals panel: anything that *should* fill the sheet
    still does, or the fix for one block would quietly break the other.
    """
    from reportlab.lib.pagesizes import A5

    geometry = documents._page(A5)
    with app.app_context():
        letterhead = documents._letterhead([("Quotation", "QT-1")],
                                           width=geometry["width"])
    assert abs(sum(letterhead._argW) - geometry["width"]) < 0.5


# ── the download button ──────────────────────────────────────────────────────
def _record_sends(monkeypatch, module):
    """Swap the WhatsApp client in ``module`` for one that writes the calls down.

    The row keeps the body the customer reads, not the payload that produced it,
    so the only way to assert what was actually offered is to watch the call.
    """
    calls: list[tuple[str, tuple, dict]] = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            def record(*args, **kwargs):
                calls.append((name, args, kwargs))
                return {"simulated": True}
            return record

    monkeypatch.setattr(module, "WhatsAppClient", Recorder)
    return calls


def _button_rows(calls) -> list[list[dict]]:
    """The button rows that were sent, in order: send_buttons(to, body, buttons)."""
    return [call[1][2] for call in calls if call[0] == "send_buttons"]


def _delivered_documents(app, auth_client, *, reg="DLBTN"):
    """Send all three documents and report their ids, tokens and buttons."""
    from app.models import Invoice

    job = _job_with_estimate(auth_client, reg=reg)
    created = _ensure_invoice(auth_client, job["id"])
    auth_client.post(f"/api/invoices/{created['id']}/payment", json={"amount": 5})

    with app.app_context():
        job_card = JobCard.query.first()
        estimate = Estimate.query.first()
        invoice = Invoice.query.first()
        payment = Payment.query.first()
        # Open the 24h service window. These tests are about the free-form path;
        # with the window shut everything correctly goes as a template instead,
        # and there are no ids in a template's buttons to assert on.
        from app.models import utcnow
        from app.services.whatsapp_client import get_or_create_conversation

        conversation = get_or_create_conversation(job_card.customer.wa_number,
                                                  profile_name=job_card.customer.name)
        conversation.last_inbound_at = utcnow()
        db.session.commit()

        notifications.send_quotation(estimate)
        notifications.send_invoice(job_card, invoice)
        notifications.send_receipt(payment)

        return {
            "customer": Customer.query.first(),
            "documents": {
                "quote": (estimate.id, estimate.public_token, estimate.reference),
                "invoice": (invoice.id, invoice.public_token, invoice.invoice_no),
                "receipt": (payment.id, payment.public_token, payment.receipt_no),
            },
        }


def test_every_customer_document_offers_a_download_button(app, auth_client, monkeypatch):
    """The PDF is sent once, when the document is raised.

    By the time somebody wants it again it has scrolled away, so each of the
    three carries its own Download button that fetches the file on demand. The
    payloads are bare — the same ones the approved templates send — because a
    template's buttons cannot carry a record id.
    """
    from app.services import notifications as service

    calls = _record_sends(monkeypatch, service)
    _delivered_documents(app, auth_client)
    offered = [button["id"] for row in _button_rows(calls) for button in row]

    for payload in ("doc_quote", "doc_invoice", "doc_receipt"):
        assert payload in offered, (payload, offered)


def test_the_quotation_button_row_stays_within_metas_limit(app, auth_client, monkeypatch):
    """Approve, Decline and Download quotation is exactly Meta's ceiling of three.

    A fourth button is rejected outright by the API, so the download has to ride
    along with the decision rather than follow it in its own message.
    """
    from app.services import notifications as service

    calls = _record_sends(monkeypatch, service)
    _delivered_documents(app, auth_client, reg="DLBTN3")

    decision_rows = [row for row in _button_rows(calls)
                     if any(b["id"].startswith("a_approve") for b in row)]
    assert len(decision_rows) == 1, decision_rows
    row = decision_rows[0]
    assert len(row) <= 3, row
    assert [b["id"] for b in row] == ["a_approve", "a_decline", "doc_quote"], row


def test_tapping_download_answers_with_the_document(app, auth_client):
    """The reply says what it is doing, then hands over the file.

    Nothing is attached in the reply itself: Meta fetches the link when it
    delivers the message, and the /doc route builds the PDF on demand.
    """
    built = _delivered_documents(app, auth_client, reg="DLTAP")
    labels = {"quote": "Quotation", "invoice": "Invoice", "receipt": "Receipt"}

    for kind, (record_id, token, number) in built["documents"].items():
        with app.app_context():
            conversation = get_or_create_conversation(built["customer"].wa_number)
            replies = intent_router.handle_inbound(
                conversation, interactive_id=f"doc:{kind}:{record_id}")

        assert replies[0]["type"] == "text", replies
        assert "Generating" in replies[0]["body"], replies[0]

        document = replies[1]
        assert document["type"] == "document", replies
        assert document["filename"] == f"{labels[kind]}-{number}.pdf"
        assert document["link"].endswith(f"/doc/{kind}/{token}.pdf")


def test_the_downloaded_link_serves_the_real_document(app, auth_client):
    """The link the customer taps has to resolve for somebody with no login.

    Meta fetches it from its own servers, so a link that only works inside a
    session would leave the customer with a button that does nothing.
    """
    from reportlab.lib.pagesizes import A5

    built = _delivered_documents(app, auth_client, reg="DLLINK")
    anonymous = app.test_client()          # the customer's phone, no session

    for kind, (_record_id, token, _number) in built["documents"].items():
        res = anonymous.get(f"/doc/{kind}/{token}.pdf")
        assert res.status_code == 200, kind
        assert res.data[:4] == b"%PDF", kind
        width, height = _page_size(res.data)
        assert abs(width - A5[0]) < 1, (kind, width, height)
        assert abs(height - A5[1]) < 1, (kind, width, height)


def test_a_download_button_for_a_vanished_document_says_so(app, auth_client):
    """A button outlives the thread it was sent in.

    Tapping one for a document that has since gone must say so and offer a way
    out — not crash, and not leak a link to whatever now holds that row id.
    """
    built = _delivered_documents(app, auth_client, reg="DLGONE")

    with app.app_context():
        conversation = get_or_create_conversation(built["customer"].wa_number)
        replies = intent_router.handle_inbound(
            conversation, interactive_id="doc:invoice:999999")

    assert [r["type"] for r in replies] == ["text"], replies
    assert "no longer available" in replies[0]["body"]
    assert "menu" in replies[0]["body"].lower()


def test_a_malformed_download_button_is_not_a_crash(app, auth_client):
    """Ids arrive from outside, so a bad one has to degrade rather than raise.

    An unusable id gets silence, which is the safe answer: there is nothing
    useful to say, and saying nothing cannot mislead anybody.
    """
    built = _delivered_documents(app, auth_client, reg="DLBAD")

    with app.app_context():
        conversation = get_or_create_conversation(built["customer"].wa_number)
        for bad in ("doc:receipt:abc", "doc:widget:1", "doc:", "doc:quote:1:2"):
            replies = intent_router.handle_inbound(conversation, interactive_id=bad)
            # Whatever it does, it must never hand out a file on a bad id.
            assert all(r.get("type") != "document" for r in replies), (bad, replies)


def test_the_webhook_sends_the_document_the_customer_asked_for(app, client, auth_client,
                                                              monkeypatch):
    """The button tap has to reach the send loop, not just the router.

    The router returning a document reply is only half of it: the webhook has to
    know how to put one on the wire.
    """
    from app.views import whatsapp as webhook

    built = _delivered_documents(app, auth_client, reg="DLHOOK")
    record_id = built["documents"]["receipt"][0]
    wa_id = built["customer"].wa_number
    calls = _record_sends(monkeypatch, webhook)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": wa_id, "profile": {"name": "Downloader"}}],
                    "messages": [{
                        "from": wa_id,
                        "id": "wamid.DOWNLOADTAP",
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {"id": f"doc:receipt:{record_id}",
                                             "title": "Download"},
                        },
                    }],
                },
            }],
        }],
    }
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 200

    documents = [call for call in calls if call[0] == "send_document"]
    assert documents, calls
    _name, args, _kwargs = documents[0]
    assert args[1].endswith(f"/doc/receipt/{built['documents']['receipt'][1]}.pdf"), args
    assert args[2].startswith("Receipt-") and args[2].endswith(".pdf"), args

    spoken = [call[1][1] for call in calls if call[0] == "send_text"]
    assert any("Generating" in body for body in spoken), calls


def test_the_closing_sheet_carries_the_day_book(app, auth_client):
    """The end-of-day sheet is a handover document.

    The work nobody has finished yet matters as much as the money, so the to-do
    list, its statuses and who is carrying them all belong on the paper.
    """
    auth_client.post("/api/tasks", json={
        "title": "Chase the bumper supplier", "category": "Parts",
        "due_date": date.today().isoformat(),
    })

    with app.app_context():
        sheet = documents.build_end_of_day_pdf(reporting.end_of_day())

    text = _pdf_text(sheet)
    for heading in ("TO-DO", "THE DAY BOOK", "Open work by status",
                    "Who is carrying what", "Open to-do list"):
        assert heading in text, f"the closing sheet is missing {heading!r}"
    # And the work itself, not just the headings.
    assert "Chase the bumper supplier" in text
    assert "Parts" in text


def test_the_closing_sheet_hides_an_empty_day_book(app, auth_client):
    """With nothing on the list the tables are dropped, not printed empty."""
    with app.app_context():
        sheet = documents.build_end_of_day_pdf(reporting.end_of_day())

    text = _pdf_text(sheet)
    assert "TO-DO" in text              # the status breakdown always shows
    assert "Open work by status" in text
    assert "Open to-do list" not in text
    assert "To-do completed today" not in text


def test_finished_work_is_listed_under_its_own_heading(app, auth_client):
    """The job card block already says "Completed today" for vehicles.

    The to-do table needs its own wording or the closing sheet reads as though
    the same figure appears twice.
    """
    task = auth_client.post("/api/tasks", json={"title": "Chase the bumper supplier"})
    task_id = task.get_json()["task"]["id"]
    auth_client.patch(f"/api/tasks/{task_id}", json={"status": "DONE"})

    with app.app_context():
        sheet = documents.build_end_of_day_pdf(reporting.end_of_day())

    text = _pdf_text(sheet)
    assert "To-do completed today" in text
    assert "Chase the bumper supplier" in text   # listed as finished, not open
    assert "Open to-do list" not in text


def test_a_status_stamp_sizes_itself_to_its_text(app):
    """A stamp that fills the sheet is a banner, not a stamp.

    It used to: the width was taken from a Paragraph, which reports the width it
    was *given* rather than the width it needs, so every stamp came out 1000mm
    wide and stretched edge to edge.
    """
    from reportlab.lib.units import mm

    with app.app_context():
        short = documents._pill("PAID", documents.GREEN)
        long_ = documents._pill("PART PAID · USD 1,234.56 OUTSTANDING", documents.AMBER)

    assert short._argW[0] < 60 * mm
    assert short._argW[0] < long_._argW[0] < 120 * mm


def test_a_long_stamp_is_capped_to_the_frame(app):
    """...but a stamp longer than the page must not run off it."""
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.units import mm

    frame = documents._page(A5)["width"]
    with app.app_context():
        stamp = documents._pill("X" * 400, documents.CRIMSON, width=frame)
    assert stamp._argW[0] <= frame


def _row_background(table, row: int) -> str:
    """The background colour a table applies to ``row``, as a bare hex string."""
    for _command, start, end, colour in table._bkgrndcmds:
        if start[1] <= row <= end[1]:
            return colour.hexval()[2:].lower()
    return ""


def test_report_tables_hug_the_left_margin(app):
    """A Table with no alignment centres itself.

    On the closing sheet that floated the narrow tables — the stage counts, the
    takings by method — into the middle of the page while every other block sat
    against the margin.
    """
    with app.app_context():
        table = documents._sheet_table(["Stage", "Jobs"], [["Intake", 6]], [60, 20])
    assert table.hAlign == "LEFT"


def test_a_stat_block_can_close_without_an_alarm_band(app):
    """`Overdue 0` in a crimson band reads as a problem when there is none.

    The closing sheet is full of figures that are not badges, so it closes each
    block with a tint instead of a solid accent fill.
    """
    with app.app_context():
        banded = documents._totals_block([("Overdue", "0", True)])
        quiet = documents._totals_block([("Overdue", "0", True)], band=False)

    assert _row_background(banded, 0) == documents.CRIMSON.lstrip("#").lower()
    assert _row_background(quiet, 0) == documents.TINT_STRONG.lstrip("#").lower()


def test_the_letterhead_carries_no_shouted_title(app):
    """The number and dates already identify the document.

    A second "TAX INVOICE" above "Invoice INV-…" is noise, and on the printed
    page it was the loudest thing on the sheet. The band promotes the first meta
    line — the identifier somebody actually looks for — instead.
    """
    with app.app_context():
        table = documents._letterhead([("Invoice", "INV-2026-0001")])
    right_cell = table._cellvalues[0][1]
    text = " ".join(flowable.text for flowable in right_cell)
    assert "INV-2026-0001" in text
    assert not re.search(r"TAX INVOICE|QUOTATION|RECEIPT|END OF DAY", text)


def test_no_pdf_builder_passes_a_shouted_heading():
    """Guard every builder, not just the helper's default.

    Matched against the call sites rather than the whole file, so the comment
    explaining *why* the heading is optional does not trip the guard.
    """
    source = (ROOT / "app" / "services" / "documents.py").read_text(encoding="utf-8")
    # Call sites only — the definition starts with `def ` so it is skipped.
    calls = re.findall(r"^\s*_letterhead\([^)]*", source, re.MULTILINE)
    assert len(calls) == 4, f"expected four letterhead calls, found {len(calls)}"
    for call in calls:
        assert not re.search(r"['\"](TAX INVOICE|QUOTATION|RECEIPT|END OF DAY)['\"]", call), call


def test_the_document_page_does_not_repeat_its_kind(client, auth_client, app):
    job = _job_with_estimate(auth_client, reg="TITLE1")
    _ensure_invoice(auth_client, job["id"])
    with app.app_context():
        from app.models import Invoice
        token = Invoice.query.first().public_token

    html = client.get(f"/doc/invoice/{token}").get_data(as_text=True)
    assert 'class="doc-title"' not in html
    # The document number still says what it is, and the tab keeps the full name.
    assert "INV-" in html
    assert "<title>Tax invoice " in html


# ── public document routes ───────────────────────────────────────────────────
def test_public_document_pages_need_no_login(client, app, auth_client):
    job = _job_with_estimate(auth_client, reg="PUB555")
    invoice = _ensure_invoice(auth_client, job["id"])
    auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 7})

    with app.app_context():
        from app.models import Invoice
        tokens = {
            "quote": Estimate.query.first().public_token,
            "invoice": db.session.get(Invoice, invoice["id"]).public_token,
            "receipt": Payment.query.first().public_token,
        }

    for kind, token in tokens.items():
        page = client.get(f"/doc/{kind}/{token}")
        assert page.status_code == 200, kind
        html = page.get_data(as_text=True)
        assert "Topclass Auto Body" in html
        assert f"/doc/{kind}/{token}.pdf" in html

        pdf = client.get(f"/doc/{kind}/{token}.pdf")
        assert pdf.status_code == 200, kind
        assert pdf.headers["Content-Type"] == "application/pdf"
        assert pdf.data[:4] == b"%PDF"


def test_public_document_routes_reject_junk(client, auth_client, app):
    job = _job_with_estimate(auth_client, reg="JNK666")
    with app.app_context():
        token = Estimate.query.first().public_token
    assert job

    assert client.get("/doc/wibble/abcdefghijklmnopqrst").status_code == 404
    assert client.get("/doc/quote/short").status_code == 404
    assert client.get("/doc/quote/aaaaaaaaaaaaaaaaaaaa").status_code == 404
    assert client.get(f"/doc/quote/{token}").status_code == 200


def test_blank_slate_client_can_open_documents(client):
    """Customer-facing document routes must never ask for a staff session."""
    assert client.get("/doc/quote/aaaaaaaaaaaaaaaaaaaa").status_code == 404


def test_share_links_endpoint(client, auth_client, app):
    job = _job_with_estimate(auth_client, reg="LNK777")
    invoice = _ensure_invoice(auth_client, job["id"])
    assert invoice
    with app.app_context():
        est_id = Estimate.query.first().id
    res = auth_client.get(f"/api/documents/links/quote/{est_id}")
    assert res.status_code == 200
    body = res.get_json()
    assert body["view"].endswith(".pdf") is False
    assert body["pdf"].endswith(".pdf")

    assert auth_client.get(f"/api/documents/links/wibble/{est_id}").status_code == 404
    assert auth_client.get("/api/documents/links/quote/999999").status_code == 404


def test_share_links_need_authentication():
    """A brand-new app with no session must reject the internal links API."""
    from app import create_app
    from config import TestConfig

    fresh = create_app(TestConfig)
    with fresh.app_context():
        db.create_all()
        anon = fresh.test_client()
        assert anon.get("/api/documents/links/quote/1").status_code == 401
        db.drop_all()


# ── WhatsApp delivery ────────────────────────────────────────────────────────
def test_sending_a_quotation_logs_a_document_message(app, auth_client):
    job = _job_with_estimate(auth_client, reg="SND888")
    with app.app_context():
        est_id = Estimate.query.first().id
        # The customer has spoken recently, so the free-form document is allowed.
        from app.models import utcnow
        from app.services.whatsapp_client import get_or_create_conversation

        customer = Customer.query.first()
        conversation = get_or_create_conversation(customer.wa_number,
                                                  profile_name=customer.name)
        conversation.last_inbound_at = utcnow()
        db.session.commit()

    res = auth_client.post(f"/api/estimates/{est_id}/send", json={})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["result"]["sent"] is True
    assert body["estimate"]["status"] == "SENT"

    with app.app_context():
        doc = WaMessage.query.filter_by(direction="outbound", msg_type="document").first()
        assert doc is not None, "no outbound document message logged"
        assert doc.payload.get("link", "").endswith(".pdf")
        assert doc.payload.get("filename", "").endswith(".pdf")

        buttons = [
            b["id"]
            for m in WaMessage.query.filter_by(direction="outbound", msg_type="interactive").all()
            for b in (m.payload.get("buttons") or [])
        ]
        # Bare payloads, matching the approved `quotation_share` template. They
        # carry no estimate id — a template's buttons cannot — so the sending code
        # records the estimate on the thread and a tap resolves against that.
        assert buttons == ["a_approve", "a_decline", "doc_quote"], buttons
        conversation = get_or_create_conversation("+263771234567")
        assert conversation.ctx_get("last_estimate_id") == est_id
        assert ActivityLog.query.filter_by(action="estimate.sent").count() == 1


def test_sending_an_invoice_and_a_receipt(app, auth_client):
    job = _job_with_estimate(auth_client, reg="SND999")
    invoice = _ensure_invoice(auth_client, job["id"])
    res = auth_client.post(f"/api/invoices/{invoice['id']}/send", json={})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["result"]["sent"] is True

    pay = auth_client.post(f"/api/invoices/{invoice['id']}/payment",
                           json={"amount": 12, "send_receipt": True})
    assert pay.status_code == 200
    assert pay.get_json()["receipt_sent"]["sent"] is True
    payment_id = pay.get_json()["payment"]["id"]

    res = auth_client.post(f"/api/payments/{payment_id}/receipt/send", json={})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["result"]["sent"] is True

    with app.app_context():
        assert ActivityLog.query.filter_by(action="receipt.sent").count() >= 2


def test_payments_register_lists_receipts(auth_client):
    job = _job_with_estimate(auth_client, reg="REG101")
    invoice = _ensure_invoice(auth_client, job["id"])
    auth_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 3})

    res = auth_client.get("/api/payments")
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == 1
    assert body["items"][0]["receipt_no"].startswith("RCT-")

    filtered = auth_client.get(f"/api/payments?invoice_id={invoice['id']}").get_json()
    assert filtered["count"] == 1
    assert auth_client.get("/api/payments?invoice_id=999999").get_json()["count"] == 0


# ── approve / decline from the chatbot ───────────────────────────────────────
@pytest.mark.parametrize("tap,expected", [
    ("a_approve:{id}", "APPROVED"),
    ("a_decline:{id}", "DECLINED"),
])
def test_button_tap_updates_the_quotation(app, tap, expected):
    with app.app_context():
        customer = Customer(name="Tap Tester", phone="+263 77 111 9999")
        db.session.add(customer)
        db.session.flush()
        vehicle = Vehicle(reg_no="TAP123", make="Toyota", model="Hilux",
                          customer_id=customer.id)
        db.session.add(vehicle)
        db.session.flush()
        job = JobCard(job_no="TC-2099-0001", customer_id=customer.id,
                      vehicle_id=vehicle.id, stage="ESTIMATE",
                      service="Panel Beating & Spray Painting")
        db.session.add(job)
        db.session.flush()
        estimate = Estimate(job_id=job.id, reference="TC-EST-TEST1",
                            currency="USD", subtotal=100, vat=15, total=115,
                            status="SENT")
        db.session.add(estimate)
        db.session.commit()
        estimate_id = estimate.id
        job_id = job.id

        conv = get_or_create_conversation(customer.wa_number, customer.name)
        replies = intent_router.handle_inbound(
            conv, interactive_id=tap.format(id=estimate_id))

        assert replies, "the bot did not answer the button tap"
        assert replies[0]["type"] == "text"
        assert db.session.get(Estimate, estimate_id).status == expected
        log = NotificationLog.query.filter_by(template="quotation_decision").first()
        assert log is not None
        assert expected.capitalize() in log.body
        assert log.job_id == job_id


def test_button_tap_on_a_missing_quotation_is_handled(app):
    with app.app_context():
        conv = get_or_create_conversation("263771118888", "Ghost")
        replies = intent_router.handle_inbound(conv, interactive_id="a_approve:987654")
        assert replies
        assert "could not find" in replies[0]["body"].lower()


def test_tapping_approve_twice_does_not_re_celebrate(app):
    """Meta redelivers a tap when the webhook is slow, and customers do tap twice.

    Replying with the full celebration again reads as a second approval.
    """
    with app.app_context():
        customer = Customer(name="Twice Tapper", phone="+263 77 111 7777")
        db.session.add(customer)
        db.session.flush()
        vehicle = Vehicle(reg_no="TAP999", make="Toyota", model="Hilux",
                          customer_id=customer.id)
        db.session.add(vehicle)
        db.session.flush()
        job = JobCard(job_no="TC-2099-0002", customer_id=customer.id,
                      vehicle_id=vehicle.id, stage="ESTIMATE",
                      service="Panel Beating & Spray Painting")
        db.session.add(job)
        db.session.flush()
        estimate = Estimate(job_id=job.id, reference="TC-EST-TWICE", currency="USD",
                            subtotal=100, vat=15, total=115, status="SENT")
        db.session.add(estimate)
        db.session.commit()
        estimate_id = estimate.id

        conv = get_or_create_conversation(customer.wa_number, customer.name)
        first = intent_router.handle_inbound(conv, interactive_id=f"a_approve:{estimate_id}")
        assert "is approved" in first[0]["body"]
        assert db.session.get(Estimate, estimate_id).status == "APPROVED"

        second = intent_router.handle_inbound(conv, interactive_id=f"a_approve:{estimate_id}")
        assert "already approved" in second[0]["body"]
        assert "🎉" not in second[0]["body"], "the second tap read as a fresh approval"


# ── raising an invoice straight from the desk ────────────────────────────────
def _walk_in_customer(app, name="Walk-in Wendy", phone="+263771200001") -> int:
    """A customer with no job card behind them, for desk-raised invoices."""
    from app.models import Customer

    with app.app_context():
        customer = Customer(name=name, phone=phone, whatsapp=phone)
        db.session.add(customer)
        db.session.commit()
        return customer.id


def test_an_invoice_can_be_raised_without_a_job_card(app, auth_client):
    """Front desk must be able to bill a walk-in: Invoice.job_id is nullable."""
    customer_id = _walk_in_customer(app)

    res = auth_client.post("/api/invoices", json={
        "customer_id": customer_id,
        "description": "Full respray — Toyota Hilux",
        "subtotal": 500,
        "issue": True,
    })
    assert res.status_code == 201, res.get_data(as_text=True)
    invoice = res.get_json()["invoice"]

    assert invoice["job_id"] is None, "a desk invoice must not need a job card"
    assert invoice["invoice_no"].startswith("INV-")
    assert invoice["subtotal"] == 500
    assert invoice["vat"] == 75, "VAT is derived from VAT_RATE, never trusted from the client"
    assert invoice["total"] == 575
    assert invoice["status"] == "ISSUED"


def test_a_desk_invoice_can_be_left_as_a_draft(app, auth_client):
    customer_id = _walk_in_customer(app, "Draft Dale")

    invoice = auth_client.post("/api/invoices", json={
        "customer_id": customer_id,
        "description": "Wheel refurbishment",
        "subtotal": 100,
        "issue": False,
    }).get_json()["invoice"]

    assert invoice["status"] == "DRAFT"
    assert invoice["issued_at"] is None


def test_a_desk_invoice_prints_its_particulars(app, auth_client):
    """With no estimate behind it, the invoice's own wording is the line item.

    Otherwise the PDF is a totals block with nothing above it.
    """
    from app.models import Invoice

    customer_id = _walk_in_customer(app, "Pdf Petra")
    created = auth_client.post("/api/invoices", json={
        "customer_id": customer_id,
        "description": "Full respray — Toyota Hilux, 2 panels plus paint materials",
        "subtotal": 500,
        "issue": True,
    }).get_json()["invoice"]

    with app.app_context():
        invoice = db.session.get(Invoice, created["id"])
        pdf, filename = documents.build_for("invoice", invoice)

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500
    assert filename == f"Invoice-{created['invoice_no']}.pdf"


def test_desk_invoice_creation_is_validated(app, auth_client):
    from app.models import Invoice

    customer_id = _walk_in_customer(app, "Strict Sue")

    assert auth_client.post("/api/invoices", json={
        "description": "No customer", "subtotal": 10}).status_code == 400
    assert auth_client.post("/api/invoices", json={
        "customer_id": customer_id, "subtotal": 10}).status_code == 400
    assert auth_client.post("/api/invoices", json={
        "customer_id": customer_id, "description": "Free work", "subtotal": 0}).status_code == 400
    assert auth_client.post("/api/invoices", json={
        "customer_id": 999999, "description": "Ghost", "subtotal": 10}).status_code == 400

    with app.app_context():
        assert Invoice.query.count() == 0, "a rejected request must not leave a row behind"


# ── outside the 24-hour service window ───────────────────────────────────────
@pytest.fixture()
def payloads(monkeypatch):
    """Everything the WhatsApp client tries to put on the wire, in order.

    The message row keeps only the body and a summary, so the only way to prove
    *which* Meta message type was used is to watch the payload. Meta rejects a
    free-form message outside the service window, so this is the difference
    between a quotation arriving and a silent 400.
    """
    from app.services.whatsapp_client import WhatsAppClient

    captured: list[dict] = []
    monkeypatch.setattr(
        WhatsAppClient, "_post",
        lambda self, payload: (captured.append(payload), {"simulated": True})[1],
    )
    return captured


def _closed_window(app, auth_client, *, reg="COLD1") -> dict:
    """A job with a quotation, invoice and receipt, and a shut service window.

    The window is shut by simply never receiving an inbound message, which is the
    real state of a thread the workshop starts after the customer went quiet.
    Returns primary keys, because ORM instances do not survive the context.
    """
    from app.models import Invoice

    job = _job_with_estimate(auth_client, reg=reg)
    created = _ensure_invoice(auth_client, job["id"])
    auth_client.post(f"/api/invoices/{created['id']}/payment", json={"amount": 5})
    with app.app_context():
        return {
            "job": JobCard.query.first().id,
            "estimate": Estimate.query.first().id,
            "invoice": Invoice.query.first().id,
            "payment": Payment.query.first().id,
            "customer": Customer.query.first().id,
        }


def _load(ids: dict):
    """Re-fetch the records inside the caller's app context."""
    return (db.session.get(JobCard, ids["job"]), db.session.get(Estimate, ids["estimate"]),
            db.session.get(Invoice, ids["invoice"]), db.session.get(Payment, ids["payment"]),
            db.session.get(Customer, ids["customer"]))


def test_notifications_outside_the_window_are_never_free_form(app, auth_client, payloads):
    """Everything the workshop initiates goes out as an approved template.

    Meta refuses a free-form message more than 24 hours after the customer's last
    one, so a quotation or a receipt sent to a quiet thread would fail — and fail
    silently, because a 400 is not visible to the desk.
    """
    ids = _closed_window(app, auth_client)
    with app.app_context():
        job, estimate, invoice, payment, _customer = _load(ids)
        notifications.send_quotation(estimate)
        notifications.send_invoice(job, invoice)
        notifications.send_receipt(payment)

    assert payloads, "nothing was sent at all"
    free_form = [p for p in payloads if p.get("type") != "template"]
    assert not free_form, f"free-form outside the window: {[p.get('type') for p in free_form]}"


def test_the_pdfs_ride_on_the_templates_document_header(app, auth_client, payloads):
    """A template is the only way to deliver a PDF outside the window.

    Meta carries it as a *document header* on the approved template, so the
    template has to be built with one and the link must be publicly fetchable.
    """
    ids = _closed_window(app, auth_client, reg="COLD2")
    with app.app_context():
        job, estimate, _invoice, payment, _customer = _load(ids)
        notifications.send_quotation(estimate)
        notifications.send_receipt(payment)

    for payload in payloads:
        components = payload["template"]["components"]
        header = next((c for c in components if c["type"] == "header"), None)
        assert header, f"no document header on {payload['template']['name']}"
        document = header["parameters"][0]
        assert document["type"] == "document"
        assert document["document"]["link"].startswith("http")
        assert document["document"]["filename"].endswith(".pdf")


def test_the_quotation_gets_its_own_buttoned_template(app, auth_client, payloads):
    """Approve / Decline / Download must survive a shut window.

    A template's quick-reply buttons are fixed when it is approved — Meta cannot
    interpolate the estimate id into them — so the quotation cannot share the
    plain document template used for invoices and receipts. It gets its own.
    """
    from app.services import notifications as service

    ids = _closed_window(app, auth_client, reg="COLD3")
    with app.app_context():
        service.send_quotation(db.session.get(Estimate, ids["estimate"]))

    names = [p["template"]["name"] for p in payloads]
    assert service.TEMPLATE_QUOTATION in names, names
    # Three different documents, three different templates: each carries a button
    # naming what it fetches, and a template's buttons are frozen at approval.
    assert service.TEMPLATE_INVOICE not in {service.TEMPLATE_QUOTATION,
                                            service.TEMPLATE_RECEIPT}
    assert service.TEMPLATE_RECEIPT != service.TEMPLATE_QUOTATION


def test_the_template_buttons_resolve_against_the_last_quotation(app, auth_client, payloads):
    """The buttoned template sends bare payloads, so they must still resolve.

    The customer taps "Approve" on the *approved template*, which cannot carry
    `a_approve:<id>`. Sending the quotation records which estimate the thread is
    about, and that is what the bare payload is resolved against.
    """
    from app.services.whatsapp_client import get_or_create_conversation

    ids = _closed_window(app, auth_client, reg="COLD4")
    with app.app_context():
        job, estimate, _invoice, _payment, customer = _load(ids)
        notifications.send_quotation(estimate)
        assert estimate.status == "SENT"

        conversation = get_or_create_conversation(customer.wa_number)
        replies = intent_router.handle_inbound(conversation, interactive_id="a_approve")

        assert estimate.status == "APPROVED", replies
        assert any("approved" in (r.get("body") or "") for r in replies), replies


def test_download_still_works_from_the_templates_fixed_button(app, auth_client, payloads):
    """`doc_quote` is the template's Download payload and carries no id either."""
    from app.services.whatsapp_client import get_or_create_conversation

    ids = _closed_window(app, auth_client, reg="COLD5")
    with app.app_context():
        job, estimate, _invoice, _payment, customer = _load(ids)
        notifications.send_quotation(estimate)
        token = estimate.public_token
        conversation = get_or_create_conversation(customer.wa_number)
        replies = intent_router.handle_inbound(conversation, interactive_id="doc_quote")

    assert [r["type"] for r in replies] == ["text", "document"], replies
    assert replies[1]["link"].endswith(f"/doc/quote/{token}.pdf")


def test_a_bare_button_on_a_thread_with_no_quotation_is_not_a_crash(app):
    """A stale template button from an old thread must degrade, not raise."""
    from app.services.whatsapp_client import get_or_create_conversation

    with app.app_context():
        conversation = get_or_create_conversation("263779990001", "No Quotation")
        for tap in ("a_approve", "a_decline", "doc_quote"):
            replies = intent_router.handle_inbound(conversation, interactive_id=tap)
            assert all(r.get("type") != "document" for r in replies), (tap, replies)
