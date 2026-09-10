"""Seed the database with staff accounts, reference stock and demo work."""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from .constants import SUPPLIERS
from .extensions import db
from .models import (
    Booking,
    Claim,
    Customer,
    Invoice,
    JobCard,
    JobPart,
    Part,
    User,
    Vehicle,
    WaConversation,
    WaMessage,
    utcnow,
)
from .services import job_flow, pricing

DEFAULT_PASSWORD = "topclass123"

STAFF = [
    ("Tendai Moyo", "owner@topclass.co.zw", "owner", "+263 77 555 0555"),
    ("Rutendo Chikafu", "manager@topclass.co.zw", "manager", "+263 77 555 0556"),
    ("Blessing Ncube", "estimator@topclass.co.zw", "estimator", "+263 77 555 0557"),
    ("Farai Gumbo", "store@topclass.co.zw", "storeman", "+263 77 555 0558"),
    ("Tapiwa Sibanda", "tech1@topclass.co.zw", "technician", "+263 77 555 0559"),
    ("Nyasha Mhaka", "tech2@topclass.co.zw", "technician", "+263 77 555 0560"),
    ("Chiedza Dube", "front@topclass.co.zw", "frontdesk", "+263 77 555 0561"),
]

PARTS = [
    ("BP-1001", "Front Bumper — Toyota Hilux D4D", "Body Panels", 145.00, 195.00, 4),
    ("BP-1002", "Bonnet — Nissan NP300", "Body Panels", 180.00, 240.00, 2),
    ("BP-1003", "Front Door RH — Honda Fit", "Body Panels", 110.00, 155.00, 3),
    ("BP-1004", "Tailgate — Mazda BT-50", "Body Panels", 210.00, 285.00, 1),
    ("LT-2001", "Headlamp Assembly LH — Toyota Aqua", "Lights & Lamps", 95.00, 135.00, 6),
    ("LT-2002", "Tail Lamp RH — Mercedes C200", "Lights & Lamps", 120.00, 170.00, 2),
    ("PN-3001", "Spies Hecker 2K Clear Coat (5L)", "Paint & Consumables", 68.00, 92.00, 8),
    ("PN-3002", "Base Coat — Pearl White (1L)", "Paint & Consumables", 42.00, 58.00, 10),
    ("PN-3003", "Bulldog Abrasive Discs P80 (pack)", "Paint & Consumables", 18.00, 26.00, 14),
    ("PN-3004", "Masking Tape 50mm (roll)", "Paint & Consumables", 3.50, 6.00, 2),
    ("TR-4001", "Windscreen — Toyota Fortuner", "Glass", 240.00, 320.00, 2),
    ("MC-5001", "Radiator — Isuzu KB250", "Mechanical", 165.00, 225.00, 3),
    ("TR-6001", "Wheel Arch Trim LH — Ford Ranger", "Trim & Interior", 55.00, 80.00, 5),
    ("HW-7001", "Panel Clip Assortment (box)", "Hardware", 12.00, 20.00, 20),
    ("TY-8001", "265/65R17 All-Terrain Tyre", "Tyres & Wheels", 130.00, 175.00, 8),
]

DEMO_CUSTOMERS = [
    ("Takudzwa Marufu", "+263 77 111 2233", "taku@example.co.zw", False, None),
    ("Rudo Nyathi", "+263 78 222 3344", "rudo@example.co.zw", False, None),
    ("Simba Logistics (Pvt) Ltd", "+263 242 778899", "fleet@simbalogistics.co.zw", True,
     "Simba Logistics"),
    ("Chipo Zvenyika", "+263 71 333 4455", None, False, None),
    ("Msasa Executive Fleet", "+263 242 445566", "fleet@msasaexec.co.zw", True,
     "Msasa Executive"),
]

DEMO_VEHICLES = [
    ("ABC 1234", "Toyota", "Hilux D4D", 2016, "White", 4),
    ("ADF 8842", "Nissan", "NP300", 2018, "Silver", 2),
    ("AEB 5521", "Honda", "Fit", 2013, "Blue", 3),
    ("AEE 7788", "Mazda", "BT-50", 2015, "Grey", 1),
    ("AFG 9021", "Mercedes-Benz", "C200", 2014, "Black", 1),
    ("ACG 3311", "Isuzu", "KB250", 2012, "White", 1),
    ("ADH 6677", "Toyota", "Fortuner", 2019, "Pearl White", 5),
    ("AEF 1122", "Ford", "Ranger", 2017, "Red", 5),
    ("AEI 4455", "Toyota", "Aqua", 2015, "Silver", 1),
    ("AEJ 8899", "Isuzu", "FRR Truck", 2014, "White", 3),
]

