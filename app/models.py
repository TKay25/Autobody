"""SQLAlchemy domain models for the Topclass Auto Body Workshop OS.

The job card is the centre of the universe: everything else (estimates, claims,
parts, invoices, WhatsApp threads) hangs off it.
"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .constants import (
    BOOKING_OUTCOMES,
    CLAIM_STATUS_LABELS,
    ENQUIRY_REF_PREFIX,
    STAGE_LABELS,
    STAGE_PROGRESS,
)
from .extensions import db


def utcnow() -> datetime:
    """Naive UTC timestamp.

    SQLite/SQLAlchemy columns here are naive, so we strip the tzinfo rather than
    storing aware datetimes. Avoids the Python 3.12+ ``utcnow()`` deprecation.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now() -> datetime:
    return utcnow()


def _money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    return Decimal(str(value)).quantize(Decimal("0.01"))


def gen_ref(prefix: str) -> str:
    """Human-readable reference, e.g. TC-JOB-4F9C21."""
    return f"{prefix}-{secrets.token_hex(3).upper()}"


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now, nullable=False)


# ─────────────────────────────────────────────────────────────────────────────
# People
# ─────────────────────────────────────────────────────────────────────────────
class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(40))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="frontdesk", nullable=False)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime)

    def set_password(self, raw: str) -> None:
        """Hash and store a password.

        The work factor is overridable so the test suite does not spend a minute
        on scrypt while production keeps the strong default. Werkzeug rejects an
        explicit ``method=None``, so only pass it when actually configured.
        """
        method = None
        try:
            from flask import current_app

            method = current_app.config.get("PASSWORD_HASH_METHOD")
        except RuntimeError:  # outside an app context
            method = None

        if method:
            self.password_hash = generate_password_hash(raw, method=method)
        else:
            self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def initials(self) -> str:
        parts = [p for p in (self.full_name or "?").split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "?"

    @property
    def role_label(self) -> str:
        from .constants import ROLE_LABELS

        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_manager(self) -> bool:
        return self.role in {"owner", "manager"}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "role": self.role,
            "role_label": self.role_label,
            "initials": self.initials,
            "is_manager": self.is_manager,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    phone = db.Column(db.String(40), index=True)
    whatsapp = db.Column(db.String(40), index=True)
    email = db.Column(db.String(160))
    address = db.Column(db.String(255))
    is_fleet = db.Column(db.Boolean, default=False, nullable=False)
    company = db.Column(db.String(160))
    notes = db.Column(db.Text)
    whatsapp_opt_in = db.Column(db.Boolean, default=True, nullable=False)
    portal_token = db.Column(db.String(40), unique=True, default=lambda: secrets.token_urlsafe(16))

    vehicles = db.relationship("Vehicle", back_populates="customer", cascade="all, delete-orphan")
    jobs = db.relationship("JobCard", back_populates="customer")
    bookings = db.relationship("Booking", back_populates="customer")
    invoices = db.relationship("Invoice", back_populates="customer")

    @property
    def wa_number(self) -> str | None:
        """Normalised WhatsApp number in E.164-ish digits, e.g. 263775550555."""
        raw = self.whatsapp or self.phone
        if not raw:
            return None
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits.startswith("0"):
            digits = "263" + digits[1:]
        return digits or None

    @property
    def open_jobs(self) -> int:
        return sum(1 for j in self.jobs if j.stage != "COLLECTED")

    def to_dict(self, deep: bool = False) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "whatsapp": self.whatsapp,
            "wa_number": self.wa_number,
            "email": self.email,
            "address": self.address,
            "company": self.company,
            "is_fleet": self.is_fleet,
            "notes": self.notes,
            "whatsapp_opt_in": self.whatsapp_opt_in,
            "open_jobs": self.open_jobs,
            "created_at": self.created_at.isoformat(),
        }
        if deep:
            data["vehicles"] = [v.to_dict() for v in self.vehicles]
            data["jobs"] = [j.to_dict(brief=True) for j in self.jobs]
        return data

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Customer {self.name}>"


class Vehicle(TimestampMixin, db.Model):
    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    reg_no = db.Column(db.String(30), nullable=False, index=True)
    make = db.Column(db.String(60))
    model = db.Column(db.String(60))
    year = db.Column(db.Integer)
    colour = db.Column(db.String(40))
    vin = db.Column(db.String(60))
    mileage = db.Column(db.Integer)
    notes = db.Column(db.Text)

    customer = db.relationship("Customer", back_populates="vehicles")
    jobs = db.relationship("JobCard", back_populates="vehicle")

    @property
    def title(self) -> str:
        bits = [self.year, self.make, self.model]
        label = " ".join(str(b) for b in bits if b)
        return label or "Vehicle"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "reg_no": self.reg_no,
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "colour": self.colour,
            "vin": self.vin,
            "mileage": self.mileage,
            "title": self.title,
            "notes": self.notes,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Vehicle {self.reg_no}>"


