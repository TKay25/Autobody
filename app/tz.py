"""Workshop time: the one place that decides what "now" means.

Storage is naive UTC -- see :func:`app.models.utcnow`. Everything a *person*
reads, and every decision about *which day it is*, is in Harare, because that is
where the workshop is:

* a job promised for "today" is promised for the Harare day;
* a payment taken at 23:30 belongs on that day's end-of-day sheet, not the next;
* a timestamp captured at 14:05 is shown as 14:05, not 12:05.

Before this module, every date in the app came from ``date.today()``, which is
the *server's* calendar day. On a UTC host that is a UTC day, so between 00:00
and 02:00 Harare time the app believed it was still yesterday.

Zimbabwe has been on Central Africa Time (UTC+2) with no daylight saving since
1903, so the offset is a constant. That is deliberate: a fixed offset needs no
IANA tz database, so there is no ``tzdata`` package to install on the host and
nothing to go stale. If Zimbabwe ever reintroduced DST, swap ``WORKSHOP_TZ`` for
``ZoneInfo("Africa/Harare")`` and everything below keeps working.

The wire format
---------------
Timestamps cross the API as naive-UTC ISO-8601 strings with **no** suffix
(``"2026-10-05T12:00:00"``). A browser would read that as its *own* local time,
which is an hour or two out, so the SPA funnels every one of them through
``TC.parseStamp`` in ``app/static/js/core.js``, which treats a suffix-less string
as UTC and then renders it in ``Africa/Harare``. If a client outside this SPA
ever consumes the API, hand it :func:`iso` instead, which appends the ``Z``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

#: Harare is UTC+2 all year. A fixed offset, so no tzdata is required.
WORKSHOP_TZ = timezone(timedelta(hours=2), "CAT")

#: The IANA name, for the browser side (``Intl`` needs a real zone name).
WORKSHOP_TZ_NAME = "Africa/Harare"

#: Short label for UI and documents.
WORKSHOP_TZ_LABEL = "CAT"


def now() -> datetime:
    """The current instant, as an aware datetime in Harare."""
    return datetime.now(WORKSHOP_TZ)


def today() -> date:
    """The current Harare calendar date.

    This is what "today" means everywhere in the app -- this is the replacement
    for ``date.today()``, which reads the server's day instead of the workshop's.
    """
    return now().date()


def to_local(value: datetime | None) -> datetime | None:
    """Stored naive-UTC datetime -> aware datetime in Harare.

    ``None`` passes through, so this is safe to call on nullable columns.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(WORKSHOP_TZ)


def to_utc(value: datetime | None) -> datetime | None:
    """A Harare wall-clock datetime -> naive UTC, ready to store.

    Use this when turning something a person typed (a slot, a cutoff) into a
    column value. Naive input is read as Harare time; aware input is converted.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=WORKSHOP_TZ)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def start_of_day_utc(day: date | None = None) -> datetime:
    """Naive-UTC instant at which the given Harare day begins.

    For comparing against stored timestamps: "today's payments" is not
    ``created_at >= today``, because a Harare day starts two hours before the
    UTC date ticks over, so the first two hours of trading would be attributed
    to the previous day.
    """
    day = day or today()
    local_midnight = datetime(day.year, day.month, day.day, tzinfo=WORKSHOP_TZ)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def end_of_day_utc(day: date | None = None) -> datetime:
    """Naive-UTC instant at which the given Harare day ends (exclusive)."""
    return start_of_day_utc(day) + timedelta(days=1)


def iso(value) -> str | None:
    """Serialise a stored timestamp for a *generic* API client.

    Appends an explicit ``Z`` so the value is unambiguous. Calendar dates (with
    no time component) pass through untouched -- a ``Date`` is already
    unambiguous and adding a zone to it would be wrong. ``None`` passes through.
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value.isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def format_local(value: datetime | None, fmt: str = "%d %b %Y") -> str:
    """A stored timestamp rendered in Harare, for documents and messages.

    Anything falsy renders as an em dash, matching the ``nice_date`` filter, so
    a missing timestamp reads as "no date" rather than "1970".
    """
    local = to_local(value)
    if local is None:
        return "—"
    return local.strftime(fmt)


def format_local_date(value: datetime | None, fmt: str = "%d %b %Y") -> str:
    """Just the Harare calendar date of a stored timestamp."""
    return format_local(value, fmt)


def format_local_time(value: datetime | None, fmt: str = "%H:%M") -> str:
    """Just the Harare clock time of a stored timestamp."""
    return format_local(value, fmt)
