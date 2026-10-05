"""Messages the workshop owed a customer and could not send.

When the internet drops, the bot cannot reply. Before this module existed, a
reply that failed to send was logged as failed and discarded: the customer asked
a question, never heard back, and nobody knew. That is the worst possible failure
for a business whose whole promise is "the front desk will get back to you".

So a send that fails for a *transient* reason is kept — the entire Meta envelope,
exactly as it would have been POSTed — and re-sent when the link returns.

Two things decide whether this is safe:

* **Only transient failures are kept.** Meta answering 400 means the message
  itself is wrong (an unapproved template, a bad number) and will be wrong every
  time; queueing it would build a pile of messages that can never be delivered
  and would keep hammering the API. 429 and 5xx are Meta having a bad moment and
  are worth another go.
* **The 24-hour window is not ours to negotiate.** A free-form reply replayed
  hours later is outside the customer's window and Meta will refuse it with a
  400. That is a *permanent* failure by the time we see it, so the retry path
  gives up and flags it rather than looping.

Once attempts run out the message becomes ``failed`` and is surfaced — the desk
should phone that customer. An automatic system that silently gives up on a
customer is worse than one that never tried.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from ..extensions import db
from ..models import OUTBOUND_MAX_ATTEMPTS, OutboundQueue, utcnow

log = logging.getLogger(__name__)

# Minutes to wait before each successive attempt. Deliberately front-loaded: most
# outages are seconds or minutes long, and the "was that worth reconnecting for?"
# window is short. The tail covers a longer loss without hammering Meta.
BACKOFF_MINUTES = (1, 5, 15, 60, 240)


def next_delay(attempts: int) -> timedelta:
    """How long to wait after ``attempts`` failures."""
    index = max(0, min(attempts - 1, len(BACKOFF_MINUTES) - 1))
    return timedelta(minutes=BACKOFF_MINUTES[index])


def enqueue(*, wa_id: str, payload: dict, body: str | None = None,
            msg_type: str = "text", intent: str | None = None,
            conversation_id: int | None = None,
            message_id: int | None = None,
            error: str | None = None) -> OutboundQueue:
    """Keep a message that could not be sent, for another try."""
    row = OutboundQueue(
        wa_id=wa_id,
        payload_json=json.dumps(payload),
        body=body,
        msg_type=msg_type,
        intent=intent,
        conversation_id=conversation_id,
        message_id=message_id,
        attempts=0,
        last_error=(error or "")[:400] or None,
        next_attempt_at=utcnow(),
        status="pending",
    )
    db.session.add(row)
    db.session.commit()
    log.warning("Queued an undelivered WhatsApp message to %s for retry (%s).",
                wa_id, (error or "unknown")[:120])
    return row


def due(limit: int = 50) -> list[OutboundQueue]:
    """Messages ready for another attempt, oldest first."""
    return (OutboundQueue.query
            .filter(OutboundQueue.status == "pending",
                    OutboundQueue.next_attempt_at <= utcnow())
            .order_by(OutboundQueue.next_attempt_at.asc(), OutboundQueue.id.asc())
            .limit(limit)
            .all())


def pending_count() -> int:
    return OutboundQueue.query.filter_by(status="pending").count()


def failed_count() -> int:
    return OutboundQueue.query.filter_by(status="failed").count()


def retry_due(*, limit: int = 50) -> dict:
    """Try every message that is due. Returns a summary for the cron log.

    Imported lazily: this module is called from inside the send path, and
    ``whatsapp_client`` imports back into here for the queue itself.
    """
    from .whatsapp_client import WhatsAppClient, WhatsAppError

    client = WhatsAppClient()
    attempted = sent = rescheduled = failed = 0

    for row in due(limit=limit):
        attempted += 1
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, ValueError) as exc:
            row.status = "failed"
            row.last_error = f"unreadable payload: {exc}"[:400]
            failed += 1
            db.session.commit()
            continue

        try:
            client._post(payload)
        except WhatsAppError as exc:
            row.attempts = (row.attempts or 0) + 1
            row.last_error = str(exc)[:400]
            if not exc.retryable or row.attempts >= OUTBOUND_MAX_ATTEMPTS:
                row.status = "failed"
                failed += 1
                log.error("Giving up on a queued message to %s after %s attempt(s): %s",
                          row.wa_id, row.attempts, exc)
            else:
                row.next_attempt_at = utcnow() + next_delay(row.attempts)
                rescheduled += 1
            db.session.commit()
            continue

        # Delivered at last. The message it came from is *updated*, not
        # duplicated, so the customer's thread shows one reply rather than a
        # failed one and a delivered one that read as though we answered twice.
        row.status = "sent"
        row.sent_at = utcnow()
        row.last_error = None
        _mark_delivered(row)
        db.session.commit()
        sent += 1

    if attempted:
        log.info("Outbound retry: %s due, %s sent, %s rescheduled, %s failed.",
                 attempted, sent, rescheduled, failed)
    return {"due": attempted, "sent": sent, "rescheduled": rescheduled, "failed": failed,
            "pending": pending_count(), "given_up": failed_count()}


def _mark_delivered(row: OutboundQueue) -> None:
    """Flip the failed message this row came from into a delivered one.

    *Updating* rather than inserting is the point. The customer received one
    reply; if the retry inserted a second WaMessage the inbox would show two, and
    the thread would read as though we answered the same thing twice.

    Does not commit — the caller does, so the message and the row land together.
    """
    if not row.message_id:
        return
    try:
        from ..models import WaMessage

        message = db.session.get(WaMessage, row.message_id)
        if message is not None:
            message.status = "delivered"
    except Exception as exc:  # noqa: BLE001 - the message is already out
        log.warning("Sent a queued message but could not update its thread entry: %s",
                    exc)


def prune(days: int = 14) -> int:
    """Drop the settled rows so the table stays small."""
    cutoff = utcnow() - timedelta(days=days)
    try:
        rows = (OutboundQueue.query
                .filter(OutboundQueue.status.in_(("sent", "failed")),
                        OutboundQueue.created_at < cutoff)
                .all())
        for row in rows:
            db.session.delete(row)
        db.session.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.warning("Could not prune the outbound queue: %s", exc)
        return 0
