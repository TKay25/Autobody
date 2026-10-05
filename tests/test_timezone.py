"""The workshop's clock.

Everything here answers one question: what does "now" mean to this app? The
answer has to be Harare -- not the server's local day, and not the viewer's
laptop. The failures this pins down are the quiet ones: a job promised "today"
that the system files as due tomorrow, and a payment taken at 23:30 that lands
on the next day's takings. Neither throws an error; both are simply wrong on the
paperwork.

Zimbabwe is UTC+2 all year, so the interesting window is 22:00-00:00 UTC, when
Harare has already turned the page and UTC has not. `at_harare_midnight` below
freezes the clock inside that window.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import tz
from app.models import Estimate, Invoice, JobCard, Task

APP_DIR = Path(__file__).resolve().parent.parent / "app"
JS_DIR = APP_DIR / "static" / "js"


@pytest.fixture()
def at_harare_midnight(monkeypatch):
    """00:30 on 1 April in Harare, which is still 22:30 on 31 March in UTC.

    Freezes :mod:`app.tz`'s clock so the two calendars disagree. Everything that
    asks the workshop what day it is must answer with the April date.
    """
    instant = datetime(2026, 3, 31, 22, 30, tzinfo=timezone.utc)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: A002 - mirrors datetime.now's signature
            return instant if tz is None else instant.astimezone(tz)

    monkeypatch.setattr(tz, "datetime", Frozen)
    return instant


# ── the offset ───────────────────────────────────────────────────────────
def test_zimbabwe_is_two_hours_ahead_all_year():
    """A fixed offset is what lets this work without a tz database."""
    assert tz.WORKSHOP_TZ.utcoffset(None) == timedelta(hours=2)
    assert tz.now().utcoffset() == timedelta(hours=2)
    assert tz.WORKSHOP_TZ_NAME == "Africa/Harare"


def test_today_is_the_harare_date():
    assert tz.today() == (datetime.now(timezone.utc) + timedelta(hours=2)).date()


def test_the_harare_day_rolls_over_before_the_utc_day(at_harare_midnight):
    assert at_harare_midnight.date() == date(2026, 3, 31), "UTC should still say the 31st"
    assert tz.today() == date(2026, 4, 1), "the workshop should already say the 1st"


# ── "today" is used to decide what is late ───────────────────────────────
def test_open_work_due_yesterday_is_overdue_at_harare_midnight(at_harare_midnight):
    yesterday, today = date(2026, 3, 31), date(2026, 4, 1)

    assert JobCard(promised_date=yesterday, stage="INTAKE").is_overdue is True
    assert Invoice(due_date=yesterday, status="ISSUED").is_overdue is True
    assert Task(due_date=yesterday, status="OPEN").is_overdue is True

    # Due today is not late, at any hour of the day.
    assert JobCard(promised_date=today, stage="INTAKE").is_overdue is False
    assert Invoice(due_date=today, status="ISSUED").is_overdue is False
    assert Task(due_date=today, status="OPEN").is_overdue is False


def test_a_collected_job_is_never_overdue(at_harare_midnight):
    assert JobCard(promised_date=date(2026, 3, 1), stage="COLLECTED").is_overdue is False


def test_an_estimate_runs_for_harare_days():
    """Raised at 01:30 Harare on 1 April, so 14 days ends on the 15th.

    Read off the UTC calendar it would end on the 14th -- a day early, on a day
    the workshop was open.
    """
    raised = datetime(2026, 3, 31, 23, 30)          # 01:30 on 1 April in Harare
    estimate = Estimate(created_at=raised, valid_days=14)
    assert estimate.expires_on == date(2026, 4, 15)


# ── day windows ──────────────────────────────────────────────────────────
def test_a_workshop_day_starts_the_evening_before_in_utc():
    assert tz.start_of_day_utc(date(2026, 4, 1)) == datetime(2026, 3, 31, 22, 0)
    assert tz.end_of_day_utc(date(2026, 4, 1)) == datetime(2026, 4, 1, 22, 0)


def test_the_reporting_window_covers_the_harare_day():
    """23:30 in Harare belongs to that day's takings, not the next one's.

    This is the bug the end-of-day sheet had: the window was UTC midnight to UTC
    midnight, so the last two hours of every trading day counted towards the
    following day.
    """
    from app.services import reporting

    start, end = reporting._day_window(date(2026, 4, 1))

    assert start == datetime(2026, 3, 31, 22, 0)
    assert end == datetime(2026, 4, 1, 22, 0)

    # 23:30 on 1 April, Harare time.
    assert reporting._in_window(datetime(2026, 4, 1, 21, 30), start, end) is True
    # 23:30 on 31 March, Harare time -- the previous sheet.
    assert reporting._in_window(datetime(2026, 3, 31, 21, 30), start, end) is False


# ── conversions ──────────────────────────────────────────────────────────
def test_to_local_shifts_a_stored_utc_stamp_into_harare():
    local = tz.to_local(datetime(2026, 4, 1, 12, 5))
    assert local == datetime(2026, 4, 1, 14, 5, tzinfo=tz.WORKSHOP_TZ)
    assert local.utcoffset() == timedelta(hours=2)


def test_to_local_moves_the_calendar_date_when_it_must():
    assert tz.to_local(datetime(2026, 4, 1, 23, 30)).date() == date(2026, 4, 2)


def test_to_local_passes_none_and_aware_values_through():
    assert tz.to_local(None) is None
    already = datetime(2026, 4, 1, 14, 5, tzinfo=tz.WORKSHOP_TZ)
    assert tz.to_local(already) == already


def test_a_harare_wall_clock_round_trips_back_to_utc():
    local = tz.now()
    stored = tz.to_utc(local)
    assert stored.tzinfo is None, "storage is naive UTC"
    assert tz.to_local(stored).replace(microsecond=0) == local.replace(microsecond=0)


def test_to_utc_reads_a_naive_value_as_harare():
    assert tz.to_utc(datetime(2026, 4, 1, 14, 5)) == datetime(2026, 4, 1, 12, 5)


def test_iso_marks_a_timestamp_as_utc():
    """An unmarked stamp is ambiguous, and a browser reads it as its own time."""
    assert tz.iso(datetime(2026, 4, 1, 12, 5)) == "2026-04-01T12:05:00Z"
    assert tz.iso(None) is None


def test_iso_leaves_a_calendar_date_alone():
    """A date has no time to be wrong about, so it must not gain a zone."""
    assert tz.iso(date(2026, 4, 1)) == "2026-04-01"


def test_format_local_renders_the_harare_wall_clock():
    assert tz.format_local(datetime(2026, 4, 1, 12, 5)) == "01 Apr 2026"
    assert tz.format_local(datetime(2026, 4, 1, 23, 30)) == "02 Apr 2026"
    assert tz.format_local(datetime(2026, 4, 1, 12, 5), "%H:%M") == "14:05"
    assert tz.format_local(None) == "—"


# ── what the workshop sees ───────────────────────────────────────────────
def test_the_end_of_day_sheet_defaults_to_the_harare_day(app):
    from app.services import reporting

    assert reporting.end_of_day()["date"] == tz.today().isoformat()


def test_the_daily_runs_default_to_harare_days(app):
    from app.services import notifications

    assert notifications.send_due_booking_reminders()["day"] == \
        (tz.today() + timedelta(days=1)).isoformat()
    assert notifications.send_due_feedback_requests()["day"] == \
        (tz.today() - timedelta(days=1)).isoformat()


def test_the_nice_date_filter_shifts_timestamps_into_harare(app):
    """`portal.html` renders `job.checked_in_at`, a stored timestamp."""
    nice = app.jinja_env.filters["nice_date"]

    assert nice(datetime(2026, 4, 1, 12, 5)) == "01 Apr 2026"
    assert nice(datetime(2026, 4, 1, 23, 30)) == "02 Apr 2026"
    # A calendar date is already unambiguous.
    assert nice(date(2026, 4, 1)) == "01 Apr 2026"
    assert nice(None) == "—"


def test_the_quotation_pdf_prints_harare_dates():
    from app.services import documents

    source = Path(documents.__file__).read_text(encoding="utf-8")
    assert "tz.format_local(" in source
    assert "created_at.strftime(" not in source
    assert "created_at or date.today()" not in source


# ── guards against the old habit coming back ─────────────────────────────
def test_no_module_reads_the_servers_calendar_day():
    """`date.today()` is the server's day; app code must use `tz.today()`."""
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name == "tz.py":      # its docstring discusses the old call
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "date.today()" in line:
                offenders.append(f"{path.relative_to(APP_DIR)}:{number}")
    assert not offenders, (
        "these read the server's calendar day instead of the workshop's, so they "
        "are wrong for two hours every night; use app.tz.today(): "
        + ", ".join(offenders))


