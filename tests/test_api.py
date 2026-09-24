"""API and workflow tests."""
from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models import JobCard, Vehicle
from app.services import job_flow


def test_login_required_for_api(client):
    assert client.get("/api/jobs").status_code == 401


def test_login_rejects_bad_password(client):
    res = client.post("/auth/login", json={"email": "owner@topclass.co.zw", "password": "wrong"})
    assert res.status_code == 401


def test_dashboard_returns_metrics(auth_client):
    res = auth_client.get("/api/dashboard")
    assert res.status_code == 200
    body = res.get_json()
    assert "metrics" in body and "board" in body
    assert body["metrics"]["open_jobs"] == 0


def test_create_job_card_generates_job_number(auth_client):
    res = auth_client.post("/api/jobs", json={
        "customer_name": "Test Customer",
        "customer_phone": "+263771234567",
        "reg_no": "abc1234",
        "make": "Toyota",
        "model": "Hilux",
        "service": "Panel Beating & Spray Painting",
        "panels": ["Front Bumper", "Bonnet"],
        "is_insurance": False,
    })
    assert res.status_code == 201, res.get_json()
    job = res.get_json()["job"]
    assert job["job_no"].startswith("TC-")
    assert job["stage"] == "INTAKE"
    # Estimate was built from the panels we passed.
    estimate = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]["estimate"]
    assert estimate is not None
    assert estimate["total"] > 0


def test_vehicle_registration_is_normalised(auth_client, app):
    auth_client.post("/api/jobs", json={"customer_name": "A", "reg_no": "xyz 9999"})
    with app.app_context():
        assert Vehicle.query.filter_by(reg_no="XYZ9999").first() is not None


def test_advance_blocked_when_awaiting_insurer_approval(auth_client, app):
    res = auth_client.post("/api/jobs", json={
        "customer_name": "Claim Client", "reg_no": "CLM1111",
        "service": "Panel Beating & Spray Painting",
        "is_insurance": True, "panels": ["Front Bumper"],
    })
    job_id = res.get_json()["job"]["id"]

    auth_client.post(f"/api/jobs/{job_id}/stage", json={"stage": "AWAITING_APPROVAL"})
    res = auth_client.post(f"/api/jobs/{job_id}/advance", json={})
    assert res.status_code == 409
    assert "insurer approval" in res.get_json()["message"].lower()


def test_qc_gate_blocks_release(auth_client):
    res = auth_client.post("/api/jobs", json={"customer_name": "QC Client", "reg_no": "QCC123"})
    job_id = res.get_json()["job"]["id"]
    auth_client.post(f"/api/jobs/{job_id}/stage", json={"stage": "QC"})

    res = auth_client.post(f"/api/jobs/{job_id}/advance", json={})
    assert res.status_code == 409
    assert "Re-work required" in res.get_json()["message"]

    qc = auth_client.get(f"/api/jobs/{job_id}").get_json()["job"]["qc_results"]
    auth_client.post(f"/api/jobs/{job_id}/qc", json={
        "results": [{"item": r["item"], "passed": True} for r in qc],
    })
    res = auth_client.post(f"/api/jobs/{job_id}/advance", json={})
    assert res.status_code == 200
    assert res.get_json()["job"]["stage"] == "READY"


def test_estimating_preview_prices_panels(auth_client):
    res = auth_client.post("/api/estimating/preview", json={"panels": ["Front Door", "Rear Door"]})
    assert res.status_code == 200
    body = res.get_json()
    assert body["summary"]["total"] > 0
    assert any(l["kind"] == "LABOUR" for l in body["lines"])


def test_parts_blocking_prevents_advance(auth_client):
    res = auth_client.post("/api/jobs", json={"customer_name": "Parts Client", "reg_no": "PRT555"})
    job_id = res.get_json()["job"]["id"]
    auth_client.post(f"/api/jobs/{job_id}/parts", json={
        "description": "Front bumper", "quantity": 1, "unit_price": 200, "status": "ORDERED",
    })
    auth_client.post(f"/api/jobs/{job_id}/stage", json={"stage": "PARTS_ORDER"})
    res = auth_client.post(f"/api/jobs/{job_id}/advance", json={})
    assert res.status_code == 409
    assert "outstanding" in res.get_json()["message"]


