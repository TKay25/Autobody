"""API and workflow tests."""
from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models import JobCard, User, Vehicle
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


# ── phone numbers entering from the desk ─────────────────────────────────────
def test_a_customer_number_is_stored_so_the_bot_can_dial_it(auth_client, app):
    """What the desk types is stored as a number the bot can actually reach.

    Typed the way it is written on a job card ("0775550555"), this used to be
    saved verbatim. On screen it looked right; every message the bot sent to it
    was rejected, and the desk saw only silence. The country code comes from the
    same setting the bot dials with.
    """
    res = auth_client.post("/api/customers", json={
        "name": "Local Format", "phone": "0775550555",
    })
    assert res.status_code == 201, res.get_json()

    with app.app_context():
        from app.models import Customer

        customer = Customer.query.filter_by(name="Local Format").first()
        assert customer.phone == "+263775550555", customer.phone
        # The WhatsApp number the bot will dial.
        assert customer.wa_number == "263775550555"


def test_whatsapp_defaults_to_the_phone_number_but_is_normalised_too(auth_client, app):
    res = auth_client.post("/api/customers", json={
        "name": "No WhatsApp Given", "phone": "00263775551234",
    })
    with app.app_context():
        from app.models import Customer

        customer = Customer.query.filter_by(name="No WhatsApp Given").first()
        assert customer.whatsapp == "+263775551234", customer.whatsapp


def test_a_foreign_number_keeps_its_own_country_code(auth_client, app):
    """The dropdown default must not re-home a number the desk gave a code to."""
    auth_client.post("/api/customers", json={
        "name": "UK Client", "phone": "+44 7911 123456",
    })
    with app.app_context():
        from app.models import Customer

        customer = Customer.query.filter_by(name="UK Client").first()
        assert customer.phone == "+447911123456", customer.phone


def test_editing_a_customer_tidies_the_number_it_was_given(auth_client, app):
    with app.app_context():
        from app.models import Customer

        res = auth_client.post("/api/customers", json={
            "name": "Tidy Me", "phone": "+263 77 555 0555",
        })
        assert res.status_code == 201
        customer = Customer.query.filter_by(name="Tidy Me").first()
        before = customer.phone

        res = auth_client.patch(f"/api/customers/{customer.id}",
                                json={"phone": "077 555 0999"})
        assert res.status_code == 200
        assert res.get_json()["customer"]["phone"] == "+263775550999", before


def test_the_same_customer_is_not_created_twice_for_two_spellings(auth_client, app):
    """Canonicalising on write must not orphan rows saved before it existed.

    Rows already in the database hold "0775550555" and "+263 77 555 0555". A
    lookup that only matched the new canonical form would create a second
    customer for somebody already on file.
    """
    with app.app_context():
        from app.models import Customer
        from app.services import job_flow

        legacy = Customer(name="Already On File", phone="0775550555")
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id

        # The desk now types it with the code, from the dropdown.
        found = job_flow.find_or_create_customer(
            name="Already On File", phone="+263775550555")

        assert found.id == legacy_id, "created a duplicate customer"
        assert Customer.query.count() == 1


def test_meta_gives_the_frontend_the_dial_codes(auth_client):
    """Same list and same default the server dials with, so they cannot drift."""
    meta = auth_client.get("/api/meta").get_json()
    assert meta["default_country_code"] == "263"
    assert meta["countries"][0] == {"code": "263", "iso": "ZW", "name": "Zimbabwe"}


# ── ID numbers ───────────────────────────────────────────────────────────────
def test_a_customer_id_number_is_recorded_and_tidied(auth_client, app):
    """An ID is read aloud and compared to a document at the counter.

    Storing ``63-1234567 a 00`` and ``63-1234567 A 00`` as two different strings
    makes the desk think one customer gave two IDs.
    """
    res = auth_client.post("/api/customers", json={
        "name": "Identified Client", "phone": "0775550111",
        "id_number": "  63-1234567 a 00  ",
    })
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["customer"]["id_number"] == "63-1234567 A 00"

    with app.app_context():
        from app.models import Customer

        assert Customer.query.filter_by(name="Identified Client").first().id_number \
            == "63-1234567 A 00"


def test_blank_id_numbers_are_stored_as_nothing_not_as_empty_text(auth_client, app):
    """"No ID on file" has to be one value.

    The collection check asks whether there is an ID; ``""``, ``"  "`` and ``None``
    all answering differently is how a warning ends up not shown.
    """
    auth_client.post("/api/customers", json={
        "name": "No ID Client", "phone": "0775550112", "id_number": "   ",
    })
    with app.app_context():
        from app.models import Customer

        assert Customer.query.filter_by(name="No ID Client").first().id_number is None


def test_an_id_number_can_be_added_later_from_the_edit_form(auth_client, app):
    """Often the ID only appears at the counter, when somebody collects a car."""
    res = auth_client.post("/api/customers", json={
        "name": "Late ID", "phone": "0775550113",
    })
    cid = res.get_json()["customer"]["id"]
    assert res.get_json()["customer"]["id_number"] is None

    res = auth_client.patch(f"/api/customers/{cid}",
                            json={"id_number": "08-7654321 b 42"})
    assert res.status_code == 200
    assert res.get_json()["customer"]["id_number"] == "08-7654321 B 42"


