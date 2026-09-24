"""The day book — tasks with an activity, a custodian and a status."""
from __future__ import annotations

from datetime import date, timedelta

from app.models import User


def _task(auth_client, **overrides):
    body = {"title": "Chase the bumper supplier", "category": "Parts", **overrides}
    res = auth_client.post("/api/tasks", json=body)
    assert res.status_code == 201, res.get_data(as_text=True)
    return res.get_json()["task"]


def _end_of_week() -> date:
    return date.today() - timedelta(days=date.today().weekday()) + timedelta(days=6)


def test_a_task_needs_a_title(auth_client):
    assert auth_client.post("/api/tasks", json={"title": ""}).status_code == 400


def test_tasks_require_login(client):
    assert client.get("/api/tasks").status_code == 401


def test_creating_a_task_assigns_the_signed_in_custodian(auth_client):
    task = _task(auth_client)
    # Falls back to whoever added it, so nothing lands unowned by accident.
    assert task["custodian"]
    assert task["status"] == "OPEN"
    assert task["status_label"] == "Not started"


def test_the_day_view_shows_today_and_anything_overdue(auth_client):
    """Overdue work is more urgent than work due later, not less."""
    _task(auth_client, title="Overdue chase",
          due_date=(date.today() - timedelta(days=1)).isoformat())
    _task(auth_client, title="Due today", due_date=date.today().isoformat())
    _task(auth_client, title="Much later",
          due_date=(date.today() + timedelta(days=30)).isoformat())

    titles = [t["title"] for t in auth_client.get("/api/tasks?window=day").get_json()["items"]]
    assert "Overdue chase" in titles
    assert "Due today" in titles
    assert "Much later" not in titles


def test_the_week_view_reaches_the_end_of_the_week(auth_client):
    _task(auth_client, title="Later this week", due_date=_end_of_week().isoformat())
    titles = [t["title"]
              for t in auth_client.get("/api/tasks?window=week").get_json()["items"]]
    assert "Later this week" in titles


def test_undated_work_is_counted_but_kept_out_of_the_day(auth_client):
    _task(auth_client, title="No day set")
    day = auth_client.get("/api/tasks?window=day").get_json()
    assert day["undated"] == 1
    assert "No day set" not in [t["title"] for t in day["items"]]
    # It is reported, not lost.
    everything = auth_client.get("/api/tasks?window=all").get_json()
    assert "No day set" in [t["title"] for t in everything["items"]]


def test_marking_done_stamps_the_time_and_reopening_clears_it(auth_client):
    task = _task(auth_client)

    done = auth_client.patch(f"/api/tasks/{task['id']}",
                             json={"status": "DONE"}).get_json()["task"]
    assert done["completed_at"] is not None
    assert done["status_label"] == "Done"

    reopened = auth_client.patch(f"/api/tasks/{task['id']}",
                                 json={"status": "DOING"}).get_json()["task"]
    assert reopened["completed_at"] is None, "a stale completion time was left behind"
    assert reopened["status_label"] == "In progress"


def test_an_unknown_status_is_rejected(auth_client):
    task = _task(auth_client)
    res = auth_client.patch(f"/api/tasks/{task['id']}", json={"status": "NOPE"})
    assert res.status_code == 400


def test_the_custodian_can_be_reassigned(auth_client, app):
    with app.app_context():
        other = User.query.filter(User.role != "owner").first()
        other_id, other_name = other.id, other.full_name

    task = _task(auth_client)
    updated = auth_client.patch(f"/api/tasks/{task['id']}",
                                json={"custodian_id": other_id}).get_json()["task"]
    assert updated["custodian"] == other_name


def test_open_filter_excludes_finished_work(auth_client):
    task = _task(auth_client, title="Finish me", due_date=date.today().isoformat())
    auth_client.patch(f"/api/tasks/{task['id']}", json={"status": "DONE"})

    items = auth_client.get("/api/tasks?window=day&status=open").get_json()["items"]
    assert "Finish me" not in [t["title"] for t in items]


def test_a_task_can_be_deleted(auth_client):
    task = _task(auth_client)
    assert auth_client.delete(f"/api/tasks/{task['id']}").status_code == 200
    assert auth_client.get("/api/tasks?window=all").get_json()["count"] == 0


def test_overdue_is_flagged_on_the_row(auth_client):
    task = _task(auth_client, due_date=(date.today() - timedelta(days=2)).isoformat())
    assert task["is_overdue"] is True


def test_a_task_can_point_at_a_job_card(auth_client):
    job = auth_client.post("/api/jobs", json={
        "customer_name": "Task Linker", "reg_no": "TSK001",
        "service": "Car Detailing",
    }).get_json()["job"]
    task = _task(auth_client, title="Call about the quote", job_id=job["id"])
    assert task["job_no"] == job["job_no"]


def test_statuses_are_published_for_the_ui(auth_client):
    meta = auth_client.get("/api/meta").get_json()
    assert meta["task_statuses"] == ["OPEN", "DOING", "BLOCKED", "DONE"]
    assert meta["task_status_labels"]["DONE"] == "Done"
    assert meta["task_status_colours"]["BLOCKED"] == "danger"
