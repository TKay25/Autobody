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


def test_the_override_boots_where_the_guard_would_refuse(production_config):
    """The escape hatch: get the site up, knowingly, without deleting the checks."""
    app = create_app(broken(
        production_config,
        ALLOW_UNSAFE_PRODUCTION=True,
        WA_MODE="live",
        WA_APP_SECRET="",
        PUBLIC_BASE_URL="http://127.0.0.1:5000",
    ))
    assert app is not None
    # Still live, so the guard was waived rather than the config quietly downgraded.
    assert app.config["WA_MODE"] == "live"


def test_the_override_names_what_it_waived(production_config, caplog):
    """A waived check has to be visible on every boot. The whole reason these
    checks raise instead of warn is that a warning nobody reads is worthless."""
    with caplog.at_level(logging.ERROR):
        create_app(broken(
            production_config,
            ALLOW_UNSAFE_PRODUCTION=True,
            WA_MODE="live",
            WA_APP_SECRET="",
        ))
    assert "ALLOW_UNSAFE_PRODUCTION" in caplog.text
    assert "WA_APP_SECRET" in caplog.text


def test_the_override_is_off_unless_it_is_asked_for(production_config):
    """Default behaviour is unchanged: an unsafe deploy is still refused."""
    with pytest.raises(RuntimeError, match="WA_APP_SECRET"):
        create_app(broken(
            production_config,
            ALLOW_UNSAFE_PRODUCTION=False,
            WA_MODE="live",
            WA_APP_SECRET="",
        ))


def test_the_override_does_nothing_in_a_safe_production_config(production_config,
                                                               caplog):
    """It is a waiver, not a mode: a correct deploy logs nothing extra."""
    with caplog.at_level(logging.ERROR):
        create_app(broken(production_config, ALLOW_UNSAFE_PRODUCTION=True))
    assert "ALLOW_UNSAFE_PRODUCTION" not in caplog.text


def test_the_guard_message_is_pure_ascii(production_config):
    """The message is printed by whatever logger the host runs, often on a cp1252
    console. A UnicodeEncodeError there would hide the real problem."""
    with pytest.raises(RuntimeError) as caught:
        create_app(broken(production_config, SECRET_KEY="dev-secret-change-me"))
    str(caught.value).encode("ascii")


def test_render_supplies_the_public_url_when_it_is_not_set(monkeypatch):
    """A deploy that forgets PUBLIC_BASE_URL should still get the right answer.

    Render sets RENDER_EXTERNAL_URL to the service's own public HTTPS URL. Without
    this fallback the guard refuses the deploy pointing at 127.0.0.1 — a value
    nobody configured and nobody can act on, since the real URL was right there
    in the environment.
    """
    import config as config_module

    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://autobody.onrender.com")
    assert config_module._public_base_url() == "https://autobody.onrender.com"


def test_an_explicit_public_url_still_wins_over_render(monkeypatch):
    """The fallback must never override a value somebody actually set."""
    import config as config_module

    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://autobody.onrender.com")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://workshop.topclass.co.zw/")
    # Trailing slash is stripped: these URLs are concatenated with a path.
    assert config_module._public_base_url() == "https://workshop.topclass.co.zw"


def test_without_any_public_url_it_falls_back_to_localhost(monkeypatch):
    """A local run keeps working; the guard is what refuses this in production."""
    import config as config_module

    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert config_module._public_base_url() == "http://127.0.0.1:5000"