def test_the_job_card_carries_the_id_checked_at_collection(auth_client):
    """The vehicle is released against the ID, so it travels with the job card.

    Read from the job card payload rather than the customer record because that
    is the screen the person handing the keys over is looking at.
    """
    res = auth_client.post("/api/jobs", json={
        "customer_name": "Collecting Client", "customer_phone": "0775550114",
        "customer_id_number": "63-9999999 C 00", "reg_no": "IDC1234",
    })
    assert res.status_code == 201, res.get_json()
    job = res.get_json()["job"]
    assert job["customer_id_number"] == "63-9999999 C 00"

    fetched = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]
    assert fetched["customer_id_number"] == "63-9999999 C 00"


def test_an_id_given_later_never_overwrites_one_already_on_file(app):
    """The number on file is the one that was checked against the document.

    A later form that happens to carry the customer's number again must not
    quietly replace it — that is how the wrong ID ends up on a handover.
    """
    with app.app_context():
        from app.models import Customer
        from app.services import job_flow

        first = job_flow.find_or_create_customer(
            name="Existing ID", phone="0775550115", id_number="63-1111111 A 00")
        assert first.id_number == "63-1111111 A 00"

        again = job_flow.find_or_create_customer(
            name="Existing ID", phone="+263775550115", id_number="63-2222222 B 00")
        assert again.id == first.id, "created a duplicate customer"
        assert again.id_number == "63-1111111 A 00", "the checked ID was overwritten"

        # But a blank one is filled in.
        blank = job_flow.find_or_create_customer(
            name="No ID Yet", phone="0775550116")
        assert blank.id_number is None
        filled = job_flow.find_or_create_customer(
            name="No ID Yet", phone="0775550116", id_number="63-3333333 C 00")
        assert filled.id == blank.id
        assert filled.id_number == "63-3333333 C 00"


def test_a_booking_can_carry_the_id_number(auth_client, app):
    """The enquiry desk is told the ID over the phone, so record it then."""
    res = auth_client.post("/api/bookings", json={
        "name": "Phone ID Lead", "phone": "0775550117",
        "id_number": "63-4444444 D 00", "service": "Ceramic Coating",
        "slot_date": date.today().isoformat(),
    })
    assert res.status_code == 201, res.get_json()

    with app.app_context():
        from app.models import Customer

        assert Customer.query.filter_by(name="Phone ID Lead").first().id_number \
            == "63-4444444 D 00"


def test_the_customer_payload_carries_the_portal_token(auth_client):
    """Otherwise every "Open customer portal" button links to /portal/undefined.

    A 404 there reads as though the portal is broken, so the desk stops offering
    it to customers — and nobody reports it, because the button looks fine.
    """
    res = auth_client.post("/api/customers", json={
        "name": "Portal Client", "phone": "0775550118",
    })
    assert res.status_code == 201
    token = res.get_json()["customer"]["portal_token"]
    assert token, "the portal token was left out of the payload"

    # And the link it builds actually resolves.
    assert auth_client.get(f"/portal/{token}").status_code == 200
    assert auth_client.get("/portal/undefined").status_code == 404


def test_advance_blocked_until_the_customer_approves_the_estimate(auth_client, app):
    res = auth_client.post("/api/jobs", json={
        "customer_name": "Approval Client", "reg_no": "APP1111",
        "service": "Panel Beating & Spray Painting",
        "panels": ["Front Bumper"],
    })
    job_id = res.get_json()["job"]["id"]

    auth_client.post(f"/api/jobs/{job_id}/stage", json={"stage": "AWAITING_APPROVAL"})
    res = auth_client.post(f"/api/jobs/{job_id}/advance", json={})
    assert res.status_code == 409
    assert "customer approval" in res.get_json()["message"].lower()


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


def test_a_staff_edit_does_not_silently_disable_the_account(auth_client):
    """The console's edit dialog posts back the row the list gave it.

    `is_active_user` was missing from the payload, so the dialog's Active switch
    started OFF for everybody — the table's badge read "Disabled" against every
    account, *including the owner signed in at the time*, and saving an ordinary
    edit sent `is_active_user: false`, disabling the person being edited. The
    owner changing their own phone number would have locked themselves out.
    """
    rows = auth_client.get("/api/users").get_json()["items"]
    owner = next(r for r in rows if r["role"] == "owner")
    assert owner["is_active_user"] is True, "the payload lost the flag"

    # Exactly what the dialog would post: the row it was handed, minus the
    # read-only decorations, with the phone changed.
    posted = {k: v for k, v in owner.items()
              if k not in {"id", "initials", "role_label", "is_manager"}}
    posted["phone"] = "+263 77 555 0555"
    res = auth_client.patch(f"/api/users/{owner['id']}", json=posted)

    assert res.status_code == 200, res.get_json()
    assert res.get_json()["user"]["is_active_user"] is True

    with auth_client.application.app_context():
        from app.models import User

        assert User.query.filter_by(email=owner["email"]).first().is_active_user is True


def test_an_account_can_still_be_disabled_on_purpose(auth_client):
    """The flag has to keep working in the other direction."""
    rows = auth_client.get("/api/users").get_json()["items"]
    target = next(r for r in rows if r["role"] != "owner")

    res = auth_client.patch(f"/api/users/{target['id']}", json={"is_active_user": False})
    assert res.status_code == 200
    assert res.get_json()["user"]["is_active_user"] is False

    # And a disabled account really cannot sign in.
    from app import create_app
    from config import TestConfig

    fresh = create_app(TestConfig)
    with fresh.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)
        user = User.query.filter_by(email=target["email"]).first()
        user.is_active_user = False
        db.session.commit()
        anon = fresh.test_client()
        res = anon.post("/auth/login", json={"email": target["email"],
                                            "password": "topclass123"})
        assert res.status_code == 401, res.status_code
        db.drop_all()


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