# ─────────────────────────────────────────────────────────────────────────────
# Job cards
# ─────────────────────────────────────────────────────────────────────────────
class JobCard(TimestampMixin, db.Model):
    __tablename__ = "job_cards"

    id = db.Column(db.Integer, primary_key=True)
    job_no = db.Column(db.String(30), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)

    service = db.Column(db.String(80), default="Panel Beating & Spray Painting")
    stage = db.Column(db.String(30), default="INTAKE", nullable=False, index=True)
    priority = db.Column(db.String(20), default="NORMAL", nullable=False)
    is_insurance = db.Column(db.Boolean, default=False, nullable=False)

    description = db.Column(db.Text)
    damage_summary = db.Column(db.Text)
    bay = db.Column(db.String(20))
    technician_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    estimator_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    checked_in_at = db.Column(db.DateTime, default=_now)
    promised_date = db.Column(db.Date)
    completed_at = db.Column(db.DateTime)
    collected_at = db.Column(db.DateTime)
    keys_received = db.Column(db.Boolean, default=True, nullable=False)
    fuel_level = db.Column(db.String(10))
    valuables = db.Column(db.Text)
    odometer_in = db.Column(db.Integer)

    customer = db.relationship("Customer", back_populates="jobs")
    vehicle = db.relationship("Vehicle", back_populates="jobs")
    technician = db.relationship("User", foreign_keys=[technician_id])
    estimator = db.relationship("User", foreign_keys=[estimator_id])

    events = db.relationship(
        "JobStageEvent", back_populates="job", cascade="all, delete-orphan",
        order_by="JobStageEvent.id.desc()",
    )
    photos = db.relationship("JobPhoto", back_populates="job", cascade="all, delete-orphan")
    estimates = db.relationship(
        "Estimate", back_populates="job", cascade="all, delete-orphan",
        order_by="Estimate.id.desc()",
    )
    claims = db.relationship("Claim", back_populates="job", cascade="all, delete-orphan")
    job_parts = db.relationship("JobPart", back_populates="job", cascade="all, delete-orphan")
    invoices = db.relationship("Invoice", back_populates="job", cascade="all, delete-orphan")
    qc_results = db.relationship("QcResult", back_populates="job", cascade="all, delete-orphan")

    # ── derived helpers ─────────────────────────────────────────────────
    @property
    def stage_label(self) -> str:
        return STAGE_LABELS.get(self.stage, self.stage)

    @property
    def progress(self) -> int:
        return STAGE_PROGRESS.get(self.stage, 0)

    @property
    def is_open(self) -> bool:
        return self.stage != "COLLECTED"

    @property
    def days_in_shop(self) -> int:
        end = self.collected_at or _now()
        return max(0, (end - (self.checked_in_at or end)).days)

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.promised_date
            and self.is_open
            and self.promised_date < date.today()
        )

    @property
    def latest_estimate(self):
        return self.estimates[0] if self.estimates else None

    @property
    def active_claim(self):
        return self.claims[0] if self.claims else None

    @property
    def outstanding_invoice(self):
        for inv in self.invoices:
            if inv.status not in {"PAID", "CANCELLED"}:
                return inv
        return None

    def stage_history(self) -> list[dict]:
        return [e.to_dict() for e in sorted(self.events, key=lambda e: e.id)]

    def qc_rows(self) -> list["QcResult"]:
        """Always read the checklist from the database.

        The relationship collection can be cached as empty right after the rows
        are created, which would silently break the QC release gate.
        """
        return QcResult.query.filter_by(job_id=self.id).order_by(QcResult.id).all()

    def qc_score(self) -> dict:
        rows = self.qc_rows()
        passed = sum(1 for r in rows if r.passed)
        return {"total": len(rows), "passed": passed, "failed": len(rows) - passed}

    def to_dict(self, brief: bool = False) -> dict:
        data = {
            "id": self.id,
            "job_no": self.job_no,
            "customer_id": self.customer_id,
            "vehicle_id": self.vehicle_id,
            "customer_name": self.customer.name if self.customer else None,
            "customer_phone": self.customer.phone if self.customer else None,
            "reg_no": self.vehicle.reg_no if self.vehicle else None,
            "vehicle_title": self.vehicle.title if self.vehicle else None,
            "service": self.service,
            "stage": self.stage,
            "stage_label": self.stage_label,
            "progress": self.progress,
            "priority": self.priority,
            "is_insurance": self.is_insurance,
            "bay": self.bay,
            "technician": self.technician.full_name if self.technician else None,
            "technician_id": self.technician_id,
            "estimator": self.estimator.full_name if self.estimator else None,
            "promised_date": self.promised_date.isoformat() if self.promised_date else None,
            "checked_in_at": self.checked_in_at.isoformat() if self.checked_in_at else None,
            "days_in_shop": self.days_in_shop,
            "is_overdue": self.is_overdue,
            "is_open": self.is_open,
            "description": self.description,
        }
        if brief:
            return data
        est = self.latest_estimate
        claim = self.active_claim
        inv = self.outstanding_invoice
        data.update({
            "damage_summary": self.damage_summary,
            "keys_received": self.keys_received,
            "fuel_level": self.fuel_level,
            "valuables": self.valuables,
            "odometer_in": self.odometer_in,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
            "vehicle": self.vehicle.to_dict() if self.vehicle else None,
            "customer": self.customer.to_dict() if self.customer else None,
            "estimate": est.to_dict() if est else None,
            "claim": claim.to_dict() if claim else None,
            "invoice": inv.to_dict() if inv else None,
            "parts": [p.to_dict() for p in self.job_parts],
            "photos": [p.to_dict() for p in self.photos],
            "stage_history": self.stage_history(),
            "qc": self.qc_score(),
            "qc_results": [r.to_dict() for r in self.qc_rows()],
        })
        return data

    def __repr__(self) -> str:  # pragma: no cover
        return f"<JobCard {self.job_no} {self.stage}>"


