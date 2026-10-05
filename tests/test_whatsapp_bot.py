"""WhatsApp chatbot tests — no network required (simulator mode)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timedelta

from app.constants import BOOKING_EXPECTED_STATUSES, BOOKING_SLOT_CAPACITY
from app.extensions import db
from app.models import (Booking, BookingPhoto, Customer, Estimate, Invoice, JobCard, JobPhoto,
                        NotificationLog, PaymentProof, Task, Vehicle, WaConversation,
                        WaMessage)
from app.services import bookings as booking_ops
from app.services import intent_router, notifications
from app.services.whatsapp_client import get_or_create_conversation


def test_normalises_numbers():
    from app.services.whatsapp_client import normalise_msisdn

    assert normalise_msisdn("+263 77 555 0555") == "263775550555"
    assert normalise_msisdn("0775550555") == "263775550555"
    assert normalise_msisdn("00263775550555") == "263775550555"


def test_greeting_returns_main_menu(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110001", "Tester")
        replies = intent_router.handle_inbound(conv, text_body="Hello")
        assert replies
        assert replies[0]["type"] == "list"
        ids = [row["id"] for s in replies[0]["sections"] for row in s["rows"]]
        # "Get a quote" and "Book a service" were two rows leading to the same
        # desk; they are one now, and the service choice is behind it.
        assert "m_enquiries" in ids
        assert "m_quote" not in ids
        assert "m_book" not in ids


def test_the_greeting_names_the_customer_in_bold(app):
    """Once per session, and bolded — WhatsApp renders *name* as bold."""
    from app.models import utcnow

    with app.app_context():
        conv = get_or_create_conversation("263771110002", "Tendai Moyo")
        body = intent_router.handle_inbound(conv, text_body="Hi")[0]["body"]
        assert body.startswith("Hi *Tendai*"), body

        # Re-opening the menu inside the session must not greet again.
        again = intent_router.handle_inbound(conv, interactive_id="m_menu")[0]["body"]
        assert not again.startswith("Hi *Tendai*"), again

    # A new session greets again: the clock, not the conversation row, decides.
    with app.app_context():
        conv = WaConversation.query.filter_by(wa_id="263771110002").first()
        stale = (utcnow() - timedelta(hours=30)).isoformat()
        conv.ctx_set(greeted_at=stale)
        db.session.commit()
        back = intent_router.handle_inbound(conv, text_body="Hello again")
        menu = next(r for r in back if r.get("type") == "list")
        assert menu["body"].startswith("Hi *Tendai*"), menu["body"]


def test_a_number_with_no_name_is_not_greeted_as_whatsapp(app):
    """`_wa_name()` falls back to "WhatsApp +263...", which is right for a record
    and wrong in a greeting — "Hi *WhatsApp*" is what dropping it in produced."""
    with app.app_context():
        conv = get_or_create_conversation("263771110005", None)
        body = intent_router.handle_inbound(conv, text_body="Hi")[0]["body"]
        assert body.startswith("Hello,"), body
        assert "WhatsApp" not in body.split("\n")[0], body
        assert "*" not in body.split("\n")[0], body


def test_an_enquiry_never_asks_for_a_registration_number(app):
    """The form stopped asking for a plate, so the chat must not either.

    Asking before anything else was also the wrong order: the customer has not yet
    been told what we do, or what it costs.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771110003", "No Plate")
        first = intent_router.handle_inbound(conv, interactive_id="m_enquiries")
        assert first[0]["type"] == "list"
        assert not any("registration number" in (r.get("body") or "") for r in first)
        assert not any("ABC 1234" in (r.get("body") or "") for r in first)

        # And the service choice leads straight to the brief, not to a plate.
        replies = intent_router.handle_inbound(conv, interactive_id="enq:Car Detailing")
        assert conv.state == "QUOTE_DESC", conv.state
        assert not any("registration" in (r.get("body") or "").lower() for r in replies)


def test_tapping_a_service_explains_it_before_asking_anything(app):
    """The brief is the point of the row: what the work involves, and a price."""
    with app.app_context():
        conv = get_or_create_conversation("263771110004", "Curious")
        intent_router.handle_inbound(conv, interactive_id="m_enquiries")
        replies = intent_router.handle_inbound(conv, interactive_id="enq:Ceramic Coating")
        brief = replies[0]["body"]
        assert brief.startswith("*Ceramic Coating*"), brief
        assert "water" in brief.lower(), brief
        assert "USD 350" in brief, brief


def test_the_follow_up_question_follows_the_service(app):
    """"Please describe the damage" is the wrong question for a coating.

    Now that every service funnels through the same enquiry, one wording for all
    of them reads as though the bot did not hear which service was picked.
    """
    with app.app_context():
        for index, (service, expected) in enumerate([
            ("Ceramic Coating", "Tell us about the vehicle"),
            ("Car Detailing", "Tell us about the vehicle"),
            ("Car Vinyl Wrapping", "Tell us about the vehicle"),
            ("Panel Beating & Spray Painting", "describe the damage"),
            ("Auto Body", "describe the damage"),
        ]):
            conv = get_or_create_conversation(f"2637711100{index + 20}", "Ask Me")
            body = intent_router.handle_inbound(
                conv, interactive_id=f"enq:{service}")[0]["body"]
            assert expected in body, f"{service}: {body}"


