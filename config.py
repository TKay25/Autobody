"""Application configuration for the Topclass Auto Body Workshop OS."""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _database_uri() -> str:
    """The configured database, defaulting to a local SQLite file.

    Render and Heroku hand out URLs beginning ``postgres://``, which SQLAlchemy
    2.x refuses to load. Normalise the scheme so the same env var just works.
    """
    raw = os.getenv(
        "DATABASE_URL", f"sqlite:///{(INSTANCE_DIR / 'topclass.db').as_posix()}"
    )
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    return raw


def _public_base_url() -> str:
    """The absolute, publicly reachable URL customers get links to.

    Render sets ``RENDER_EXTERNAL_URL`` to the service's own public HTTPS URL,
    so a deploy that forgets ``PUBLIC_BASE_URL`` still gets the right answer
    instead of the localhost default — which the production check rejects, and
    which would hand customers links only the server itself can open.
    """
    return (
        os.getenv("PUBLIC_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or "http://127.0.0.1:5000"
    ).rstrip("/")


class Config:    # Lets the factory tell a real deployment from a local run without guessing
    # from hostnames or env vars. See `_assert_production_ready`.
    IS_PRODUCTION = False
    # ── Flask ────────────────────────────────────────────────────────────
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    # Boot in production even when the checks in `_assert_production_ready`
    # fail. Off by default, because every check there guards a failure that is
    # otherwise silent — the app boots, renders, and is quietly wide open. Turn
    # it on knowingly: each waived risk is printed at ERROR on every boot, and
    # waiving it does not fix anything, it only stops the refusal.
    ALLOW_UNSAFE_PRODUCTION = _bool("ALLOW_UNSAFE_PRODUCTION", False)
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = 24 * 1024 * 1024  # 24 MB photo uploads
    WTF_CSRF_TIME_LIMIT = None

    # ── Database ─────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Uploads ──────────────────────────────────────────────────────────
    UPLOAD_DIR = INSTANCE_DIR / "uploads"
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "pdf"}

    # ── Session / auth ───────────────────────────────────────────────────
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 14
    SESSION_COOKIE_SAMESITE = "Lax"

    # ── Company profile ──────────────────────────────────────────────────
    COMPANY_NAME = os.getenv("COMPANY_NAME", "Topclass Auto Body")
    COMPANY_ADDRESS = os.getenv("COMPANY_ADDRESS", "23 George Avenue, Msasa, Harare")
    COMPANY_TEL = os.getenv("COMPANY_TEL", "+263 242 446954")
    COMPANY_MOBILE = os.getenv("COMPANY_MOBILE", "+263 77 555 0555")
    COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "info@topclass.co.zw")
    COMPANY_HOURS = os.getenv("COMPANY_HOURS", "Mon - Fri: 08:00 - 17:00")
    COMPANY_WEBSITE = os.getenv("COMPANY_WEBSITE", "https://topclass.co.zw")

    # The dial code the desk's phone fields default to, and the code the WhatsApp
    # bot dials with when a number is stored in local form ("0775550555").
    # Zimbabwe by default because that is where the customers are. See
    # app/services/phone.py — this one setting covers the UI and the bot together.
    DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "263")

    # ── Money ────────────────────────────────────────────────────────────
    VAT_RATE = Decimal(os.getenv("VAT_RATE", "0.15"))
    DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "USD")
    ZWL_RATE = Decimal(os.getenv("ZWL_RATE", "13.50"))

    # ── WhatsApp ─────────────────────────────────────────────────────────
    WA_MODE = os.getenv("WA_MODE", "simulator").strip().lower()
    WA_API_VERSION = os.getenv("WA_API_VERSION", "v21.0")
    WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
    WA_BUSINESS_ACCOUNT_ID = os.getenv("WA_BUSINESS_ACCOUNT_ID", "")
    WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
    # Meta app secret (Settings → Basic). When set, inbound webhook calls must
    # carry a valid X-Hub-Signature-256 or they are rejected. Optional so that
    # simulator installs keep working, but set it in production: the webhook URL
    # is public, and without it anyone can post fake messages at the bot.
    WA_APP_SECRET = os.getenv("WA_APP_SECRET", "")
    WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "topclass-verify-token")
    # Optional second lock on the webhook *delivery* URL. The verify token only
    # proves who configured the webhook — it rides in the GET handshake and never
    # touches a POST body. Setting this makes the same shared secret a query
    # check on every delivery, so "anyone who knows our domain" becomes "anyone
    # who knows the exact URL pasted into Meta". NOT a substitute for
    # WA_APP_SECRET: it proves the caller knows the URL, not that Meta sent it.
    WA_WEBHOOK_TOKEN = os.getenv("WA_WEBHOOK_TOKEN", "")
    # The enquiry Flow's id, from Meta → Flows. Empty by default so the "Enquiry
    # form" menu row is simply not offered until a form has actually been built —
    # a row that opens nothing is worse than no row.
    WA_FLOW_ENQUIRY_ID = os.getenv("WA_FLOW_ENQUIRY_ID", "")
    # The **first screen's API name** inside that Flow. Meta rejects a screen the
    # Flow does not define, so this must match the builder exactly. Meta's own
    # builder names the first screen `QUESTION_ONE`, and that is the default here
    # for the same reason: a Flow built by pasting the JSON we hand over is the
    # common case, and a mismatch is a form that will not open at all. Change it
    # in `.env` if you rename the screen.
    WA_FLOW_ENQUIRY_SCREEN = os.getenv("WA_FLOW_ENQUIRY_SCREEN", "QUESTION_ONE")
    # The booking Flow's id. A second Flow rather than a second screen on the
    # first: one asks what is wrong with the vehicle and collects damage, the
    # other asks which day suits and collects a slot. Each is offered in the menu
    # only once its own id is configured.
    WA_FLOW_BOOKING_ID = os.getenv("WA_FLOW_BOOKING_ID", "")
    # Same rule as the enquiry Flow's screen name.
    WA_FLOW_BOOKING_SCREEN = os.getenv("WA_FLOW_BOOKING_SCREEN", "BOOKING")
    WA_GRAPH_URL = os.getenv("WA_GRAPH_URL", "https://graph.facebook.com")
    WA_SESSION_WINDOW_HOURS = 24

    # Absolute, publicly reachable base URL for customer-facing links and for
    # document URLs that Meta must fetch. Must be HTTPS in production.
    PUBLIC_BASE_URL = _public_base_url()

    # ── Documents ────────────────────────────────────────────────────────
    BANK_DETAILS = os.getenv(
        "BANK_DETAILS",
        "CBZ Bank · Topclass Auto Body · Acc 01234567890 · Branch Msasa",
    )
    ECONET_NUMBER = os.getenv("ECONET_NUMBER", "+263 77 555 0555")

    # ── Behaviour ────────────────────────────────────────────────────────
    NOTIFY_ON_STAGE_CHANGE = _bool("NOTIFY_ON_STAGE_CHANGE", True)

    # ── Seeding ──────────────────────────────────────────────────────────
    # Password handed to the staff accounts the seeder creates. Set
    # SEED_PASSWORD before a real deployment — the default is published in this
    # repository, so any instance left on it is effectively unlocked.
    SEED_PASSWORD = os.getenv("SEED_PASSWORD", "topclass123")
    # Offer the one-click demo logins on the sign-in page. A convenience while
    # developing; a production sign-in page must never list working accounts.
    SHOW_DEMO_ACCOUNTS = _bool("SHOW_DEMO_ACCOUNTS", True)
    # Recreate staff accounts and stock when the database is completely empty.
    # Off locally, where seeding is a deliberate step; on in production, where
    # an ephemeral SQLite file is wiped on every deploy and would otherwise
    # leave behind an app that nobody can sign in to.
    AUTO_SEED_STAFF = _bool("AUTO_SEED_STAFF", False)

    # ── Bootstrap owner account ──────────────────────────────────────────
    # Optional. Set both to have the app create this account on boot if it does
    # not exist. Every account the seeder makes shares one password, so this is
    # how you get a specific login with its own credentials into a deployment
    # whose database may start empty. Nothing is written to the repository.
    OWNER_EMAIL = (os.getenv("OWNER_EMAIL") or "").strip().lower()
    OWNER_PASSWORD = os.getenv("OWNER_PASSWORD") or ""
    OWNER_NAME = os.getenv("OWNER_NAME", "Workshop Owner").strip()


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    WA_MODE = "simulator"
    # The whole WhatsApp block is pinned, because ``config.py`` runs ``load_dotenv``
    # at import: without this a developer's real `.env` leaks into the suite. That
    # already broke the webhook tests once (WA_VERIFY_TOKEN) and had the live
    # access token sitting in test config.
    WA_ACCESS_TOKEN = ""
    WA_PHONE_NUMBER_ID = ""
    WA_BUSINESS_ACCOUNT_ID = ""
    # Pinned so the suite never depends on the developer's local .env — the
    # webhook verification tests assert on this exact string.
    WA_VERIFY_TOKEN = "topclass-verify-token"
    # Also pinned, and empty: a developer with WA_WEBHOOK_TOKEN in .env must not
    # turn every webhook test into a 403.
    WA_WEBHOOK_TOKEN = ""
    # Same reasoning. The signature test subclasses this with its own secret; a
    # real secret leaking in here would 403 every unsigned webhook test.
    WA_APP_SECRET = ""
    WA_FLOW_ENQUIRY_ID = ""
    WA_FLOW_ENQUIRY_SCREEN = "QUESTION_ONE"
    WA_FLOW_BOOKING_ID = ""
    WA_FLOW_BOOKING_SCREEN = "BOOKING"
    # Pinned rather than inherited: a developer with DEFAULT_COUNTRY_CODE in .env
    # must not shift every phone-number assertion in the suite.
    DEFAULT_COUNTRY_CODE = "263"
    SECRET_KEY = "test-secret"
    # Keep hashing cheap in tests; production uses Werkzeug's scrypt default.
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"


class ProductionConfig(Config):
    IS_PRODUCTION = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    # Anyone can reach the sign-in page, so don't advertise demo credentials
    # there. Opt back in with SHOW_DEMO_ACCOUNTS=true for a staging app.
    SHOW_DEMO_ACCOUNTS = _bool("SHOW_DEMO_ACCOUNTS", False)
    # A wiped database still has to be able to hand someone a working login,
    # otherwise the deploy is unreachable. Reference data only, never demo
    # customers or job cards.
    AUTO_SEED_STAFF = _bool("AUTO_SEED_STAFF", True)


CONFIG_MAP = {
    "development": Config,
    "testing": TestConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    env = (name or os.getenv("FLASK_ENV") or "development").lower()
    return CONFIG_MAP.get(env, Config)
