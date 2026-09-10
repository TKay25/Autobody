"""The sign-in page, the demo shortcuts and post-login redirection.

The demo buttons are a developer convenience that must never reach production,
and `?next=` is attacker-controlled, so both get a guard here.
"""
from __future__ import annotations

from app import create_app
from app.seed import STAFF
from config import Config, ProductionConfig, TestConfig


def test_login_page_renders_with_the_demo_shortcuts(client):
    page = client.get("/login").get_data(as_text=True)
    assert "auth-shell" in page
    # One button per offered demo account.
    assert page.count("auth-demo-btn") >= 4
    assert "topclass123" in page


def test_demo_shortcuts_come_from_the_seed(client):
    """A button must never advertise an account the seeder doesn't create."""
    seeded = {email for _name, email, _role, _phone in STAFF}
    page = client.get("/login").get_data(as_text=True)
    for email in ("owner@topclass.co.zw", "manager@topclass.co.zw",
                  "estimator@topclass.co.zw", "front@topclass.co.zw"):
        assert email in seeded
        assert f'data-email="{email}"' in page


def test_demo_shortcuts_are_hidden_in_production():
    """A public sign-in page must not list working credentials."""
    assert Config.SHOW_DEMO_ACCOUNTS is True
    assert ProductionConfig.SHOW_DEMO_ACCOUNTS is False

    application = create_app(ProductionConfig)
    with application.app_context():
        # The routes need tables; production uses whatever DATABASE_URL points at.
        from app.extensions import db

        db.create_all()
        try:
            page = application.test_client().get("/login").get_data(as_text=True)
            # The markup is gone, so there is no button to click and no
            # credentials on the page. (The script that drives the buttons is
            # still shipped; it simply finds nothing.)
            assert 'data-email="' not in page
            assert "Demo accounts" not in page
            assert "topclass123" not in page
        finally:
            db.session.remove()


def test_the_root_url_shows_the_sign_in_page_to_strangers(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "auth-shell" in res.get_data(as_text=True)


def test_the_root_url_shows_the_app_to_a_signed_in_user(auth_client):
    res = auth_client.get("/")
    assert res.status_code == 200
    page = res.get_data(as_text=True)
    # The SPA shell, not the sign-in page. The views themselves are built in JS.
    assert 'id="app"' in page
    assert "__BOOTSTRAP__" in page
    assert "auth-shell" not in page


def test_a_bad_password_explains_what_happened(client):
    res = client.post("/auth/login", data={"email": "dummy@topclass.co.zw",
                                           "password": "dummy"})
    assert res.status_code == 401
    page = res.get_data(as_text=True)
    assert "Incorrect email or password." in page
    # The address is echoed back so a typo is obvious, and the shortcuts are offered.
    assert "dummy@topclass.co.zw" in page
    assert "Try one of the demo accounts" in page


def test_sign_in_lands_on_the_requested_path(client):
    res = client.post("/auth/login?next=/app?x=1",
                      data={"email": "owner@topclass.co.zw", "password": "topclass123"})
    assert res.status_code == 302
    assert "/app" in res.headers["Location"]


def test_sign_in_refuses_to_redirect_off_site(client):
    """`?next=` is attacker-controlled — it must never become an open redirect."""
    for hostile in ("https://evil.example.com/steal",
                    "//evil.example.com/steal",
                    "http://evil.example.com"):
        res = client.post("/auth/login",
                          query_string={"next": hostile},
                          data={"email": "owner@topclass.co.zw", "password": "topclass123"})
        assert res.status_code == 302
        location = res.headers["Location"]
        assert "evil.example.com" not in location, f"leaked {hostile!r}"
        assert location.endswith("/app")


def test_the_seed_password_can_be_overridden():
    """A deployed instance must be able to avoid the published default."""

    class Custom(TestConfig):
        SEED_PASSWORD = "a-much-better-secret"

    application = create_app(Custom)
    with application.app_context():
        from app.extensions import db
        from app.models import User
        from app.seed import run_seed

        db.create_all()
        try:
            run_seed(with_demo=False)
            owner = User.query.filter_by(email="owner@topclass.co.zw").first()
            assert owner.check_password("a-much-better-secret")
            assert not owner.check_password("topclass123")
        finally:
            db.session.remove()
            db.drop_all()


def test_only_managers_may_create_staff(client):
    """The Add-staff button is manager-only, and so is the endpoint behind it."""
    client.post("/auth/login", json={"email": "front@topclass.co.zw",
                                     "password": "topclass123"})
    res = client.post("/api/users", json={
        "full_name": "Should Not Exist", "email": "nope@topclass.co.zw",
        "password": "whatever123", "role": "technician",
    })
    assert res.status_code == 403


def test_a_manager_can_create_a_staff_account_who_can_then_sign_in(auth_client):
    res = auth_client.post("/api/users", json={
        "full_name": "Panashe Chirwa", "email": "Panashe@Topclass.co.zw",
        "phone": "+263 77 555 0999", "password": "workshop2026", "role": "frontdesk",
    })
    assert res.status_code == 201, res.get_data(as_text=True)
    # Emails are stored lower-cased, so signing in is case-insensitive.
    assert res.get_json()["user"]["email"] == "panashe@topclass.co.zw"

    fresh = auth_client.application.test_client()
    login = fresh.post("/auth/login", json={"email": "panashe@topclass.co.zw",
                                            "password": "workshop2026"})
    assert login.status_code == 200
    assert login.get_json()["user"]["role_label"] == "Front Desk"


def test_a_duplicate_email_is_refused(auth_client):
    res = auth_client.post("/api/users", json={
        "full_name": "Clone", "email": "owner@topclass.co.zw",
        "password": "workshop2026", "role": "technician",
    })
    assert res.status_code == 400
    assert "already registered" in res.get_json()["message"]