def test_a_booking_completes_by_tapping_and_keeps_the_chosen_slot(app):
    """Two bugs in one flow: it could not be finished, and it lost the day.

    The service list used `svc:` ids, which the router sent into the *quote* flow,
    so tapping a service while booking never reached the day step. And the day the
    customer then picked was collected but discarded, so every booking silently
    landed on tomorrow.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771120010", "Booker")

        picker = intent_router.handle_inbound(conv, interactive_id="m_book")[0]
        service_ids = [r["id"] for s in picker["sections"] for r in s["rows"]]
        assert service_ids, "the booking flow offered no services"
        assert all(i.startswith("bsvc:") for i in service_ids), service_ids

        day_picker = intent_router.handle_inbound(conv, interactive_id=service_ids[0])[0]
        day_ids = [r["id"] for s in day_picker["sections"] for r in s["rows"]]
        assert day_ids, "no days were offered"
        chosen_day = day_ids[2]
        wanted_date = chosen_day.split(":", 1)[1]

        # The day now leads to a time picker rather than straight to the contact
        # question — an appointment with no time is not an appointment.
        time_picker = intent_router.handle_inbound(conv, interactive_id=chosen_day)[0]
        assert time_picker["type"] == "list"
        slot_ids = [r["id"] for s in time_picker["sections"] for r in s["rows"]]
        assert slot_ids, "no times were offered"
        assert all(i.startswith("bslot:") for i in slot_ids), slot_ids
        chosen_slot = slot_ids[0]
        # "bslot:<date>:<HH:MM>" — the time is the last two colon-separated parts.
        wanted_time = ":".join(chosen_slot.split(":")[-2:])

        intent_router.handle_inbound(conv, interactive_id=chosen_slot)
        confirmation = intent_router.handle_inbound(conv, text_body="Tariro Moyo")
        assert "Preferred slot" in confirmation[0]["body"], confirmation[0]["body"]
        assert wanted_time in confirmation[0]["body"]

        booking = Booking.query.order_by(Booking.id.desc()).first()
        assert booking is not None, "the booking was never created"
        assert booking.source == "whatsapp"
        assert booking.status == "REQUESTED"
        assert booking.slot_date.isoformat() == wanted_date, (
            f"booking landed on {booking.slot_date}, the customer asked for {wanted_date}"
        )
        assert booking.slot_time == wanted_time
        # The answers survive the flow reset only as cleared context.
        assert conv.ctx_get("book_date") is None
        assert conv.ctx_get("book_time") is None


def test_a_full_slot_is_not_offered(app):
    """Capacity comes from the booking table, so the bot cannot overbook a bay."""
    with app.app_context():
        customer = Customer(name="Slot Blocker", phone="+263771120099",
                            whatsapp="+263771120099")
        db.session.add(customer)
        db.session.flush()
        day = date.today() + timedelta(days=1)
        for _ in range(BOOKING_SLOT_CAPACITY):
            db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                                   slot_date=day, slot_time="08:00",
                                   status="CONFIRMED", source="whatsapp"))
        db.session.commit()

        conv = get_or_create_conversation("263771120011", "Slot Tester")
        intent_router.handle_inbound(conv, interactive_id="m_book")
        intent_router.handle_inbound(conv, interactive_id="bsvc:Car Detailing")
        picker = intent_router.handle_inbound(
            conv, interactive_id=f"day:{day.isoformat()}")[0]
        offered = [r["title"] for s in picker["sections"] for r in s["rows"]]
        assert "08:00" not in offered
        assert offered, "a full slot emptied the rest of the day"


def test_a_cancelled_booking_gives_its_slot_back(app):
    with app.app_context():
        customer = Customer(name="Cancel Slot", phone="+263771120098",
                            whatsapp="+263771120098")
        db.session.add(customer)
        db.session.flush()
        day = date.today() + timedelta(days=1)
        for _ in range(BOOKING_SLOT_CAPACITY):
            db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                                   slot_date=day, slot_time="09:00",
                                   status="CANCELLED", source="whatsapp"))
        db.session.commit()

        conv = get_or_create_conversation("263771120012", "Slot Reuser")
        intent_router.handle_inbound(conv, interactive_id="m_book")
        intent_router.handle_inbound(conv, interactive_id="bsvc:Car Detailing")
        picker = intent_router.handle_inbound(
            conv, interactive_id=f"day:{day.isoformat()}")[0]
        offered = [r["title"] for s in picker["sections"] for r in s["rows"]]
        assert "09:00" in offered


def test_a_typed_time_is_understood_and_a_bad_one_re_offers(app):
    with app.app_context():
        conv = get_or_create_conversation("263771120013", "Typed Time")
        day = date.today() + timedelta(days=1)
        conv.ctx_set(service="Car Detailing", book_date=day.isoformat())
        conv.state = "BOOK_TIME"
        db.session.commit()

        intent_router.handle_inbound(conv, text_body="9am")
        assert conv.ctx_get("book_time") == "09:00"
        assert conv.state == "BOOK_CONTACT"

        # A time the shop does not run must not be accepted on the quiet.
        conv.ctx_clear("book_time")
        conv.state = "BOOK_TIME"
        db.session.commit()
        replies = intent_router.handle_inbound(conv, text_body="2:30")
        assert conv.ctx_get("book_time") is None
        assert replies[0]["type"] == "text"
        assert replies[1]["type"] == "list"


def test_booking_reminders_go_out_exactly_once(app):
    """The day-before nudge must survive being run twice."""
    with app.app_context():
        customer = Customer(name="Remind Me", phone="+263771130001",
                            whatsapp="+263771130001")
        db.session.add(customer)
        db.session.flush()
        tomorrow = date.today() + timedelta(days=1)
        booking = Booking(customer_id=customer.id, service="Car Detailing",
                          slot_date=tomorrow, slot_time="10:00",
                          status="CONFIRMED", source="whatsapp")
        db.session.add(booking)
        db.session.commit()

        first = notifications.send_due_booking_reminders(tomorrow)
        assert (first["due"], first["sent"]) == (1, 1)
        assert booking.reminder_sent_at is not None

        second = notifications.send_due_booking_reminders(tomorrow)
        assert (second["due"], second["sent"]) == (0, 0)
        assert NotificationLog.query.filter_by(template="booking_reminder").count() == 1


def test_a_cancelled_booking_is_not_reminded(app):
    with app.app_context():
        customer = Customer(name="Dropped Out", phone="+263771130002",
                            whatsapp="+263771130002")
        db.session.add(customer)
        db.session.flush()
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                               slot_date=tomorrow, slot_time="11:00",
                               status="CANCELLED", source="whatsapp"))
        db.session.commit()

        result = notifications.send_due_booking_reminders(tomorrow)
        assert result["due"] == 0
        assert NotificationLog.query.filter_by(template="booking_reminder").count() == 0


def test_the_bot_dials_a_locally_written_number_with_the_configured_code(app):
    """A customer saved the way the desk writes numbers must still be reachable.

    Everything downstream of `Customer.wa_number` — the conversation row, the API
    call, the log — is addressed with the normalised number. Store it in local
    form and the bot dials "0775550555", which is not a phone number at all: the
    send is rejected and the desk sees only a customer who never replies.
    """
    with app.app_context():
        customer = Customer(name="Local Writer", phone="0775550555")
        db.session.add(customer)
        db.session.flush()
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                               slot_date=tomorrow, slot_time="09:00",
                               status="CONFIRMED", source="whatsapp"))
        db.session.commit()

        result = notifications.send_due_booking_reminders(tomorrow)
        assert result["sent"] == 1

        logged = NotificationLog.query.filter_by(template="booking_reminder").one()
        assert logged.recipient == "263775550555", logged.recipient
        # The thread the message went into is the dialled number.
        assert WaConversation.query.filter_by(wa_id="263775550555").first() is not None


def test_changing_the_country_code_changes_what_the_bot_dials(app):
    """One setting moves the desk's dropdown and the bot's dialling together.

    This is the point of the shared module: the two used to be separate hard-coded
    "263"s, so a shop anywhere else could not be set up at all.
    """
    app.config["DEFAULT_COUNTRY_CODE"] = "27"
    try:
        with app.app_context():
            customer = Customer(name="Joburg Client", phone="0791123456")
            db.session.add(customer)
            db.session.flush()
            tomorrow = date.today() + timedelta(days=1)
            db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                                   slot_date=tomorrow, slot_time="09:00",
                                   status="CONFIRMED", source="whatsapp"))
            db.session.commit()

            assert notifications.send_due_booking_reminders(tomorrow)["sent"] == 1
            logged = NotificationLog.query.filter_by(template="booking_reminder").one()
            assert logged.recipient == "27791123456", logged.recipient
    finally:
        app.config["DEFAULT_COUNTRY_CODE"] = "263"


def test_a_customer_with_no_usable_number_is_not_dialled(app):
    """Better a logged failure than an API call to nonsense."""
    with app.app_context():
        customer = Customer(name="No Number", phone="123")
        db.session.add(customer)
        db.session.flush()
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                               slot_date=tomorrow, slot_time="09:00",
                               status="CONFIRMED", source="whatsapp"))
        db.session.commit()

        # "123" normalises to 263123, which is too short to be a phone number.
        assert notifications.send_due_booking_reminders(tomorrow)["sent"] == 0
        assert NotificationLog.query.filter_by(template="booking_reminder").count() == 0


def test_the_reminder_endpoint_runs_and_is_manager_only(app, auth_client):
    with app.app_context():
        customer = Customer(name="Endpoint Reminder", phone="+263771130003",
                            whatsapp="+263771130003")
        db.session.add(customer)
        db.session.flush()
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                               slot_date=tomorrow, slot_time="12:00",
                               status="CONFIRMED", source="whatsapp"))
        db.session.commit()

    res = auth_client.post(f"/api/bookings/reminders?date={tomorrow.isoformat()}")
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["sent"] == 1

    tech = app.test_client()
    assert tech.post("/auth/login", json={"email": "tech1@topclass.co.zw",
                                          "password": "topclass123"}).status_code == 200
    assert tech.post("/api/bookings/reminders").status_code == 403


def _invoice_for(customer, number: str = "INV-2026-9001", total: str = "115"):
    invoice = Invoice(invoice_no=number, customer_id=customer.id, currency="USD",
                      subtotal=100, vat=15, total=total, amount_paid=0, status="ISSUED")
    db.session.add(invoice)
    db.session.commit()
    return invoice


def test_payment_proof_is_filed_against_the_invoice(app):
    """A screenshot the customer sends must become a record, not just a chat line.

    Customers send EcoCash confirmations whether or not we ask for them, and
    nothing used to catch them: the picture sat in the thread and the payment
    went unrecorded until somebody happened to notice.
    """
    with app.app_context():
        customer = Customer(name="Payer", phone="+263771140001", whatsapp="+263771140001")
        db.session.add(customer)
        db.session.flush()
        invoice = _invoice_for(customer)

        conv = get_or_create_conversation("263771140001", "Payer")
        prompt = intent_router.handle_inbound(conv, interactive_id="m_pay")[0]
        assert "EcoCash" in prompt["body"]
        assert "INV-2026-9001" in prompt["body"]
        assert conv.state == "PAYMENT_PROOF"

        replies = intent_router.handle_inbound(
            conv, media_url="/uploads/whatsapp/ecocash.jpg")
        assert "Proof received" in replies[0]["body"]
        assert conv.state == "MAIN_MENU"

        proof = PaymentProof.query.first()
        assert proof is not None
        assert proof.url == "/uploads/whatsapp/ecocash.jpg"
        # A claim, not a payment: somebody still has to check it.
        assert proof.is_verified is False
        assert invoice.proof_count == 1

        task = Task.query.filter_by(category="Front desk").first()
        assert task is not None
        assert "INV-2026-9001" in task.title


def test_a_payment_proof_with_no_invoice_says_so(app):
    with app.app_context():
        conv = get_or_create_conversation("263771140002", "No Invoice")
        intent_router.handle_inbound(conv, interactive_id="m_pay")
        replies = intent_router.handle_inbound(
            conv, media_url="/uploads/whatsapp/mystery.jpg")
        assert "could not find an invoice" in replies[0]["body"]
        assert PaymentProof.query.count() == 0
        assert conv.state == "MAIN_MENU"


def test_a_settled_invoice_is_not_offered_for_payment(app):
    with app.app_context():
        customer = Customer(name="Paid Up", phone="+263771140005",
                            whatsapp="+263771140005")
        db.session.add(customer)
        db.session.flush()
        _invoice_for(customer, number="INV-2026-9002", total="0")

        conv = get_or_create_conversation("263771140005", "Paid Up")
        prompt = intent_router.handle_inbound(conv, interactive_id="m_pay")[0]
        assert "INV-2026-9002" not in prompt["body"]


def test_typing_instead_of_a_screenshot_keeps_waiting(app):
    """The nudge must not drop us out of the state, or the screenshot that
    follows would be filed as a damage photo."""
    with app.app_context():
        conv = get_or_create_conversation("263771140003", "Talker")
        intent_router.handle_inbound(conv, interactive_id="m_pay")
        intent_router.handle_inbound(conv, text_body="I paid yesterday")
        assert conv.state == "PAYMENT_PROOF"

        intent_router.handle_inbound(conv, media_url="/uploads/whatsapp/late.jpg")
        assert conv.state == "MAIN_MENU"


def test_a_damage_photo_is_not_mistaken_for_payment_proof(app):
    with app.app_context():
        conv = get_or_create_conversation("263771140004", "Photo Only")
        intent_router.handle_inbound(conv, media_url="/uploads/whatsapp/damage.jpg")
        assert PaymentProof.query.count() == 0
        pending = conv.ctx_get("pending_media")
        assert [m["url"] for m in pending] == ["/uploads/whatsapp/damage.jpg"]


def _collected_job(customer, job_no: str, reg: str = "WRK1000", collected=None):
    vehicle = Vehicle(customer_id=customer.id, reg_no=reg)
    db.session.add(vehicle)
    db.session.flush()
    job = JobCard(job_no=job_no, customer_id=customer.id, vehicle_id=vehicle.id,
                  service="Panel Beating & Spray Painting", stage="COLLECTED",
                  collected_at=collected)
    db.session.add(job)
    db.session.commit()
    return job


def test_a_warranty_complaint_becomes_a_workshop_task(app):
    """The warranty keyword used to print small print and forget the customer."""
    with app.app_context():
        customer = Customer(name="Comeback", phone="+263771150001",
                            whatsapp="+263771150001")
        db.session.add(customer)
        db.session.flush()
        job = _collected_job(customer, "TC-2026-9100", "CMB1000")

        conv = get_or_create_conversation("263771150001", "Comeback")
        prompt = intent_router.handle_inbound(conv, text_body="warranty")
        assert "TC-2026-9100" in prompt[0]["body"]
        assert conv.state == "WARRANTY_CLAIM"

        intent_router.handle_inbound(conv, text_body="Paint is lifting off the bonnet")
        task = Task.query.filter_by(category="Workshop").first()
        assert task is not None
        assert "TC-2026-9100" in task.title
        assert task.job_id == job.id
        assert task.priority == "HIGH"
        assert "Paint is lifting" in task.detail

        # The photo lands on the job card, where whoever assesses the claim looks.
        intent_router.handle_inbound(conv, media_url="/uploads/whatsapp/claim.jpg")
        assert JobPhoto.query.filter_by(job_id=job.id, kind="WARRANTY").count() == 1
        # …and it is still the same claim, not a second ticket.
        assert Task.query.filter_by(category="Workshop").count() == 1
        assert "claim.jpg" in task.detail


def test_a_warranty_claim_without_a_job_card_still_gets_a_ticket(app):
    with app.app_context():
        conv = get_or_create_conversation("263771150002", "No Job Card")
        prompt = intent_router.handle_inbound(conv, text_body="warranty")
        assert "could not match a job card" in prompt[0]["body"]
        assert conv.state == "WARRANTY_CLAIM"

        intent_router.handle_inbound(conv, text_body="Rust is coming through")
        task = Task.query.filter_by(category="Workshop").first()
        assert task is not None
        assert task.job_id is None
        assert "unmatched job card" in task.title
        assert "Rust is coming through" in task.detail


def test_a_poor_rating_raises_a_follow_up_task(app):
    with app.app_context():
        customer = Customer(name="Unhappy", phone="+263771160002",
                            whatsapp="+263771160002")
        db.session.add(customer)
        db.session.flush()
        job = _collected_job(customer, "TC-2026-9201", "UNH1000")

        conv = get_or_create_conversation("263771160002", "Unhappy")
        replies = intent_router.handle_inbound(conv, interactive_id=f"rate:{job.id}:1")

        assert job.feedback_rating == 1
        assert job.feedback_at is not None
        assert "sorry" in replies[0]["body"].lower()

        task = Task.query.filter_by(category="Front desk").first()
        assert task is not None
        assert job.job_no in task.title
        assert task.job_id == job.id


def test_an_excellent_rating_is_recorded_without_a_task(app):
    with app.app_context():
        customer = Customer(name="Happy", phone="+263771160001",
                            whatsapp="+263771160001")
        db.session.add(customer)
        db.session.flush()
        job = _collected_job(customer, "TC-2026-9200", "HAP1000")

        conv = get_or_create_conversation("263771160001", "Happy")
        intent_router.handle_inbound(conv, interactive_id=f"rate:{job.id}:5")

        assert job.feedback_rating == 5
        assert job.feedback_text == "Rated Excellent via WhatsApp"
        assert Task.query.filter_by(category="Front desk").count() == 0


def test_a_stale_rating_button_does_not_crash(app):
    """Meta redelivers taps, and a job can be deleted — say thank you either way."""
    with app.app_context():
        conv = get_or_create_conversation("263771160003", "Stale Tap")
        replies = intent_router.handle_inbound(conv, interactive_id="rate:99999:5")
        assert "Thank you" in replies[0]["body"]
        assert conv.state == "MAIN_MENU"


def test_feedback_requests_go_out_exactly_once(app):
    with app.app_context():
        customer = Customer(name="Ask Me", phone="+263771160004",
                            whatsapp="+263771160004")
        db.session.add(customer)
        db.session.flush()
        yesterday = date.today() - timedelta(days=1)
        job = _collected_job(
            customer, "TC-2026-9300", "ASK1000",
            collected=datetime(yesterday.year, yesterday.month, yesterday.day, 10, 0))

        first = notifications.send_due_feedback_requests(yesterday)
        assert (first["due"], first["sent"]) == (1, 1)
        assert job.feedback_requested_at is not None

        second = notifications.send_due_feedback_requests(yesterday)
        assert (second["due"], second["sent"]) == (0, 0)
        assert NotificationLog.query.filter_by(template="job_feedback").count() == 1


def test_open_jobs_are_not_asked_for_feedback(app):
    """Only a collected vehicle has an experience to rate."""
    with app.app_context():
        customer = Customer(name="Still In", phone="+263771160005",
                            whatsapp="+263771160005")
        db.session.add(customer)
        db.session.flush()
        vehicle = Vehicle(customer_id=customer.id, reg_no="STI1000")
        db.session.add(vehicle)
        db.session.flush()
        yesterday = date.today() - timedelta(days=1)
        db.session.add(JobCard(
            job_no="TC-2026-9301", customer_id=customer.id, vehicle_id=vehicle.id,
            service="Car Detailing", stage="PAINT",
            collected_at=datetime(yesterday.year, yesterday.month, yesterday.day, 10, 0)))
        db.session.commit()

        result = notifications.send_due_feedback_requests(yesterday)
        assert result["due"] == 0


def test_a_simulated_send_is_recorded_as_simulated_not_delivered(app):
    """The bug that made a production deployment look healthy.

    ``_post`` returns for a simulated send instead of raising, so ``_dispatch``
    used to fall through to its default status of *delivered* — and the inbox drew
    delivery ticks for messages that never left the machine.
    """
    from app.services.whatsapp_client import WhatsAppClient

    with app.app_context():
        conv = get_or_create_conversation("263771190001", "Sim Tester")
        message = WhatsAppClient().send_text(conv.wa_id, "hello", conversation=conv)
        assert message is not None
        assert message.status == "simulated"


def test_the_client_names_what_is_missing_for_a_live_send(app):
    """A silent no-op is indistinguishable from a working bot; name the gap."""
    from app.services.whatsapp_client import WhatsAppClient

    with app.app_context():
        missing = WhatsAppClient().missing_live_settings
        # TestConfig is deliberately simulator, so all three are absent.
        assert "WA_MODE=live" in missing
        assert "WA_ACCESS_TOKEN" in missing
        assert "WA_PHONE_NUMBER_ID" in missing



def test_nothing_is_missing_when_a_live_send_is_configured(app):
    """All three settings are required, and all three are reported when absent."""
    from app.services.whatsapp_client import WhatsAppClient

    with app.app_context():
        client = WhatsAppClient()
        original = {k: app.config[k] for k in ("WA_MODE", "WA_ACCESS_TOKEN",
                                               "WA_PHONE_NUMBER_ID")}
        try:
            app.config["WA_MODE"] = "live"
            app.config["WA_ACCESS_TOKEN"] = "token"
            app.config["WA_PHONE_NUMBER_ID"] = "123456"
            assert client.is_live is True
            assert client.missing_live_settings == []

            # Any one of them missing puts it back in simulator mode.
            app.config["WA_ACCESS_TOKEN"] = ""
            assert client.is_live is False
            assert client.missing_live_settings == ["WA_ACCESS_TOKEN"]
        finally:
            for key, value in original.items():
                app.config[key] = value


def test_the_whatsapp_status_endpoint_reports_the_mode(app, auth_client):
    body = auth_client.get("/api/whatsapp/status").get_json()
    assert body["live"] is False
    assert body["mode"] == "simulator"
    assert "WA_MODE=live" in body["missing"]
    # Booleans about the secrets, never the secrets themselves.
    assert body["verify_token_set"] is True
    assert body["app_secret_set"] is False
    assert "app-secret" not in str(body)


def test_a_manual_reply_says_so_when_it_was_not_sent(app):
    """The operator must not tick off a customer that was never answered."""
    from app.services.notifications import send_custom

    with app.app_context():
        conv = get_or_create_conversation("263771190002", "Manual Reply")
        assert send_custom(conv, "We will call you back.") is False


def test_an_enquiry_keeps_photos_and_pdfs_with_their_original_names(app):
    """A customer sends damage photos and an assessor's PDF in one go.

    Both must survive onto the enquiry, and the PDF must keep the name the
    customer's file actually had — otherwise the desk sees an anonymous link.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771110008", "Mixed Media")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        intent_router.handle_inbound(conv, text_body="ABC 4321")
        intent_router.handle_inbound(
            conv, interactive_id="svc:Panel Beating & Spray Painting")

        intent_router.handle_inbound(conv, media_url="/uploads/wa_1.jpg")
        intent_router.handle_inbound(conv, media_url="/uploads/wa_2.png",
                                     media_name="IMG_0431.PNG")
        intent_router.handle_inbound(conv, media_url="/uploads/wa_3.pdf",
                                     media_name="assessor report - ABC1234.pdf")

        intent_router.handle_inbound(conv, text_body="Bumper and bonnet")
        intent_router.handle_inbound(conv, text_body="Mixed Media Person")
        intent_router.handle_inbound(conv, text_body="skip")

        booking = (Booking.query.filter_by(source="whatsapp")
                   .order_by(Booking.id.desc()).first())
        assert booking is not None
        assert booking.photo_count == 3

        kinds = [p.kind for p in booking.photos]
        assert kinds == ["DAMAGE", "DAMAGE", "DOCUMENT"], kinds

        names = [p.caption for p in booking.photos]
        assert names[0] == "Sent via WhatsApp"
        assert names[1] == "IMG_0431.PNG"
        assert names[2] == "assessor report - ABC1234.pdf"


def test_a_caption_becomes_the_damage_description(app):
    """The words a customer types with a photo are the best description we get.

    The media branch runs before the state handlers, so the caption used to be
    discarded even though it had already been parsed off the message.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771110009", "Caption Tester")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        intent_router.handle_inbound(conv, text_body="ABC 7777")
        intent_router.handle_inbound(
            conv, interactive_id="svc:Panel Beating & Spray Painting")

        intent_router.handle_inbound(
            conv, media_url="/uploads/whatsapp/dent.jpg",
            text_body="Rear door caved in after a taxi reversed into it")

        assert "taxi reversed" in (conv.ctx_get("damage") or "")

        # A description the customer already typed must not be overwritten by a
        # later attachment's caption.
        intent_router.handle_inbound(conv, text_body="And the sill is rusted")
        intent_router.handle_inbound(conv, media_url="/uploads/whatsapp/x.jpg",
                                     text_body="another angle")
        assert conv.ctx_get("damage") == "And the sill is rusted"


def test_attachments_are_capped(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110010", "Album Sender")
        for n in range(intent_router.IntentRouter.MAX_PENDING_MEDIA + 4):
            intent_router.handle_inbound(conv, media_url=f"/uploads/whatsapp/{n}.jpg")

        pending = conv.ctx_get("pending_media")
        assert len(pending) == intent_router.IntentRouter.MAX_PENDING_MEDIA
        # The most recent ones are kept, not the first.
        assert pending[-1]["url"].endswith("11.jpg")


def test_a_pdf_caption_is_kept_on_the_job_card_attachment(app):
    """The warranty job photo should say what the file was called."""
    with app.app_context():
        customer = Customer(name="Warranty Pdf", phone="+263771150009",
                            whatsapp="+263771150009")
        db.session.add(customer)
        db.session.flush()
        job = _collected_job(customer, "TC-2026-9110", "WPDF100")

        conv = get_or_create_conversation("263771150009", "Warranty Pdf")
        intent_router.handle_inbound(conv, text_body="warranty")
        intent_router.handle_inbound(conv, media_url="/uploads/wa_9.pdf",
                                     media_name="paint report.pdf")

        photo = JobPhoto.query.filter_by(job_id=job.id, kind="WARRANTY").first()
        assert photo is not None
        assert photo.caption == "paint report.pdf"
        task = Task.query.filter_by(category="Workshop").first()
        assert "paint report.pdf" in task.detail


def test_the_services_list_does_not_trap_the_customer(app):
    """Showing the price list must not leave them stuck choosing a service.

    An earlier version entered QUOTE_SERVICE so that typed numbers would work —
    which meant "track my repair" typed next was read as a bad service choice and
    answered with the same list again.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771110011", "Not Trapped")
        intent_router.handle_inbound(conv, interactive_id="m_services")
        assert conv.state == "MAIN_MENU", conv.state

        replies = intent_router.handle_inbound(conv, text_body="track my repair")
        assert conv.state == "TRACK_REF", conv.state
        assert "job number" in replies[0]["body"].lower()


def test_service_rows_fit_whatsapps_limits(app):
    """WhatsApp caps a list row title at 24 characters and the description at 72.

    "Panel Beating & Spray Painting" is 30, so the rows carry a short label and
    the full name moves into the description.
    """
    with app.app_context():
        picker = intent_router.service_list_reply("en")
        rows = [r for s in picker["sections"] for r in s["rows"]]
        assert len(rows) == len(intent_router.SERVICE_NAMES)
        for row in rows:
            assert len(row["title"]) <= 24, row
            assert len(row.get("description", "")) <= 72, row
        titles = [r["title"] for r in rows]
        assert len(set(titles)) == len(titles), "two rows would read identically"

        priced = {r["title"]: r.get("description", "") for r in rows}
        assert "from USD 45" in priced["Car Detailing"]
        assert "Panel Beating & Spray Painting" in priced["Panel & Paint"]


