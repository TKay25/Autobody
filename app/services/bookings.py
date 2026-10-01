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
from ..models import Booking

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
    db.session.commit()

    notified = notifications.notify_booking_rescheduled(booking, previous)
    return {"previous": previous, "notified": notified}


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
