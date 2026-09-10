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


class Config:
    # ── Flask ────────────────────────────────────────────────────────────
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
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
    WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "topclass-verify-token")
    WA_GRAPH_URL = os.getenv("WA_GRAPH_URL", "https://graph.facebook.com")
    WA_SESSION_WINDOW_HOURS = 24

    # Absolute, publicly reachable base URL for customer-facing links and for
    # document URLs that Meta must fetch. Must be HTTPS in production.
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/")

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


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    WA_MODE = "simulator"
    SECRET_KEY = "test-secret"
    # Keep hashing cheap in tests; production uses Werkzeug's scrypt default.
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    # Anyone can reach the sign-in page, so don't advertise demo credentials
    # there. Opt back in with SHOW_DEMO_ACCOUNTS=true for a staging app.
    SHOW_DEMO_ACCOUNTS = _bool("SHOW_DEMO_ACCOUNTS", False)


CONFIG_MAP = {
    "development": Config,
    "testing": TestConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    env = (name or os.getenv("FLASK_ENV") or "development").lower()
    return CONFIG_MAP.get(env, Config)
