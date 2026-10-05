"""Messages the workshop owed a customer and could not send.

The failure this guards against is the worst one a service business can have: a
customer asks a question, the workshop loses its internet, the bot's reply fails,
and nobody ever finds out. It used to be logged as `failed` and discarded.

Two distinctions decide whether the queue is safe or a liability:

* **transient vs permanent** — a dead link is worth retrying, a 400 from Meta is
  not and would be retried for ever;
* **one reply, not two** — a delivered retry must *update* the failed message,
  not add a second one, or the inbox shows the customer being answered twice.
"""
from __future__ import annotations

from datetime import timedelta

from app.extensions import db
from app.models import OUTBOUND_MAX_ATTEMPTS, OutboundQueue, WaMessage, utcnow
from app.services import outbound
from app.services.whatsapp_client import (
    WhatsAppClient,
    WhatsAppError,
    get_or_create_conversation,
)

WA = "263775550555"


def _fail(monkeypatch, *, retryable=True, message="Connection refused"):
    """Make every send fail the way a dead link does."""
    def _post(self, payload):
        raise WhatsAppError(message, retryable=retryable)
    monkeypatch.setattr(WhatsAppClient, "_post", _post)


def _succeed(monkeypatch, sent=None):
    def _post(self, payload):
        if sent is not None:
            sent.append(payload)
        return {"messages": [{"id": "wamid.OK"}]}
    monkeypatch.setattr(WhatsAppClient, "_post", _post)


def _send(body="Your car is ready for collection."):
    conversation = get_or_create_conversation(WA, "Tariro Moyo")
    return WhatsAppClient().send_text(WA, body, conversation=conversation)


def test_a_dead_link_keeps_the_message_instead_of_losing_it(app, monkeypatch):
    _fail(monkeypatch)
    with app.app_context():
        message = _send()

        # The caller still gets its WaMessage, marked failed as before.
        assert message is not None and message.status == "failed"

        # But the reply is not gone — it is waiting, with its body intact.
        assert OutboundQueue.query.count() == 1
        row = OutboundQueue.query.first()
        assert row.status == "pending"
        assert row.wa_id == WA
        assert row.body == "Your car is ready for collection."
        assert row.message_id == message.id
        assert outbound.pending_count() == 1


def test_a_permanent_refusal_is_not_queued(app, monkeypatch):
    """Meta saying the message itself is wrong will not change on a retry.

    This is also the 24-hour-window case: a free-form reply that sat in the queue
    long enough is refused with a 400, and looping on it would hammer the API and
    build a pile of messages that can never be delivered.
    """
    _fail(monkeypatch, retryable=False, message="400: outside the 24h window")
    with app.app_context():
        _send()
        assert OutboundQueue.query.count() == 0


def test_simulator_mode_never_queues(app, monkeypatch):
    """Nothing fails in simulator mode, so nothing should accumulate."""
    with app.app_context():
        _send("A reply in simulator mode.")
        assert OutboundQueue.query.count() == 0


def test_a_delivered_retry_updates_the_thread_instead_of_duplicating_it(app, monkeypatch):
    """The customer got one message. The inbox must show one message."""
    _fail(monkeypatch)
    with app.app_context():
        _send()
        assert WaMessage.query.filter_by(direction="outbound").count() == 1
        # Snapshot the id: an ORM instance does not survive the context, and
        # `original.id` after a commit is exactly the DetachedInstanceError trap.
        original_id = WaMessage.query.filter_by(direction="outbound").first().id

    sent = []
    _succeed(monkeypatch, sent)
    with app.app_context():
        result = outbound.retry_due()

        assert result["sent"] == 1, result
        messages = WaMessage.query.filter_by(direction="outbound").all()
        assert len(messages) == 1, "the retry added a second copy of the reply"
        assert messages[0].id == original_id
        assert messages[0].status == "delivered"

        row = OutboundQueue.query.first()
        assert row.status == "sent" and row.sent_at is not None
        # And what actually reached Meta is the message we were holding.
        assert sent[0]["text"]["body"] == "Your car is ready for collection."


def test_a_failed_attempt_schedules_the_next_one_rather_than_hammering(app, monkeypatch):
    _fail(monkeypatch)
    with app.app_context():
        _send()
        row = OutboundQueue.query.first()
        before = utcnow()

        outbound.retry_due()
        db.session.refresh(row)

        assert row.attempts == 1
        assert row.status == "pending"
        assert row.next_attempt_at > before
        assert row.last_error == "Connection refused"


