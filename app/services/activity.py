"""Audit trail helpers.

Every meaningful mutation calls :func:`log_activity` so the workshop has an
answer to "who changed this, and when?" — the thing that turns a tool into a
system of record.
"""
from __future__ import annotations

import json

from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import ActivityLog


def log_activity(
    action: str,
    summary: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    entity_ref: str | None = None,
    job_id: int | None = None,
    meta: dict | None = None,
    actor=None,
    commit: bool = False,
) -> ActivityLog:
    """Record one audit entry. Never raises — auditing must not break a workflow."""
    try:
        if actor is None:
            try:
                actor = current_user if getattr(current_user, "is_authenticated", False) else None
            except Exception:  # noqa: BLE001 - outside a request context
                actor = None

        entry = ActivityLog(
            actor_id=getattr(actor, "id", None),
            actor_name=getattr(actor, "full_name", None) or "System",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_ref=entity_ref,
            summary=summary[:400],
            meta_json=json.dumps(meta or {}),
            job_id=job_id,
            ip=request.remote_addr if has_request_context() else None,
        )
        db.session.add(entry)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return entry
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return None  # type: ignore[return-value]


def recent_activity(limit: int = 50, *, job_id: int | None = None,
                    entity_type: str | None = None, actor_id: int | None = None):
    query = ActivityLog.query
    if job_id is not None:
        query = query.filter(ActivityLog.job_id == job_id)
    if entity_type:
        query = query.filter(ActivityLog.entity_type == entity_type)
    if actor_id:
        query = query.filter(ActivityLog.actor_id == actor_id)
    return query.order_by(ActivityLog.id.desc()).limit(limit).all()