class JobStageEvent(db.Model):
    __tablename__ = "job_stage_events"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), nullable=False, index=True)
    stage = db.Column(db.String(30), nullable=False)
    note = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=_now, nullable=False)

    job = db.relationship("JobCard", back_populates="events")
    user = db.relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage, self.stage),
            "note": self.note,
            "user": self.user.full_name if self.user else "System",
            "at": self.created_at.isoformat(),
        }


class JobPhoto(db.Model):
    __tablename__ = "job_photos"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(400), nullable=False)
    kind = db.Column(db.String(30), default="DAMAGE")  # DAMAGE | PROGRESS | FINAL
    caption = db.Column(db.String(255))
    source = db.Column(db.String(30), default="web")  # web | whatsapp
    created_at = db.Column(db.DateTime, default=_now, nullable=False)

    job = db.relationship("JobCard", back_populates="photos")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "kind": self.kind,
            "caption": self.caption,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Estimating
# ─────────────────────────────────────────────────────────────────────────────
class Estimate(TimestampMixin, db.Model):
    __tablename__ = "estimates"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), nullable=False, index=True)
    reference = db.Column(db.String(30), unique=True, default=lambda: gen_ref("TC-EST"))
    public_token = db.Column(
        db.String(40), unique=True, index=True,
        default=lambda: secrets.token_urlsafe(18),
    )
    version = db.Column(db.Integer, default=1, nullable=False)
    status = db.Column(db.String(20), default="DRAFT", nullable=False)  # DRAFT|SENT|APPROVED|DECLINED
    currency = db.Column(db.String(8), default="USD")
    is_insurance = db.Column(db.Boolean, default=False, nullable=False)
    valid_days = db.Column(db.Integer, default=14, nullable=False)

    labour_total = db.Column(db.Numeric(12, 2), default=0)
    materials_total = db.Column(db.Numeric(12, 2), default=0)
    parts_total = db.Column(db.Numeric(12, 2), default=0)
    subtotal = db.Column(db.Numeric(12, 2), default=0)
    vat = db.Column(db.Numeric(12, 2), default=0)
    total = db.Column(db.Numeric(12, 2), default=0)
    excess = db.Column(db.Numeric(12, 2), default=0)

    notes = db.Column(db.Text)
    approved_by = db.Column(db.String(120))
    approved_at = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)

    job = db.relationship("JobCard", back_populates="estimates")
    items = db.relationship(
        "EstimateItem", back_populates="estimate", cascade="all, delete-orphan",
        order_by="EstimateItem.sort_order",
    )

    @property
    def customer_payable(self) -> Decimal:
        """What the customer owes out of pocket."""
        if self.is_insurance:
            return _money(self.excess)
        return _money(self.total)

    @property
    def expires_on(self) -> date:
        return (self.created_at or _now()).date() + timedelta(days=self.valid_days or 14)

    @property
    def is_expired(self) -> bool:
        return date.today() > self.expires_on and self.status != "APPROVED"

    def recalculate(self, vat_rate: Decimal) -> None:
        self.labour_total = _money(sum((i.line_total for i in self.items if i.kind == "LABOUR"), Decimal("0")))
        self.parts_total = _money(sum((i.line_total for i in self.items if i.kind == "PART"), Decimal("0")))
        self.materials_total = _money(
            sum((i.line_total for i in self.items if i.kind in {"MATERIAL", "CONSUMABLE"}), Decimal("0"))
        )
        self.subtotal = _money(self.labour_total + self.materials_total + self.parts_total)
        self.vat = _money(self.subtotal * (Decimal("1") if self.is_insurance else vat_rate))
        self.total = _money(self.subtotal + self.vat)

    def to_dict(self, deep: bool = True) -> dict:
        data = {
            "id": self.id,
            "job_id": self.job_id,
            "reference": self.reference,
            "version": self.version,
            "status": self.status,
            "currency": self.currency,
            "is_insurance": self.is_insurance,
            "labour_total": float(_money(self.labour_total)),
            "materials_total": float(_money(self.materials_total)),
            "parts_total": float(_money(self.parts_total)),
            "subtotal": float(_money(self.subtotal)),
            "vat": float(_money(self.vat)),
            "total": float(_money(self.total)),
            "excess": float(_money(self.excess)),
            "customer_payable": float(self.customer_payable),
            "notes": self.notes,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "created_at": self.created_at.isoformat(),
            "public_token": self.public_token,
            "valid_days": self.valid_days,
            "expires_on": self.expires_on.isoformat(),
            "is_expired": self.is_expired,
        }
        if deep:
            data["items"] = [i.to_dict() for i in self.items]
        return data


