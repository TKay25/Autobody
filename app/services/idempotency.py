"""Making a replayed write safe.

The workshop console has to keep working when the internet drops, which means a
write can be queued in the browser and replayed later. Replaying is fine for
"move this job to QC" and catastrophic for "record this payment" — the same POST
sent twice takes the money twice.

So every write endpoint accepts an ``Idempotency-Key`` header. The first request
carrying a key claims a row; every repeat of that key gets the *stored* response
back without the handler running again. This is the same idea as the
``wa_message_id`` claim that stops Meta's redelivered webhooks from being
processed twice — deliberately, because that bug is already fixed in this
codebase and this is the same bug wearing a different hat.

The endpoints do not need to know about any of it: :func:`begin` and
:func:`finish` are called from two hooks on the API blueprint, so every write
endpoint — including ones written later — is covered.

Four properties decide whether this is safe or merely plausible:

* **A key is scoped to its user.** ``(user_id, key)`` is unique, so one operator
  cannot read another's response by guessing a key, and two operators who happen
  to generate the same key cannot collide.
* **A *rejection* is not an outcome.** A 400 releases the key so the client can
  fix the payload and retry with the *same* key — which is exactly what an outbox
  does when the server says "no". Only a 2xx is stored. Getting this wrong wedges
  the key permanently, and the queue then never drains.
* **Only the owning request settles its claim.** Ownership is carried on ``g``.
  Without it, a concurrent duplicate could delete the claim while the original
  was still running, leaving a payment recorded but unclaimed — and therefore
  re-runnable.
* **An abandoned claim expires.** If the process dies mid-write the claim would
  otherwise sit unsettled for ever, so one older than :data:`STALE_SECONDS` is
  taken over rather than waited on.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from flask import g, request
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import IdempotencyKey, utcnow

log = logging.getLogger(__name__)

# Long enough to cover a plausible outage and the retries that follow it, short
# enough that the table does not grow without bound on a busy day.
RETENTION_HOURS = 48
KEY_MAX_LEN = 80
# How long a claim may sit unsettled before we assume the request that made it
# died. Comfortably longer than any write in this app takes.
STALE_SECONDS = 90

# `g` is attribute-based, not a dict — ``g['x'] = 1`` raises TypeError. No leading
# underscore: keep it a plain public-looking name so attribute access is unambiguous.
_CLAIM_ATTR = "idem_claim_id"


def client_key() -> str | None:
    """The key the caller supplied, if any, or ``None``.

    Absent is the normal case for the SPA online — the client only mints a key
    when it is about to queue a write. A request with no key behaves exactly as
    it did before this module existed, which is what keeps every existing test
    and every curl honest.
    """
    try:
        raw = (request.headers.get("Idempotency-Key") or "").strip()
    except RuntimeError:          # no request context
        return None
    if not raw:
        return None
    # A client is free to send a UUID; anything longer is not one of ours.
    return raw[:KEY_MAX_LEN]


def begin() -> tuple | None:
    """Claim the key, or return the response we already gave for it.

    Returns ``None`` when the caller should run the handler as normal.
    Returns a ``(body, status)`` pair when this request is a replay and the
    handler must be skipped.
    """
    key = client_key()
    if not key:
        return None

    from flask_login import current_user

    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return None               # unauthenticated writes have nothing to key on

    # Two passes at most: the first may lose the insert race, the second reads
    # whatever the winner wrote.
    for _ in range(2):
        existing = IdempotencyKey.query.filter_by(user_id=user_id, key=key).first()

        if existing is None:
            claim = IdempotencyKey(
                user_id=user_id, key=key,
                method=request.method, path=request.path,
                created_at=utcnow(),
            )
            db.session.add(claim)
            try:
                db.session.commit()
            except IntegrityError:
                # Another request claimed the same key between our SELECT and
                # INSERT. An ordinary race, not an error — go round and read it.
                db.session.rollback()
                continue
            setattr(g, _CLAIM_ATTR, claim.id)
            return None

        if existing.is_complete:
            return _replay(existing)

        if _age_seconds(existing) > STALE_SECONDS:
            # The request that claimed this is gone. Take the claim over rather
            # than leaving the key permanently unsettleable.
            log.warning("Taking over a stale idempotency claim: %s %s key=%s",
                        existing.method, existing.path, key)
            existing.method = request.method
            existing.path = request.path
            existing.created_at = utcnow()
            db.session.commit()
            setattr(g, _CLAIM_ATTR, existing.id)
            return None

        # Genuinely still in flight: tell the client to wait, rather than running
        # the handler a second time alongside the first.
        return ({"error": "in_progress",
                 "message": "That change is still being saved. It will settle in "
                            "a moment."}, 409)

    return None


def _replay(existing: IdempotencyKey) -> tuple:
    """Answer a repeat with the first answer, without running anything."""
    existing.replay_count = (existing.replay_count or 0) + 1
    db.session.commit()

    log.info("Idempotent replay: %s %s key=%s (x%d)", existing.method,
             existing.path, existing.key, existing.replay_count)

    body = None
    if existing.response_json:
        try:
            body = json.loads(existing.response_json)
        except (TypeError, ValueError):
            body = None
    return (body if body is not None else {}, existing.status_code)


def finish(status_code: int, body) -> None:
    """Settle the claim this request owns.

    A 2xx is stored so a replay can reproduce it. Anything else **releases** the
    claim: a rejection is not an outcome, and the whole point of the key is that
    the client can send a corrected payload under it. Leaving the claim behind
    would answer every later attempt with 409 and the queue would never drain.
    """
    claim_id = _take_claim_id()
    if claim_id is None:
        return                # unkeyed, or a replay — nothing of ours to settle

    claim = db.session.get(IdempotencyKey, claim_id)
    if claim is None:
        return                # already released or pruned

    try:
        if 200 <= status_code < 300:
            if claim.is_complete:
                return        # the winner already recorded it
            claim.status_code = status_code
            claim.response_json = json.dumps(body) if body is not None else None
        else:
            db.session.delete(claim)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - a write must not fail over this
        db.session.rollback()
        log.warning("Could not settle idempotency claim %s: %s", claim_id, exc)


def prune(hours: int = RETENTION_HOURS) -> int:
    """Drop keys old enough that no client could still be holding one.

    Called on boot, not on a schedule: a stale key is only a problem when the
    table is large, and the table only grows while the app is running.
    """
    cutoff = utcnow() - timedelta(hours=hours)
    try:
        rows = IdempotencyKey.query.filter(IdempotencyKey.created_at < cutoff).all()
        for row in rows:
            db.session.delete(row)
        db.session.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.warning("Could not prune idempotency keys: %s", exc)
        return 0


def _age_seconds(claim: IdempotencyKey) -> float:
    try:
        return (utcnow() - claim.created_at).total_seconds()
    except Exception:  # noqa: BLE001
        return 0.0


def _take_claim_id() -> int | None:
    """Read and clear this request's claim id, if it owns one."""
    try:
        claim_id = g.get(_CLAIM_ATTR)
    except RuntimeError:      # no request context
        return None
    if claim_id is None:
        return None
    try:
        delattr(g, _CLAIM_ATTR)
    except AttributeError:
        pass
    return claim_id