def test_unpriced_services_carry_no_invented_figure(app):
    """No "from USD" on a service whose price depends on seeing the vehicle.

    Auto body and panel work used to inherit a flat USD 75 fallback, so the menu
    advertised "from USD 75" for a respray-class job nobody had looked at.
    """
    with app.app_context():
        picker = intent_router.service_list_reply("en")
        rows = {r["title"]: r for s in picker["sections"] for r in s["rows"]}

        assert "USD" not in rows["Auto Body"].get("description", "")
        assert "USD" not in rows["Panel & Paint"].get("description", "")
        # The priced services are untouched by this.
        assert "from USD 350" in rows["Ceramic Coating"]["description"]

        # And the brief says so in words, rather than staying silent.
        conv = get_or_create_conversation("263771110009", "No Price")
        intent_router.handle_inbound(conv, interactive_id="m_enquiries")
        brief = intent_router.handle_inbound(conv, interactive_id="enq:Auto Body")[0]["body"]
        assert "USD" not in brief, brief
        assert "Priced off the damage" in brief, brief


def test_an_unpriced_enquiry_records_no_quote_rather_than_a_guess(app):
    """The record holds no figure, and the reply omits the price line.

    Auto Body is priced off the damage. Before this, the record carried the flat
    USD 75 fallback and the confirmation quoted it back to the customer as an
    "indicative price".
    """
    with app.app_context():
        conv = get_or_create_conversation("263771110008", "No Guess")
        intent_router.handle_inbound(conv, interactive_id="m_enquiries")
        intent_router.handle_inbound(conv, interactive_id="enq:Auto Body")
        intent_router.handle_inbound(conv, text_body="Front bumper is bent in.")

        replies = intent_router.handle_inbound(conv, text_body="Tarisai Moyo")
        assert conv.state == "QUOTE_EMAIL"
        replies = intent_router.handle_inbound(conv, text_body="skip")

        booking = (Booking.query.filter_by(source="whatsapp")
                   .order_by(Booking.id.desc()).first())
        assert booking is not None
        assert booking.service == "Auto Body"
        assert not booking.quoted_from
        assert "Indicative price" not in replies[0]["body"], replies[0]["body"]
        # Rendered for the console without inventing a figure either.
        assert booking.to_dict()["quoted_from"] == 0.0


def test_a_priced_enquiry_still_quotes_its_published_price(app):
    """The other half of the guard: a real price is still shown and stored."""
    with app.app_context():
        conv = get_or_create_conversation("263771110010", "Has Price")
        intent_router.handle_inbound(conv, interactive_id="m_enquiries")
        intent_router.handle_inbound(conv, interactive_id="enq:Ceramic Coating")
        intent_router.handle_inbound(conv, text_body="Swirl marks all over.")

        intent_router.handle_inbound(conv, text_body="Rudo Chikafu")
        replies = intent_router.handle_inbound(conv, text_body="skip")

        booking = (Booking.query.filter_by(source="whatsapp")
                   .order_by(Booking.id.desc()).first())
        assert booking.service == "Ceramic Coating"
        assert float(booking.quoted_from) == 350.0
        assert "Indicative price" in replies[0]["body"], replies[0]["body"]
        assert "USD 350" in replies[0]["body"], replies[0]["body"]


def _emoji(chunk: str) -> list[str]:
    """Anything at or above U+2190 is a symbol or emoji.

    General punctuation (em dash U+2014, curly quotes, ellipsis) sits below that
    and is ordinary English typography, so it is allowed — the house rule is no
    icons, not pure ASCII.
    """
    return [c for c in chunk if ord(c) >= 0x2190]


def test_no_message_the_bot_sends_contains_an_emoji(app):
    """House style: no emoji in anything the customer receives."""
    with app.app_context():
        conv = get_or_create_conversation("263771110012", "Plain Text")
        emitted = []
        for step in ("hi", "menu", "warranty", "how much for a respray"):
            emitted += intent_router.handle_inbound(conv, text_body=step)
        emitted.append(intent_router.service_list_reply("en"))
        emitted.append(intent_router.main_menu_reply("Topclass", "en"))
        emitted.append(intent_router.more_menu_reply("en"))

        for reply in emitted:
            for chunk in (reply.get("body", ""), reply.get("footer", ""),
                          reply.get("button", "")):
                bad = _emoji(chunk)
                assert not bad, f"emoji {bad} in {chunk!r}"
            for section in reply.get("sections") or []:
                for row in section["rows"]:
                    for key in ("title", "description"):
                        bad = _emoji(row.get(key, ""))
                        assert not bad, f"emoji {bad} in row {row!r}"


def test_intent_detection():
    assert intent_router.detect_intent("how much for a respray?") == "quote"
    assert intent_router.detect_intent("where is my car") == "track"
    assert intent_router.detect_intent("I want to book") == "book"
    assert intent_router.detect_intent("can I speak to a person") == "human"
    assert intent_router.detect_intent("asdfgh") is None


def test_a_greeting_with_anything_after_it_still_opens_the_menu():
    """Requiring the *whole* message to be a greeting answered "hello again"
    with "Sorry, I did not understand that".

    A greeting carrying a real request must still be routed to the request, which
    is why the greeting pattern is last in the list and matches only at the start.
    """
    for text in ("hello", "Hello", "hi", "Hi there", "hello again",
                 "hey, good morning", "menu", "start"):
        assert intent_router.detect_intent(text) == "menu", text

    assert intent_router.detect_intent("hi, how much is a respray?") == "quote"
    assert intent_router.detect_intent("hello, is my car ready?") == "track"
    assert intent_router.detect_intent("hi, can I speak to a person") == "human"


def test_service_matching_is_fuzzy():
    assert intent_router.match_service("I need my bumper resprayed") == "Panel Beating & Spray Painting"
    assert intent_router.match_service("ceramic") == "Ceramic Coating"
    assert intent_router.match_service("ppf") == "Paint Protection Film"
    assert intent_router.match_service("nonsense") is None


