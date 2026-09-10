"""WSGI entrypoint for production servers.

    gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120

Defaults to the production config (secure cookies); set FLASK_ENV to override.
"""
from __future__ import annotations

import os

from app import create_app
from config import get_config

app = create_app(get_config(os.getenv("FLASK_ENV", "production")))

if __name__ == "__main__":  # pragma: no cover
    app.run()
