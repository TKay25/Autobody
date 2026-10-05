"""The navigation rail's per-account order.

The rail is arranged by dragging, and the arrangement is deliberately kept on the
user rather than in the browser: the same person works the front desk and the
workshop tablet, and a single unscoped localStorage key on a shared PC would hand
one operator's layout to the next one to sign in.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import User


def test_login_required_for_the_nav_order_route(client):
    assert client.patch("/api/me/nav-order", json={"routes": ["/jobs"]}).status_code == 401


def test_a_saved_order_round_trips(auth_client):
    res = auth_client.patch("/api/me/nav-order", json={"routes": ["/board", "/dashboard"]})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["nav_order"] == ["/board", "/dashboard"]

    # and it comes back on the next read, which is how the rail restores itself
    user = auth_client.get("/api/me").get_json()["user"]
    assert user["nav_order"] == ["/board", "/dashboard"]


def test_the_order_belongs_to_the_account_not_the_browser(app, auth_client):
    auth_client.patch("/api/me/nav-order", json={"routes": ["/board", "/dashboard"]})

    with app.app_context():
        owner = User.query.filter_by(email="owner@topclass.co.zw").one()
        manager = User.query.filter_by(email="manager@topclass.co.zw").one()
        assert owner.nav_order_routes == ["/board", "/dashboard"]
        assert manager.nav_order_routes == [], "another account must not inherit it"


def test_the_first_paint_already_knows_the_order(auth_client):
    """No second request: the order ships inside the bootstrap payload."""
    auth_client.patch("/api/me/nav-order", json={"routes": ["/board", "/dashboard"]})
    html = auth_client.get("/app").get_data(as_text=True)

    start = html.find('"nav_order"')
    assert start != -1, "the bootstrap payload must carry nav_order"
    window = html[start:start + 240]
    assert "/board" in window and "/dashboard" in window
    assert window.index("/board") < window.index("/dashboard"), "order must be preserved"


def test_a_payload_that_is_not_a_list_is_refused(auth_client):
    for bad in ("/jobs", 7, {"routes": "/jobs"}, None):
        res = auth_client.patch("/api/me/nav-order", json={"routes": bad})
        assert res.status_code == 400, f"accepted {bad!r}"


def test_junk_is_stripped_out_of_a_saved_order(auth_client):
    """Only things that look like a route are filed away."""
    res = auth_client.patch("/api/me/nav-order", json={
        "routes": ["/jobs", "not a route", "/../../etc/passwd", 42, "", "/", "/board", None],
    })
    assert res.status_code == 200
    stored = res.get_json()["nav_order"]
    assert stored == ["/jobs", "/board"], stored


def test_a_corrupt_column_reads_as_no_order(app, auth_client):
    """Junk already in the database must not take the rail down."""
    with app.app_context():
        user = User.query.filter_by(email="owner@topclass.co.zw").one()
        for junk in ("{not json", '{"a": 1}', "[1, 2, 3]", ""):
            user.nav_order = junk
            assert user.nav_order_routes == [], f"junk {junk!r} should read as empty"
        user.nav_order = None
        assert user.nav_order_routes == []


def test_the_order_is_capped(auth_client):
    lots = [f"/screen-{i}" for i in range(200)]
    stored = auth_client.patch("/api/me/nav-order", json={"routes": lots}).get_json()["nav_order"]
    assert len(stored) == User.NAV_ORDER_MAX
    assert stored[0] == "/screen-0"


def test_an_empty_order_clears_it(auth_client):
    auth_client.patch("/api/me/nav-order", json={"routes": ["/board"]})
    res = auth_client.patch("/api/me/nav-order", json={"routes": []})
    assert res.status_code == 200
    assert res.get_json()["nav_order"] == []
    assert auth_client.get("/api/me").get_json()["user"]["nav_order"] == []


def test_the_guard_adds_the_column_to_an_older_users_table(app):
    """`create_all()` makes a missing table but never ALTERs one.

    Any database provisioned before this column existed is a step behind, and
    every query touching `users` would fail on it — locally and on Render. This
    exercises the guard for real rather than trusting that it was written down.
    """
    from app import _ensure_schema

    with app.app_context():
        db.session.execute(text("ALTER TABLE users DROP COLUMN nav_order"))
        db.session.commit()
        columns = {c["name"] for c in inspect(db.engine).get_columns("users")}
        assert "nav_order" not in columns

        _ensure_schema(app)

        columns = {c["name"] for c in inspect(db.engine).get_columns("users")}
        assert "nav_order" in columns