class EstimateItem(db.Model):
    __tablename__ = "estimate_items"

    id = db.Column(db.Integer, primary_key=True)
    estimate_id = db.Column(db.Integer, db.ForeignKey("estimates.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)  # LABOUR | PART | MATERIAL | CONSUMABLE
    panel = db.Column(db.String(80))                 # e.g. "Front Door" (labour rows)
    description = db.Column(db.String(255), nullable=False)
    operation = db.Column(db.String(20))             # PANEL | PAINT | METAL | DETAIL
    quantity = db.Column(db.Numeric(10, 2), default=1)
    unit = db.Column(db.String(20), default="ea")
    unit_price = db.Column(db.Numeric(12, 2), default=0)
    markup_pct = db.Column(db.Numeric(6, 4), default=0)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"))
    sort_order = db.Column(db.Integer, default=0)

    estimate = db.relationship("Estimate", back_populates="items")

    @property
    def line_total(self) -> Decimal:
        base = Decimal(str(self.quantity or 0)) * Decimal(str(self.unit_price or 0))
        return _money(base * (Decimal("1") + Decimal(str(self.markup_pct or 0))))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "panel": self.panel,
            "description": self.description,
            "operation": self.operation,
            "quantity": float(self.quantity or 0),
            "unit": self.unit,
            "unit_price": float(_money(self.unit_price)),
            "markup_pct": float(self.markup_pct or 0),
            "line_total": float(self.line_total),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Insurance claims
# ─────────────────────────────────────────────────────────────────────────────
class Claim(TimestampMixin, db.Model):
    __tablename__ = "claims"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), nullable=False, index=True)
    insurer_code = db.Column(db.String(20), nullable=False, index=True)
    policy_no = db.Column(db.String(60))
    claim_no = db.Column(db.String(60), index=True)
    assessor_name = db.Column(db.String(120))
    assessor_phone = db.Column(db.String(40))
    assessor_date = db.Column(db.Date)
    status = db.Column(db.String(30), default="DRAFT", nullable=False, index=True)
    claimed_amount = db.Column(db.Numeric(12, 2), default=0)
    approved_amount = db.Column(db.Numeric(12, 2), default=0)
    excess = db.Column(db.Numeric(12, 2), default=0)
    excess_paid = db.Column(db.Boolean, default=False, nullable=False)
    submitted_at = db.Column(db.DateTime)
    decision_at = db.Column(db.DateTime)
    repudiation_reason = db.Column(db.Text)
    notes = db.Column(db.Text)

    job = db.relationship("JobCard", back_populates="claims")

    @property
    def insurer_name(self) -> str:
        from .constants import INSURER_BY_CODE

        return INSURER_BY_CODE.get(self.insurer_code, self.insurer_code)

    @property
    def status_label(self) -> str:
        return CLAIM_STATUS_LABELS.get(self.status, self.status)

    @property
    def shortfall(self) -> Decimal:
        return _money(Decimal(str(self.claimed_amount or 0)) - Decimal(str(self.approved_amount or 0)))

    @property
    def aging_days(self) -> int:
        start = self.submitted_at or self.created_at
        end = self.decision_at or _now()
        return max(0, (end - start).days) if start else 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "insurer_code": self.insurer_code,
            "insurer_name": self.insurer_name,
            "policy_no": self.policy_no,
            "claim_no": self.claim_no,
            "assessor_name": self.assessor_name,
            "assessor_phone": self.assessor_phone,
            "assessor_date": self.assessor_date.isoformat() if self.assessor_date else None,
            "status": self.status,
            "status_label": self.status_label,
            "claimed_amount": float(_money(self.claimed_amount)),
            "approved_amount": float(_money(self.approved_amount)),
            "excess": float(_money(self.excess)),
            "excess_paid": self.excess_paid,
            "shortfall": float(self.shortfall),
            "aging_days": self.aging_days,
            "repudiation_reason": self.repudiation_reason,
            "notes": self.notes,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Parts & inventory
