"""Additive schema guard.

The project ships no migration tool. ``db.create_all()`` creates missing
*tables* and nothing else — it never alters an existing one. So the moment a
model gains a column, any database that was provisioned earlier (the Render
Postgres instance, or a developer's SQLite file) is left a step behind and
every query touching that table starts failing with an "unknown column" error.

``ensure_columns`` closes that gap for the purely additive case: it inspects
the live table and issues a plain ``ALTER TABLE ... ADD COLUMN`` for anything
the model declares but the database is missing. It only ever *adds* — it never
drops, renames or rewrites, so running it against a populated production
database is safe and idempotent.

Anything beyond adding a column (changing a type, backfilling, dropping) should
go through Flask-Migrate instead.
"""
from __future__ import annotations

import logging
from typing import Mapping

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def ensure_columns(engine, table: str, columns: Mapping[str, str]) -> list[str]:
    """Add any of ``columns`` that ``table`` is missing.

    ``columns`` maps a column name to its SQL type, for example
    ``{"confirmed_by_id": "INTEGER", "confirmed_at": "TIMESTAMP"}``.
    Table and column names are internal literals, never user input.

    Returns the names actually added, which makes the helper easy to assert on
    in tests.
    """
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        # Nothing to patch — create_all() will build it in full on first use.
        return []

    existing = {column["name"] for column in inspector.get_columns(table)}
    missing = [(name, ddl_type) for name, ddl_type in columns.items()
               if name not in existing]
    if not missing:
        return []

    added: list[str] = []
    with engine.begin() as connection:
        for name, ddl_type in missing:
            try:
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
                )
            except Exception:  # pragma: no cover - dialect-specific quirks
                logger.warning("Could not add column %s.%s", table, name,
                               exc_info=True)
                continue
            added.append(name)

    if added:
        logger.info("Added missing column(s) to %s: %s", table, ", ".join(added))
    return added
