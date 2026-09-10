"""Pytest fixtures."""
from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db
from config import TestConfig


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
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(app):
    client = app.test_client()
    res = client.post("/auth/login", json={"email": "owner@topclass.co.zw", "password": "topclass123"})
    assert res.status_code == 200, res.get_data(as_text=True)
    return client