# ─────────────────────────────────────────────────────────────────────────────
class Part(TimestampMixin, db.Model):
    __tablename__ = "parts"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    category = db.Column(db.String(60), default="Body Panels")
    supplier = db.Column(db.String(120))
    unit = db.Column(db.String(20), default="ea")
    cost_price = db.Column(db.Numeric(12, 2), default=0)
    sell_price = db.Column(db.Numeric(12, 2), default=0)
    qty_on_hand = db.Column(db.Numeric(10, 2), default=0)
    reorder_level = db.Column(db.Numeric(10, 2), default=2)
    location = db.Column(db.String(60))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    job_parts = db.relationship("JobPart", back_populates="part")

    @property
    def stock_value(self) -> Decimal:
        return _money(Decimal(str(self.qty_on_hand or 0)) * Decimal(str(self.cost_price or 0)))

    @property
    def needs_reorder(self) -> bool:
        return Decimal(str(self.qty_on_hand or 0)) <= Decimal(str(self.reorder_level or 0))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sku": self.sku,
            "name": self.name,
            "category": self.category,
            "supplier": self.supplier,
            "unit": self.unit,
            "cost_price": float(_money(self.cost_price)),
            "sell_price": float(_money(self.sell_price)),
            "qty_on_hand": float(self.qty_on_hand or 0),
            "reorder_level": float(self.reorder_level or 0),
            "location": self.location,
            "stock_value": float(self.stock_value),
            "needs_reorder": self.needs_reorder,
            "is_active": self.is_active,
        }


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)
    delta = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.String(60))
    reference = db.Column(db.String(60))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=_now, nullable=False)

    part = db.relationship("Part")
    user = db.relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "part_id": self.part_id,
            "delta": float(self.delta or 0),
            "reason": self.reason,
            "reference": self.reference,
            "user": self.user.full_name if self.user else "System",
            "created_at": self.created_at.isoformat(),
        }


class JobPart(db.Model):
    __tablename__ = "job_parts"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), nullable=False, index=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), index=True)
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Numeric(10, 2), default=1)
    unit_price = db.Column(db.Numeric(12, 2), default=0)
    status = db.Column(db.String(20), default="REQUIRED", nullable=False)
    supplier = db.Column(db.String(120))
    eta = db.Column(db.Date)
    ordered_at = db.Column(db.DateTime)
    received_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)

    job = db.relationship("JobCard", back_populates="job_parts")
    part = db.relationship("Part", back_populates="job_parts")

    @property
    def line_total(self) -> Decimal:
        return _money(Decimal(str(self.quantity or 0)) * Decimal(str(self.unit_price or 0)))

    @property
    def is_blocking(self) -> bool:
        """A part that is holding up the repair."""
        return self.status in {"REQUIRED", "ORDERED", "IN_TRANSIT"}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "part_id": self.part_id,
            "sku": self.part.sku if self.part else None,
            "description": self.description,
            "quantity": float(self.quantity or 0),
            "unit_price": float(_money(self.unit_price)),
            "line_total": float(self.line_total),
            "status": self.status,
            "supplier": self.supplier or (self.part.supplier if self.part else None),
            "eta": self.eta.isoformat() if self.eta else None,
            "is_blocking": self.is_blocking,
            "notes": self.notes,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Quality control
# ─────────────────────────────────────────────────────────────────────────────
class QcResult(db.Model):
    __tablename__ = "qc_results"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), nullable=False, index=True)
    item = db.Column(db.String(200), nullable=False)
    passed = db.Column(db.Boolean, default=False, nullable=False)
    comment = db.Column(db.String(255))
    checked_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=_now, nullable=False)

    job = db.relationship("JobCard", back_populates="qc_results")
    user = db.relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "item": self.item,
            "passed": self.passed,
            "comment": self.comment,
            "checked_by": self.user.full_name if self.user else None,
            "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Money
