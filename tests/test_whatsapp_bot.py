"""WhatsApp chatbot tests — no network required (simulator mode)."""
from __future__ import annotations

import hashlib
import hmac
from datetime import date, timedelta

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
        assert replies[0]["type"] == "list"
        ids = [row["id"] for s in replies[0]["sections"] for row in s["rows"]]
        assert "m_quote" in ids
        # Booking used to be unreachable from the greeting: WhatsApp caps buttons
        # at three and the menu spent all three on quote/track/claim.
        assert "m_book" in ids


def test_a_booking_completes_by_tapping_and_keeps_the_chosen_day(app):
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
        chosen = day_ids[2]
        wanted = chosen.split(":", 1)[1]

        intent_router.handle_inbound(conv, interactive_id=chosen)
        confirmation = intent_router.handle_inbound(conv, text_body="Tariro Moyo")
        assert "Preferred day" in confirmation[0]["body"], confirmation[0]["body"]

        booking = Booking.query.order_by(Booking.id.desc()).first()
        assert booking is not None, "the booking was never created"
        assert booking.source == "whatsapp"
        assert booking.status == "REQUESTED"
        assert booking.slot_date.isoformat() == wanted, (
            f"booking landed on {booking.slot_date}, the customer asked for {wanted}"
        )
        # The chosen day also survives the flow reset, so it can be re-read.
        assert conv.ctx_get("book_date") is None


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
        # An inbound WhatsApp request is an *enquiry* until somebody confirms
        # it, so it carries an enquiry reference — not a booking reference.
        assert "TC-ENQ" in text
        assert "TC-BKG" not in text
        assert conv.state == "MAIN_MENU"

        customer = Customer.query.filter_by(name="Tendai Moyo").first()
        assert customer is not None
        booking = Booking.query.filter_by(customer_id=customer.id).first()
        assert booking is not None
        assert booking.source == "whatsapp"
        assert booking.service == "Car Detailing"
        assert booking.booking_reference is None


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
    assert again.get_json()["status"] == "duplicate_ignored"

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
