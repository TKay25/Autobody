"""End-of-day report, booking attribution and the schema guard."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine, inspect, text

from app.constants import BOOKING_STATUSES, TASK_STATUSES
from app.models import User
from app.schema import drop_columns, drop_tables, ensure_columns


def _booking(auth_client, phone="+263772100100", name="Report Lead"):
    res = auth_client.post("/api/bookings", json={
        "name": name, "phone": phone, "service": "Ceramic Coating",
        "slot_date": date.today().isoformat(), "source": "phone",
    })
    assert res.status_code == 201, res.get_data(as_text=True)
    return res.get_json()["booking"]


def _fetch(auth_client, booking_id):
    rows = auth_client.get("/api/bookings").get_json()["items"]
    return next(r for r in rows if r["id"] == booking_id)


# ── statuses and attribution ────────────────────────────────────────────────
def test_attended_is_a_known_booking_status():
    assert "ATTENDED" in BOOKING_STATUSES


def test_confirming_records_the_staff_member(auth_client):
    booking = _booking(auth_client)
    assert booking["confirmed_by"] is None

    res = auth_client.patch(f"/api/bookings/{booking['id']}", json={"status": "CONFIRMED"})
    assert res.status_code == 200
    updated = res.get_json()["booking"]
    assert updated["status"] == "CONFIRMED"
    # Falls back to whoever is signed in rather than leaving the column blank.
    assert updated["confirmed_by"]
    assert updated["confirmed_at"] is not None


def test_attending_can_name_another_employee(auth_client, app):
    booking = _booking(auth_client)
    with app.app_context():
        technician = User.query.filter(User.role != "owner").first()
        tech_id, tech_name = technician.id, technician.full_name

    res = auth_client.patch(f"/api/bookings/{booking['id']}", json={
        "status": "ATTENDED", "attended_by_id": tech_id,
    })
    assert res.status_code == 200
    updated = res.get_json()["booking"]
    assert updated["status"] == "ATTENDED"
    assert updated["attended_by"] == tech_name
    assert updated["attended_at"] is not None
    # Attending implies it was accepted, so the confirmation is attributed too.
    assert updated["confirmed_by"] is not None


def test_unknown_status_is_rejected(auth_client):
    booking = _booking(auth_client)
    res = auth_client.patch(f"/api/bookings/{booking['id']}", json={"status": "NONSENSE"})
    assert res.status_code == 400


def test_booking_list_exposes_attribution(auth_client):
    booking = _booking(auth_client)
    auth_client.patch(f"/api/bookings/{booking['id']}", json={"status": "CONFIRMED"})
    rows = auth_client.get("/api/bookings").get_json()["items"]
    row = next(r for r in rows if r["id"] == booking["id"])
    assert row["confirmed_by"]
    assert "attended_by" in row


# ── end of day ─────────────────────────────────────────────────────────────
def test_end_of_day_report_covers_jobs_bookings_and_money(auth_client):
    _booking(auth_client)
    res = auth_client.get("/api/reports/end-of-day")
    assert res.status_code == 200
    report = res.get_json()

    assert report["date"] == date.today().isoformat()
    assert {"jobs", "bookings", "money", "staff"} <= set(report)
    # Every stage is reported, even at zero, so the sheet reads as a pipeline.
    assert len(report["jobs"]["by_stage"]) > 0
    assert {"code", "label", "count"} <= set(report["jobs"]["by_stage"][0])
    assert report["bookings"]["awaiting_confirmation"] >= 1
    assert {"collected", "invoiced", "outstanding", "unpaid_count"} <= set(report["money"])


def test_end_of_day_report_accepts_a_date(auth_client):
    res = auth_client.get("/api/reports/end-of-day?date=2026-01-02")
    assert res.status_code == 200
    assert res.get_json()["date"] == "2026-01-02"


def test_end_of_day_pdf_is_a_pdf(auth_client):
    res = auth_client.get("/api/reports/end-of-day/pdf")
    assert res.status_code == 200
    assert res.mimetype == "application/pdf"
    assert res.get_data()[:4] == b"%PDF"


def test_report_requires_login(client):
    assert client.get("/api/reports/end-of-day").status_code == 401


# ── references: an enquiry is not a booking ─────────────────────────────────
def test_an_enquiry_only_becomes_a_booking_when_confirmed(auth_client):
    booking = _booking(auth_client)
    assert booking["reference"].startswith("TC-ENQ")
    assert booking["booking_reference"] is None
    assert booking["display_reference"] == booking["reference"]

    res = auth_client.patch(f"/api/bookings/{booking['id']}", json={"status": "CONFIRMED"})
    assert res.status_code == 200
    updated = res.get_json()["booking"]
    assert updated["booking_reference"].startswith("TC-BKG")
    # The enquiry reference is kept, not overwritten, so the customer's
    # original number can still be traced.
    assert updated["reference"] == booking["reference"]
    assert updated["display_reference"] == updated["booking_reference"]


def test_confirming_again_does_not_reissue_the_reference(auth_client):
    booking = _booking(auth_client)
    auth_client.patch(f"/api/bookings/{booking['id']}", json={"status": "CONFIRMED"})
    first = _fetch(auth_client, booking["id"])["booking_reference"]
    auth_client.patch(f"/api/bookings/{booking['id']}", json={"status": "CONFIRMED"})
    assert _fetch(auth_client, booking["id"])["booking_reference"] == first


def test_a_booking_can_be_created_already_confirmed(auth_client):
    res = auth_client.post("/api/bookings", json={
        "name": "Walk-in", "phone": "+263772999888",
        "service": "Car Detailing", "slot_date": date.today().isoformat(),
        "confirm": True,
    })
    booking = res.get_json()["booking"]
    assert booking["status"] == "CONFIRMED"
    assert booking["booking_reference"].startswith("TC-BKG")
    assert booking["confirmed_by"]


# ── rescheduling ───────────────────────────────────────────────────────────
def test_rescheduling_moves_the_slot_and_counts_it(auth_client):
    booking = _booking(auth_client)
    new_day = (date.today() + timedelta(days=3)).isoformat()

    res = auth_client.post(f"/api/bookings/{booking['id']}/reschedule",
                           json={"slot_date": new_day, "slot_time": "10:00"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["booking"]["slot_date"] == new_day
    assert body["booking"]["slot_time"] == "10:00"
    assert body["booking"]["rescheduled_count"] == 1
    assert body["previous"]          # what it used to be, for the message
    assert "message" in body


def test_rescheduling_needs_a_real_change(auth_client):
    booking = _booking(auth_client)
    res = auth_client.post(f"/api/bookings/{booking['id']}/reschedule",
                           json={"slot_date": booking["slot_date"],
                                 "slot_time": booking["slot_time"]})
    assert res.status_code == 400


def test_rescheduling_requires_a_date(auth_client):
    booking = _booking(auth_client)
    res = auth_client.post(f"/api/bookings/{booking['id']}/reschedule", json={})
    assert res.status_code == 400


# ── outcome: did the visit convert? ────────────────────────────────────────
def test_outcome_records_whether_the_visit_converted(auth_client):
    booking = _booking(auth_client)
    res = auth_client.patch(f"/api/bookings/{booking['id']}",
                            json={"status": "ARRIVED", "outcome": "SECURED"})
    assert res.status_code == 200
    body = res.get_json()["booking"]
    assert body["status"] == "ARRIVED"
    assert body["outcome"] == "SECURED"
    assert body["outcome_label"] == "Job secured"


def test_a_walk_out_is_recorded_too(auth_client):
    booking = _booking(auth_client)
    res = auth_client.patch(f"/api/bookings/{booking['id']}",
                            json={"status": "ARRIVED", "outcome": "WALKED_OUT"})
    assert res.get_json()["booking"]["outcome_label"] == "Walked out — no commitment"


def test_an_unknown_outcome_is_rejected(auth_client):
    booking = _booking(auth_client)
    res = auth_client.patch(f"/api/bookings/{booking['id']}", json={"outcome": "MAYBE"})
    assert res.status_code == 400


# ── schema guard ───────────────────────────────────────────────────────────
def test_ensure_columns_adds_only_what_is_missing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE bookings (id INTEGER PRIMARY KEY, notes TEXT)"))

    added = ensure_columns(engine, "bookings", {
        "notes": "TEXT", "attended_by_id": "INTEGER", "attended_at": "TIMESTAMP",
    })
    assert added == ["attended_by_id", "attended_at"]

    columns = {c["name"] for c in inspect(engine).get_columns("bookings")}
    assert {"id", "notes", "attended_by_id", "attended_at"} <= columns
    # Idempotent: a second run has nothing left to do.
    assert ensure_columns(engine, "bookings", {"attended_by_id": "INTEGER"}) == []


def test_ensure_columns_ignores_unknown_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert ensure_columns(engine, "nope", {"x": "INTEGER"}) == []


# A retired model field is not gone until the *column* is gone. ``is_insurance``
# was NOT NULL with only a Python-side default, so once SQLAlchemy stopped
# sending it every INSERT failed. These pin the other half of the guard.
def test_drop_columns_removes_a_retired_field(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retire.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE job_cards (id INTEGER PRIMARY KEY, job_no TEXT, "
            "is_insurance BOOLEAN NOT NULL DEFAULT 0)"
        ))
        conn.execute(text("INSERT INTO job_cards (job_no, is_insurance) VALUES ('TC-1', 1)"))

    assert drop_columns(engine, "job_cards", ["is_insurance"]) == ["is_insurance"]

    columns = {c["name"] for c in inspect(engine).get_columns("job_cards")}
    assert "is_insurance" not in columns
    # Idempotent, and the rows survived.
    assert drop_columns(engine, "job_cards", ["is_insurance"]) == []
    with engine.begin() as conn:
        assert conn.execute(text("SELECT job_no FROM job_cards")).scalar() == "TC-1"
    assert drop_columns(engine, "nope", ["x"]) == []


def test_drop_tables_removes_a_retired_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retire2.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE claims (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE job_cards (id INTEGER PRIMARY KEY)"))

    assert drop_tables(engine, ["claims"]) == ["claims"]

    remaining = set(inspect(engine).get_table_names())
    assert "claims" not in remaining
    assert "job_cards" in remaining
    # Idempotent: nothing left to drop.
    assert drop_tables(engine, ["claims"]) == []


# ── the day book on the closing sheet ───────────────────────────────────────
def _task(auth_client, **overrides):
    body = {"title": "Chase the bumper supplier", "category": "Parts", **overrides}
    res = auth_client.post("/api/tasks", json=body)
    assert res.status_code == 201, res.get_data(as_text=True)
    return res.get_json()["task"]


def test_the_report_carries_the_day_book(auth_client):
    """The closing sheet is a handover document.

    What nobody has finished yet matters as much as the money, so the to-do list
    and its statuses belong in the same report — not on a separate screen the
    foreman has to remember to open.
    """
    _task(auth_client, title="Order the front bumper",
          due_date=date.today().isoformat())
    _task(auth_client, title="Call Mrs Moyo back", category="Customer call")

    tasks = auth_client.get("/api/reports/end-of-day").get_json()["tasks"]

    assert tasks["open"] == 2
    assert tasks["due_today"] == 1
    assert tasks["undated"] == 1
    # Every status is published even at zero, so the sheet reads as a full board
    # rather than only the columns somebody happened to use.
    assert [row["code"] for row in tasks["by_status"]] == TASK_STATUSES
    counts = {row["code"]: row["count"] for row in tasks["by_status"]}
    assert counts["OPEN"] == 2
    assert counts["BLOCKED"] == 0

    # Dated work first, undated last — the same order the board uses.
    assert [row["title"] for row in tasks["list"]] == [
        "Order the front bumper", "Call Mrs Moyo back"
    ]


def test_every_task_on_the_sheet_names_a_custodian(auth_client):
    """A list of work with nobody's name against it is not a handover."""
    _task(auth_client)
    tasks = auth_client.get("/api/reports/end-of-day").get_json()["tasks"]

    assert tasks["by_custodian"], "somebody has to be carrying it"
    assert tasks["by_custodian"][0]["open"] == 1
    assert tasks["by_custodian"][0]["name"]
    assert tasks["list"][0]["custodian"]


