"""Idempotent writes — the thing that makes a replayed queue safe.

The console has to keep working when the internet drops, so a write can be sent
from the browser without ever being acknowledged and then sent again when the
link returns. For "move this job to QC" a duplicate is untidy. For "record this
payment" it takes the customer's money twice.

Every write endpoint therefore accepts an ``Idempotency-Key`` header. These tests
pin the behaviour that matters, and in particular the two ways this is easy to
get subtly wrong:

* a **rejected** write must release its key, or the corrected retry is answered
  with 409 for ever and the queue never drains;
* a claim abandoned by a **crashed** request must expire, or that key is wedged
  for ever.

Both were real bugs in the first cut of this module.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from app.extensions import db
from app.models import IdempotencyKey, Payment, User, utcnow


def _invoice(client, *, reg, phone="+263772334455"):
    """A job card with an invoice against it — something worth double-charging."""
    res = client.post("/api/jobs", json={
        "customer_name": f"Payee {reg}", "customer_phone": phone,
        "reg_no": reg, "panels": ["Bonnet"],
    })
    assert res.status_code == 201, res.get_json()
    job_id = res.get_json()["job"]["id"]
    res = client.post(f"/api/jobs/{job_id}/invoice", json={})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["invoice"]


def _pay(client, invoice, *, key=None, amount=25, method="CASH"):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"/api/invoices/{invoice['id']}/payment",
                       json={"amount": amount, "method": method}, headers=headers)


def _payments(app, invoice_id):
    with app.app_context():
        return Payment.query.filter_by(invoice_id=invoice_id).count()


def test_a_replayed_payment_is_only_taken_once(app, auth_client):
    """The whole reason this module exists."""
    invoice = _invoice(auth_client, reg="IDEM1")
    key = str(uuid.uuid4())

    first = _pay(auth_client, invoice, key=key, amount=60)
    assert first.status_code == 200

    # The outbox wakes up after a reconnect and sends exactly the same thing.
    replay = _pay(auth_client, invoice, key=key, amount=60)

    assert replay.status_code == 200
    assert replay.headers.get("Idempotent-Replay") == "true"
    # Same body back, so the operator is not shown a second receipt number.
    assert replay.get_json() == first.get_json()
    assert _payments(app, invoice["id"]) == 1


def test_a_replay_does_not_move_the_balance_twice(app, auth_client):
    invoice = _invoice(auth_client, reg="IDEM2")
    key = str(uuid.uuid4())
    _pay(auth_client, invoice, key=key, amount=40)
    _pay(auth_client, invoice, key=key, amount=40)

    after = auth_client.get("/api/invoices").get_json()["items"][0]
    assert after["balance"] == invoice["balance"] - 40


def test_a_new_key_is_a_new_payment(app, auth_client):
    """Two deliberate payments of the same size must both land.

    A second EcoCash payment is a real thing the shop has to be able to record;
    the guard is against *repeats*, not against repetition.
    """
    invoice = _invoice(auth_client, reg="IDEM3")
    _pay(auth_client, invoice, key=str(uuid.uuid4()), amount=30)
    _pay(auth_client, invoice, key=str(uuid.uuid4()), amount=30)
    assert _payments(app, invoice["id"]) == 2


def test_a_write_with_no_key_behaves_as_before(app, auth_client):
    """Every existing caller, and every curl, must be unaffected."""
    invoice = _invoice(auth_client, reg="IDEM4")
    _pay(auth_client, invoice, amount=20)
    _pay(auth_client, invoice, amount=20)
    assert _payments(app, invoice["id"]) == 2


def test_the_same_key_from_a_different_user_is_not_a_replay(app, auth_client, client):
    """Keys are scoped to their owner.

    Otherwise one operator could read another's response by guessing a key, and
    two operators who happened to generate the same one would collide.
    """
    invoice = _invoice(auth_client, reg="IDEM5")
    key = str(uuid.uuid4())
    _pay(auth_client, invoice, key=key, amount=35)

    assert client.post("/auth/login", data={
        "email": "manager@topclass.co.zw", "password": "topclass123"}).status_code == 302
    other = _pay(client, invoice, key=key, amount=35)

    assert other.headers.get("Idempotent-Replay") is None
    assert _payments(app, invoice["id"]) == 2


def test_a_rejected_write_releases_its_key(app, auth_client):
    """A 400 is not an outcome.

    If the rejection left the key claimed, the client's corrected retry would be
    answered with 409 for ever — and a queue that cannot drain is worse than no
    queue at all. This was a real bug.
    """
    invoice = _invoice(auth_client, reg="IDEM6")
    key = str(uuid.uuid4())

    rejected = _pay(auth_client, invoice, key=key, amount=0)
    assert rejected.status_code == 400, rejected.get_json()

    corrected = _pay(auth_client, invoice, key=key, amount=45)
    assert corrected.status_code == 200, corrected.get_json()
    assert _payments(app, invoice["id"]) == 1

    # And the corrected write is itself replayable, like any other.
    again = _pay(auth_client, invoice, key=key, amount=45)
    assert again.headers.get("Idempotent-Replay") == "true"
    assert _payments(app, invoice["id"]) == 1


def test_a_claim_abandoned_by_a_crash_is_taken_over(app, auth_client):
    """A process that dies mid-write must not wedge the key for ever."""
    invoice = _invoice(auth_client, reg="IDEM7")
    key = str(uuid.uuid4())

    with app.app_context():
        owner_id = User.query.filter_by(role="owner").first().id
        db.session.add(IdempotencyKey(
            user_id=owner_id, key=key, method="POST",
            path=f"/api/invoices/{invoice['id']}/payment",
            created_at=utcnow() - timedelta(seconds=300),
        ))
        db.session.commit()

    res = _pay(auth_client, invoice, key=key, amount=15)
    assert res.status_code == 200, res.get_json()
    assert _payments(app, invoice["id"]) == 1


def test_a_claim_still_in_flight_asks_the_client_to_wait(app, auth_client):
    """Two identical requests at once must not run the handler twice."""
    invoice = _invoice(auth_client, reg="IDEM8")
    key = str(uuid.uuid4())

    with app.app_context():
        owner_id = User.query.filter_by(role="owner").first().id
        # Fresh, so not stale — this is the genuine "still being saved" case.
        db.session.add(IdempotencyKey(
            user_id=owner_id, key=key, method="POST",
            path=f"/api/invoices/{invoice['id']}/payment",
            created_at=utcnow(),
        ))
        db.session.commit()

    res = _pay(auth_client, invoice, key=key, amount=10)
    assert res.status_code == 409, res.get_json()
    assert res.get_json()["error"] == "in_progress"
    # Nothing was taken, so a later retry is still free to succeed.
    assert _payments(app, invoice["id"]) == 0


def test_keys_older_than_the_retention_window_are_pruned(app, auth_client):
    """The table must not grow without bound on a long-running app."""
    from app.services import idempotency

    invoice = _invoice(auth_client, reg="IDEM9")
    _pay(auth_client, invoice, key=str(uuid.uuid4()), amount=10)

    with app.app_context():
        owner_id = User.query.filter_by(role="owner").first().id
        db.session.add(IdempotencyKey(
            user_id=owner_id, key="ancient", method="POST", path="/api/whatever",
            status_code=200, created_at=utcnow() - timedelta(days=9),
        ))
        db.session.commit()
        before = IdempotencyKey.query.count()
        removed = idempotency.prune()

        assert removed == 1
        assert IdempotencyKey.query.count() == before - 1
        # The recent, still-replayable key survived.
        assert IdempotencyKey.query.filter_by(key="ancient").first() is None


def test_a_get_is_never_treated_as_a_write(app, auth_client):
    """Reading with a key header must not claim anything."""
    invoice = _invoice(auth_client, reg="IDEM10")
    auth_client.get("/api/invoices", headers={"Idempotency-Key": str(uuid.uuid4())})
    auth_client.get(f"/api/invoices", headers={"Idempotency-Key": str(uuid.uuid4())})

    with app.app_context():
        assert IdempotencyKey.query.count() == 0