def test_the_frontend_reads_a_bare_stamp_as_utc():
    """The API sends naive-UTC stamps, and a browser would read them as local."""
    source = (JS_DIR / "core.js").read_text(encoding="utf-8")

    assert "const TZ_NAME = 'Africa/Harare';" in source
    assert "function parseStamp(value)" in source

    for name in ("dateShort", "dateTime", "timeOnly", "relTime"):
        body = re.search(rf"function {name}\(value\) \{{(.*?)\n  \}}", source, re.S)
        assert body, f"{name} is missing from core.js"
        assert "parseStamp(" in body.group(1), f"{name} does not mark stamps as UTC"

    assert "new Date(value)" not in source, "a formatter still trusts the browser's zone"


def test_the_frontend_formatters_are_pinned_to_harare():
    source = (JS_DIR / "core.js").read_text(encoding="utf-8")
    for name in ("dateShort", "dateTime", "timeOnly"):
        body = re.search(rf"function {name}\(value\) \{{(.*?)\n  \}}", source, re.S)
        assert "timeZone: TZ_NAME" in body.group(1), f"{name} is not pinned to Harare"


def test_the_frontend_groups_by_the_harare_day():
    """Today's-count and the chat's day dividers must not use the browser's day."""
    for name in ("views/activity.js", "views/inbox.js"):
        source = (JS_DIR / name).read_text(encoding="utf-8")
        assert "T.dateKey(" in source, f"{name} does not group by Harare day"
        assert "T.TZ_NAME" in source, f"{name} does not render in Harare"

    activity = (JS_DIR / "views/activity.js").read_text(encoding="utf-8")
    assert "toDateString()" not in activity, "still comparing browser-local days"


def test_the_sidebar_clock_shows_harare_time():
    source = (JS_DIR / "app.js").read_text(encoding="utf-8")
    clock = re.search(r"function paintClock\(\) \{(.*?)\n    \}", source, re.S)
    assert clock, "paintClock is missing from app.js"
    assert "clockFmt" in clock.group(1), "the clock uses getHours() — the PC's zone"
    assert "Africa/Harare" in source