def test_quote_flow_creates_booking_and_customer(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110002", "Quote Tester")

        # Straight to the service: no registration number is asked for.
        picker = intent_router.handle_inbound(conv, interactive_id="m_enquiries")
        assert conv.state == "QUOTE_SERVICE"
        assert picker[0]["type"] == "list"
        assert all(r["id"].startswith("enq:") for r in picker[0]["sections"][0]["rows"])

        # The brief, then the damage question.
        intent_router.handle_inbound(conv, interactive_id="enq:Car Detailing")
        assert conv.state == "QUOTE_DESC"
        assert conv.ctx_get("service") == "Car Detailing"

        intent_router.handle_inbound(conv, text_body="Interior and exterior deep clean")
        assert conv.state == "QUOTE_CONTACT"

        # The name is held rather than used: the email is the last question.
        replies = intent_router.handle_inbound(conv, text_body="Tendai Moyo")
        assert conv.state == "QUOTE_EMAIL"
        assert "email" in replies[0]["body"].lower()

        replies = intent_router.handle_inbound(conv, text_body="tendai@example.co.zw")
        text = replies[0]["body"]
        # An inbound WhatsApp request is an *enquiry* until somebody confirms
        # it, so it carries an enquiry reference — not a booking reference.
        assert "TC-ENQ" in text
        assert "TC-BKG" not in text
        assert conv.state == "MAIN_MENU"

        customer = Customer.query.filter_by(name="Tendai Moyo").first()
        assert customer is not None
        assert customer.email == "tendai@example.co.zw"
        booking = Booking.query.filter_by(customer_id=customer.id).first()
        assert booking is not None
        assert booking.source == "whatsapp"
        assert booking.service == "Car Detailing"
        assert booking.booking_reference is None
        # No plate asked for, so no Vehicle — exactly as the form behaves. The
        # plate is taken when the car actually arrives.
        assert booking.vehicle_id is None


def test_enquiry_keeps_the_photos_the_customer_sent(app):
    """A damage photo sent mid-quote must survive onto the enquiry.

    This was the biggest gap in the enquiry flow: the photo was parked in the
    conversation context and then dropped when the lead was created, so the desk
    never saw the one thing that lets it price the job.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771110007", "Photo Tester")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        intent_router.handle_inbound(conv, text_body="ABC 1234")
        intent_router.handle_inbound(
            conv, interactive_id="svc:Panel Beating & Spray Painting")

        # Two photos arrive while the bot is waiting for the description.
        intent_router.handle_inbound(conv, media_url="/uploads/whatsapp/one.jpg")
        intent_router.handle_inbound(conv, media_url="/uploads/whatsapp/two.jpg")

        intent_router.handle_inbound(conv, text_body="Rear bumper dented")
        intent_router.handle_inbound(conv, text_body="Takudzwa Marufu")
        intent_router.handle_inbound(conv, text_body="skip")

        booking = (Booking.query.filter_by(source="whatsapp")
                   .order_by(Booking.id.desc()).first())
        assert booking is not None
        assert booking.photo_count == 2
        assert [p.url for p in booking.photos] == ["/uploads/whatsapp/one.jpg",
                                                   "/uploads/whatsapp/two.jpg"]
        assert booking.to_dict()["photo_count"] == 2
        # Cleared, so the next enquiry cannot inherit these photos.
        assert conv.ctx_get("pending_media") is None


def test_the_bookings_api_surfaces_the_enquiry_photos(app, auth_client):
    """The desk must see the damage on the Bookings screen, not only in chat."""
    with app.app_context():
        conv = get_or_create_conversation("263771110003", "API Photo")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        intent_router.handle_inbound(conv, text_body="ACB 4321")
        intent_router.handle_inbound(conv, interactive_id="svc:Car Detailing")
        intent_router.handle_inbound(conv, media_url="/uploads/whatsapp/dent.jpg")
        intent_router.handle_inbound(conv, text_body="Dent on the rear door")
        intent_router.handle_inbound(conv, text_body="API Photo")
        intent_router.handle_inbound(conv, text_body="skip")

    rows = auth_client.get("/api/bookings").get_json()["items"]
    row = next(r for r in rows if r.get("photo_count"))
    assert row["photo_count"] == 1
    assert row["photos"][0]["url"] == "/uploads/whatsapp/dent.jpg"
    assert row["photos"][0]["kind"] == "DAMAGE"


def test_skipping_the_email_still_creates_the_enquiry(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110006", "No Email")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        intent_router.handle_inbound(conv, text_body="ABC 9999")
        intent_router.handle_inbound(conv, interactive_id="svc:Car Detailing")
        intent_router.handle_inbound(conv, text_body="Deep clean")
        intent_router.handle_inbound(conv, text_body="Skip Person")
        replies = intent_router.handle_inbound(conv, text_body="skip")
        assert "TC-ENQ" in replies[0]["body"]

        customer = Customer.query.filter_by(name="Skip Person").first()
        assert customer is not None
        assert customer.email is None
        assert Booking.query.filter_by(customer_id=customer.id).first() is not None


def test_an_unparseable_email_does_not_lose_the_enquiry(app):
    """Garbage in the email slot must not loop the bot or drop the lead."""
    with app.app_context():
        conv = get_or_create_conversation("263771110005", "Bad Email")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        intent_router.handle_inbound(conv, text_body="ABC 8888")
        intent_router.handle_inbound(conv, interactive_id="svc:Car Detailing")
        intent_router.handle_inbound(conv, text_body="Deep clean")
        intent_router.handle_inbound(conv, text_body="Bad Email Person")
        replies = intent_router.handle_inbound(conv, text_body="not an email at all")
        assert "TC-ENQ" in replies[0]["body"]
        assert conv.state == "MAIN_MENU"


def test_a_photo_without_a_job_card_is_not_attached_to_a_job(app):
    """With no open job the attachment waits for the enquiry, and says so."""
    with app.app_context():
        conv = get_or_create_conversation("263771110004", "Loose Photo")
        replies = intent_router.handle_inbound(
            conv, media_url="/uploads/whatsapp/stray.jpg")
        assert "1 attachment so far" in replies[0]["body"]
        pending = conv.ctx_get("pending_media")
        assert [m["url"] for m in pending] == ["/uploads/whatsapp/stray.jpg"]
        assert pending[0]["kind"] == "DAMAGE"


def test_tracking_replies_with_stage_progress(app):
    with app.app_context():
        customer = Customer(name="Track Tester", phone="+263771110003",
                            whatsapp="+263771110003")
        db.session.add(customer)
        db.session.flush()
        vehicle = Vehicle(customer_id=customer.id, reg_no="TRK1000",
                          make="Toyota", model="Hilux")
        db.session.add(vehicle)
        db.session.flush()
        job = JobCard(
            job_no="TC-2026-9001", customer_id=customer.id, vehicle_id=vehicle.id,
            service="Panel Beating & Spray Painting", stage="PAINT",
            promised_date=date(2026, 10, 5),
        )
        db.session.add(job)
        db.session.commit()

        conv = get_or_create_conversation("263771110003", "Track Tester")
        replies = intent_router.handle_inbound(conv, text_body="track TC-2026-9001")

        body = replies[0]["body"]
        assert "TC-2026-9001" in body
        assert "Spray Painting" in body
        assert "82%" in body                     # progress bar for the PAINT stage
        assert "spray booth" in body.lower()


def test_tracking_works_by_registration_plate(app):
    with app.app_context():
        customer = Customer(name="Plate Tester", phone="+263771110013",
                            whatsapp="+263771110013")
        db.session.add(customer)
        db.session.flush()
        vehicle = Vehicle(customer_id=customer.id, reg_no="PLT2000")
        db.session.add(vehicle)
        db.session.flush()
        job = JobCard(job_no="TC-2026-9002", customer_id=customer.id, vehicle_id=vehicle.id,
                      service="Car Detailing", stage="QC")
        db.session.add(job)
        db.session.commit()

        conv = get_or_create_conversation("263771110013", "Plate Tester")
        replies = intent_router.handle_inbound(conv, text_body="where is PLT 2000")

        assert "TC-2026-9002" in replies[0]["body"]
        assert "Quality Control" in replies[0]["body"]


def test_unknown_input_falls_back_then_offers_human(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110004", "Fallback Tester")
        first = intent_router.handle_inbound(conv, text_body="zzzzz")
        assert first[0]["type"] == "text"
        second = intent_router.handle_inbound(conv, text_body="qqqqq")
        assert conv.human_takeover is True
        assert "team" in second[0]["body"].lower()


def test_human_takeover_silences_the_bot(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110005", "Silent Tester")
        conv.human_takeover = True
        db.session.commit()
        replies = intent_router.handle_inbound(conv, text_body="hello?")
        assert replies == []


def test_talk_to_a_person_leaves_a_callback_ticket(app):
    """Silencing the bot is not the same as asking somebody to phone back."""
    with app.app_context():
        conv = get_or_create_conversation("263771110021", "Callback Tester")
        replies = intent_router.handle_inbound(conv, interactive_id="m_human")

        assert conv.human_takeover is True
        assert conv.state == "HUMAN"
        assert "CALL-" in replies[0]["body"]

        ticket = Task.query.filter_by(category="Front desk").first()
        assert ticket is not None
        assert ticket.status == "OPEN"
        assert ticket.priority == "HIGH"
        assert ticket.title == "Call back Callback Tester"
        assert "263771110021" in ticket.detail
        assert ticket.due_date == date.today()
        # The state it was in, not the HUMAN state it just moved to.
        assert "Bot was at: MAIN_MENU" in ticket.detail


def test_the_callback_ticket_is_not_duplicated(app):
    """Three taps is one phone call, not three tickets."""
    with app.app_context():
        conv = get_or_create_conversation("263771110022", "Repeat Caller")
        intent_router.handle_inbound(conv, interactive_id="m_human")
        assert Task.query.filter_by(category="Front desk").count() == 1

        # A redelivered tap, or the customer asking again after the takeover
        # flag is cleared, must reuse the open ticket.
        conv.human_takeover = False
        db.session.commit()
        intent_router.handle_inbound(conv, interactive_id="m_human")
        assert Task.query.filter_by(category="Front desk").count() == 1


def test_the_callback_ticket_reaches_the_to_do_board(app, auth_client):
    with app.app_context():
        conv = get_or_create_conversation("263771110023", "Board Callback")
        intent_router.handle_inbound(conv, interactive_id="m_human")

    data = auth_client.get("/api/tasks?window=day").get_json()
    assert any("Board Callback" in t["title"] for t in data["items"])
    assert data["count"] >= 1


def test_confusion_escalates_to_a_callback_ticket(app):
    """Two unanswered messages in a row must leave something action-able."""
    with app.app_context():
        conv = get_or_create_conversation("263771110024", "Confused Caller")
        intent_router.handle_inbound(conv, text_body="wibble")
        assert conv.human_takeover is False

        intent_router.handle_inbound(conv, text_body="wobble")
        assert conv.human_takeover is True
        assert Task.query.filter_by(category="Front desk").count() == 1


def test_language_switch_persists(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110006", "Lang Tester")
        intent_router.handle_inbound(conv, interactive_id="lang:sn")
        assert conv.ctx_get("lang") == "sn"
        replies = intent_router.handle_inbound(conv, text_body="menu")
        assert "Sarudzai" in replies[0]["body"] or replies[0]["type"] == "buttons"


def test_stop_unsubscribes_customer(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110007", "Opt Out Tester")
        conv.customer_id = None
        customer = Customer(name="Opt Out Tester", phone="+263771110007", whatsapp="+263771110007")
        db.session.add(customer)
        db.session.commit()
        conv.customer_id = customer.id
        db.session.commit()

        intent_router.handle_inbound(conv, text_body="STOP")
        db.session.refresh(customer)
        assert customer.whatsapp_opt_in is False


def test_webhook_verification(app, client):
    res = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=topclass-verify-token&hub.challenge=42")
    assert res.status_code == 200
    assert res.get_data(as_text=True) == "42"

    res = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42")
    assert res.status_code == 403


def test_a_redelivered_webhook_message_is_ignored(app, client):
    """Meta retries when we do not acknowledge fast enough.

    Handling the same message twice double-replies and double-notifies, so the
    message id is claimed before any work happens.
    """
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "263771110009",
                                  "profile": {"name": "Redelivery Tester"}}],
                    "messages": [{
                        "from": "263771110009",
                        "id": "wamid.REDELIVER1",
                        "type": "text",
                        "text": {"body": "Hi"},
                    }],
                },
            }],
        }],
    }
    first = client.post("/webhooks/whatsapp", json=payload)
    assert first.status_code == 200
    assert first.get_json()["received"] is True

    again = client.post("/webhooks/whatsapp", json=payload)
    assert again.status_code == 200
    assert again.get_json()["duplicates"] == 1

    with app.app_context():
        conv = WaConversation.query.filter_by(wa_id="263771110009").first()
        inbound = [m for m in conv.messages if m.direction == "inbound"]
        assert len(inbound) == 1, "the redelivery was processed twice"


def test_the_simulator_logs_what_the_customer_saw(app, auth_client):
    """Taps used to be logged as "[button:m_quote]", which reads as noise."""
    auth_client.post("/api/whatsapp/simulate",
                     json={"wa_id": "+263775550901", "body": "hi"})
    res = auth_client.post("/api/whatsapp/simulate",
                           json={"wa_id": "+263775550901", "interactive_id": "m_quote"})
    assert res.status_code == 200

    bodies = [m["body"] for m in res.get_json()["conversation"]["messages"]]
    assert not any(b.startswith("[button:") for b in bodies), bodies
    # The label is taken from the menu we actually offered them.
    assert any("quote" in b.lower() for b in bodies), bodies


def test_webhook_processes_inbound_message(app, client):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "263771110008", "profile": {"name": "Webhook Tester"}}],
                    "messages": [{
                        "from": "263771110008",
                        "id": "wamid.TEST",
                        "type": "text",
                        "text": {"body": "Hi"},
                    }],
                },
            }],
        }],
    }
    res = client.post("/webhooks/whatsapp", json=payload)
    assert res.status_code == 200
    assert res.get_json()["received"] is True

    with app.app_context():
        conv = WaConversation.query.filter_by(wa_id="263771110008").first()
        assert conv is not None
        assert conv.state == "MAIN_MENU"
        outbound = [m for m in conv.messages if m.direction == "outbound"]
        assert outbound, "the bot should have replied to the greeting"
        assert outbound[0].is_bot is True
        assert conv.last_message_at is not None


# ── language switching ───────────────────────────────────────────────────────
def test_language_can_be_switched_while_a_flow_is_waiting_for_data(app):
    """Naming a language mid-flow must switch, not be eaten as bad input.

    This used to be tested on the registration prompt, which rejected "Shona" as
    a dodgy plate number and answered in English — so there was no way to change
    language part-way through. The prompt it guards has moved; the rule has not.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771120001", "Midflow Tester")
        intent_router.handle_inbound(conv, interactive_id="m_enquiries")
        assert conv.state == "QUOTE_SERVICE"

        replies = intent_router.handle_inbound(conv, text_body="Shona")
        assert conv.ctx_get("lang") == "sn"
        body = " ".join(r.get("body", "") for r in replies)
        assert "sevhisi" in body.lower(), f"should re-ask in Shona: {body}"
        assert conv.state == "QUOTE_SERVICE", "the flow should continue, not reset"


def test_switching_language_mid_flow_keeps_earlier_answers(app):
    """Changing language must not silently discard what was already given."""
    with app.app_context():
        conv = get_or_create_conversation("263771120002", "Keep Answers")
        intent_router.handle_inbound(conv, interactive_id="enq:Car Detailing")
        intent_router.handle_inbound(conv, text_body="Deep clean inside and out")
        assert conv.ctx_get("service") == "Car Detailing"
        assert conv.state == "QUOTE_CONTACT"

        intent_router.handle_inbound(conv, text_body="ndebele")
        assert conv.ctx_get("lang") == "nd"
        assert conv.ctx_get("service") == "Car Detailing", "the service was thrown away"
        assert conv.ctx_get("damage") == "Deep clean inside and out"
        assert conv.state == "QUOTE_CONTACT", "the flow should continue, not reset"


def test_language_names_phrases_and_codes_all_switch(app):
    cases = [("English", "en"), ("chishona", "sn"), ("isiNdebele", "nd")]
    with app.app_context():
        for index, (raw, expected) in enumerate(cases):
            conv = get_or_create_conversation(f"2637711300{index:02d}", "Names")
            intent_router.handle_inbound(conv, text_body=raw)
            assert conv.ctx_get("lang") == expected, raw

        # Generic "change language" wording opens the chooser rather than guessing
        # which one they meant — in all three languages.
        for index, raw in enumerate(["mutauro", "shandura mutauro", "change language"]):
            conv = get_or_create_conversation(f"2637711400{index:02d}", "Chooser")
            replies = intent_router.handle_inbound(conv, text_body=raw)
            assert replies[0]["type"] == "list", raw
            rows = [row["id"] for s in replies[0]["sections"] for row in s["rows"]]
            assert "lang:sn" in rows


def test_the_language_option_is_reachable_from_a_menu(app):
    """m_lang was wired into the router but no menu ever offered it."""
    with app.app_context():
        conv = get_or_create_conversation("263771120004", "Reachability")
        replies = intent_router.handle_inbound(conv, text_body="hours")
        rows = [row["id"]
                for r in replies if r.get("type") == "list"
                for section in r.get("sections", [])
                for row in section["rows"]]
        assert "m_lang" in rows, f"no way to reach the language list: {rows}"

        replies = intent_router.handle_inbound(conv, interactive_id="m_lang")
        assert replies[0]["type"] == "list"
        intent_router.handle_inbound(conv, interactive_id="lang:sn")
        assert conv.ctx_get("lang") == "sn"


def test_language_is_detected_from_how_the_customer_writes(app):
    with app.app_context():
        conv = get_or_create_conversation("263771120005", "Auto Detect")
        intent_router.handle_inbound(
            conv, text_body="ndapota ndinoda kuziva nezve mota yangu")
        assert conv.ctx_get("lang") == "sn"


def test_detection_defers_to_an_explicit_choice(app):
    """A borrowed word must not override a language the customer actually picked."""
    with app.app_context():
        conv = get_or_create_conversation("263771120006", "Explicit Wins")
        intent_router.handle_inbound(conv, text_body="English")
        assert conv.ctx_get("lang") == "en"
        intent_router.handle_inbound(
            conv, text_body="ngicela ngifuna ukwazi ngemoto yami")
        assert conv.ctx_get("lang") == "en", "auto-detect overrode an explicit choice"


def test_a_single_stray_word_does_not_switch_language(app):
    with app.app_context():
        conv = get_or_create_conversation("263771120007", "One Marker")
        intent_router.handle_inbound(conv, text_body="how much is the mari for a respray")
        assert conv.ctx_get("lang") in (None, "en")


# ── webhook authenticity ─────────────────────────────────────────────────────
def test_webhook_signature_is_enforced_when_a_secret_is_configured():
    """The webhook URL is public; without this anyone could post fake messages."""
    from app import create_app
    from config import TestConfig

    class SignedConfig(TestConfig):
        WA_APP_SECRET = "app-secret"

    client = create_app(SignedConfig).test_client()
    payload = b'{"object":"whatsapp_business_account","entry":[]}'

    unsigned = client.post("/webhooks/whatsapp", data=payload,
                           content_type="application/json")
    assert unsigned.status_code == 403

    digest = hmac.new(b"app-secret", payload, hashlib.sha256).hexdigest()
    signed = client.post("/webhooks/whatsapp", data=payload,
                         content_type="application/json",
                         headers={"X-Hub-Signature-256": f"sha256={digest}"})
    assert signed.status_code == 200


def test_the_webhook_token_locks_the_delivery_url():
    """The verify token normally guards only the GET handshake, so it protects
    nothing about the messages Meta delivers. WA_WEBHOOK_TOKEN extends it to
    every delivery — the stopgap when there is no app secret yet."""
    from app import create_app
    from config import TestConfig

    class TokenConfig(TestConfig):
        WA_WEBHOOK_TOKEN = "011235"

    client = create_app(TokenConfig).test_client()
    payload = {"object": "whatsapp_business_account", "entry": []}

    # No token: an open door to forged messages, so it is refused outright.
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 403
    assert client.post("/webhooks/whatsapp?token=wrong", json=payload).status_code == 403
    # Header works too, for a proxy that cannot rewrite the URL.
    assert client.post("/webhooks/whatsapp", json=payload,
                       headers={"X-Webhook-Token": "nope"}).status_code == 403

    ok = client.post("/webhooks/whatsapp?token=011235", json=payload)
    assert ok.status_code == 200
    assert ok.get_json()["received"] is True
    assert client.post("/webhooks/whatsapp", json=payload,
                       headers={"X-Webhook-Token": "011235"}).status_code == 200


def test_the_webhook_token_covers_the_short_alias_too():
    """Both routes are the same door; locking one and not the other is useless."""
    from app import create_app
    from config import TestConfig

    class TokenConfig(TestConfig):
        WA_WEBHOOK_TOKEN = "011235"

    client = create_app(TokenConfig).test_client()
    payload = {"object": "whatsapp_business_account", "entry": []}

    assert client.post("/webhook", json=payload).status_code == 403
    assert client.post("/webhook?token=011235", json=payload).status_code == 200


def test_the_verify_handshake_is_not_gated_by_the_webhook_token():
    """Saving the webhook in Meta sends the verify token, never the URL token —
    gating the GET would make the handshake impossible to pass."""
    from app import create_app
    from config import TestConfig

    class TokenConfig(TestConfig):
        WA_WEBHOOK_TOKEN = "011235"

    client = create_app(TokenConfig).test_client()
    res = client.get("/webhooks/whatsapp?hub.mode=subscribe"
                     "&hub.verify_token=topclass-verify-token&hub.challenge=77")
    assert res.status_code == 200
    assert res.get_data(as_text=True) == "77"


def test_an_empty_webhook_token_leaves_behaviour_unchanged(app, client):
    """Opt-in: an install that has not set it must keep working."""
    assert app.config["WA_WEBHOOK_TOKEN"] == ""
    res = client.post("/webhooks/whatsapp",
                      json={"object": "whatsapp_business_account", "entry": []})
    assert res.status_code == 200


# ── WhatsApp Flows ───────────────────────────────────────────────────────────
def _flow_payload(wa_id: str, answers: dict, *, token="enquiry:2026-10-01",
                  response_json: str | None = None, message_id="wamid.FLOW"):
    """A Meta nfm_reply, as Meta actually sends it.

    ``response_json`` is a JSON **string**, not an object — building the fixture
    with a real dict would pass against a parser that cannot read Meta's payload.
    """
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": wa_id, "profile": {"name": "Form Filler"}}],
            "messages": [{
                "from": wa_id, "id": message_id, "type": "interactive",
                "interactive": {
                    "type": "nfm_reply",
                    "nfm_reply": {
                        "response_json": (response_json if response_json is not None
                                          else json.dumps(answers)),
                        "flow_token": token,
                    },
                },
            }],
        }}]}],
    }


ENQUIRY_ANSWERS = {
    "contact_name": "Tariro Moyo",
    "contact_email": "tariro@example.co.zw",
    "reg_no": "adz 4477",
    "service": "Panel Beating & Spray Painting",
    "vehicle": "Toyota Hilux 2019",
    "damage": "Front bumper cracked, bonnet dented on the left.",
    "preferred_date": (date.today() + timedelta(days=5)).isoformat(),
    "preferred_time": "09:00",
}


def test_a_submitted_enquiry_form_raises_an_enquiry(app, client):
    """The answers become a real record, not a chat message.

    A Flow is only worth building if the form fields land in the database as
    fields — otherwise the desk still has to read them off the thread.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload("263773330001",
                                                             ENQUIRY_ANSWERS))
    assert res.status_code == 200

    with app.app_context():
        booking = Booking.query.first()
        assert booking is not None, "the form created no enquiry"
        assert booking.status == "REQUESTED"
        assert booking.source == "whatsapp"
        assert booking.reference.startswith("TC-ENQ-")
        assert booking.service == "Panel Beating & Spray Painting"

        customer = Customer.query.first()
        assert customer.name == "Tariro Moyo"
        assert customer.email == "tariro@example.co.zw"
        # The plate is normalised the same way the desk and the chat path do it.
        assert Vehicle.query.first().reg_no == "ADZ4477"

        # The form's own words are kept, plus what it adds about the vehicle.
        assert "Front bumper cracked" in booking.notes
        assert "Toyota Hilux 2019" in booking.notes
        assert booking.slot_time == "09:00"


def test_attachments_sent_before_the_form_land_on_the_enquiry(app, client):
    """Pictures sent before the form still land on the enquiry.

    They arrive *before* the form is submitted, so they are sitting in the
    conversation context. Losing them here would throw away the most useful thing
    the desk receives.

    And because the enquiry already has files against it, the bot does **not**
    ask for photographs afterwards.
    """
    with app.app_context():
        conv = get_or_create_conversation("263773330002", "Picture Sender")
        for index in range(3):
            intent_router.handle_inbound(conv, media_url=f"/uploads/x/p{index}.jpg",
                                         media_name=f"damage{index}.jpg",
                                         text_body="Front end damage")
        intent_router.handle_inbound(conv, media_url="/uploads/x/report.pdf",
                                     media_name="assessor report.pdf")

    assert client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330002", ENQUIRY_ANSWERS, message_id="wamid.FLOW2")).status_code == 200

    with app.app_context():
        booking = Booking.query.first()
        photos = BookingPhoto.query.filter_by(booking_id=booking.id).all()
        assert len(photos) == 4, [p.filename for p in photos]
        kinds = sorted(p.kind for p in photos)
        assert kinds == ["DAMAGE", "DAMAGE", "DAMAGE", "DOCUMENT"]
        # The customer's own filename survives — "assessor report.pdf" is the
        # only clue the desk has about what the PDF is.
        assert any(p.caption == "assessor report.pdf" for p in photos)

        conv = WaConversation.query.filter_by(wa_id="263773330002").first()
        spoken = [m.body or "" for m in sorted(conv.messages, key=lambda m: m.id)
                  if m.direction == "outbound"]
    assert not any("photographs of the damage" in b for b in spoken), spoken


def test_a_form_that_carried_files_does_not_ask_for_them_again(app, client):
    """Asking again reads as though nobody looked at what was just sent."""
    client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330031", _picker_answers(names=("damage.jpg",)),
        message_id="wamid.HASFILES"))
    with app.app_context():
        conv = WaConversation.query.filter_by(wa_id="263773330031").first()
        spoken = [m.body or "" for m in sorted(conv.messages, key=lambda m: m.id)
                  if m.direction == "outbound"]
    assert any("Request logged" in b for b in spoken), spoken
    assert not any("photographs of the damage" in b for b in spoken), spoken


def test_a_form_with_no_files_still_asks_for_them(app, client):
    """The picker is optional, so an empty one must still get the prompt."""
    client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330032", _picker_answers(names=(), mimes=()),
        message_id="wamid.NOFILES2"))
    with app.app_context():
        conv = WaConversation.query.filter_by(wa_id="263773330032").first()
        spoken = [m.body or "" for m in sorted(conv.messages, key=lambda m: m.id)
                  if m.direction == "outbound"]
    assert any("photographs of the damage" in b for b in spoken), spoken


# The payload the builder produced and Meta accepted, verbatim. The picker is
# deliberately NOT referenced in it — adding that key fails Flow validation with
# two errors — so no key for the media can be predicted from our side.
ACCEPTED_FORM_ANSWERS = {
    "screen_0_What_do_you_need_0": "0_Autobody",
    "screen_0_Vehicle_Make_Model_1": "Toyota Hilux 2019",
    "screen_0_Describe_the_enquiry_2": "Someone reversed into the left rear door.",
}


def test_the_accepted_form_payload_raises_a_correct_enquiry(app, client):
    """The exact keys the live Flow sends, with no picker key at all.

    This is the shape that will really arrive, so it is the one worth pinning: a
    service id the builder generated, a make and model, a description, and nothing
    else.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330041", ACCEPTED_FORM_ANSWERS, message_id="wamid.ACCEPTED"))
    assert res.status_code == 200

    with app.app_context():
        booking = Booking.query.first()
        assert booking is not None, "the live payload raised no enquiry"
        assert booking.service == "Auto Body", booking.service
        assert booking.vehicle_id is None, "a plate-less enquiry got a vehicle"
        assert BookingPhoto.query.count() == 0, "media appeared from nowhere"

        # The make and model, and the customer's own words, both reach the desk.
        assert "Toyota Hilux 2019" in (booking.notes or ""), booking.notes
        assert "reversed into the left rear door" in (booking.notes or ""), booking.notes