# ─────────────────────────────────────────────────────────────────────────────
class Invoice(TimestampMixin, db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(30), unique=True, nullable=False, index=True)
    public_token = db.Column(
        db.String(40), unique=True, index=True,
        default=lambda: secrets.token_urlsafe(18),
    )
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    currency = db.Column(db.String(8), default="USD")
    subtotal = db.Column(db.Numeric(12, 2), default=0)
    vat = db.Column(db.Numeric(12, 2), default=0)
    total = db.Column(db.Numeric(12, 2), default=0)
    amount_paid = db.Column(db.Numeric(12, 2), default=0)
    status = db.Column(db.String(20), default="DRAFT", nullable=False, index=True)
    is_insurance = db.Column(db.Boolean, default=False, nullable=False)
    insurer_code = db.Column(db.String(20))
    due_date = db.Column(db.Date)
    issued_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)

    job = db.relationship("JobCard", back_populates="invoices")
    customer = db.relationship("Customer", back_populates="invoices")
    payments = db.relationship(
        "Payment", back_populates="invoice", cascade="all, delete-orphan",
        order_by="Payment.id.desc()",
    )

    @property
    def balance(self) -> Decimal:
        return _money(Decimal(str(self.total or 0)) - Decimal(str(self.amount_paid or 0)))

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.due_date
            and self.status not in {"PAID", "CANCELLED"}
            and self.due_date < date.today()
        )

    def to_dict(self, deep: bool = False) -> dict:
        data = {
            "id": self.id,
            "invoice_no": self.invoice_no,
            "job_id": self.job_id,
            "job_no": self.job.job_no if self.job else None,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "currency": self.currency,
            "subtotal": float(_money(self.subtotal)),
            "vat": float(_money(self.vat)),
            "total": float(_money(self.total)),
            "amount_paid": float(_money(self.amount_paid)),
            "balance": float(self.balance),
            "status": self.status,
            "is_insurance": self.is_insurance,
            "insurer_code": self.insurer_code,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "is_overdue": self.is_overdue,
            "issued_at": self.issued_at.isoformat() if self.issued_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "public_token": self.public_token,
        }
        if deep:
            data["payments"] = [p.to_dict() for p in self.payments]
        return data


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False, index=True)
    receipt_no = db.Column(db.String(30), unique=True, index=True)
    public_token = db.Column(
        db.String(40), unique=True, index=True,
        default=lambda: secrets.token_urlsafe(18),
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(30), default="CASH")
    reference = db.Column(db.String(80))
    received_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=_now, nullable=False)

    invoice = db.relationship("Invoice", back_populates="payments")
    user = db.relationship("User")

    @property
    def payer_name(self) -> str:
        return self.invoice.customer.name if self.invoice and self.invoice.customer else "—"

    @property
    def balance_after(self) -> Decimal:
        """Balance left on the invoice as at this payment."""
        if not self.invoice:
            return Decimal("0.00")
        later = [
            p for p in self.invoice.payments
            if p.id != self.id and p.id > self.id
        ]
        paid_later = sum((Decimal(str(p.amount or 0)) for p in later), Decimal("0"))
        return _money(
            Decimal(str(self.invoice.total or 0))
            - Decimal(str(self.invoice.amount_paid or 0))
            + paid_later
        )

    @property
    def is_fully_settled(self) -> bool:
        return self.balance_after <= 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "receipt_no": self.receipt_no,
            "amount": float(_money(self.amount)),
            "method": self.method,
            "reference": self.reference,
            "received_by": self.user.full_name if self.user else None,
            "created_at": self.created_at.isoformat(),
            "public_token": self.public_token,
            "invoice_no": self.invoice.invoice_no if self.invoice else None,
            "payer_name": self.payer_name,
            "balance_after": float(self.balance_after),
            "is_fully_settled": self.is_fully_settled,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Bookings (detailing / coating / PPF appointments)
