"""Quoting an enquiry, and the job card that only exists once it is accepted.

A quotation used to live on a job card, which meant the car had to be booked in
before anyone could price the work — so intake priced it, and then the shop
assessed and priced the same panels again once the customer agreed. The
quotation is now raised against the *enquiry* and the card is opened only when
the customer accepts, carrying that quotation with it.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Booking, Estimate, JobCard, Vehicle

SERVICE = "Panel Beating & Spray Painting"


def _enquiry(client, *, name="Enquiry Client", phone="+263771234567", reg=None):
    body = {"name": name, "phone": phone, "service": SERVICE}
    if reg:
        body["reg_no"] = reg
    res = client.post("/api/bookings", json=body)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["booking"]


def _quote(client, booking_id, *, panels=("Front Bumper", "Bonnet")):
    res = client.post(f"/api/bookings/{booking_id}/estimate", json={"panels": list(panels)})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["estimate"]


# ── raising the quotation ────────────────────────────────────────────────────
def test_a_quotation_can_be_raised_on_an_enquiry_with_no_job_card(app, auth_client):
    booking = _enquiry(auth_client, reg="ENQ111")
    estimate = _quote(auth_client, booking["id"])

    assert estimate["job_id"] is None
    assert estimate["booking_id"] == booking["id"]
    assert estimate["status"] == "DRAFT"
    assert estimate["total"] > 0
    assert len(estimate["items"]) > 0
    # Read through the enquiry, because the card does not exist to read it from.
    assert estimate["reg_no"] == "ENQ111"
    assert estimate["service"] == SERVICE
    assert estimate["booking_reference"] == booking["reference"]

    with app.app_context():
        assert JobCard.query.count() == 0


def test_a_bot_enquiry_with_no_vehicle_can_still_be_quoted(auth_client):
    """The bot takes most enquiries from a photo and a message, with no car.

    Demanding a registration before a price can be written would stop the desk
    quoting at all, which is the conversation the whole flow exists to shorten.
    """
    booking = _enquiry(auth_client, reg=None)
    estimate = _quote(auth_client, booking["id"])

    assert estimate["reg_no"] is None
    assert estimate["total"] > 0


def test_a_second_quotation_on_one_enquiry_is_a_new_version(auth_client):
    booking = _enquiry(auth_client)
    first = _quote(auth_client, booking["id"])
    second = _quote(auth_client, booking["id"], panels=("Front Bumper", "Bonnet", "Roof"))

    assert first["version"] == 1
    assert second["version"] == 2
    assert second["total"] > first["total"]


def test_quoting_nothing_is_refused(auth_client):
    booking = _enquiry(auth_client)
    res = auth_client.post(f"/api/bookings/{booking['id']}/estimate", json={"panels": []})
    assert res.status_code == 400
    assert "nothing to quote" in res.get_json()["message"].lower()


def test_quoting_an_enquiry_that_does_not_exist_is_a_404(auth_client):
    res = auth_client.post("/api/bookings/999999/estimate", json={"panels": ["Bonnet"]})
    assert res.status_code == 404


# ── the quotations screen ────────────────────────────────────────────────────
def test_a_quotation_with_no_job_card_is_still_listed(auth_client):
    """The listing used to inner-join the job card, which hid every one of these."""
    booking = _enquiry(auth_client, name="Listed Enquiry", reg="LIST99")
    estimate = _quote(auth_client, booking["id"])

    body = auth_client.get("/api/quotations").get_json()
    row = next(q for q in body["items"] if q["id"] == estimate["id"])

    assert row["job_id"] is None
    assert row["job_no"] is None
    assert row["booking_reference"] == booking["reference"]
    assert row["reg_no"] == "LIST99"
    assert row["customer_name"] == "Listed Enquiry"
    assert row["service"] == SERVICE
    assert row["is_expired"] is False


def test_the_quotations_screen_can_ask_for_the_undecided_ones(auth_client):
    waiting = _quote(auth_client, _enquiry(auth_client, phone="+263771111111")["id"])
    decided = _quote(auth_client, _enquiry(auth_client, phone="+263772222222")["id"])
    auth_client.post(f"/api/estimates/{decided['id']}/decline", json={})

    body = auth_client.get("/api/quotations?open_only=1").get_json()
    ids = {q["id"] for q in body["items"]}
    assert waiting["id"] in ids
    assert decided["id"] not in ids


def test_the_quotations_screen_can_search_by_enquiry_reference(auth_client):
    booking = _enquiry(auth_client, phone="+263773333333")
    estimate = _quote(auth_client, booking["id"])

    body = auth_client.get(f"/api/quotations?q={booking['reference']}").get_json()
    assert [q["id"] for q in body["items"]] == [estimate["id"]]


# ── accepting it ─────────────────────────────────────────────────────────────
def test_approving_an_enquiry_with_a_car_opens_the_job_card(app, auth_client):
    booking = _enquiry(auth_client, reg="AUTO12")
    estimate = _quote(auth_client, booking["id"])

    res = auth_client.post(f"/api/estimates/{estimate['id']}/approve", json={})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()

    assert body["booked_in"] is True
    job = body["job"]
    assert job["stage"] == "INTAKE"
    assert job["job_no"]

    with app.app_context():
        card = JobCard.query.one()
        assert card.vehicle.reg_no == "AUTO12"
        assert card.booking_id == booking["id"]
        assert card.latest_estimate.id == estimate["id"]


def test_approving_an_enquiry_with_no_car_waits_for_the_desk(app, auth_client):
    """No registration means no vehicle, and a card cannot exist without one."""
    booking = _enquiry(auth_client, reg=None)
    estimate = _quote(auth_client, booking["id"])

    res = auth_client.post(f"/api/estimates/{estimate['id']}/approve", json={})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()

    assert body["booked_in"] is False
    assert body["job"] is None
    assert body["estimate"]["status"] == "APPROVED"

    with app.app_context():
        assert JobCard.query.count() == 0


def test_booking_in_takes_the_registration_and_opens_the_card(app, auth_client):
    booking = _enquiry(auth_client, reg=None)
    estimate = _quote(auth_client, booking["id"])
    auth_client.post(f"/api/estimates/{estimate['id']}/approve", json={})

    res = auth_client.post(f"/api/estimates/{estimate['id']}/book-in",
                           json={"reg_no": "NEW123", "colour": "White"})
    assert res.status_code == 201, res.get_json()
    job = res.get_json()["job"]

    assert job["stage"] == "INTAKE"
    with app.app_context():
        card = JobCard.query.one()
        assert card.vehicle.reg_no == "NEW123"
        assert card.vehicle.colour == "White"
        assert card.booking_id == booking["id"]
        # The car is remembered on the enquiry, so a second quotation on it
        # already knows what it is for.
        assert db.session.get(Booking, booking["id"]).vehicle_id == card.vehicle_id


def test_booking_in_without_a_registration_is_refused(auth_client):
    estimate = _quote(auth_client, _enquiry(auth_client, reg=None)["id"])
    auth_client.post(f"/api/estimates/{estimate['id']}/approve", json={})

    res = auth_client.post(f"/api/estimates/{estimate['id']}/book-in", json={})
    assert res.status_code == 409
    assert "registration" in res.get_json()["message"].lower()


def test_an_unapproved_quotation_cannot_be_booked_in(auth_client):
    estimate = _quote(auth_client, _enquiry(auth_client, reg="UNAPP1")["id"])
    res = auth_client.post(f"/api/estimates/{estimate['id']}/book-in", json={"reg_no": "UNAPP1"})
    assert res.status_code == 409
    assert "accept" in res.get_json()["message"].lower()


# ── the card carries the quotation, it does not re-price it ──────────────────
def test_the_quotation_moves_onto_the_card_rather_than_being_copied(app, auth_client):
    """One document, one reference, one link — the one the customer already has.

    Re-issuing the quotation under a new number at the moment they say yes is how
    a customer ends up believing they have been quoted twice.
    """
    booking = _enquiry(auth_client, reg="MOVE11")
    estimate = _quote(auth_client, booking["id"])
    auth_client.post(f"/api/estimates/{estimate['id']}/approve", json={})

    with app.app_context():
        assert Estimate.query.count() == 1
        card = JobCard.query.one()
        moved = card.latest_estimate
        assert moved.id == estimate["id"]
        assert moved.reference == estimate["reference"]
        assert moved.public_token == estimate["public_token"]
        assert float(moved.total) == estimate["total"]

    # And the link the customer was sent still resolves, now showing the card.
    res = auth_client.get(f"/doc/quote/{estimate['public_token']}.pdf")
    assert res.status_code == 200
    assert res.data.startswith(b"%PDF")


def test_a_job_card_taken_from_an_enquiry_carries_its_quotation(app, auth_client):
    """The manual path: the desk books the car in from the enquiries screen."""
    booking = _enquiry(auth_client, name="Manual Booking", reg="MAN111")
    estimate = _quote(auth_client, booking["id"], panels=("Bonnet", "Roof", "Boot Lid"))

    customer = auth_client.get("/api/customers?q=Manual").get_json()["items"][0]
    res = auth_client.post("/api/jobs", json={
        "customer_id": customer["id"],
        "booking_id": booking["id"],
        "reg_no": "MAN111",
    })
    assert res.status_code == 201, res.get_json()
    job = res.get_json()["job"]

    with app.app_context():
        # Priced once. Booking the car in must not build a second estimate.
        assert Estimate.query.count() == 1
        card = db.session.get(JobCard, job["id"])
        assert card.latest_estimate.id == estimate["id"]
        assert len(card.latest_estimate.items) == len(estimate["items"]) > 0
        assert card.booking_id == booking["id"]


def test_an_enquiry_cannot_be_booked_in_under_another_customer(auth_client):
    booking = _enquiry(auth_client, name="Right Owner", phone="+263774444444", reg="OWN111")
    other = auth_client.post("/api/customers", json={
        "name": "Wrong Owner", "phone": "+263775555555",
    }).get_json()["customer"]

    res = auth_client.post("/api/jobs", json={
        "customer_id": other["id"], "booking_id": booking["id"], "reg_no": "OWN111",
    })
    assert res.status_code == 400
    assert "different customer" in res.get_json()["message"].lower()


# ── the customer's WhatsApp tap ──────────────────────────────────────────────
def test_approving_on_whatsapp_books_the_car_in(app, auth_client):
    from app.services import intent_router, notifications
    from app.services.whatsapp_client import get_or_create_conversation, normalise_msisdn

    booking = _enquiry(auth_client, phone="+263776666666", reg="WAPP11")
    estimate = _quote(auth_client, booking["id"])

    with app.app_context():
        record = db.session.get(Estimate, estimate["id"])
        notifications.send_quotation(record)
        conversation = get_or_create_conversation(normalise_msisdn("+263776666666"))
        assert conversation.ctx_get("last_estimate_id") == estimate["id"]

        replies = intent_router.handle_inbound(conversation, interactive_id="a_approve")

        assert db.session.get(Estimate, estimate["id"]).status == "APPROVED"
        card = JobCard.query.one()
        assert card.latest_estimate.id == estimate["id"]
        # The customer is told the truth about what just happened.
        assert card.job_no in " ".join(r.get("body", "") for r in replies)


def test_declining_on_whatsapp_never_opens_a_job_card(app, auth_client):
    from app.services import intent_router, notifications
    from app.services.whatsapp_client import get_or_create_conversation, normalise_msisdn

    booking = _enquiry(auth_client, phone="+263777777777", reg="WAPP22")
    estimate = _quote(auth_client, booking["id"])

    with app.app_context():
        record = db.session.get(Estimate, estimate["id"])
        notifications.send_quotation(record)
        conversation = get_or_create_conversation(normalise_msisdn("+263777777777"))
        intent_router.handle_inbound(conversation, interactive_id="a_decline")

        assert db.session.get(Estimate, estimate["id"]).status == "DECLINED"
        assert JobCard.query.count() == 0
        assert Vehicle.query.count() == 1