def test_a_description_named_for_the_enquiry_is_read_as_the_damage(app, client):
    """The description field is labelled "Describe the enquiry", not "damage".

    It has to be resolved by *meaning* — nothing in the payload key or the label
    says "damage" — or the one field the desk actually reads lands nowhere. It must
    also not be mistaken for anything else: no other lookup may claim it.
    """
    client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330042", ACCEPTED_FORM_ANSWERS, message_id="wamid.DESCRIBE"))

    with app.app_context():
        booking = Booking.query.first()
        assert "reversed into the left rear door" in (booking.notes or ""), booking.notes
        # Nothing else took it instead.
        assert booking.slot_time is None, booking.slot_time
        assert Vehicle.query.count() == 0, "the description became a plate"
        assert (booking.customer.email or "") == "", booking.customer.email


def test_a_blank_description_is_not_an_answer(app, client):
    """Optional, so an empty one must fall back rather than write blank notes."""
    answers = dict(ACCEPTED_FORM_ANSWERS, **{"screen_0_Describe_the_enquiry_2": ""})
    client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330043", answers, message_id="wamid.NODESC"))

    with app.app_context():
        booking = Booking.query.first()
        assert booking is not None
        assert "reversed into" not in (booking.notes or "")
        assert "Submitted the enquiry form on WhatsApp" in (booking.notes or ""), booking.notes


def test_picker_media_is_found_whatever_key_carries_it(app, client):
    """Which key holds the media is not ours to choose, so do not depend on it.

    The accepted Flow does not reference its picker in the ``complete`` payload,
    which means the media — if Meta sends it at all — arrives under a key we
    cannot predict. Meta's own docs show the *component* name (``photo_picker``);
    the builder generates payload-style keys. Our reader looks for the **shape**
    instead, so either route attaches the files and neither re-asks for them.
    """
    for index, key in enumerate(("Photos_of_the_damage",
                                 "screen_0_Photos_of_the_damage_2",
                                 "media")):
        wa_id = f"26377333005{index}"
        answers = dict(ACCEPTED_FORM_ANSWERS)
        answers[key] = [{"file_name": "panel.jpg", "mime_type": "image/jpeg",
                         "sha256": "PqHgadp8cJ/N6mvAYGNMxhs9Ra5hbZFcctCtCClXsMU=",
                         "id": f"3631120727158{index:02d}"}]

        assert client.post("/webhooks/whatsapp", json=_flow_payload(
            wa_id, answers, message_id=f"wamid.PICKERKEY{index}")).status_code == 200

        with app.app_context():
            conv = WaConversation.query.filter_by(wa_id=wa_id).first()
            assert conv is not None, f"{key}: the form was not processed"
            spoken = [m.body or "" for m in sorted(conv.messages, key=lambda m: m.id)
                      if m.direction == "outbound"]
            assert any("Request logged" in b for b in spoken), (key, spoken)
            assert not any("photographs of the damage" in b for b in spoken), \
                f"{key}: asked for photos it already had"

        # each of the three forms attached exactly one file
        with app.app_context():
            assert BookingPhoto.query.count() == index + 1, \
                f"{key}: {BookingPhoto.query.count()} attachments for {index + 1} forms"


def test_the_form_without_a_plate_still_names_the_vehicle(app, client):
    """`reg_no` and `damage` are off the form; the make and model replaces them.

    With no registration, `Vehicle.reg_no` is NOT NULL, so **no Vehicle record is
    created at all** — the make and model is all that identifies the car, so it
    has to reach both the booking notes and the confirmation the customer reads.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330033",
        {"service": "Car Detailing", "vehicle": "Toyota Hilux 2019"},
        message_id="wamid.VEHONLY"))
    assert res.status_code == 200

    with app.app_context():
        booking = Booking.query.first()
        assert booking is not None
        assert booking.vehicle_id is None, "a plate-less enquiry got a vehicle"
        assert Vehicle.query.count() == 0
        assert "Toyota Hilux 2019" in (booking.notes or "")

        conv = WaConversation.query.filter_by(wa_id="263773330033").first()
        confirmation = next(m.body for m in conv.messages
                            if m.direction == "outbound" and "Request logged" in (m.body or ""))
    assert "*Vehicle:* Toyota Hilux 2019" in confirmation, confirmation
    # An empty label would read as a missing field.
    assert "*Vehicle:* \n" not in confirmation, confirmation


def test_the_form_prompts_for_the_photographs(app, client):
    """A form submission does not skip the photo request."""
    client.post("/webhooks/whatsapp", json=_flow_payload("263773330003",
                                                        ENQUIRY_ANSWERS))
    with app.app_context():
        conv = WaConversation.query.filter_by(wa_id="263773330003").first()
        spoken = [m.body or "" for m in
                  WaMessage.query.filter_by(conversation_id=conv.id,
                                            direction="outbound")
                  .order_by(WaMessage.id).all()]

    confirmation = next((b for b in spoken if "Request logged" in b), None)
    assert confirmation, spoken
    assert "TC-ENQ-" in confirmation
    invitation = next(index for index, body in enumerate(spoken)
                      if "photographs of the damage" in body)
    # Confirmation first, then the ask — the other order reads as a non-sequitur.
    assert spoken.index(confirmation) < invitation, spoken


def test_the_enquiry_form_is_readable_in_the_thread(app, client):
    """The row keeps the body the desk reads, so the answers belong in it."""
    client.post("/webhooks/whatsapp", json=_flow_payload("263773330004",
                                                        ENQUIRY_ANSWERS))
    with app.app_context():
        bodies = [m.body or "" for m in WaConversation.query.filter_by(
            wa_id="263773330004").first().messages if m.direction == "inbound"]

    assert any("reg no: adz 4477" in body for body in bodies), bodies
    assert any("contact name: Tariro Moyo" in body for body in bodies), bodies


def test_a_flow_whose_answers_are_not_json_does_not_crash(app, client):
    """Meta owns the payload, so a malformed one has to degrade quietly.

    The message is still accepted (Meta retries a non-200 for ever), but nothing
    is invented from it — a record reading "reg TBC, service Panel Beating" on
    the desk's list is worse than no record at all.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330005", {}, response_json="{not json at all"))
    assert res.status_code == 200

    with app.app_context():
        assert Booking.query.count() == 0
        assert Customer.query.count() == 0
        spoken = [m.body or "" for m in
                  WaMessage.query.filter_by(direction="outbound").all()]
    assert any("came through empty" in body for body in spoken), spoken


def test_a_form_that_arrives_empty_is_not_turned_into_an_enquiry(app, client):
    """Valid JSON with nothing in it is the same problem as invalid JSON."""
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330009", {}, message_id="wamid.EMPTY"))
    assert res.status_code == 200

    with app.app_context():
        assert Booking.query.count() == 0
        assert Customer.query.count() == 0


# The Flow builder names its own components ("What_do_you_need_11da7f") and the
# completion payload key carries the screen and component index, so a real
# ``response_json`` key looks like ``screen_0_What_do_you_need_0``. A form pasted
# out of the builder must work without the builder being renamed to suit us.
BUILDER_NAMED_ANSWERS = {
    "screen_0_What_do_you_need_0": "0_Autobody",
    "screen_0_Vehicle_Make_Model_1": "Toyota Hilux 2019",
}


def test_a_form_named_by_the_meta_builder_still_lands(app, client):
    """The customer's answers must not be lost to a naming convention.

    Reading only our own field names meant a Flow that worked perfectly in the
    builder produced an enquiry priced against the default service card and with
    nothing on it saying which vehicle it was about — a wrong quotation nobody
    would notice until the customer queried it.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330021", BUILDER_NAMED_ANSWERS, message_id="wamid.BUILDER"))
    assert res.status_code == 200

    with app.app_context():
        booking = Booking.query.first()
        assert booking is not None, "a builder-named form raised no enquiry"
        # "0_Autobody" is the builder's option id, not the service name.
        assert booking.service == "Auto Body", booking.service
        assert "Toyota Hilux 2019" in (booking.notes or "")

        spoken = " ".join(m.body or "" for m in
                          WaMessage.query.filter_by(direction="outbound").all())
        assert "Toyota Hilux 2019" in spoken, spoken

        # No plate on the form, so no vehicle row — rather than one called "TBC".
        assert Vehicle.query.count() == 0


def test_a_picker_key_is_never_read_as_the_damage_description(app):
    """ "Photos of the damage" must not answer the question "describe the damage".

    A picker's answer is a list of media objects and its key contains the word
    "damage". Read as text it would be a wall of JSON in the booking notes and on
    every screen that shows them.
    """
    with app.app_context():
        conv = get_or_create_conversation("263773330022", "Picker Owner")
        intent_router.handle_inbound(conv, flow_response={
            "flow_token": "enquiry",
            "data": {
                "screen_0_What_do_you_need_0": "3_Car_Detailing",
                "screen_0_Vehicle_Make_Model_1": "Mazda 3",
                "screen_0_Photos_of_the_damage_2": [
                    {"id": "9988", "file_name": "panel.jpg", "mime_type": "image/jpeg"},
                ],
            },
        })
        booking = Booking.query.first()
        assert booking is not None
        assert "{" not in (booking.notes or ""), booking.notes
        assert "file_name" not in (booking.notes or ""), booking.notes


def test_an_empty_picker_does_not_count_as_an_answer(app, client):
    """``str([])`` is truthy, so an unanswered picker used to look answered.

    A form carrying nothing but an empty picker is a form the customer did not
    fill in, and the desk should not get a hollow record for it.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330023",
        {"screen_0_Photos_of_the_damage_2": []},
        message_id="wamid.EMPTYPICK"))
    assert res.status_code == 200

    with app.app_context():
        assert Booking.query.count() == 0
        assert Customer.query.count() == 0


def test_the_enquiry_form_opens_the_screen_configured_for_it(app):
    """Meta rejects a screen name the Flow does not define.

    The name lives in the Flow, not in our code, so it is configurable and
    defaults to what Meta's builder calls the first screen.
    """
    with app.app_context():
        conv = get_or_create_conversation("263773330024", "Screen Opener")
        cfg = app.config
        cfg["WA_FLOW_ENQUIRY_ID"] = "1234567890123456"
        cfg["WA_FLOW_ENQUIRY_SCREEN"] = "QUESTION_ONE"

        replies = intent_router.handle_inbound(conv, interactive_id="m_form")
        flow = [r for r in replies if r.get("type") == "flow"]
        assert flow, replies
        assert flow[0]["screen"] == "QUESTION_ONE", flow[0]

        # And a rename in Meta is an .env change, not a code change.
        cfg["WA_FLOW_ENQUIRY_SCREEN"] = "ENQUIRY"
        replies = intent_router.handle_inbound(conv, interactive_id="m_form")
        assert [r for r in replies if r.get("type") == "flow"][0]["screen"] == "ENQUIRY"
        cfg["WA_FLOW_ENQUIRY_ID"] = ""


def test_a_flow_with_an_unknown_token_still_answers(app, client):
    """A form we do not recognise must not leave the customer talking to a wall."""
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330006", {"anything": "at all"}, token="something-else"))
    assert res.status_code == 200

    with app.app_context():
        spoken = [m.body or "" for m in
                  WaMessage.query.filter_by(direction="outbound").all()]
    assert spoken, "an unrecognised form got no reply at all"
    assert Booking.query.count() == 0


def test_an_unknown_service_on_the_form_still_logs_the_enquiry(app):
    """A dropdown carries whatever was typed into the Flow builder.

    An unrecognised option must not raise inside the pricing lookup and lose the
    enquiry — the desk would rather have it with the default service than not.
    """
    with app.app_context():
        conv = get_or_create_conversation("263773330007", "Odd Service")
        replies = intent_router.handle_inbound(conv, flow_response={
            "flow_token": "enquiry",
            "data": {"contact_name": "Odd Service", "reg_no": "ABC123",
                     "service": "Respray", "damage": "Scratched door"},
        })
        assert replies, "no reply for an unrecognised service"
        booking = Booking.query.first()
        assert booking is not None
        assert booking.service  # fell back to something real
        assert booking.service in intent_router.SERVICE_NAMES


def test_a_flow_response_is_not_eaten_by_a_waiting_state(app):
    """A form can be submitted while the bot is mid-question.

    The answers must be read as a form, not matched against the pending step —
    otherwise a service name typed while the bot was waiting for a damage
    description would be recorded as the description, and the rest of the form
    would be silently dropped.
    """
    with app.app_context():
        conv = get_or_create_conversation("263773330008", "Mid Flow")
        intent_router.handle_inbound(conv, interactive_id="enq:Car Detailing")
        assert conv.state == "QUOTE_DESC"

        intent_router.handle_inbound(conv, flow_response={
            "flow_token": "enquiry",
            "data": {"contact_name": "Mid Flow", "reg_no": "XYZ999",
                     "service": "Car Detailing", "damage": "Interior deep clean"},
        })
        booking = Booking.query.first()
        assert booking is not None, "the form was swallowed by the waiting state"
        assert booking.service == "Car Detailing"
        assert booking.vehicle.reg_no == "XYZ999"
        assert conv.state == "MAIN_MENU"


def test_a_form_that_does_not_ask_for_a_name_uses_the_whatsapp_one(app):
    """The name comes from WhatsApp, not from a form field.

    WhatsApp sends the profile name with every inbound message, so asking the
    customer to type it again is one more field to abandon — and an empty
    ``contact_name`` used to create a customer record with a blank name.
    """
    with app.app_context():
        conversation = get_or_create_conversation("263773330011", "Rudo Chikafu")
        intent_router.handle_inbound(conversation, flow_response={
            "flow_token": "enquiry",
            "data": {"reg_no": "NAM111", "service": "Car Detailing",
                     "damage": "Interior deep clean"},
        })

        customer = Customer.query.first()
        assert customer is not None
        assert customer.name == "Rudo Chikafu", customer.name
        assert Booking.query.first().customer_id == customer.id


def test_a_form_supplied_name_still_wins_over_the_whatsapp_one(app):
    """An explicit answer beats an inferred one — the customer typed it."""
    with app.app_context():
        conversation = get_or_create_conversation("263773330012", "Rudo C")
        intent_router.handle_inbound(conversation, flow_response={
            "flow_token": "enquiry",
            "data": {"contact_name": "Rudo Chikafu", "reg_no": "NAM222",
                     "service": "Car Detailing", "damage": "Interior deep clean"},
        })
        assert Customer.query.first().name == "Rudo Chikafu"


