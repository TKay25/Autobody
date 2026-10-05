"""The boot-time production safety guard.

Each of these is a failure that would otherwise be silent: the app boots, renders,
and is quietly wide open or quietly unreachable. They are asserted against a real
`create_app` rather than against the checks in isolation, because the one thing
that matters is whether a *deploy* is refused.

The safe baseline comes from the `production_config` fixture, so there is exactly
one definition of "production, configured correctly" and it cannot drift between
this file and the tests that exercise production in other ways.
"""
from __future__ import annotations

import logging

import pytest
from config import Config

from app import create_app


def broken(base, **overrides):
    """The safe production config with one thing (or more) wrong with it."""
    return type("BrokenProduction", (base,), overrides)


def test_a_correctly_configured_production_boot_starts(production_config):
    assert create_app(production_config) is not None


def test_development_is_never_guarded():
    """A local run with defaults must keep working; that is how it gets developed."""
    class Dev(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        AUTO_SEED_STAFF = False
        OWNER_EMAIL = ""
        PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"

    assert create_app(Dev) is not None


def test_the_published_secret_key_is_refused(production_config):
    """With a known SECRET_KEY every session cookie is forgeable, so this is a
    full account takeover rather than a hardening item."""
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(broken(production_config, SECRET_KEY="dev-secret-change-me"))


@pytest.mark.parametrize("blank", ["", None])
def test_an_empty_secret_key_is_refused(production_config, blank):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(broken(production_config, SECRET_KEY=blank))


def test_the_published_seed_password_is_refused_when_staff_would_be_created(production_config):
    """AUTO_SEED_STAFF + the default password is exactly how an empty database
    ends up reachable as owner@topclass.co.zw / topclass123 on a public URL."""
    with pytest.raises(RuntimeError, match="SEED_PASSWORD"):
        create_app(broken(production_config, AUTO_SEED_STAFF=True, SEED_PASSWORD="topclass123"))


def test_the_default_seed_password_is_fine_when_nothing_is_seeded(production_config):
    """Seeding off means the password is never used, so it is not a reason to
    refuse a deploy."""
    assert create_app(broken(production_config, AUTO_SEED_STAFF=False,
                             SEED_PASSWORD="topclass123")) is not None


def test_live_mode_without_the_app_secret_is_refused(production_config):
    """The webhook is public; unsigned POSTs create real customers and job cards."""
    with pytest.raises(RuntimeError, match="WA_APP_SECRET"):
        create_app(broken(production_config, WA_MODE="live", WA_APP_SECRET=""))


def test_live_mode_pointing_at_localhost_is_refused(production_config):
    """Customer documents go out as links and Meta fetches them from the public
    internet, so a localhost link is a dead document."""
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        create_app(broken(production_config, WA_MODE="live",
                          PUBLIC_BASE_URL="http://127.0.0.1:5000"))


def test_simulator_mode_is_not_required_to_be_reachable(production_config):
    """Nothing is sent, so a localhost base URL is harmless until go-live."""
    assert create_app(broken(production_config, WA_MODE="simulator",
                             PUBLIC_BASE_URL="http://127.0.0.1:5000")) is not None


def test_every_problem_is_reported_at_once(production_config):
    """A deploy is usually wrong in more than one way, and fixing them one failed
    deploy at a time is how a launch eats an afternoon."""
    with pytest.raises(RuntimeError) as caught:
        create_app(broken(
            production_config,
            SECRET_KEY="dev-secret-change-me",
            AUTO_SEED_STAFF=True,
            SEED_PASSWORD="topclass123",
            WA_MODE="live",
            WA_APP_SECRET="",
        ))
    message = str(caught.value)
    assert "SECRET_KEY" in message
    assert "SEED_PASSWORD" in message
    assert "WA_APP_SECRET" in message


def test_sqlite_in_production_warns_but_still_starts(production_config, caplog):
    """Survivable — a host with a persistent disk is a legitimate setup — but it
    should never be a surprise after the first deploy."""
    with caplog.at_level(logging.WARNING):
        create_app(production_config)
    assert "SQLite" in caplog.text


def test_the_guard_message_is_pure_ascii(production_config):
    """The message is printed by whatever logger the host runs, often on a cp1252
    console. A UnicodeEncodeError there would hide the real problem."""
    with pytest.raises(RuntimeError) as caught:
        create_app(broken(production_config, SECRET_KEY="dev-secret-change-me"))
    str(caught.value).encode("ascii")
