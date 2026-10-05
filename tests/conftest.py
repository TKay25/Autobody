"""Pytest fixtures."""
from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db
from config import ProductionConfig, TestConfig


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        from app.seed import STAFF, run_seed

        run_seed(with_demo=False)
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def production_config():
    """A production config that is deliberately safe.

    Tests that need to see what production actually does must start from a config
    the boot guard will accept. Every value is pinned explicitly because
    ``config.py`` runs ``load_dotenv`` at import: a developer's real ``.env``
    (WhatsApp live, no app secret, the published seed password) otherwise leaks
    into ``ProductionConfig`` and the guard refuses to start. That is the guard
    doing its job — but it makes the raw class unusable as a test subject.

    ``SHOW_DEMO_ACCOUNTS`` is pinned False to match production's own default,
    which is asserted separately against the class itself.
    """
    class SafeProduction(ProductionConfig):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SECRET_KEY = "a-real-secret-for-this-test"
        SEED_PASSWORD = "not-the-published-one"
        AUTO_SEED_STAFF = False
        WA_MODE = "simulator"
        WA_APP_SECRET = "an-app-secret"
        PUBLIC_BASE_URL = "https://autobody.example.com"
        SHOW_DEMO_ACCOUNTS = False
        OWNER_EMAIL = ""
        OWNER_PASSWORD = ""
        # Keep the suite quick; production keeps Werkzeug's scrypt default.
        PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"

    return SafeProduction


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(app):
    client = app.test_client()
    res = client.post("/auth/login", json={"email": "owner@topclass.co.zw", "password": "topclass123"})
    assert res.status_code == 200, res.get_data(as_text=True)
    return client
