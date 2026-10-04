"""Moving and calling off appointments.

Two callers do these things — the desk
(``POST /api/bookings/<id>/reschedule``, and the status change in
``PATCH /api/bookings/<id>``) and the customer (the *Move it* and *Cancel*
buttons on the day-before reminder). Both have to agree about what happened, and
both have to tell the customer afterwards, so the behaviour lives here rather
than being written twice.

``notifications`` imports this module, so the import of ``notifications`` below
is deliberately *inside* the functions that need it. Hoisting it would make the
two modules import each other at load time.
"""
from __future__ import annotations

import logging
from datetime import date

from ..extensions import db
from ..models import Booking, utcnow

log = logging.getLogger(__name__)


def slot_text(booking) -> str:
    """"Mon 23 Sep 2026 at 08:00", or just the day when no time was booked."""
    if not booking.slot_date:
        return "to be advised"
    when = booking.slot_date.strftime("%a %d %b %Y")
    return f"{when} at {booking.slot_time}" if booking.slot_time else when


def reschedule(booking: Booking, *, slot_date: date,
               slot_time: str | None = None) -> dict:
    """Move an appointment, then tell the customer.

    Returns ``{"previous", "notified"}``. The message goes out **after** the move
    is committed, so a customer can never be told about a change that did not
    happen — the failure mode that matters here, because the customer acts on it.
    """
    from . import notifications

    previous = slot_text(booking)
    booking.slot_date = slot_date
    booking.slot_time = slot_time or None
    booking.rescheduled_count = (booking.rescheduled_count or 0) + 1
    # A move satisfies whatever the customer had asked for, so an open request
    # is closed here rather than being left to be answered twice.
    _drop_request(booking)
    db.session.commit()

    notified = notifications.notify_booking_rescheduled(booking, previous)
    return {"previous": previous, "notified": notified}


def request_reschedule(booking: Booking, *, slot_date: date,
                       slot_time: str | None = None,
                       note: str | None = None) -> dict:
    """Record the customer's ask to move it. Moves **nothing**.

    The workshop is the side that promises a slot, so a customer cannot take one
    on their own: they ask, the desk agrees, and the customer is told once it is
    done. That is why this is separate from :func:`reschedule` — the bot's
    *Move it* button used to move the appointment outright, which let a customer
    overwrite an arrangement the front desk had made with them.

    Deliberately does **not** message the customer: the only caller is the bot,
    which answers with its own reply, and the customer getting two messages
    saying the same thing is worse than one. Returns ``{"text", "task"}``;
    ``text`` is what they asked for, so a caller can echo it back.
    """
    booking.requested_slot_date = slot_date
    booking.requested_slot_time = slot_time or None
    booking.requested_at = utcnow()
    booking.requested_note = (note or "").strip() or None
    db.session.commit()

    asked_for = booking.requested_slot_text
    return {"text": asked_for, "task": _request_task(booking, asked_for)}


def accept_reschedule_request(booking: Booking) -> dict:
    """Do as asked, then tell the customer.

    Delegates to :func:`reschedule`, which is the same call the desk's own
    Reschedule dialog makes, so agreeing to a request and moving it by hand
    cannot produce different outcomes for the same change.

    Returns ``{"moved", "previous", "notified", "reason"}``. ``moved`` is False
    when there was nothing to do — no open request, or the customer asked for
    the slot they are already on.
    """
    if not booking.has_reschedule_request or not booking.requested_slot_date:
        return {"moved": False, "previous": None, "notified": False,
                "reason": "no_request"}

    wanted = booking.requested_slot_date
    wanted_time = booking.requested_slot_time
    if wanted == booking.slot_date and wanted_time == (booking.slot_time or None):
        # Asking for the slot you are already on is not a move. Clearing the
        # request is the whole job, or the banner would sit there for ever.
        _drop_request(booking)
        db.session.commit()
        return {"moved": False, "previous": slot_text(booking), "notified": False,
                "reason": "same_slot"}

    result = reschedule(booking, slot_date=wanted, slot_time=wanted_time)
    return {"moved": True, "reason": "", **result}


def decline_reschedule_request(booking: Booking, *, reason: str | None = None) -> dict:
    """Turn the requested time down and say so, leaving the appointment alone.

    Silently ignoring an ask is worse than saying no, so the customer always gets
    a message. Returns ``{"declined", "notified", "slot"}``.
    """
    from . import notifications

    if not booking.has_reschedule_request:
        return {"declined": False, "notified": False, "slot": slot_text(booking)}

    held = slot_text(booking)
    _drop_request(booking)
    if reason:
        note = f"Reschedule request declined: {reason}"
        booking.notes = f"{booking.notes}\n{note}" if booking.notes else note
    db.session.commit()

    notified = notifications.notify_reschedule_declined(booking, held, reason=reason)
    return {"declined": True, "notified": notified, "slot": held}


def _drop_request(booking: Booking) -> None:
    """Clear an open request. Does not commit — the caller does."""
    booking.requested_slot_date = None
    booking.requested_slot_time = None
    booking.requested_at = None
    booking.requested_note = None


def _request_task(booking: Booking, asked_for: str | None) -> bool:
    """Put the ask on the front desk's day book. ``True`` if one was raised.

    It is not enough to show it on the bookings screen: the screen only helps
    somebody who is already looking at it, and a booking that has been confirmed
    is not on the enquiry bell any more. A ticket is the thing that gets noticed.
    Swallows its own errors — a missing day book must never lose a customer's
    request, which is already committed by the time this runs.
    """
    try:
        from ..models import Task

        who = booking.customer.name if booking.customer else "a customer"
        task = Task(
            title=f"Move {who} to {asked_for or 'a new time'}",
            detail=(f"{booking.display_reference} · {booking.service}\n"
                    f"Currently {slot_text(booking)}.\n"
                    f"They have asked for {asked_for or 'another time'}.\n"
                    + (f'Their note: "{booking.requested_note}"'
                       if booking.requested_note else
                       "Accept it on the Enquiries & Bookings screen once agreed.")),
            category="Front desk",
            priority="MEDIUM",
            status="OPEN",
            due_date=date.today(),
        )
        db.session.add(task)
        db.session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not raise a desk task for %s: %s",
                    booking.display_reference, exc)
        return False


def cancel(booking: Booking, *, by: str = "customer") -> bool:
    """Call an appointment off. ``True`` if this changed anything.

    The date and time are deliberately left where they are: the desk needs to see
    what the day was asked to hold, and the end-of-day report counts by status,
    not by the slot being empty. Only the status moves, which is what every query
    already filters on.

    An already-cancelled booking is not cancelled twice — the caller has to know
    the difference so it can say "that one is already cancelled" instead of
    confirming a change it did not make.
    """
    if booking.status == "CANCELLED":
        return False

    booking.status = "CANCELLED"
    note = f"Cancelled by {by} on WhatsApp."
    booking.notes = f"{booking.notes}\n{note}" if booking.notes else note
    db.session.commit()
    return True
