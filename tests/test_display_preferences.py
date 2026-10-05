"""Rail mode, compact rows and folded nav sections, held per account.

Same reasoning as the rail's order: these are a person's working layout, and the
same person works the front desk and the workshop tablet. One unscoped
localStorage key on a shared PC does the opposite — it hands one operator's
layout to whoever signs in next.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import User


def test_login_required_for_the_preferences_route(client):
    assert client.patch("/api/me/preferences", json={"rail": True}).status_code == 401


def test_the_default_is_nothing_stored(app, auth_client):
    """Not defaulted on purpose: the client must be able to tell "never chose"
    from "chose the default", or a browser-only layout gets overwritten by an
    empty one the first time somebody signs in after this moved to the account."""
    assert auth_client.get("/api/me").get_json()["user"]["preferences"] == {}


def test_preferences_merge_rather_than_replace(auth_client):
    """Three different gestures save three different keys; saving one must not
    reset the other two."""
    auth_client.patch("/api/me/preferences", json={"rail": True})
    auth_client.patch("/api/me/preferences", json={"compact_rows": True})
    auth_client.patch("/api/me/preferences", json={"collapsed_sections": ["money"]})

    stored = auth_client.get("/api/me").get_json()["user"]["preferences"]
    assert stored == {"rail": True, "compact_rows": True, "collapsed_sections": ["money"]}


def test_a_toggle_can_be_turned_back_off(auth_client):
    auth_client.patch("/api/me/preferences", json={"rail": True})
    res = auth_client.patch("/api/me/preferences", json={"rail": False})
    assert res.get_json()["preferences"]["rail"] is False


def test_the_preferences_belong_to_the_account(app, auth_client):
    auth_client.patch("/api/me/preferences", json={"rail": True, "compact_rows": True})

    with app.app_context():
        owner = User.query.filter_by(email="owner@topclass.co.zw").one()
        manager = User.query.filter_by(email="manager@topclass.co.zw").one()
        assert owner.display_preferences["rail"] is True
        assert manager.display_preferences == {}, "another account must not inherit it"


def test_only_known_values_are_kept(auth_client):
    """A malformed payload cannot be filed away and handed back to the rail."""
    res = auth_client.patch("/api/me/preferences", json={
        "rail": "yes",                      # a string, not a boolean
        "compact_rows": True,
        "collapsed_sections": ["money", 7, "", None, "x" * 100],
        "something_else": "ignore me",      # an unknown key
    })
    assert res.status_code == 200
    assert res.get_json()["preferences"] == {"compact_rows": True, "collapsed_sections": ["money"]}


def test_the_section_list_is_capped(auth_client):
    lots = [f"section-{i}" for i in range(200)]
    stored = auth_client.patch(
        "/api/me/preferences", json={"collapsed_sections": lots}
    ).get_json()["preferences"]["collapsed_sections"]
    assert len(stored) == User.PREFERENCE_SECTIONS_MAX
    assert stored[0] == "section-0"


def test_the_body_must_be_an_object(auth_client):
    for bad in ([], "rail", 7):
        assert auth_client.patch("/api/me/preferences", json=bad).status_code in (400, 415), bad


def test_a_corrupt_column_reads_as_no_preferences(app):
    with app.app_context():
        user = User.query.filter_by(email="owner@topclass.co.zw").one()
        for junk in ("{not json", "[1, 2, 3]", '"rail"', "{}"):
            user.preferences = junk
            assert user.display_preferences == {}, f"junk {junk!r} should read as empty"


def test_unknown_stored_keys_are_dropped_not_returned(app):
    with app.app_context():
        user = User.query.filter_by(email="owner@topclass.co.zw").one()
        user.preferences = '{"rail": true, "retired_thing": 1, "compact_rows": "no"}'
        assert user.display_preferences == {"rail": True}


def test_the_first_paint_already_knows_the_preferences(auth_client):
    """No flash: the shell script reads these before it draws anything."""
    auth_client.patch("/api/me/preferences", json={"rail": True, "compact_rows": True})
    html = auth_client.get("/app").get_data(as_text=True)

    start = html.find('"preferences"')
    assert start != -1, "the bootstrap payload must carry the preferences"
    window = html[start:start + 200]
    assert "rail" in window and "compact_rows" in window


def test_the_guard_adds_the_column_to_an_older_users_table(app):
    """`create_all()` never ALTERs an existing table, so every database
    provisioned before this column existed would fail on `users`."""
    from app import _ensure_schema

    with app.app_context():
        db.session.execute(text("ALTER TABLE users DROP COLUMN preferences"))
        db.session.commit()
        columns = {c["name"] for c in inspect(db.engine).get_columns("users")}
        assert "preferences" not in columns

        _ensure_schema(app)

        columns = {c["name"] for c in inspect(db.engine).get_columns("users")}
        assert "preferences" in columns
