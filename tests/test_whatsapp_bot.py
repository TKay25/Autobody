"""WhatsApp chatbot tests — no network required (simulator mode)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timedelta

from app.constants import BOOKING_SLOT_CAPACITY
from app.extensions import db
from app.models import (Booking, BookingPhoto, Customer, Invoice, JobCard, JobPhoto,
                        NotificationLog, PaymentProof, Task, Vehicle, WaConversation,
                        WaMessage)
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
        assert "m_quote" in ids
        # Booking used to be unreachable from the greeting: WhatsApp caps buttons
        # at three, and the old menu spent all three on quote/track/claim.
        assert "m_book" in ids


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


def test_service_matching_is_fuzzy():
    assert intent_router.match_service("I need my bumper resprayed") == "Panel Beating & Spray Painting"
    assert intent_router.match_service("ceramic") == "Ceramic Coating"
    assert intent_router.match_service("ppf") == "Paint Protection Film"
    assert intent_router.match_service("nonsense") is None


def test_quote_flow_creates_booking_and_customer(app):
    with app.app_context():
        conv = get_or_create_conversation("263771110002", "Quote Tester")

        intent_router.handle_inbound(conv, interactive_id="m_quote")
        assert conv.state == "QUOTE_REG"

        replies = intent_router.handle_inbound(conv, text_body="ABC 1234")
        assert conv.ctx_get("reg") == "ABC1234"
        assert conv.state == "QUOTE_SERVICE"
        assert replies[0]["type"] == "list"

        intent_router.handle_inbound(conv, interactive_id="svc:Car Detailing")
        assert conv.state == "QUOTE_DESC"

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

    The registration prompt used to reject "Shona" as a dodgy plate number and
    answer in English, so there was no way to change language part-way through.
    """
    with app.app_context():
        conv = get_or_create_conversation("263771120001", "Midflow Tester")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        assert conv.state == "QUOTE_REG"

        replies = intent_router.handle_inbound(conv, text_body="Shona")
        assert conv.ctx_get("lang") == "sn"
        body = " ".join(r.get("body", "") for r in replies)
        assert "Nderipi" in body, f"should re-ask for the plate in Shona: {body}"
        assert conv.state == "QUOTE_REG", "the flow should continue, not reset"


def test_switching_language_mid_flow_keeps_earlier_answers(app):
    """Changing language must not silently discard what was already given."""
    with app.app_context():
        conv = get_or_create_conversation("263771120002", "Keep Answers")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        intent_router.handle_inbound(conv, text_body="ABC 1234")
        assert conv.ctx_get("reg") == "ABC1234"

        intent_router.handle_inbound(conv, text_body="ndebele")
        assert conv.ctx_get("lang") == "nd"
        assert conv.ctx_get("reg") == "ABC1234", "the registration was thrown away"
        assert conv.state == "QUOTE_SERVICE"


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
    """A Flow cannot upload a file, so the pictures come in the chat.

    They arrive *before* the form is submitted, which means they are sitting in
    the conversation context. Losing them here would throw away the most useful
    thing the desk receives.
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


def test_the_form_prompts_for_the_photographs(app, client):
    """The Flow cannot carry a file, so the bot has to ask for one."""
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
    otherwise "abc123" submitted during the registration prompt becomes a plate
    and the rest of the form is silently dropped.
    """
    with app.app_context():
        conv = get_or_create_conversation("263773330008", "Mid Flow")
        intent_router.handle_inbound(conv, interactive_id="m_quote")
        assert conv.state == "QUOTE_REG"

        intent_router.handle_inbound(conv, flow_response={
            "flow_token": "enquiry",
            "data": {"contact_name": "Mid Flow", "reg_no": "XYZ999",
                     "service": "Car Detailing", "damage": "Interior deep clean"},
        })
        booking = Booking.query.first()
        assert booking is not None, "the form was swallowed by the waiting state"
        assert booking.vehicle.reg_no == "XYZ999"
        assert conv.state == "MAIN_MENU"


# ── offering the Flow ────────────────────────────────────────────────────────
class _WithForm:
    """TestConfig with an enquiry Flow built and its id configured."""

    @staticmethod
    def config():
        from config import TestConfig

        class WithForm(TestConfig):
            WA_FLOW_ENQUIRY_ID = "1234567890123456"

        return WithForm


def test_the_enquiry_form_is_offered_only_when_one_is_configured(app):
    """A menu row that opens nothing is worse than no row at all.

    The form's id comes from Meta, so an install without a Flow built must not
    advertise one — and the row has to appear as soon as it is configured.
    """
    from app import create_app
    from config import TestConfig

    plain = create_app(TestConfig)
    with plain.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)
        conversation = get_or_create_conversation("263774440001", "No Form")
        rows = intent_router.handle_inbound(conversation, text_body="hi")[0]
        ids = [r["id"] for s in rows["sections"] for r in s["rows"]]
        assert "m_form" not in ids, ids

    configured = create_app(_WithForm.config())
    with configured.app_context():
        db.create_all()
        from app.seed import run_seed

        run_seed(with_demo=False)
        conversation = get_or_create_conversation("263774440002", "Has Form")
        rows = intent_router.handle_inbound(conversation, text_body="hi")[0]
        ids = [r["id"] for s in rows["sections"] for r in s["rows"]]
        assert "m_form" in ids, ids


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
    """A `flow` reply has to reach the send loop, like `document` and `list`."""
    from app.views import whatsapp as webhook

    sent: list[dict] = []
    monkeypatch.setattr(
        webhook.WhatsAppClient, "_post",
        lambda self, payload: (sent.append(payload), {"simulated": True})[1],
    )
    monkeypatch.setitem(webhook.current_app.config, "WA_FLOW_ENQUIRY_ID",
                        "1234567890123456")

    res = client.post("/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": "263774440004", "profile": {"name": "Wire"}}],
            "messages": [{"from": "263774440004", "id": "wamid.FORMROW",
                          "type": "interactive",
                          "interactive": {"type": "list_reply",
                                          "list_reply": {"id": "m_form",
                                                         "title": "Enquiry form"}}}],
        }}]}],
    })
    assert res.status_code == 200

    flows = [p for p in sent if p["interactive"]["type"] == "flow"]
    assert flows, [p.get("type") for p in sent]
    parameters = flows[0]["interactive"]["action"]["parameters"]
    assert parameters["flow_id"] == "1234567890123456"
    assert parameters["flow_action_payload"] == {"screen": "ENQUIRY"}


def test_no_flow_is_configured_falls_back_to_the_chat_quote_flow(app):
    """Tapping a stale form row on an install without a Flow must still help."""
    with app.app_context():
        conversation = get_or_create_conversation("263774440005", "No Form Tap")
        replies = intent_router.handle_inbound(conversation, interactive_id="m_form")

        assert replies, "tapping the form row produced no reply at all"
        assert all(r["type"] != "flow" for r in replies), replies
        # The chat quote flow starts by asking for the registration.
        assert conversation.state == "QUOTE_REG"


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
