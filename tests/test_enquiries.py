"""Enquiries: where they came from, and whether anybody has priced them.

An enquiry is the record everything else hangs off — the bot makes one from a
WhatsApp conversation, the Flow makes one from a form, the desk makes one from a
phone call. They have to be told apart on the screen, because chasing the
WhatsApp ones overnight is a different job from working the phone list.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Booking

SERVICE = "Panel Beating & Spray Painting"


def _enquiry(client, *, name="Channel Tester", phone="+263771234500", source=None,
             service=SERVICE):
    body = {"name": name, "phone": phone, "service": service}
    if source:
        body["source"] = source
    res = client.post("/api/bookings", json=body)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["booking"]


# ── where it came from ───────────────────────────────────────────────────────
def test_an_enquiry_records_the_channel_it_arrived_on(auth_client):
    whatsapp = _enquiry(auth_client, phone="+263771111000", source="whatsapp")
    walkin = _enquiry(auth_client, name="Walk In", phone="+263771111001", source="walkin")

    assert whatsapp["source"] == "whatsapp"
    assert whatsapp["source_label"] == "WhatsApp"
    assert walkin["source_label"] == "Walk-in"


def test_an_unrecognised_channel_is_shown_rather_than_hidden(auth_client):
    """A code nobody has a label for must still print. Falling back to the raw
    value beats a blank cell that reads as "we do not know"."""
    booking = _enquiry(auth_client, phone="+263771111002", source="carrier-pigeon")
    assert booking["source_label"] == "carrier-pigeon"


def test_the_enquiries_list_filters_by_channel(auth_client):
    _enquiry(auth_client, phone="+263771112000", source="whatsapp")
    _enquiry(auth_client, name="Phoned", phone="+263771112001", source="phone")

    body = auth_client.get("/api/bookings?source=whatsapp").get_json()
    assert body["count"] == 1
    assert body["items"][0]["source"] == "whatsapp"

    assert auth_client.get("/api/bookings?source=phone").get_json()["count"] == 1
    assert auth_client.get("/api/bookings?source=web").get_json()["count"] == 0
    assert auth_client.get("/api/bookings").get_json()["count"] == 2


# ── whether anybody has priced it ────────────────────────────────────────────
def test_an_unpriced_enquiry_reports_no_quotation(auth_client):
    booking = _enquiry(auth_client, phone="+263771113000")
    assert booking["quotation"] is None


def test_an_enquiry_carries_its_quotation_for_the_list_to_show(auth_client):
    booking = _enquiry(auth_client, phone="+263771114000")
    res = auth_client.post(f"/api/bookings/{booking['id']}/estimate",
                           json={"panels": ["Bonnet"]})
    assert res.status_code == 201, res.get_json()
    estimate = res.get_json()["estimate"]

    row = auth_client.get("/api/bookings").get_json()["items"][0]
    assert row["quotation"]["id"] == estimate["id"]
    assert row["quotation"]["reference"] == estimate["reference"]
    assert row["quotation"]["status"] == "DRAFT"
    assert row["quotation"]["total"] == estimate["total"]
    assert row["quotation"]["count"] == 1


def test_the_unquoted_filter_finds_the_enquiries_nobody_has_priced(auth_client):
    priced = _enquiry(auth_client, phone="+263771115000")
    _enquiry(auth_client, name="Ignored", phone="+263771115001")
    auth_client.post(f"/api/bookings/{priced['id']}/estimate", json={"panels": ["Bonnet"]})

    body = auth_client.get("/api/bookings?unquoted=1").get_json()
    assert body["count"] == 1
    assert body["items"][0]["quotation"] is None


def test_the_unquoted_filter_drops_an_enquiry_once_it_is_priced(auth_client):
    """A quotation is what takes an enquiry off the "nobody has answered this
    yet" list — that is the whole point of quoting before booking the car in."""
    booking = _enquiry(auth_client, phone="+263771116000")
    assert auth_client.get("/api/bookings?unquoted=1").get_json()["count"] == 1

    auth_client.post(f"/api/bookings/{booking['id']}/estimate", json={"panels": ["Bonnet"]})
    assert auth_client.get("/api/bookings?unquoted=1").get_json()["count"] == 0


def test_a_second_quotation_on_an_enquiry_is_counted(auth_client):
    booking = _enquiry(auth_client, phone="+263771117000")
    auth_client.post(f"/api/bookings/{booking['id']}/estimate", json={"panels": ["Bonnet"]})
    auth_client.post(f"/api/bookings/{booking['id']}/estimate", json={"panels": ["Bonnet", "Roof"]})

    row = auth_client.get("/api/bookings").get_json()["items"][0]
    assert row["quotation"]["count"] == 2


# ── the desk's own corrections ───────────────────────────────────────────────
def test_a_customer_already_on_file_needs_no_retyping(auth_client):
    """Picking a customer from the list must not require their name and number
    again — and must not be refused for the customers nobody has a number for."""
    created = auth_client.post("/api/customers", json={"name": "No Number Ltd"})
    assert created.status_code in (200, 201), created.get_json()
    customer = created.get_json()["customer"]

    res = auth_client.post("/api/bookings",
                           json={"customer_id": customer["id"], "service": SERVICE})
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["booking"]["customer_id"] == customer["id"]


def test_a_new_customer_still_needs_a_way_to_be_contacted(auth_client):
    res = auth_client.post("/api/bookings", json={"name": "Nameless", "service": SERVICE})
    assert res.status_code == 400
    assert "phone" in res.get_json()["message"].lower()


def test_the_service_on_an_enquiry_can_be_corrected(app, auth_client):
    """The desk describes the job on the phone; the estimator sometimes finds it
    is something else entirely."""
    booking = _enquiry(auth_client, phone="+263771118000")
    res = auth_client.patch(f"/api/bookings/{booking['id']}", json={"service": "Ceramic Coating"})

    assert res.status_code == 200, res.get_json()
    assert res.get_json()["booking"]["service"] == "Ceramic Coating"
    with app.app_context():
        assert db.session.get(Booking, booking["id"]).service == "Ceramic Coating"


def test_correcting_the_service_to_something_unknown_is_refused(auth_client):
    booking = _enquiry(auth_client, phone="+263771119000")
    res = auth_client.patch(f"/api/bookings/{booking['id']}", json={"service": "Wishful Thinking"})
    assert res.status_code == 400

    row = auth_client.get("/api/bookings").get_json()["items"][0]
    assert row["service"] == SERVICE
