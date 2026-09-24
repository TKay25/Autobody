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

``drop_columns`` and ``drop_tables`` handle the opposite case: a column or
whole table that the models have stopped declaring. These exist because removing
an attribute from a model is **not** the same as removing it from the database.
A ``NOT NULL`` column with only a Python-side default has no server default, so
once SQLAlchemy stops sending it every ``INSERT`` fails outright. Retiring a
field therefore means dropping the physical column, and that is what these do.
Both no-op when the column or table is already gone, and both are guarded so a
dialect that refuses the operation degrades to a logged warning rather than a
boot failure.

Anything beyond adding or removing a column (changing a type, backfilling,
rewriting data) should go through Flask-Migrate instead.
"""
from __future__ import annotations

import logging
from typing import Iterable, Mapping

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


def drop_columns(engine, table: str, names: Iterable[str]) -> list[str]:
    """Drop the named columns from ``table`` if they are still there.

    Used when a model field is retired. Returns the names actually dropped.
    A column cannot be dropped on a dialect that does not support it, so each
    statement is individually guarded — one stubborn column never stops the
    application from booting.
    """
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return []

    existing = {column["name"] for column in inspector.get_columns(table)}
    doomed = [name for name in names if name in existing]
    if not doomed:
        return []

    dropped: list[str] = []
    with engine.begin() as connection:
        for name in doomed:
            try:
                connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {name}"))
            except Exception:  # pragma: no cover - dialect-specific quirks
                logger.warning("Could not drop column %s.%s", table, name,
                               exc_info=True)
                continue
            dropped.append(name)

    if dropped:
        logger.info("Dropped retired column(s) from %s: %s", table, ", ".join(dropped))
    return dropped


def drop_tables(engine, names: Iterable[str]) -> list[str]:
    """Drop whole tables the models no longer declare.

    Destructive by design, so the caller has to name the table explicitly and
    the whole thing is idempotent — a missing table is simply skipped.
    """
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    doomed = [name for name in names if name in present]
    if not doomed:
        return []

    dropped: list[str] = []
    with engine.begin() as connection:
        for name in doomed:
            try:
                connection.execute(text(f"DROP TABLE {name}"))
            except Exception:  # pragma: no cover - dialect-specific quirks
                logger.warning("Could not drop table %s", name, exc_info=True)
                continue
            dropped.append(name)

    if dropped:
        logger.info("Dropped retired table(s): %s", ", ".join(dropped))
    return dropped