def test_a_customer_record_is_never_created_with_a_blank_name(app):
    """The safety net, for a form and a conversation with no name at all.

    ``Customer.name`` is NOT NULL, but an empty string satisfies that — so a
    nameless form used to store a customer whose name rendered as nothing on every
    screen that shows it.
    """
    with app.app_context():
        conversation = get_or_create_conversation("263773330013")
        assert conversation.profile_name in (None, "")

        intent_router.handle_inbound(conversation, flow_response={
            "flow_token": "enquiry",
            "data": {"reg_no": "NAM333", "service": "Car Detailing",
                     "damage": "Interior deep clean"},
        })

        customer = Customer.query.first()
        assert customer is not None
        assert customer.name.strip(), "a blank customer name was written"
        assert customer.name == "WhatsApp +263773330013"


def test_the_booking_form_uses_the_whatsapp_name_too(app):
    with app.app_context():
        conversation = get_or_create_conversation("263775550005", "Rudo Chikafu")
        intent_router.handle_inbound(conversation, flow_response={
            "flow_token": "booking",
            "data": {k: v for k, v in BOOKING_ANSWERS.items() if k != "contact_name"},
        })
        assert Customer.query.first().name == "Rudo Chikafu"


def test_an_enquiry_form_with_no_day_does_not_promise_a_slot(app):
    """Day and time belong on the booking form, not the enquiry.

    With neither collected, the confirmation must not print a preferred slot — and
    it must not say the booking will be *confirmed*, because nothing was reserved.
    """
    with app.app_context():
        conversation = get_or_create_conversation("263775550006", "No Day")
        replies = intent_router.handle_inbound(conversation, flow_response={
            "flow_token": "enquiry",
            "data": {"reg_no": "NOD111", "service": "Car Detailing",
                     "damage": "Interior deep clean"},
        })
        spoken = "\n".join(r.get("body", "") for r in replies)
        assert "Preferred slot" not in spoken, spoken
        assert Booking.query.first().slot_time is None


# ── the Flow's own media (PhotoPicker / DocumentPicker) ──────────────────────
def _picker_answers(*, key="attachment", names=("IMG_5237.jpg",), mimes=("image/jpeg",)):
    """A submitted form carrying picker media, in Meta's response-message shape."""
    return {**ENQUIRY_ANSWERS,
            key: [{"file_name": n, "mime_type": m, "sha256": "PqHgadp8=",
                   "id": f"3631120727156{index:03d}"}
                  for index, (n, m) in enumerate(zip(names, mimes))]}


def test_a_service_id_from_the_flow_builder_still_resolves(app):
    """A Dropdown returns the **id**, and the builder decorates it.

    Meta's builder generates option ids from the titles, so the answer arrives as
    ``0_Autobody`` / ``1_Panel_Beating_&_Spray_Painting`` rather than the plain
    name. Matching only the plain name made every form enquiry fall back to the
    default service — a wrong price, not a visible error.
    """
    assert intent_router.match_flow_service("0_Autobody") == "Auto Body"
    assert intent_router.match_flow_service(
        "1_Panel_Beating_&_Spray_Painting") == "Panel Beating & Spray Painting"
    assert intent_router.match_flow_service("6_Car_Vinyl_Wrapping") == "Car Vinyl Wrapping"
    # ...and the plain forms still work.
    assert intent_router.match_flow_service("Auto Body") == "Auto Body"
    assert intent_router.match_flow_service("Autobody") == "Auto Body"
    assert intent_router.match_flow_service("panel_spray") == "Panel Beating & Spray Painting"
    assert intent_router.match_flow_service("AUTO_BODY") == "Auto Body"
    # A genuinely odd answer still degrades instead of raising.
    assert intent_router.match_flow_service("Respray") == "Panel Beating & Spray Painting"


def test_the_worst_case_id_is_the_one_that_used_to_misfile(app):
    """`auto_body` and `0_Autobody` both used to become Panel & Paint.

    Nothing in the synonym table matches "auto", so Auto Body — the first option
    in the list, and the one a customer picks by default — silently landed on the
    wrong rate card.
    """
    with app.app_context():
        conversation = get_or_create_conversation("263773330021", "Auto Body Picker")
        intent_router.handle_inbound(conversation, flow_response={
            "flow_token": "enquiry",
            "data": {"reg_no": "AUT111", "service": "0_Autobody",
                     "damage": "Kerbed the front bumper."},
        })
        assert Booking.query.first().service == "Auto Body"