def test_the_wait_grows_with_each_attempt(app, monkeypatch):
    """Front-loaded: most outages are seconds long, so the early retries are close."""
    delays = [outbound.next_delay(n).total_seconds() for n in range(1, 6)]
    assert delays == sorted(delays), delays
    assert delays[0] == 60                    # a minute, not a millisecond
    assert delays[-1] >= 3600                 # and it backs right off


def test_it_gives_up_and_says_so_rather_than_retrying_for_ever(app, monkeypatch):
    """An automatic system that silently gives up is worse than one that never
    tried — the desk has to be told this customer is still waiting."""
    _fail(monkeypatch)
    with app.app_context():
        _send()
        row = OutboundQueue.query.first()

        for _ in range(OUTBOUND_MAX_ATTEMPTS + 2):
            row.next_attempt_at = utcnow()      # pretend the wait elapsed
            db.session.commit()
            outbound.retry_due()
            db.session.refresh(row)
            if row.status == "failed":
                break

        assert row.attempts == OUTBOUND_MAX_ATTEMPTS
        assert row.status == "failed"
        assert outbound.pending_count() == 0
        assert outbound.failed_count() == 1

        # A row that has been given up on must stay given up.
        row.next_attempt_at = utcnow()
        db.session.commit()
        assert outbound.retry_due()["due"] == 0


def test_a_queue_row_is_not_due_before_its_time(app, monkeypatch):
    _fail(monkeypatch)
    with app.app_context():
        _send()
        row = OutboundQueue.query.first()
        row.next_attempt_at = utcnow() + timedelta(minutes=30)
        db.session.commit()

        assert outbound.retry_due()["due"] == 0
        assert outbound.pending_count() == 1


def test_a_message_with_no_message_link_still_delivers(app, monkeypatch):
    """Defensive: the link is how we avoid a duplicate, but its absence must not
    stop the customer getting their reply."""
    _fail(monkeypatch)
    with app.app_context():
        _send()
        row = OutboundQueue.query.first()
        db.session.delete(db.session.get(WaMessage, row.message_id))
        row.message_id = None
        db.session.commit()

    _succeed(monkeypatch)
    with app.app_context():
        assert outbound.retry_due()["sent"] == 1
        assert OutboundQueue.query.first().status == "sent"


def test_pruning_keeps_anything_still_waiting(app, monkeypatch):
    """A settled row can go after a fortnight. A pending one never can — that is
    an undelivered message to a real customer."""
    _fail(monkeypatch)
    with app.app_context():
        _send()
        pending = OutboundQueue.query.first()
        old = utcnow() - timedelta(days=30)

        settled = OutboundQueue(wa_id=WA, payload_json="{}", status="sent",
                                created_at=old, next_attempt_at=old)
        db.session.add(settled)
        pending.created_at = old
        db.session.commit()

        removed = outbound.prune(days=14)

        assert removed == 1
        remaining = OutboundQueue.query.all()
        assert len(remaining) == 1
        assert remaining[0].status == "pending"


def test_the_cron_endpoint_reports_what_it_did(app, auth_client, monkeypatch):
    _fail(monkeypatch)
    with app.app_context():
        _send()

    _succeed(monkeypatch)
    response = auth_client.post("/api/whatsapp/outbox/retry")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["sent"] == 1


def test_the_desk_can_see_what_is_waiting_and_what_was_given_up(app, auth_client, monkeypatch):
    """"Waiting" answers "did they hear from us?", "given up" is a phone call."""
    _fail(monkeypatch)
    with app.app_context():
        _send()
        row = OutboundQueue.query.first()
        row.status = "failed"
        row.last_error = "400: outside the 24h window"
        db.session.commit()

    body = auth_client.get("/api/whatsapp/outbox").get_json()
    assert body["failed"] == 1
    assert body["items"][0]["last_error"].startswith("400")
    assert body["items"][0]["body"] == "Your car is ready for collection."


def test_the_retry_endpoint_is_manager_only(app, client, monkeypatch):
    """A send path anyone signed in can hammer is a way to burn the API quota."""
    _fail(monkeypatch)
    with app.app_context():
        _send()

    # Not signed in at all.
    assert client.post("/api/whatsapp/outbox/retry").status_code in (302, 401)