DEMO_DAMAGE = [
    ("Front-end collision", "Front Bumper, Bonnet, Headlamp Surround", True),
    ("Rear-end shunt", "Rear Bumper, Boot Lid / Tailgate", True),
    ("Side swipe", "Front Door, Rear Door, Rear Quarter Panel", False),
    ("Hail damage", "Bonnet, Roof, Boot Lid / Tailgate", True),
    ("Wheel arch scrape", "Front Fender / Wing, Sill / Rocker", False),
    ("Chassis pull after accident", "Chassis / Jig Alignment, Front Bumper", True),
]

SERVICES_ROTATION = [
    "Panel Beating & Spray Painting",
    "Panel Beating & Spray Painting",
    "Panel Beating & Spray Painting",
    "Car Detailing",
    "Ceramic Coating",
    "Paint Protection Film",
    "Car Vinyl Wrapping",
    "Rebuilds & Performance Upgrades",
]

BAYS = ["Bay 1", "Bay 2", "Bay 3", "Bay 4", "Spray Booth", "Jig"]
TECH_EMAILS = ["tech1@topclass.co.zw", "tech2@topclass.co.zw"]


def run_seed(with_demo: bool = True) -> None:
    """Idempotent seed — safe to run repeatedly."""
    _seed_staff()
    _seed_parts()
    if with_demo:
        _seed_demo_work()
    _seed_whatsapp_demo()
    db.session.commit()


def _seed_staff() -> None:
    for full_name, email, role, phone in STAFF:
        existing = User.query.filter(db.func.lower(User.email) == email).first()
        if existing:
            continue
        user = User(full_name=full_name, email=email, role=role, phone=phone)
        user.set_password(DEFAULT_PASSWORD)
        db.session.add(user)
    db.session.commit()


def _seed_parts() -> None:
    for idx, (sku, name, category, cost, sell, qty) in enumerate(PARTS):
        if Part.query.filter_by(sku=sku).first():
            continue
        db.session.add(Part(
            sku=sku, name=name, category=category,
            supplier=SUPPLIERS[idx % len(SUPPLIERS)],
            cost_price=Decimal(str(cost)), sell_price=Decimal(str(sell)),
            qty_on_hand=Decimal(str(qty)),
            reorder_level=Decimal("3"),
            location=f"Shelf {chr(65 + (idx % 6))}{(idx % 9) + 1}",
        ))
    db.session.commit()