# ─────────────────────────────────────────────────────────────────────────────
class Booking(TimestampMixin, db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    # Issued the moment the enquiry lands. A booking reference is issued
    # separately, and only once somebody confirms it.
    reference = db.Column(db.String(30), unique=True,
                          default=lambda: gen_ref(ENQUIRY_REF_PREFIX))
    booking_reference = db.Column(db.String(30), unique=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"))
    service = db.Column(db.String(80), nullable=False)
    slot_date = db.Column(db.Date, nullable=False)
    slot_time = db.Column(db.String(10))
    status = db.Column(db.String(20), default="REQUESTED", nullable=False, index=True)
    source = db.Column(db.String(20), default="web")  # web | whatsapp | phone | walkin
    notes = db.Column(db.Text)
    quoted_from = db.Column(db.Numeric(12, 2), default=0)

    # Who in the workshop handled this enquiry. Recorded when it is confirmed
    # and again when the customer actually turns up, so the end-of-day report
    # can put a name against every status change instead of leaving it
    # anonymous.
    confirmed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    confirmed_at = db.Column(db.DateTime)
    attended_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    attended_at = db.Column(db.DateTime)

    # How the visit ended, once the customer actually turned up: the job was
    # secured, or they walked out without committing to anything.
    outcome = db.Column(db.String(20))
    rescheduled_count = db.Column(db.Integer, default=0, nullable=False)

    customer = db.relationship("Customer", back_populates="bookings")
    vehicle = db.relationship("Vehicle")
    # Two foreign keys onto the same table, so the joins have to be explicit.
    confirmed_by = db.relationship("User", foreign_keys=[confirmed_by_id])
    attended_by = db.relationship("User", foreign_keys=[attended_by_id])

    @property
    def display_reference(self) -> str:
        """The booking reference once it has one, otherwise the enquiry's."""
        return self.booking_reference or self.reference

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "reference": self.reference,
            "booking_reference": self.booking_reference,
            # What to show on screen: the booking reference once it has one,
            # otherwise the enquiry it still is.
            "display_reference": self.display_reference,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "customer_phone": self.customer.phone if self.customer else None,
            "vehicle_id": self.vehicle_id,
            "reg_no": self.vehicle.reg_no if self.vehicle else None,
            "service": self.service,
            "slot_date": self.slot_date.isoformat() if self.slot_date else None,
            "slot_time": self.slot_time,
            "status": self.status,
            "source": self.source,
            "notes": self.notes,
            "quoted_from": float(_money(self.quoted_from)),
            "confirmed_by_id": self.confirmed_by_id,
            "confirmed_by": self.confirmed_by.full_name if self.confirmed_by else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "attended_by_id": self.attended_by_id,
            "attended_by": self.attended_by.full_name if self.attended_by else None,
            "attended_at": self.attended_at.isoformat() if self.attended_at else None,
            "outcome": self.outcome,
            "outcome_label": BOOKING_OUTCOMES.get(self.outcome or ""),
            "rescheduled_count": self.rescheduled_count or 0,
            "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp
# ─────────────────────────────────────────────────────────────────────────────
class WaConversation(TimestampMixin, db.Model):
    __tablename__ = "wa_conversations"

    id = db.Column(db.Integer, primary_key=True)
    wa_id = db.Column(db.String(40), unique=True, nullable=False, index=True)  # phone digits
    profile_name = db.Column(db.String(120))
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    state = db.Column(db.String(40), default="MAIN_MENU", nullable=False)
    context_json = db.Column(db.Text, default="{}")
    human_takeover = db.Column(db.Boolean, default=False, nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"))
    unread = db.Column(db.Integer, default=0, nullable=False)
    last_inbound_at = db.Column(db.DateTime)
    last_outbound_at = db.Column(db.DateTime)
    last_message_at = db.Column(db.DateTime, default=_now, index=True)

    customer = db.relationship("Customer")
    assignee = db.relationship("User")
    messages = db.relationship(
        "WaMessage", back_populates="conversation", cascade="all, delete-orphan",
        order_by="WaMessage.id.desc()", lazy="select",
    )

    # ── context helpers (JSON blob holding the bot's working memory) ─────
    @property
    def context(self) -> dict:
        try:
            return json.loads(self.context_json or "{}")
        except (ValueError, TypeError):
            return {}

    @context.setter
    def context(self, value: dict) -> None:
        self.context_json = json.dumps(value or {})

    def ctx_get(self, key: str, default=None):
        return self.context.get(key, default)

    def ctx_set(self, **kwargs) -> None:
        data = self.context
        data.update(kwargs)
        self.context = data

    def ctx_clear(self, *keys: str) -> None:
        data = self.context
        for key in keys:
            data.pop(key, None)
        self.context = data

    @property
    def display_name(self) -> str:
        if self.customer:
            return self.customer.name
        return self.profile_name or f"+{self.wa_id}"

    @property
    def is_session_open(self) -> bool:
        """Meta allows free-form replies for 24h after the last inbound message."""
        if not self.last_inbound_at:
            return False
        return _now() - self.last_inbound_at < timedelta(hours=24)

    def to_dict(self, deep: bool = False) -> dict:
        last = self.messages[0] if self.messages else None
        data = {
            "id": self.id,
            "wa_id": self.wa_id,
            "profile_name": self.profile_name,
            "display_name": self.display_name,
            "customer_id": self.customer_id,
            "state": self.state,
            "human_takeover": self.human_takeover,
            "assigned_to": self.assignee.full_name if self.assignee else None,
            "unread": self.unread,
            "is_session_open": self.is_session_open,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "last_message": last.body if last else None,
            "context": self.context,
        }
        if deep:
            data["messages"] = [m.to_dict() for m in sorted(self.messages, key=lambda m: m.id)]
        return data


class WaMessage(db.Model):
    __tablename__ = "wa_messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("wa_conversations.id"), nullable=False, index=True
    )
    direction = db.Column(db.String(10), nullable=False)  # inbound | outbound
    msg_type = db.Column(db.String(20), default="text")   # text | interactive | image | template
    body = db.Column(db.Text)
    payload_json = db.Column(db.Text)
    media_url = db.Column(db.String(400))
    wa_message_id = db.Column(db.String(120), index=True)
    status = db.Column(db.String(20), default="delivered")
    is_bot = db.Column(db.Boolean, default=False, nullable=False)
    intent = db.Column(db.String(40))
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"))
    created_at = db.Column(db.DateTime, default=_now, nullable=False, index=True)

    conversation = db.relationship("WaConversation", back_populates="messages")
    job = db.relationship("JobCard")

    @property
    def payload(self) -> dict:
        try:
            return json.loads(self.payload_json or "{}")
        except (ValueError, TypeError):
            return {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "direction": self.direction,
            "msg_type": self.msg_type,
            "body": self.body,
            "payload": self.payload,
            "media_url": self.media_url,
            "is_bot": self.is_bot,
            "intent": self.intent,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class NotificationLog(db.Model):
    __tablename__ = "notification_log"

    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(20), default="whatsapp")
    recipient = db.Column(db.String(40))
    template = db.Column(db.String(60))
    body = db.Column(db.Text)
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"))
    status = db.Column(db.String(20), default="queued")
    error = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=_now, nullable=False, index=True)

    job = db.relationship("JobCard")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel,
            "recipient": self.recipient,
            "template": self.template,
            "body": self.body,
            "job_id": self.job_id,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Audit trail
# ─────────────────────────────────────────────────────────────────────────────
class ActivityLog(db.Model):
    """Who did what, when — the shop's memory.

    Written on every meaningful mutation so a manager can answer "who moved this
    job to QC?" or "who discounted that quote?" months later.
    """

    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    actor_name = db.Column(db.String(120), default="System")
    action = db.Column(db.String(60), nullable=False, index=True)
    entity_type = db.Column(db.String(30))          # job | customer | vehicle | invoice …
    entity_id = db.Column(db.Integer, index=True)
    entity_ref = db.Column(db.String(60))           # human reference, e.g. TC-2026-0007
    summary = db.Column(db.String(400), nullable=False)
    meta_json = db.Column(db.Text, default="{}")
    job_id = db.Column(db.Integer, db.ForeignKey("job_cards.id"), index=True)
    ip = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=_now, nullable=False, index=True)

    actor = db.relationship("User")
    job = db.relationship("JobCard")

    @property
    def meta(self) -> dict:
        try:
            return json.loads(self.meta_json or "{}")
        except (ValueError, TypeError):
            return {}

    @property
    def icon(self) -> str:
        return {
            "job.": "clipboard-check",
            "claim.": "shield-check",
            "estimate.": "calculator",
            "invoice.": "receipt",
            "payment.": "cash-coin",
            "part.": "box-seam",
            "customer.": "person",
            "vehicle.": "car-front",
            "booking.": "calendar-check",
            "auth.": "box-arrow-in-right",
            "whatsapp.": "whatsapp",
        }.get(self.action.split(".")[0] + ".", "dot")

    @property
    def tone(self) -> str:
        if self.action.endswith((".created", ".approved", ".paid", ".received", ".confirmed")):
            return "is-success"
        if self.action.endswith((".declined", ".repudiated", ".cancelled", ".failed", ".deleted")):
            return "is-alert"
        return ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_ref": self.entity_ref,
            "summary": self.summary,
            "meta": self.meta,
            "job_id": self.job_id,
            "icon": self.icon,
            "tone": self.tone,
            "created_at": self.created_at.isoformat(),
        }


__all__ = [
    "User", "Customer", "Vehicle", "JobCard", "JobStageEvent", "JobPhoto",
    "Estimate", "EstimateItem", "Claim", "Part", "StockMovement", "JobPart",
    "QcResult", "Invoice", "Payment", "Booking", "WaConversation", "WaMessage",
    "NotificationLog", "ActivityLog", "gen_ref", "utcnow",
]