def test_creating_a_booking_requires_login(client):
    """No longer public: the marketing-site widget that needed this is gone.

    Left open, anyone could create customers and bookings anonymously. Uses only
    the anonymous client — mixing it with `auth_client` in one test makes this
    client look signed in, because Flask-Login's g._login_user leaks.
    """
    res = client.post("/api/bookings", json={
        "name": "Anonymous Lead", "phone": "+263772222222",
        "service": "Ceramic Coating", "slot_date": date.today().isoformat(),
    })
    assert res.status_code == 401


def test_staff_can_create_a_booking(auth_client):
    """An in-house request starts life as an *enquiry*, not a booking.

    It only becomes a booking — and only then earns a booking reference — when
    somebody confirms it.
    """
    res = auth_client.post("/api/bookings", json={
        "name": "Phone Lead", "phone": "+263772333333",
        "service": "Ceramic Coating", "slot_date": date.today().isoformat(),
    })
    assert res.status_code == 201
    body = res.get_json()
    assert body["booking"]["reference"].startswith("TC-ENQ")
    assert body["booking"]["booking_reference"] is None
    assert body["quote"]["from_price"] == 350.0


def test_invoice_and_payment_flow(auth_client):
    res = auth_client.post("/api/jobs", json={
        "customer_name": "Payer", "reg_no": "PAY777",
        "panels": ["Bonnet"],
    })
    job_id = res.get_json()["job"]["id"]

    res = auth_client.post(f"/api/jobs/{job_id}/invoice", json={})
    assert res.status_code == 201
    invoice = res.get_json()["invoice"]

    res = auth_client.post(f"/api/invoices/{invoice['id']}/payment",
                           json={"amount": invoice["total"], "method": "ECOCASH"})
    assert res.status_code == 200
    assert res.get_json()["invoice"]["status"] == "PAID"
    assert res.get_json()["invoice"]["balance"] == 0


def test_claim_link_flags_job_as_insurance(auth_client, app):
    res = auth_client.post("/api/jobs", json={"customer_name": "Ins", "reg_no": "INS321"})
    job_id = res.get_json()["job"]["id"]
    res = auth_client.post(f"/api/jobs/{job_id}/claim", json={
        "insurer_code": "OLD", "claim_no": "CLM12345", "excess": 150,
        "claimed_amount": 900, "status": "SUBMITTED",
    })
    assert res.status_code == 201
    with app.app_context():
        job = db.session.get(JobCard, job_id)
        assert job.is_insurance is True
        assert job.active_claim.insurer_name == "Old Mutual"


def test_reports_overview(auth_client):
    res = auth_client.get("/api/reports/overview")
    assert res.status_code == 200
    body = res.get_json()
    assert len(body["trend"]) == 14
    assert "by_service" in body


def test_manager_can_create_staff_and_front_desk_cannot(auth_client, client):
    res = auth_client.post("/api/users", json={
        "full_name": "New Tech", "email": "newtech@topclass.co.zw",
        "password": "secret123", "role": "technician",
    })
    assert res.status_code == 201

    client.post("/auth/login", json={"email": "front@topclass.co.zw", "password": "topclass123"})
    res = client.post("/api/users", json={
        "full_name": "Nope", "email": "nope@topclass.co.zw", "password": "x", "role": "owner",
    })
    assert res.status_code == 403


def test_metrics_after_creating_jobs(auth_client):
    for i in range(3):
        auth_client.post("/api/jobs", json={"customer_name": f"C{i}", "reg_no": f"REG{i:04d}"})
    body = auth_client.get("/api/dashboard").get_json()
    assert body["metrics"]["open_jobs"] == 3


def test_overdue_detection(auth_client, app):
    res = auth_client.post("/api/jobs", json={
        "customer_name": "Late", "reg_no": "LATE001",
        "promised_date": (date.today() - timedelta(days=3)).isoformat(),
    })
    job_id = res.get_json()["job"]["id"]
    body = auth_client.get(f"/api/jobs/{job_id}").get_json()
    assert body["job"]["is_overdue"] is True