def _seed_demo_work() -> None:
    if JobCard.query.count() > 0:
        return

    rng = random.Random(2026)
    technicians = User.query.filter(User.role == "technician").all()
    estimator = User.query.filter_by(role="estimator").first()

    customers = []
    for name, phone, email, is_fleet, company in DEMO_CUSTOMERS:
        customer = Customer.query.filter_by(name=name).first()
        if not customer:
            customer = Customer(
                name=name, phone=phone, whatsapp=phone, email=email,
                is_fleet=is_fleet, company=company,
                address="Harare" if not is_fleet else "Msasa, Harare",
            )
            db.session.add(customer)
            db.session.flush()
        customers.append(customer)

    vehicles = []
    for reg, make, model, year, colour, owner_idx in DEMO_VEHICLES:
        # Store registrations the same way the API does, so filters and lookups
        # behave identically for seeded and real records.
        reg = reg.strip().upper().replace(" ", "")
        vehicle = Vehicle.query.filter_by(reg_no=reg).first()
        if not vehicle:
            vehicle = Vehicle(
                customer_id=customers[owner_idx - 1].id, reg_no=reg, make=make, model=model,
                year=year, colour=colour, mileage=rng.randint(60_000, 260_000),
            )
            db.session.add(vehicle)
            db.session.flush()
        vehicles.append(vehicle)
    db.session.commit()

    stages_for_demo = [
        "INTAKE", "ASSESSMENT", "AWAITING_APPROVAL", "PARTS_ORDER", "STRIP",
        "PANEL", "PREP", "PAINT", "REASSEMBLY", "DETAILING", "QC", "READY", "COLLECTED",
    ]

    for idx, vehicle in enumerate(vehicles):
        summary, panels_text, is_insurance = DEMO_DAMAGE[idx % len(DEMO_DAMAGE)]
        service = SERVICES_ROTATION[idx % len(SERVICES_ROTATION)]
        stage = stages_for_demo[idx % len(stages_for_demo)]
        panels = [p.strip() for p in panels_text.split(",")]
        panels = [p for p in panels if p in pricing.available_panels()] or ["Front Bumper"]

        checked_in = utcnow() - timedelta(days=rng.randint(2, 28))
        job = JobCard(
            job_no=job_flow.next_job_no(),
            customer_id=vehicle.customer_id,
            vehicle_id=vehicle.id,
            service=service,
            stage=stage,
            priority=rng.choice(["LOW", "NORMAL", "NORMAL", "HIGH", "URGENT"]),
            is_insurance=is_insurance,
            description=f"{summary}. Customer reported {panels_text.lower()} damage.",
            damage_summary=panels_text,
            bay=BAYS[idx % len(BAYS)],
            technician_id=technicians[idx % len(technicians)].id if technicians else None,
            estimator_id=estimator.id if estimator else None,
            checked_in_at=checked_in,
            promised_date=(checked_in + timedelta(days=rng.randint(5, 14))).date(),
            fuel_level=rng.choice(["1/4", "1/2", "3/4", "Full"]),
            valuables=rng.choice(["None", "Spare wheel, jack", "Baby seat", "Dash cam"]),
            odometer_in=vehicle.mileage,
        )
        db.session.add(job)
        db.session.flush()

        from .models import ActivityLog, JobStageEvent, JobPhoto

        db.session.add(JobStageEvent(
            job_id=job.id, stage="INTAKE", note="Vehicle booked in", user_id=None,
        ))
        db.session.add(ActivityLog(
            actor_id=estimator.id if estimator else None,
            actor_name=estimator.full_name if estimator else "Front desk",
            action="job.created",
            entity_type="job", entity_id=job.id, entity_ref=job.job_no, job_id=job.id,
            summary=f"Opened job card {job.job_no} for {vehicle.reg_no} ({service})",
            meta_json=json.dumps({"is_insurance": is_insurance, "priority": job.priority}),
            created_at=checked_in,
        ))

        stage_index = stages_for_demo.index(stage)
        for offset, step in enumerate(stages_for_demo[1:stage_index + 1], start=1):
            at = checked_in + timedelta(days=offset)
            db.session.add(JobStageEvent(
                job_id=job.id, stage=step, note=f"Moved to {step.title()}",
                user_id=technicians[0].id if technicians else None,
            ))
            db.session.add(ActivityLog(
                actor_id=technicians[0].id if technicians else None,
                actor_name=technicians[0].full_name if technicians else "Workshop",
                action="job.stage_changed",
                entity_type="job", entity_id=job.id, entity_ref=job.job_no, job_id=job.id,
                summary=f"{job.job_no} moved to {step.replace('_', ' ').title()}",
                meta_json=json.dumps({"to": step}),
                created_at=at,
            ))

        for kind, caption in (("DAMAGE", "Before — damage"),
                              ("PROGRESS", "During repair")):
            db.session.add(JobPhoto(
                job_id=job.id, filename=f"{job.job_no}-{kind.lower()}.jpg",
                url=f"https://placehold.co/800x600/1f2a37/ffffff?text={job.job_no}+{kind}",
                kind=kind, caption=caption, source="web",
            ))

        lines = pricing.build_lines(panels, is_insurance=is_insurance,
                                    parts=[{"description": p, "quantity": 1, "unit_price": rng.randint(80, 320)}
                                           for p in (["Front Bumper"] if is_insurance else [])])
        excess = Decimal(str(rng.choice([100, 150, 200, 250, 350]))) if is_insurance else Decimal("0")
        estimate = job_flow.save_estimate(
            job, lines, is_insurance=is_insurance, excess=excess,
            notes=f"{summary} — assessed by Blessing Ncube",
            mark_sent=True,
        )

        db.session.add(ActivityLog(
            actor_id=estimator.id if estimator else None,
            actor_name=estimator.full_name if estimator else "Estimator",
            action="estimate.created",
            entity_type="estimate", entity_id=estimate.id, entity_ref=estimate.reference,
            job_id=job.id,
            summary=f"Estimate {estimate.reference} sent for {job.job_no} "
                    f"totalling {estimate.currency} {estimate.total:,.2f}",
            meta_json=json.dumps({"total": float(estimate.total), "insurance": is_insurance}),
            created_at=checked_in + timedelta(days=1, hours=3),
        ))

        if is_insurance:
            claim = Claim(
                job_id=job.id,
                insurer_code=rng.choice(["OLD", "AIC", "NDI", "FBC", "ZIMNAT", "CBZ", "FIRST"]),
                policy_no=f"POL-{rng.randint(100000, 999999)}",
                claim_no=f"CLM{rng.randint(10000, 99999)}",
                assessor_name=rng.choice(["Mr. B. Chiweshe", "Ms. T. Rusike", "Mr. K. Nyoni"]),
                assessor_phone="+263 77 900 1234",
                assessor_date=(checked_in + timedelta(days=2)).date(),
                status=("SUBMITTED" if stage_index < 3 else
                        "APPROVED" if stage_index < 10 else "SETTLED"),
                claimed_amount=Decimal(str(estimate.total)),
                approved_amount=Decimal(str(estimate.total)) if stage_index >= 3 else Decimal("0"),
                excess=excess,
                excess_paid=stage_index >= 4,
                submitted_at=checked_in + timedelta(days=1),
                decision_at=(checked_in + timedelta(days=4)) if stage_index >= 3 else None,
            )
            db.session.add(claim)

        if job.is_insurance:
            estimate.status = "APPROVED" if stage_index >= 3 else "SENT"
            estimate.approved_by = "Assessor" if stage_index >= 3 else None

        if stage_index >= 2:
            db.session.add(JobPart(
                job_id=job.id,
                part_id=Part.query.offset(idx % 10).first().id,
                description=panels[0] + " — replacement",
                quantity=Decimal("1"),
                unit_price=Decimal(str(rng.randint(90, 300))),
                status=("RECEIVED" if stage_index >= 5 else
                        "ORDERED" if stage_index >= 3 else "REQUIRED"),
                supplier=SUPPLIERS[idx % len(SUPPLIERS)],
                eta=(checked_in + timedelta(days=5)).date(),
                ordered_at=checked_in + timedelta(days=3) if stage_index >= 3 else None,
                received_at=checked_in + timedelta(days=7) if stage_index >= 5 else None,
            ))

        if stage_index >= 10:
            from .models import QcResult

            for item, passed in (
                ("Paint match verified in daylight", True),
                ("Panel gaps and alignment within tolerance", True),
                ("Final photos taken for job card", True),
                ("No runs, sags, orange peel or dry spray", stage_index >= 11),
            ):
                db.session.add(QcResult(job_id=job.id, item=item, passed=passed,
                                        checked_by=technicians[0].id if technicians else None))

        if stage in {"READY", "COLLECTED"}:
            invoice = job_flow.ensure_invoice(job)
            if invoice:
                invoice.status = "ISSUED"
                invoice.issued_at = utcnow() - timedelta(days=1)
                if stage == "COLLECTED" and not is_insurance:
                    from .services import job_flow as jf

                    jf.record_payment(invoice, invoice.total, method="ECOCASH",
                                      reference=f"ECO{rng.randint(100000, 999999)}")
                    db.session.add(ActivityLog(
                        actor_name="Chiedza Dube", action="payment.recorded",
                        entity_type="invoice", entity_id=invoice.id,
                        entity_ref=invoice.invoice_no, job_id=job.id,
                        summary=f"{invoice.total:,.2f} received for {invoice.invoice_no} (PAID)",
                        meta_json=json.dumps({"method": "ECOCASH"}),
                        created_at=utcnow() - timedelta(days=rng.randint(0, 2)),
                    ))
                if stage == "COLLECTED":
                    job.collected_at = utcnow() - timedelta(days=rng.randint(0, 3))

        if not is_insurance and idx % 4 == 0:
            db.session.add(Booking(
                customer_id=vehicle.customer_id,
                vehicle_id=vehicle.id,
                service=service,
                slot_date=date.today() + timedelta(days=rng.randint(1, 10)),
                slot_time=rng.choice(["08:00", "09:00", "10:00", "11:00", "14:00"]),
                status=rng.choice(["REQUESTED", "CONFIRMED"]),
                source=rng.choice(["web", "whatsapp", "phone"]),
                notes="Customer requested morning slot.",
                quoted_from=Decimal(str(pricing.quick_quote(service)["from_price"])),
            ))

    db.session.commit()


