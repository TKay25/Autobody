"""WhatsApp chatbot tests — no network required (simulator mode)."""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import Booking, Customer, JobCard, Vehicle, WaConversation
from app.services import intent_router
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
        assert replies[0]["type"] == "buttons"
        ids = [b["id"] for b in replies[0]["buttons"]]
        assert "m_quote" in ids


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


def test_insurer_matching():
    assert intent_router.match_insurer("it is an old mutual claim") == "OLD"
    assert intent_router.match_insurer("cbz") == "CBZ"


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

        replies = intent_router.handle_inbound(conv, text_body="Tendai Moyo")
        text = replies[0]["body"]
        assert "TC-BKG" in text
        assert conv.state == "MAIN_MENU"

        customer = Customer.query.filter_by(name="Tendai Moyo").first()
        assert customer is not None
        booking = Booking.query.filter_by(customer_id=customer.id).first()
        assert booking is not None
        assert booking.source == "whatsapp"
        assert booking.service == "Car Detailing"


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
