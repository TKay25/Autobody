"""Quotation / invoice / receipt documents and WhatsApp delivery."""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import (
    ActivityLog,
    Customer,
    Estimate,
    JobCard,
    NotificationLog,
    Payment,
    Vehicle,
    WaMessage,
)
from app.services import documents, intent_router, notifications
from app.services.whatsapp_client import get_or_create_conversation


@pytest.fixture(autouse=True)
def _simulator(monkeypatch):
    """Delivery must never hit the network during tests."""
    monkeypatch.setenv("WA_MODE", "simulator")


# ── helpers ──────────────────────────────────────────────────────────────────
def _job_with_estimate(client, *, reg="DOC111", insurance=False):
    res = client.post("/api/jobs", json={
        "customer_name": "Doc Tester",
        "customer_phone": "+263771234567",
        "reg_no": reg,
        "panels": ["Front Bumper", "Bonnet"],
        "is_insurance": insurance,
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
        assert f"a_approve:{est_id}" in buttons, f"approve/decline buttons missing: {buttons}"
        assert f"a_decline:{est_id}" in buttons
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