def _seed_whatsapp_demo() -> None:
    """A short, realistic bot conversation so the inbox is not empty."""
    if WaConversation.query.count() > 0:
        return

    conversation = WaConversation(
        wa_id="263771112233", profile_name="Takudzwa Marufu", state="MAIN_MENU",
    )
    conversation.context = {"lang": "en"}
    db.session.add(conversation)
    db.session.flush()

    script = [
        ("inbound", "Good morning, I had an accident over the weekend. Can I get a quote?"),
        ("outbound", "Hello Takudzwa! 👋 Welcome to Topclass Auto Body.\n\nHow can we help you today?"),
        ("inbound", "Get a quote"),
        ("outbound", "Panel Beating & Spray Painting — noted. 👍\n\nWhat is the vehicle registration number?"),
        ("inbound", "ABC 1234"),
        ("outbound", "Briefly describe the damage or what you need done. You can also send photos 📷."),
        ("inbound", "Front bumper and bonnet damaged. It is an Old Mutual claim."),
        ("outbound",
         "✅ Request logged, Takudzwa.\n\n*Reference:* TC-BKG-9A21C4\n*Vehicle:* ABC1234\n"
         "*Service:* Panel Beating & Spray Painting\n\nOur front desk will confirm your booking "
         "and send the firm quotation during business hours."),
    ]
    for direction, body in script:
        db.session.add(WaMessage(
            conversation_id=conversation.id, direction=direction, body=body,
            is_bot=(direction == "outbound"), msg_type="text",
            status="read" if direction == "outbound" else "received",
        ))
    db.session.commit()