def test_files_the_flow_collected_become_enquiry_attachments(app, client):
    """The picker's media has to land on the record, not vanish.

    Meta delivers it under the component's own name — the customer's to choose —
    so the webhook finds it by shape and downloads each entry exactly like a photo
    sent in the chat. Same `BookingPhoto`, so the desk cannot tell the routes apart.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330022",
        _picker_answers(names=("IMG_5237.jpg", "IMG_5238.jpg", "report.pdf"),
                        mimes=("image/jpeg", "image/jpeg", "application/pdf")),
        message_id="wamid.PICKER", token="enquiry"))
    assert res.status_code == 200

    with app.app_context():
        booking = Booking.query.first()
        assert booking is not None
        photos = booking.photos
        assert len(photos) == 3, [p.caption for p in photos]
        # A picture is DAMAGE; the report is DOCUMENT.
        kinds = sorted(p.kind for p in photos)
        assert kinds == ["DAMAGE", "DAMAGE", "DOCUMENT"], kinds
        assert any(p.caption == "report.pdf" for p in photos), [p.caption for p in photos]


def test_a_simulated_attachment_actually_resolves(app, client):
    """What the simulator points at has to exist, or the inbox shows a broken image.

    ``_download_media`` fabricates a URL in simulator mode because nothing is
    fetched from Meta. It used to fabricate ``/static/img/whatsapp-media-<id>.<ext>``,
    a file that was never there — so every photo a customer sent rendered as a
    broken image, and the comment claiming the inbox "still shows the attachment"
    was not true.
    """
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330023",
        _picker_answers(names=("IMG_1.jpg", "sheet.pdf"),
                        mimes=("image/jpeg", "application/pdf")),
        message_id="wamid.SIMIMG", token="enquiry"))
    assert res.status_code == 200

    with app.app_context():
        booking = Booking.query.first()
        urls = [p.url for p in booking.photos]
    assert len(urls) == 2, urls

    image = next(u for u in urls if u.endswith(".jpg"))
    document = next(u for u in urls if u.endswith(".pdf"))

    # An <img> renders from Content-Type, not the URL, so these draw a real
    # placeholder rather than a broken image icon.
    served = client.get(image)
    assert served.status_code == 200, image
    assert served.headers["Content-Type"].startswith("image/svg+xml")

    # The extension survives, because the inbox chooses its node by extension: a
    # simulated PDF must keep showing as a paperclip link, not as a photo.
    assert document.endswith(".pdf")
    assert client.get(document).status_code == 200


def test_the_media_placeholder_is_not_a_public_endpoint_in_live(app):
    """Nothing generates these URLs in live mode, so it must not answer."""
    from app import create_app
    from config import TestConfig

    class Live(TestConfig):
        WA_MODE = "live"

    live = create_app(Live)
    with live.app_context():
        db.create_all()
        assert live.test_client().get(
            "/webhooks/simulated-media/12345.jpg").status_code == 404
        db.drop_all()


def test_a_flow_component_that_is_not_a_picker_is_ignored(app, client):
    """Shape, not name — so an ordinary list answer cannot be mistaken for media."""
    answers = {**ENQUIRY_ANSWERS, "colours": ["red", "blue"]}
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330023", answers, message_id="wamid.NOTMEDIA"))
    assert res.status_code == 200
    with app.app_context():
        assert Booking.query.first().photos == []


def test_a_picker_with_no_files_attached_still_raises_the_enquiry(app, client):
    """The picker is optional, so submitting without it must be normal."""
    res = client.post("/webhooks/whatsapp", json=_flow_payload(
        "263773330024", _picker_answers(names=(), mimes=()),
        message_id="wamid.NOFILES"))
    assert res.status_code == 200
    with app.app_context():
        booking = Booking.query.first()
        assert booking is not None
        assert booking.photos == []


def test_the_inbox_summary_shows_attachment_names_not_json(app, client):
    """The thread is read by a person, so a picker must not dump raw JSON in it."""
    from app.views.whatsapp import _flow_summary

    summary = _flow_summary(_picker_answers(names=("damage.jpg",)))
    assert "damage.jpg" in summary, summary
    assert "{" not in summary, summary
    assert "sha256" not in summary, summary


# ── offering the Flow ────────────────────────────────────────────────────────
class _WithForm:
    """TestConfig with both Flows built and their ids configured."""

    @staticmethod
    def config():
        from config import TestConfig

        class WithForm(TestConfig):
            WA_FLOW_ENQUIRY_ID = "1234567890123456"
            WA_FLOW_BOOKING_ID = "9999999999999999"

        return WithForm


def test_the_main_menu_is_enquiries_first_with_no_booking_row(app):
    """One journey in, and no booking row whether a Flow exists or not.

    Booking is the desk's promise to hold a slot, so the customer enquires and
    the front desk books them in. The rows removed here are "Get a quote",
    "Book a service", "Book an appointment" and the enquiry-form row: all of them
    asked overlapping questions and only differed in wording.
    """
    from app import create_app
    from config import TestConfig

    gone = {"m_quote", "m_book", "m_form", "m_bform"}

    plain = create_app(TestConfig)
    with plain.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)
        conversation = get_or_create_conversation("263774440001", "No Form")
        rows = intent_router.handle_inbound(conversation, text_body="hi")[0]
        ids = [r["id"] for s in rows["sections"] for r in s["rows"]]
        assert not gone & set(ids), ids
        # The one journey that is offered, and it is the first row.
        assert ids[0] == "m_enquiries", ids
        # WhatsApp trims a list at ten rows.
        assert len(ids) <= 10, ids

    # Configuring both Flow ids must not bring a booking row back: the menu no
    # longer depends on them at all.
    configured = create_app(_WithForm.config())
    with configured.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)
        conversation = get_or_create_conversation("263774440002", "Has Form")
        rows = intent_router.handle_inbound(conversation, text_body="hi")[0]
        ids = [r["id"] for s in rows["sections"] for r in s["rows"]]
        assert not gone & set(ids), ids
        assert ids[0] == "m_enquiries", ids
        assert len(ids) <= 10, ids


def test_booking_is_still_reachable_by_typing_book(app):
    """Removing the row must not remove the capability.

    A customer who asks to book still gets the booking flow — they just have to
    say so. The menu stops advertising it; the shop has not stopped accepting it.
    """
    with app.app_context():
        conversation = get_or_create_conversation("263774440030", "Booker")
        for phrase in ("book", "I want to book", "appointment", "can I book a slot"):
            conversation.state = "MAIN_MENU"
            db.session.commit()
            replies = intent_router.handle_inbound(conversation, text_body=phrase)
            assert replies, phrase
            # The service list, carrying the booking flow's own ids.
            assert replies[0]["type"] == "list", (phrase, replies)
            assert replies[0]["sections"][0]["rows"][0]["id"].startswith("bsvc:"), replies


def test_tapping_the_form_row_sends_the_flow(app):
    """The router answers with a flow reply; the webhook is what puts it on the wire."""
    from app import create_app

    configured = create_app(_WithForm.config())
    with configured.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)
        conversation = get_or_create_conversation("263774440003", "Form Tapper")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_form")

    assert [r["type"] for r in replies] == ["flow"], replies
    flow = replies[0]
    assert flow["flow_id"] == "1234567890123456"
    # The token is echoed back in the nfm_reply and is what routes the answers,
    # so it must be the one the handler recognises.
    assert flow["flow_token"] == "enquiry"
    assert flow["header"]


def test_the_webhook_writes_the_flow_it_was_handed(app, client, monkeypatch):
    """A `flow` reply has to reach the send loop, like `document` and `list`.

    Both Flows, because the screen name is per-Flow: the send loop used to
    hard-code ``ENQUIRY``, which would have opened the booking form on a screen
    it does not define.
    """
    from app.views import whatsapp as webhook

    sent: list[dict] = []
    monkeypatch.setattr(
        webhook.WhatsAppClient, "_post",
        lambda self, payload: (sent.append(payload), {"simulated": True})[1],
    )
    monkeypatch.setitem(webhook.current_app.config, "WA_FLOW_ENQUIRY_ID",
                        "1234567890123456")
    monkeypatch.setitem(webhook.current_app.config, "WA_FLOW_BOOKING_ID",
                        "9999999999999999")

    def tap(wa_id: str, message_id: str, row_id: str, title: str):
        return client.post("/webhooks/whatsapp", json={
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "contacts": [{"wa_id": wa_id, "profile": {"name": "Wire"}}],
                "messages": [{"from": wa_id, "id": message_id,
                              "type": "interactive",
                              "interactive": {"type": "list_reply",
                                              "list_reply": {"id": row_id,
                                                             "title": title}}}],
            }}]}],
        })

    assert tap("263774440004", "wamid.FORMROW", "m_form", "Enquiry form").status_code == 200
    assert tap("263774440014", "wamid.BFORMROW", "m_bform", "Booking form").status_code == 200

    flows = [p for p in sent if p["interactive"]["type"] == "flow"]
    assert len(flows) == 2, [p.get("type") for p in sent]

    enquiry = flows[0]["interactive"]["action"]["parameters"]
    assert enquiry["flow_id"] == "1234567890123456"
    # The screen name comes from config, because Meta's builder names it and Meta
    # rejects a screen the Flow does not define. The default is what the builder
    # calls the first screen of a Flow pasted from our own JSON.
    assert enquiry["flow_action_payload"] == {
        "screen": app.config["WA_FLOW_ENQUIRY_SCREEN"]}
    assert enquiry["flow_token"] == "enquiry"

    booking = flows[1]["interactive"]["action"]["parameters"]
    assert booking["flow_id"] == "9999999999999999"
    assert booking["flow_action_payload"] == {
        "screen": app.config["WA_FLOW_BOOKING_SCREEN"]}
    assert booking["flow_token"] == "booking"


def test_no_flow_is_configured_falls_back_to_the_chat_enquiry(app):
    """Tapping a stale form row on an install without a Flow must still help.

    It used to fall back to the chat *quote* flow, which opened by asking for a
    registration number. Now it opens the enquiry journey, which asks which
    service — the same question the form would have asked first.
    """
    with app.app_context():
        conversation = get_or_create_conversation("263774440005", "No Form Tap")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_form")

        assert replies, "tapping the form row produced no reply at all"
        assert all(r["type"] != "flow" for r in replies), replies
        assert conversation.state == "QUOTE_SERVICE"
        assert replies[0]["type"] == "list"
        rows = [r["id"] for s in replies[0]["sections"] for r in s["rows"]]
        assert all(r.startswith("enq:") for r in rows), rows


# ── the booking form (a second Flow) ─────────────────────────────────────────
BOOKING_ANSWERS = {
    "contact_name": "Rudo Chikafu",
    "contact_email": "rudo@example.co.zw",
    "service": "Ceramic Coating",
    "preferred_date": (date.today() + timedelta(days=4)).isoformat(),
    "preferred_time": "10:00",
    "notes": "Silver BMW X3, parked under a tree for years.",
}


def test_a_booking_form_becomes_an_appointment_not_an_enquiry(app):
    """Same plumbing, different closing line.

    Both forms end up as a Booking — that is the model — but a booking must not
    be told to send photographs of damage, and an enquiry must not be told its
    slot is being held. The token is what tells them apart.
    """
    with app.app_context():
        conv = get_or_create_conversation("263775550001", "Rudo Chikafu")
        replies = intent_router.handle_inbound(conv, flow_response={
            "flow_token": "booking", "data": BOOKING_ANSWERS})

        booking = Booking.query.first()
        assert booking is not None, "the booking form raised nothing"
        assert booking.service == "Ceramic Coating"
        assert booking.slot_date == date.today() + timedelta(days=4)
        assert booking.slot_time == "10:00"
        assert booking.status == "REQUESTED"
        assert conv.state == "MAIN_MENU"

        spoken = "\n".join(r.get("body", "") for r in replies)
        # The slot the customer chose comes back to them, and the closing line
        # says what happens next.
        assert "*Preferred slot:*" in spoken, spoken
        assert (date.today() + timedelta(days=4)).strftime("%a %d %b %Y") in spoken, spoken
        assert "watch for" in spoken, spoken
        # The one thing it must NOT say: a booking is not a damage report.
        assert "photographs of the damage" not in spoken, spoken


def test_a_booking_with_no_plate_does_not_invent_a_vehicle(app):
    """"TBC" used to be written into the context and became a real Vehicle row.

    A detailing appointment does not need a registration, so a plate-less booking
    has to leave the vehicles list alone — otherwise every one of them adds a
    customer vehicle called TBC.
    """
    with app.app_context():
        conv = get_or_create_conversation("263775550002", "No Plate")
        intent_router.handle_inbound(conv, flow_response={
            "flow_token": "booking",
            "data": {k: v for k, v in BOOKING_ANSWERS.items() if k != "reg_no"},
        })

        booking = Booking.query.first()
        assert booking is not None
        assert booking.vehicle_id is None, "a plate-less booking got a vehicle"
        assert not Vehicle.query.filter_by(reg_no="TBC").first()
        assert Vehicle.query.count() == 0


def test_a_full_slot_is_not_confirmed_silently(app):
    """The form's dropdown cannot know how full a slot is; only we can.

    Capacity is checked before the booking is written, so a customer who asks for
    a time the shop cannot take is told so rather than being promised it.
    """
    from app.models import Customer

    day = (date.today() + timedelta(days=4)).isoformat()
    with app.app_context():
        customer = Customer(name="Already Booked", phone="+263775550003")
        db.session.add(customer)
        db.session.flush()
        for _ in range(BOOKING_SLOT_CAPACITY):
            db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                                   slot_date=date.today() + timedelta(days=4),
                                   slot_time="10:00", status="CONFIRMED"))
        db.session.commit()

        conv = get_or_create_conversation("263775550004", "Full Slot")
        replies = intent_router.handle_inbound(conv, flow_response={
            "flow_token": "booking", "data": BOOKING_ANSWERS})

        spoken = "\n".join(r.get("body", "") for r in replies)
        assert "already full" in spoken, spoken
        # Still recorded — the desk decides, the bot does not silently drop it.
        assert Booking.query.filter_by(slot_time="10:00").count() == 3


def test_the_menu_describes_every_row_it_offers(app):
    """A row the customer cannot predict is a row they will not tap.

    "Enquiries" is the first door and has to say what is behind it, because it is
    now the only way in — the quote, booking and booking-form rows are gone.
    """
    with app.app_context():
        conversation = get_or_create_conversation("263774440006", "Reader")
        rows = intent_router.handle_inbound(conversation, text_body="hi")[0]
        section = rows["sections"][0]

        assert rows["type"] == "list", rows
        assert rows["footer"], "the language hint lost its home"
        for row in section["rows"]:
            assert row.get("title"), row
            # Every row but the two shorthand ones carries a description; those
            # two name themselves, so an empty description is not a gap.
            if row["id"] not in {"m_services", "m_lang", "m_info", "m_human"}:
                assert row.get("description"), row

        enquiries = section["rows"][0]
        assert enquiries["title"] == "Enquiries"
        # It must read as an invitation, not as a synonym for "complaints".
        assert "call back" in enquiries["description"].lower(), enquiries


def test_tapping_the_booking_row_opens_the_booking_screen(app):
    """Each Flow opens on its own first screen, named by the router."""
    from app import create_app

    configured = create_app(_WithForm.config())
    with configured.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)
        conversation = get_or_create_conversation("263774440008", "Booking Tapper")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_bform")

    assert [r["type"] for r in replies] == ["flow"], replies
    flow = replies[0]
    assert flow["flow_id"] == "9999999999999999"
    assert flow["flow_token"] == "booking"
    assert flow["screen"] == "BOOKING"


def test_a_booking_form_without_a_booking_flow_configured_still_helps(app):
    """A stale row on an install with no booking Flow must not dead-end."""
    with app.app_context():
        conversation = get_or_create_conversation("263774440009", "No Booking Form Tap")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_bform")

        assert replies, "tapping the booking row produced no reply at all"
        assert all(r["type"] != "flow" for r in replies), replies
        # The chat booking flow starts by asking which service.
        assert conversation.state == "BOOK_SERVICE"


# ── the quotation notice, its Download button, and typed replies ─────────────
def _quotation_job(app, auth_client, *, reg="QT111"):
    """A job card with a quotation raised, and the service window open."""
    auth_client.post("/api/jobs", json={
        "customer_name": "Tariro Moyo", "customer_phone": "+263772334455",
        "reg_no": reg, "panels": ["Front Bumper"],
    })
    with app.app_context():
        from app.models import utcnow

        conversation = get_or_create_conversation("263772334455", profile_name="Tariro Moyo")
        conversation.last_inbound_at = utcnow()
        db.session.commit()


def test_the_quotation_notice_carries_the_full_button_row(app, auth_client):
    """Approve, decline or download — the customer never has to type.

    All three carry **bare** payloads, because a template's buttons are frozen
    when Meta approves it and cannot carry an estimate id. Sending the notice is
    what gives a tap something to resolve against.
    """
    from app.services import notifications

    _quotation_job(app, auth_client)
    with app.app_context():
        notifications.notify_quote_ready(JobCard.query.first())
        conversation = get_or_create_conversation("263772334455")
        sent = sorted(conversation.messages, key=lambda m: m.id)
        # Which quotation the thread is about — the buttons carry no id, so this
        # is the only thing a tap can resolve against.
        assert conversation.ctx_get("last_estimate_id") == Estimate.query.first().id

    message = next(m for m in sent if "is ready" in (m.body or ""))
    assert "Hello Tariro" in message.body
    assert "Tap *Approve*" in message.body

    button = next(m for m in sent if m.msg_type == "interactive")
    assert button.payload["buttons"] == [
        {"id": "a_approve", "title": "Approve"},
        {"id": "a_decline", "title": "Decline"},
        {"id": "doc_quote", "title": "Download quotation"},
    ]
    # Three is Meta's ceiling for an interactive button message, not a choice.
    assert len(button.payload["buttons"]) == 3


def test_tapping_approve_on_the_notice_approves_it(app, auth_client):
    """The bare payload path — what a real tap on an approved template sends."""
    from app.services import notifications

    _quotation_job(app, auth_client, reg="BTNA")
    with app.app_context():
        notifications.notify_quote_ready(JobCard.query.first())
        conversation = get_or_create_conversation("263772334455")
        intent_router.handle_inbound(conversation, interactive_id="a_approve")
        assert Estimate.query.first().status == "APPROVED"


def test_tapping_decline_on_the_notice_declines_it(app, auth_client):
    from app.services import notifications

    _quotation_job(app, auth_client, reg="BTND")
    with app.app_context():
        notifications.notify_quote_ready(JobCard.query.first())
        conversation = get_or_create_conversation("263772334455")
        intent_router.handle_inbound(conversation, interactive_id="a_decline")
        assert Estimate.query.first().status == "DECLINED"


def test_tapping_the_download_button_returns_the_quotation(app, auth_client):
    """A template cannot carry an estimate id, so the bare payload resolves."""
    from app.services import notifications

    _quotation_job(app, auth_client, reg="QT222")
    with app.app_context():
        notifications.notify_quote_ready(JobCard.query.first())
        conversation = get_or_create_conversation("263772334455")
        replies = intent_router.handle_inbound(conversation, interactive_id="doc_quote")
        token = Estimate.query.first().public_token

    assert [r["type"] for r in replies] == ["text", "document"], replies
    assert replies[1]["link"].endswith(f"/doc/quote/{token}.pdf")


def test_a_quiet_customer_can_still_resolve_the_notice_buttons(app, auth_client):
    """Out of the 24h window the notice goes as the approved template.

    The PDF is deliberately *not* attached here: Option B keeps the notice and
    the document apart, and a customer who has gone quiet is no exception — the
    desk sends the quotation from the job card when it is ready to.

    What has to hold either way is that the thread remembers which quotation it
    is about. A template's payloads are fixed, so Approve / Decline / Download
    resolve against that record. Written *after* the send, a notice that reached
    the customer could arrive with three buttons that resolve to nothing.
    """
    from app.services import notifications

    auth_client.post("/api/jobs", json={
        "customer_name": "Tariro Moyo", "customer_phone": "+263772334455",
        "reg_no": "QUIET9", "panels": ["Front Bumper"],
    })
    with app.app_context():
        conversation = get_or_create_conversation("263772334455", profile_name="Tariro Moyo")
        conversation.last_inbound_at = None
        db.session.commit()
        assert conversation.is_session_open is False

        notifications.notify_quote_ready(JobCard.query.first())
        db.session.expire_all()

        sent = sorted(conversation.messages, key=lambda m: m.id)
        kinds = [m.msg_type for m in sent]
        # Outside the window the approved template is the only thing Meta will
        # accept, so there is no second free-form button message.
        assert "interactive" not in kinds, kinds
        notice = next(m for m in sent if m.msg_type == "template")
        # The template name is in the logged body — `send_template` logs
        # "[template:<name>] param | param".
        assert notice.body.startswith("[template:quotation_ready]"), notice.body
        # Not `quotation_share`: the notice stays a notice, no PDF attached.
        assert "quotation_share" not in notice.body
        assert conversation.ctx_get("last_estimate_id") == Estimate.query.first().id

        replies = intent_router.handle_inbound(conversation, interactive_id="doc_quote")
        token = Estimate.query.first().public_token

    assert [r["type"] for r in replies] == ["text", "document"], replies
    assert replies[1]["link"].endswith(f"/doc/quote/{token}.pdf")


def test_typing_approve_approves_the_quotation(app, auth_client):
    """The notice tells the customer to *reply* approve — that used to do nothing.

    There was no free-text handling for it at all, so following the instruction
    printed on the quotation fell through to the fallback and the customer was
    ignored. The wording shipped with the template, so the template was a lie.
    """
    from app.services import notifications

    _quotation_job(app, auth_client, reg="QT333")
    with app.app_context():
        notifications.notify_quote_ready(JobCard.query.first())
        conversation = get_or_create_conversation("263772334455")
        intent_router.handle_inbound(conversation, text_body="approve")
        assert Estimate.query.first().status == "APPROVED"


def test_typing_decline_declines_the_quotation(app, auth_client):
    from app.services import notifications

    _quotation_job(app, auth_client, reg="QT444")
    with app.app_context():
        notifications.notify_quote_ready(JobCard.query.first())
        conversation = get_or_create_conversation("263772334455")
        intent_router.handle_inbound(conversation, text_body="decline")
        assert Estimate.query.first().status == "DECLINED"


def test_approve_the_quote_does_not_open_the_quote_flow(app):
    """Order matters here, and getting it wrong is silent.

    Approve/decline are matched before every other intent because the loop takes
    the first hit. With `quote` ahead of them, "approve the quote" opened a new
    quotation request instead of approving the one the customer already had.
    """
    assert intent_router.detect_intent("approve the quote") == "approve"
    assert intent_router.detect_intent("approved thanks") == "approve"
    assert intent_router.detect_intent("please proceed") == "approve"
    assert intent_router.detect_intent("decline") == "decline"
    # ...and asking for a price is still asking for a price.
    assert intent_router.detect_intent("I want a quote") == "quote"
    assert intent_router.detect_intent("how much for a respray") == "quote"


def test_approve_with_no_quotation_on_the_thread_does_not_crash(app):
    """A stale "approve" from an old thread must degrade, not raise."""
    with app.app_context():
        conversation = get_or_create_conversation("263779997777", "No Quotation")
        replies = intent_router.handle_inbound(conversation, text_body="approve")
        assert replies
        assert all(r.get("type") != "document" for r in replies), replies


# ── the collection notice and its button ─────────────────────────────────────
def _ready_job(app, auth_client, *, reg="RDY111"):
    """A job card with an invoice raised, and the service window open."""
    job = auth_client.post("/api/jobs", json={
        "customer_name": "Tariro Moyo", "customer_phone": "+263772334455",
        "reg_no": reg, "panels": ["Front Bumper"],
    }).get_json()["job"]
    invoice = auth_client.post(f"/api/jobs/{job['id']}/invoice", json={}).get_json()["invoice"]
    with app.app_context():
        from app.models import utcnow

        conversation = get_or_create_conversation("263772334455", profile_name="Tariro Moyo")
        conversation.last_inbound_at = utcnow()
        db.session.commit()
    return invoice


def test_the_collection_notice_puts_the_balance_behind_a_button(app, auth_client):
    """The money is **not** in the message, and that is the point.

    A Meta template's body is frozen when it is approved, so a balance written
    into the template is a snapshot taken the moment it was sent — a fortnight
    later the customer would still be reading that figure. Behind the button,
    the balance is answered from the invoice as it stands.
    """
    from app.services import notifications

    _ready_job(app, auth_client)
    with app.app_context():
        notifications.notify_ready_for_collection(JobCard.query.first())
        conversation = get_or_create_conversation("263772334455")
        sent = sorted(conversation.messages, key=lambda m: m.id)

    notice = next(m for m in sent if "ready for collection" in (m.body or ""))
    assert "Good news Tariro" in notice.body
    assert "(RDY111)" in notice.body
    assert "Check balance" in notice.body
    # No figure in the body — it would go stale.
    assert "USD" not in notice.body, notice.body

    button = next(m for m in sent if m.msg_type == "interactive")
    assert button.payload["buttons"] == [{"id": "m_pay", "title": "Check balance"}]


def test_the_check_balance_button_answers_with_the_live_balance(app, auth_client):
    """The button has to actually produce the figure, or it is a dead end.

    It carries the menu id ``m_pay`` rather than an invoice id, because a
    template's payload cannot interpolate one. That is fine here: the customer
    asking about their balance wants their newest unpaid invoice, which is what
    that handler already answers with.
    """
    from app.models import Invoice

    _ready_job(app, auth_client, reg="RDY222")
    with app.app_context():
        conversation = get_or_create_conversation("263772334455")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_pay")

        invoice = Invoice.query.first()
        text = replies[0]["body"]
        assert "Outstanding" in text, text
        assert invoice.invoice_no in text, text
        assert f"{invoice.balance:,.2f}" in text, text
        # And the customer is left in the state that captures a payment proof.
        assert conversation.state == "PAYMENT_PROOF"


def test_a_settled_account_is_not_told_it_owes_money(app, auth_client):
    """The same button, on an account already paid.

    Tapping it must not present a balance, and must not leave the customer
    thinking they still owe something.
    """
    from app.models import Invoice

    invoice = _ready_job(app, auth_client, reg="RDY333")
    auth_client.post(f"/api/invoices/{invoice['id']}/payment",
                     json={"amount": float(invoice["total"])})

    with app.app_context():
        conversation = get_or_create_conversation("263772334455")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_pay")

        text = replies[0]["body"]
        assert "Outstanding" not in text, text
        assert "USD" not in text, text
        # It still offers the way to file a proof of payment.
        assert "already paid" in text, text
        assert Invoice.query.first().balance == 0


# ── the invoice notice and its two buttons ──────────────────────────────────
def test_the_invoice_notice_carries_download_and_pay_buttons(app, auth_client):
    """The `payment_due` template, as approved in Meta.

    It reads "Hello <first name>, invoice <no> has been raised", a balance, and
    the payment methods — and it carries **two** quick-reply buttons, so the
    customer can fetch the PDF or get the payment details without typing.
    """
    _ready_job(app, auth_client, reg="INV111")

    with app.app_context():
        invoice = Invoice.query.first()
        # Snapshot the fields: the send commits, which expires the instance, and
        # an expired ORM object cannot be read once the context is gone.
        invoice_no = invoice.invoice_no
        balance = f"{invoice.balance:,.2f}"
        notifications.notify_invoice_issued(JobCard.query.first(), invoice)
        conversation = get_or_create_conversation("263772334455")
        sent = sorted(conversation.messages, key=lambda m: m.id)

    notice = next(m for m in sent if "has been raised" in (m.body or ""))
    assert "Hello Tariro," in notice.body, notice.body
    # First name only: the approved opening is "Hello {{1}},".
    assert "Tariro Moyo" not in notice.body, notice.body
    assert f"invoice {invoice_no} has been raised" in notice.body
    assert f"Balance due: USD {balance}" in notice.body
    assert "Cash, EcoCash, InnBucks, bank transfer or card at reception" in notice.body
    assert "quote the invoice number with any transfer" in notice.body

    button = next(m for m in sent if m.msg_type == "interactive")
    assert button.payload["buttons"] == [
        {"id": "doc_invoice", "title": "Download Invoice"},
        {"id": "m_pay", "title": "Pay via EcoCash"},
    ], button.payload["buttons"]


def test_the_pay_via_ecocash_button_answers_with_the_payment_details(app, auth_client):
    """The second button on the invoice notice has to lead somewhere.

    It carries ``m_pay`` — the same menu id the collection notice uses — so the
    payment instructions and the proof-of-payment state come from code that is
    already exercised, rather than a second handler that could drift.
    """
    _ready_job(app, auth_client, reg="INV222")

    with app.app_context():
        conversation = get_or_create_conversation("263772334455")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_pay")

        invoice = Invoice.query.first()
        text = replies[0]["body"]
        assert "How to pay" in text, text
        assert "EcoCash" in text, text
        assert invoice.invoice_no in text, text
        assert f"{invoice.balance:,.2f}" in text, text
        # And it leaves the customer where a payment screenshot is captured.
        assert conversation.state == "PAYMENT_PROOF"


# ── the reminder's Move it / Cancel appointment buttons ──────────────────────
WA_NUMBER = "263776660001"


def _appointment(app, *, wa_id=WA_NUMBER, days=1, slot="09:00", status="CONFIRMED"):
    """A customer with one appointment coming up. Returns the booking id."""
    with app.app_context():
        customer = Customer(name="Tariro Moyo", phone=f"+{wa_id}", whatsapp=f"+{wa_id}")
        db.session.add(customer)
        db.session.flush()
        booking = Booking(customer_id=customer.id, service="Car Detailing",
                          slot_date=date.today() + timedelta(days=days),
                          slot_time=slot, status=status, source="whatsapp")
        db.session.add(booking)
        db.session.commit()
        return booking.id


def _open_window(app, wa_id=WA_NUMBER):
    with app.app_context():
        from app.models import utcnow

        conversation = get_or_create_conversation(wa_id, "Tariro Moyo")
        conversation.last_inbound_at = utcnow()
        db.session.commit()


def _tap(app, choice, wa_id=WA_NUMBER):
    with app.app_context():
        conversation = get_or_create_conversation(wa_id)
        return intent_router.handle_inbound(conversation, interactive_id=choice)


def test_the_reminder_offers_move_and_cancel(app):
    """Inside the window the pair is free-form; outside it they ride on the
    approved template. Either way the customer gets the same two buttons.

    *Move it* is first and *Cancel appointment* second on purpose: the
    destructive one is not the one a thumb lands on.
    """
    _appointment(app)
    _open_window(app)

    with app.app_context():
        booking = Booking.query.first()
        assert notifications.notify_booking_reminder(booking) is True
        conversation = get_or_create_conversation(WA_NUMBER)
        db.session.expire_all()
        sent = sorted(conversation.messages, key=lambda m: m.id)

    message = next(m for m in sent if "Reminder" in (m.body or ""))
    assert "Reference:" in message.body
    assert "Let us know if anything has changed." in message.body
    # The old wording pointed at the main menu, which is not a way to move a
    # booking — the buttons are.
    assert "Reply *menu*" not in message.body

    button = next(m for m in sent if m.msg_type == "interactive")
    assert button.payload["buttons"] == [
        {"id": "b_move", "title": "Move it"},
        {"id": "b_cancel", "title": "Cancel appointment"},
    ], button.payload["buttons"]


def test_a_quiet_customer_gets_the_reminder_as_the_template(app):
    """Outside the 24h window the buttons have to come from the template."""
    _appointment(app)
    with app.app_context():
        notifications.notify_booking_reminder(Booking.query.first())
        conversation = get_or_create_conversation(WA_NUMBER)
        db.session.expire_all()
        sent = sorted(conversation.messages, key=lambda m: m.id)
        kinds = [m.msg_type for m in sent]
        body = sent[0].body

    # One message, not a text plus a prompt that Meta would refuse.
    assert kinds == ["template"], kinds
    assert body.startswith("[template:booking_reminder]"), body


def test_move_it_collects_a_new_day_then_a_time(app):
    """Tapping *Move it* starts the day picker, not a fresh booking."""
    _appointment(app)
    replies = _tap(app, "b_move")

    assert replies[0]["type"] == "text"
    # It must not read as though the move has happened, or as though the customer
    # can do it themselves: they are asking the desk for a time.
    assert "is currently" in replies[0]["body"], replies[0]["body"]
    assert "front desk" in replies[0]["body"], replies[0]["body"]
    assert "Let's move" not in replies[0]["body"], replies[0]["body"]
    assert replies[1]["type"] == "list"
    with app.app_context():
        assert get_or_create_conversation(WA_NUMBER).state == "BOOK_DATE"


def test_asking_to_move_records_the_request_and_moves_nothing(app):
    """The customer asks; the desk agrees. A tap moves nothing.

    The workshop is the side that promises a slot, so *Move it* cannot take one.
    The day and time are collected by the ordinary booking flow, which raises a
    *new* booking — so remembering which one is being asked about is also what
    stops the customer ending up with a duplicate beside the original.
    """
    booking_id = _appointment(app, slot="09:00")
    original_day = date.today() + timedelta(days=1)
    _tap(app, "b_move")
    new_day = date.today() + timedelta(days=3)

    with app.app_context():
        conversation = get_or_create_conversation(WA_NUMBER)
        picked = intent_router.handle_inbound(
            conversation, interactive_id=f"day:{new_day.isoformat()}")
        assert picked[0]["type"] == "list", picked
        replies = intent_router.handle_inbound(
            conversation, interactive_id=f"bslot:{new_day.isoformat()}:14:00")

        db.session.expire_all()
        assert Booking.query.count() == 1, "asking to move created a second appointment"
        booking = db.session.get(Booking, booking_id)
        # Nothing moved: that is the desk's decision to make, not the bot's.
        assert booking.slot_date == original_day
        assert booking.slot_time == "09:00"
        assert booking.rescheduled_count == 0
        # But the ask is on the record, for somebody to answer.
        assert booking.has_reschedule_request
        assert booking.requested_slot_date == new_day
        assert booking.requested_slot_time == "14:00"
        assert booking.requested_slot_text == new_day.strftime("%a %d %b %Y") + " at 14:00"
        # And it is on the desk's day book, or nobody would ever see it.
        task = Task.query.filter(Task.title.like("Move %")).first()
        assert task is not None, "no front-desk task was raised"
        assert task.category == "Front desk" and task.status == "OPEN"

    spoken = "\n".join(r.get("body", "") for r in replies)
    assert "Asked for" in spoken, spoken
    assert "Nothing has changed yet" in spoken, spoken
    assert "your *name*" not in spoken, spoken


def test_the_desk_agreeing_is_what_moves_it_and_tells_the_customer(app):
    """The notice follows the desk's decision, not the customer's ask.

    Sending it on the tap would tell the customer a time nobody had agreed, which
    is exactly what the old outright move did.
    """
    booking_id = _appointment(app, slot="09:00")
    new_day = date.today() + timedelta(days=3)
    _tap(app, "b_move")

    with app.app_context():
        conversation = get_or_create_conversation(WA_NUMBER)
        intent_router.handle_inbound(conversation,
                                     interactive_id=f"day:{new_day.isoformat()}")
        intent_router.handle_inbound(
            conversation, interactive_id=f"bslot:{new_day.isoformat()}:14:00")

        # Asking alone must not have promised anything.
        assert NotificationLog.query.filter_by(
            template="booking_rescheduled").count() == 0
        booking = db.session.get(Booking, booking_id)
        assert booking.slot_time == "09:00"

        result = booking_ops.accept_reschedule_request(booking)
        assert result["moved"] is True, result
        assert result["notified"] is True, result
        # Agreeing clears the ask, so the desk is not asked to answer it twice.
        assert booking.has_reschedule_request is False
        assert booking.slot_date == new_day and booking.slot_time == "14:00"
        assert booking.rescheduled_count == 1

    logged = NotificationLog.query.filter_by(template="booking_rescheduled").all()
    assert logged, "no reschedule notice was sent"
    assert "Was:" in logged[-1].body and "Now:" in logged[-1].body


def test_declining_keeps_the_slot_and_still_answers_the_customer(app):
    """Silently ignoring a request is worse than saying no."""
    booking_id = _appointment(app, slot="09:00")
    held = date.today() + timedelta(days=1)
    new_day = date.today() + timedelta(days=3)
    _tap(app, "b_move")

    with app.app_context():
        conversation = get_or_create_conversation(WA_NUMBER)
        intent_router.handle_inbound(conversation,
                                     interactive_id=f"day:{new_day.isoformat()}")
        intent_router.handle_inbound(
            conversation, interactive_id=f"bslot:{new_day.isoformat()}:14:00")

        booking = db.session.get(Booking, booking_id)
        result = booking_ops.decline_reschedule_request(
            booking, reason="That morning is fully booked.")
        assert result["declined"] is True and result["notified"] is True
        assert booking.slot_date == held and booking.slot_time == "09:00"
        assert booking.rescheduled_count == 0
        assert booking.has_reschedule_request is False
        # The reason is kept on the record, and given to the customer verbatim.
        assert "That morning is fully booked." in (booking.notes or "")

    logged = NotificationLog.query.filter_by(template="reschedule_declined").all()
    assert logged, "the customer was never told"
    assert "Still booked for:" in logged[-1].body
    assert "That morning is fully booked." in logged[-1].body


def test_accepting_with_no_request_or_the_same_slot_is_a_no_op(app):
    """Two ways the answer can be "nothing to do", and neither may misfire."""
    booking_id = _appointment(app, slot="09:00")
    held = date.today() + timedelta(days=1)

    with app.app_context():
        booking = db.session.get(Booking, booking_id)

        # Nothing was ever asked.
        assert booking_ops.accept_reschedule_request(booking)["reason"] == "no_request"
        assert booking_ops.decline_reschedule_request(booking)["declined"] is False

        # Asked for the slot they are already on: the ask is cleared, but there
        # is nothing to move and nothing worth waking the customer for.
        booking_ops.request_reschedule(booking, slot_date=held, slot_time="09:00")
        assert booking.has_reschedule_request
        result = booking_ops.accept_reschedule_request(booking)

        assert result["reason"] == "same_slot"
        assert result["moved"] is False and result["notified"] is False
        assert booking.slot_time == "09:00" and booking.rescheduled_count == 0
        assert booking.has_reschedule_request is False


def test_cancel_asks_before_cancelling(app):
    """One tap must never lose an appointment."""
    booking_id = _appointment(app)
    replies = _tap(app, "b_cancel")

    assert replies[0]["type"] == "buttons", replies
    assert replies[0]["buttons"] == [{"id": "b_cancel_yes", "title": "Yes, cancel it"},
                                     {"id": "b_cancel_keep", "title": "No, keep it"}]
    with app.app_context():
        assert db.session.get(Booking, booking_id).status == "CONFIRMED"


def test_yes_cancels_it(app):
    booking_id = _appointment(app)
    _tap(app, "b_cancel")
    replies = _tap(app, "b_cancel_yes")

    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.status == "CANCELLED"
        assert "Cancelled by customer" in (booking.notes or "")
        # The slot is left where it was: the desk needs to see what was given up.
        assert booking.slot_date is not None
        assert booking.slot_time == "09:00"
    assert "Cancelled" in replies[0]["body"]


def test_no_keep_it_leaves_the_appointment_alone(app):
    booking_id = _appointment(app)
    _tap(app, "b_cancel")
    replies = _tap(app, "b_cancel_keep")

    with app.app_context():
        assert db.session.get(Booking, booking_id).status == "CONFIRMED"
    assert "Kept" in replies[0]["body"]


def test_cancelling_twice_says_so_instead_of_confirming_nothing(app):
    """Meta redelivers taps, and customers double-tap."""
    booking_id = _appointment(app)
    _tap(app, "b_cancel")
    _tap(app, "b_cancel_yes")
    replies = _tap(app, "b_cancel_yes")

    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.status == "CANCELLED"
        # One note, not two — the second tap changed nothing.
        assert (booking.notes or "").count("Cancelled by customer") == 1
    assert "no longer on the books" in replies[0]["body"], replies[0]["body"]


def test_the_buttons_degrade_when_there_is_no_appointment(app):
    """A stale tap from an old reminder must not raise into the webhook."""
    moved = _tap(app, "b_move", wa_id="263776660009")
    cancelled = _tap(app, "b_cancel", wa_id="263776660009")

    assert moved and cancelled
    assert all(r.get("type") != "buttons" for r in cancelled), cancelled
    assert "could not find an appointment" in moved[0]["body"]
    with app.app_context():
        assert Booking.query.count() == 0


def test_a_cancelled_appointment_is_not_offered_to_move(app):
    """There is nothing to move, and saying so beats moving the wrong thing."""
    _appointment(app, status="CANCELLED")
    replies = _tap(app, "b_move")
    assert "could not find an appointment" in replies[0]["body"], replies[0]["body"]


def test_an_appointment_already_gone_is_not_moved_again(app):
    """The day can pass between the reminder going out and the tap."""
    _appointment(app, days=-1)
    replies = _tap(app, "b_move")
    assert "could not find an appointment" in replies[0]["body"], replies[0]["body"]


def test_typing_cancel_my_appointment_does_not_decline_a_quotation(app):
    """`decline` already owns the word "cancel", and it was winning.

    A customer with a booking but no quotation who typed "cancel my appointment"
    was told a member of staff would discuss their *quotation* — a sentence about
    something that did not exist, with their appointment left standing.
    """
    booking_id = _appointment(app)
    with app.app_context():
        conversation = get_or_create_conversation(WA_NUMBER)
        replies = intent_router.handle_inbound(conversation,
                                               text_body="cancel my appointment")
        assert replies[0]["type"] == "buttons", replies
        assert replies[0]["buttons"][0]["id"] == "b_cancel_yes"
        assert db.session.get(Booking, booking_id).status == "CONFIRMED"


def test_a_bare_cancel_still_belongs_to_the_quotation(app):
    """The other direction has to keep working: a plain "cancel" is not a booking.

    Widening the booking phrases to catch a bare "cancel" would silently break the
    quotation decline, which is the more expensive of the two mistakes.
    """
    booking_id = _appointment(app)
    with app.app_context():
        conversation = get_or_create_conversation(WA_NUMBER)
        intent_router.handle_inbound(conversation, text_body="cancel")
        assert db.session.get(Booking, booking_id).status == "CONFIRMED"


def test_typing_reschedule_my_appointment_opens_the_day_picker(app):
    _appointment(app)
    with app.app_context():
        conversation = get_or_create_conversation(WA_NUMBER)
        replies = intent_router.handle_inbound(conversation,
                                               text_body="reschedule my appointment")
        assert "is currently" in replies[0]["body"], replies[0]["body"]
        assert replies[1]["type"] == "list"
        assert conversation.state == "BOOK_DATE"


def test_a_typed_time_into_a_full_slot_is_refused(app):
    """The picker only offers free times; typing is the way round it.

    Booking into a full slot is the kind of mistake that only surfaces on the
    morning, when three cars are expected into one bay.
    """
    day = date.today() + timedelta(days=2)
    with app.app_context():
        for index in range(BOOKING_SLOT_CAPACITY):
            customer = Customer(name=f"Booked {index}", phone=f"+26377666001{index}")
            db.session.add(customer)
            db.session.flush()
            db.session.add(Booking(customer_id=customer.id, service="Car Detailing",
                                   slot_date=day, slot_time="11:00",
                                   status="CONFIRMED"))
        db.session.commit()

        conversation = get_or_create_conversation("263776660099", "Late Booker")
        conversation.state = "BOOK_TIME"
        conversation.ctx_set(book_date=day.isoformat(), service="Car Detailing")
        db.session.commit()

        replies = intent_router.handle_inbound(conversation, text_body="11:00")

        assert "already full" in replies[0]["body"], replies[0]["body"]
        # Still waiting for a time, and nothing extra was written.
        assert conversation.state == "BOOK_TIME"
        assert Booking.query.filter_by(slot_time="11:00").count() == BOOKING_SLOT_CAPACITY