def test_completed_work_is_counted_for_the_day_not_for_ever(auth_client):
    """`DONE` on the sheet means done *today*.

    A lifetime count would make the board's fourth status a running total, which
    tells a foreman nothing about the shift he is closing out.
    """
    task = _task(auth_client)
    auth_client.patch(f"/api/tasks/{task['id']}", json={"status": "DONE"})

    today = auth_client.get("/api/reports/end-of-day").get_json()["tasks"]
    assert today["done_today"] == 1
    assert {r["code"]: r["count"] for r in today["by_status"]}["DONE"] == 1
    assert today["open"] == 0
    assert [r["title"] for r in today["done"]] == ["Chase the bumper supplier"]

    # Three days ago it was not finished, so it must not be counted as finished.
    earlier = (date.today() - timedelta(days=3)).isoformat()
    other = auth_client.get(f"/api/reports/end-of-day?date={earlier}").get_json()["tasks"]
    assert other["done_today"] == 0
    assert {r["code"]: r["count"] for r in other["by_status"]}["DONE"] == 0


def test_overdue_work_is_flagged_on_the_sheet(auth_client):
    _task(auth_client, title="Late chase",
          due_date=(date.today() - timedelta(days=2)).isoformat())

    tasks = auth_client.get("/api/reports/end-of-day").get_json()["tasks"]
    assert tasks["overdue"] == 1
    assert tasks["list"][0]["is_overdue"] is True
    assert tasks["by_custodian"][0]["overdue"] == 1


def test_a_custodian_with_no_job_cards_still_appears_on_the_sheet(auth_client):
    """The staff table used to key off job cards and bookings alone.

    A storeman — or the owner — with three things on the list and no job card to
    their name would have been dropped from the closing sheet entirely.
    """
    _task(auth_client)

    sheet = auth_client.get("/api/reports/end-of-day").get_json()
    carriers = [row for row in sheet["staff"] if row["open_tasks"]]
    assert carriers, "the custodian of the only task is missing from the staff table"
    assert carriers[0]["open_jobs"] == 0
    assert carriers[0]["done_tasks"] == 0
