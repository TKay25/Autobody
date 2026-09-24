"""Tests for the newer surface: search, activity trail, exports and edits."""
from __future__ import annotations

from datetime import date, timedelta

from app import create_app
from app.extensions import db
from app.models import ActivityLog, Part, Vehicle
from config import Config


def _make_job(auth_client, **extra):
    payload = {
        "customer_name": "Audit Client",
        "customer_phone": "+263771990011",
        "reg_no": "AUD1234",
        "make": "Toyota",
        "model": "Hilux",
        "panels": ["Front Bumper"],
    }
    payload.update(extra)
    res = auth_client.post("/api/jobs", json=payload)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["job"]


# ── editing ──────────────────────────────────────────────────────────────────
def test_part_can_be_edited(auth_client, app):
    auth_client.post("/api/parts", json={
        "name": "Test Bracket", "sku": "TST-001", "cost_price": 10, "sell_price": 20,
    })
    with app.app_context():
        part_id = Part.query.filter_by(sku="TST-001").first().id

    res = auth_client.patch(f"/api/parts/{part_id}", json={
        "name": "Test Bracket v2", "cost_price": 12.5, "reorder_level": 7,
    })
    assert res.status_code == 200
    part = res.get_json()["part"]
    assert part["name"] == "Test Bracket v2"
    assert part["cost_price"] == 12.5
    assert part["reorder_level"] == 7


def test_vehicle_can_be_edited_and_reg_normalised(auth_client, app):
    _make_job(auth_client, reg_no="VEH1000")
    with app.app_context():
        vehicle_id = Vehicle.query.filter_by(reg_no="VEH1000").first().id

    res = auth_client.patch(f"/api/vehicles/{vehicle_id}", json={
        "reg_no": "new 4242", "colour": "Crimson", "mileage": 150000,
    })
    assert res.status_code == 200
    vehicle = res.get_json()["vehicle"]
    assert vehicle["reg_no"] == "NEW4242"
    assert vehicle["colour"] == "Crimson"
    assert vehicle["mileage"] == 150000


# ── audit trail ──────────────────────────────────────────────────────────────
def test_creating_a_job_writes_an_activity_entry(auth_client, app):
    job = _make_job(auth_client)
    res = auth_client.get("/api/activity")
    assert res.status_code == 200

    entries = res.get_json()["items"]
    created = [e for e in entries if e["action"] == "job.created"]
    assert created, "expected a job.created audit entry"
    assert created[0]["entity_ref"] == job["job_no"]
    assert created[0]["actor_name"] == "Tendai Moyo"
    assert created[0]["icon"] == "clipboard-check"


def test_stage_change_is_audited(auth_client):
    job = _make_job(auth_client, reg_no="STG2000")
    auth_client.post(f"/api/jobs/{job['id']}/stage", json={"stage": "STRIP"})

    entries = auth_client.get("/api/activity").get_json()["items"]
    moved = [e for e in entries if e["action"] == "job.stage_changed"]
    assert moved
    assert moved[0]["meta"]["to"] == "STRIP"
    assert "Strip Down" in moved[0]["summary"]


def test_activity_can_be_filtered_by_job(auth_client):
    job = _make_job(auth_client, reg_no="FLT3000")
    entries = auth_client.get(f"/api/activity?job_id={job['id']}").get_json()["items"]
    assert entries
    assert all(e["job_id"] == job["id"] for e in entries)


def test_payment_records_audit_entry(auth_client):
    job = _make_job(auth_client, reg_no="PAY4000")
    invoice = auth_client.post(f"/api/jobs/{job['id']}/invoice", json={}).get_json()["invoice"]
    auth_client.post(f"/api/invoices/{invoice['id']}/payment",
                     json={"amount": invoice["total"], "method": "ECOCASH"})

    entries = auth_client.get("/api/activity").get_json()["items"]
    assert any(e["action"] == "payment.recorded" for e in entries)


def test_activity_is_never_written_without_a_request(app):
    """log_activity must not explode outside a request context."""
    from app.services.activity import log_activity

    entry = log_activity("system.test", "no request context here")
    assert entry is not None
    assert entry.actor_name == "System"


# ── search ───────────────────────────────────────────────────────────────────
def test_search_finds_job_customer_and_vehicle(auth_client):
    job = _make_job(auth_client, reg_no="SRCH555", customer_name="Searchable Person")
    res = auth_client.get("/api/search?q=SRCH555")
    body = res.get_json()

    assert res.status_code == 200
    assert any(j["job_no"] == job["job_no"] for j in body["jobs"])
    assert any(v["reg_no"] == "SRCH555" for v in body["vehicles"])


def test_search_requires_two_characters(auth_client):
    body = auth_client.get("/api/search?q=a").get_json()
    assert body["jobs"] == [] and body["customers"] == [] and body["vehicles"] == []


# ── exports ──────────────────────────────────────────────────────────────────
def test_jobs_csv_export(auth_client):
    _make_job(auth_client, reg_no="CSV9000")
    res = auth_client.get("/api/export/jobs.csv")
    assert res.status_code == 200
    assert res.mimetype == "text/csv"
    assert "attachment" in res.headers["Content-Disposition"]
    body = res.get_data(as_text=True)
    assert "Job no" in body.splitlines()[0]
    assert "CSV9000" in body


def test_invoice_and_claim_and_parts_exports(auth_client):
    for dataset in ("invoices", "claims", "parts"):
        res = auth_client.get(f"/api/export/{dataset}.csv")
        assert res.status_code == 200, dataset
        assert res.mimetype == "text/csv"


def test_unknown_export_404s(auth_client):
    assert auth_client.get("/api/export/salaries.csv").status_code == 404


# ── CSRF is actually on ──────────────────────────────────────────────────────
def test_api_rejects_a_write_without_a_csrf_token():
    """Proves CSRF protection is real, not just configured."""
    class CsrfOn(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        WTF_CSRF_ENABLED = True
        SECRET_KEY = "csrf-test"
        WA_MODE = "simulator"

    app = create_app(CsrfOn)
    with app.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)

    client = app.test_client()
    client.post("/auth/login", json={"email": "owner@topclass.co.zw", "password": "topclass123"})

    blocked = client.post("/api/jobs", json={"customer_name": "No Token", "reg_no": "NOCSRF"})
    assert blocked.status_code == 400
    assert blocked.get_json()["error"] == "csrf_failed"

    with app.app_context():
        assert not Vehicle.query.filter_by(reg_no="NOCSRF").first()
        db.session.remove()
        db.drop_all()


def test_booking_creation_is_no_longer_csrf_exempt():
    """The exemption existed for the marketing site's widget, which is gone.

    Only the in-house screen creates bookings now, and it sends X-CSRFToken like
    every other write, so a token-less POST must be refused.
    """

    class CsrfOn(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        WTF_CSRF_ENABLED = True
        SECRET_KEY = "csrf-test-2"
        WA_MODE = "simulator"

    app = create_app(CsrfOn)
    with app.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)

    client = app.test_client()
    res = client.post("/api/bookings", json={
        "name": "Website Lead", "phone": "+263771000111",
        "service": "Car Detailing", "slot_date": (date.today() + timedelta(days=1)).isoformat(),
    })
    assert res.status_code == 400
    assert res.get_json()["error"] == "csrf_failed"

    with app.app_context():
        db.session.remove()
        db.drop_all()
