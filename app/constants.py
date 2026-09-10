"""Domain constants for the Topclass Auto Body workshop.

Everything a workshop manager might want to tune lives here so the estimator,
the WIP board and the WhatsApp bot all speak the same language.
"""
from __future__ import annotations

from decimal import Decimal

# ── Roles ────────────────────────────────────────────────────────────────────
ROLE_OWNER = "owner"
ROLE_MANAGER = "manager"
ROLE_ESTIMATOR = "estimator"
ROLE_STORE = "storeman"
ROLE_TECH = "technician"
ROLE_FRONT = "frontdesk"

ROLES = [ROLE_OWNER, ROLE_MANAGER, ROLE_ESTIMATOR, ROLE_STORE, ROLE_TECH, ROLE_FRONT]
ROLE_LABELS = {
    ROLE_OWNER: "Owner",
    ROLE_MANAGER: "Workshop Manager",
    ROLE_ESTIMATOR: "Estimator / Assessor Liaison",
    ROLE_STORE: "Storeman",
    ROLE_TECH: "Technician",
    ROLE_FRONT: "Front Desk",
}
MANAGER_ROLES = {ROLE_OWNER, ROLE_MANAGER}

# ── Workshop stages (the WIP board columns) ──────────────────────────────────
STAGES = [
    "INTAKE",
    "ASSESSMENT",
    "AWAITING_APPROVAL",
    "PARTS_ORDER",
    "STRIP",
    "PANEL",
    "PREP",
    "PAINT",
    "REASSEMBLY",
    "DETAILING",
    "QC",
    "READY",
    "COLLECTED",
]
STAGE_LABELS = {
    "INTAKE": "Intake",
    "ASSESSMENT": "Assessment",
    "AWAITING_APPROVAL": "Awaiting Approval",
    "PARTS_ORDER": "Awaiting Parts",
    "STRIP": "Strip Down",
    "PANEL": "Panel Beating",
    "PREP": "Prep & Primer",
    "PAINT": "Spray Painting",
    "REASSEMBLY": "Reassembly",
    "DETAILING": "Detailing / Valet",
    "QC": "Quality Control",
    "READY": "Ready for Collection",
    "COLLECTED": "Collected",
}
STAGE_COLOURS = {
    "INTAKE": "secondary",
    "ASSESSMENT": "info",
    "AWAITING_APPROVAL": "warning",      # amber — waiting on someone
    "PARTS_ORDER": "warning",             # amber — waiting on parts
    "STRIP": "primary",
    "PANEL": "primary",
    "PREP": "primary",
    "PAINT": "primary",
    "REASSEMBLY": "primary",
    "DETAILING": "dark",
    "QC": "brand",                        # crimson — a gate, not a queue
    "READY": "success",                   # green
    "COLLECTED": "success",               # green
}
# Customer-facing wording (used by the WhatsApp bot).
STAGE_CUSTOMER_TEXT = {
    "INTAKE": "Your vehicle has been booked in and the job card is open.",
    "ASSESSMENT": "Our estimator is assessing the damage and preparing the quote.",
    "AWAITING_APPROVAL": "We are waiting for quotation / insurance approval.",
    "PARTS_ORDER": "We are waiting for parts to be delivered.",
    "STRIP": "The vehicle has been stripped down for repair.",
    "PANEL": "Panel beating is in progress.",
    "PREP": "Preparation, sanding and priming is in progress.",
    "PAINT": "The vehicle is in the spray booth for painting.",
    "REASSEMBLY": "Panels and trim are being reassembled.",
    "DETAILING": "Final detailing and valeting is in progress.",
    "QC": "Going through our final quality control inspection.",
    "READY": "Your vehicle is ready for collection. 🎉",
    "COLLECTED": "The vehicle has been collected. Thank you for your business.",
}
# Rough progress weighting so the portal can show a % complete.
STAGE_PROGRESS = {
    "INTAKE": 5, "ASSESSMENT": 10, "AWAITING_APPROVAL": 15, "PARTS_ORDER": 20,
    "STRIP": 35, "PANEL": 55, "PREP": 70, "PAINT": 82, "REASSEMBLY": 90,
    "DETAILING": 95, "QC": 97, "READY": 100, "COLLECTED": 100,
}
CLOSED_STAGES = {"COLLECTED"}
ACTIVE_STAGES = [s for s in STAGES if s not in CLOSED_STAGES]
OPEN_STAGES = [s for s in STAGES if s not in {"READY", "COLLECTED"}]

PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT"]
PRIORITY_COLOURS = {"LOW": "secondary", "NORMAL": "info", "HIGH": "warning", "URGENT": "danger"}

# ── Service lines (mirrors topclass.co.zw) ───────────────────────────────────
SERVICES = [
    {"code": "AUTO_BODY", "name": "Auto Body", "icon": "bi-car-front"},
    {"code": "PANEL_SPRAY", "name": "Panel Beating & Spray Painting", "icon": "bi-hammer"},
    {"code": "REBUILD", "name": "Rebuilds & Performance Upgrades", "icon": "bi-gear-wide-connected"},
    {"code": "DETAIL", "name": "Car Detailing", "icon": "bi-stars"},
    {"code": "CERAMIC", "name": "Ceramic Coating", "icon": "bi-shield-shaded"},
    {"code": "PPF", "name": "Paint Protection Film", "icon": "bi-shield-check"},
    {"code": "WRAP", "name": "Car Vinyl Wrapping", "icon": "bi-palette"},
]
SERVICE_NAMES = [s["name"] for s in SERVICES]
SERVICE_BY_NAME = {s["name"]: s for s in SERVICES}
SERVICE_BY_CODE = {s["code"]: s for s in SERVICES}

# Indicative retail prices for the "quick quote" lines (USD).
SERVICE_FROM_PRICE = {
    "Car Detailing": Decimal("45"),
    "Ceramic Coating": Decimal("350"),
    "Paint Protection Film": Decimal("600"),
    "Car Vinyl Wrapping": Decimal("450"),
    "Rebuilds & Performance Upgrades": Decimal("900"),
}

# ── Insurers on the Topclass panel ───────────────────────────────────────────
INSURERS = [
    {"code": "OLD", "name": "Old Mutual"},
    {"code": "AIC", "name": "AIC"},
    {"code": "NDI", "name": "NDI"},
    {"code": "FBC", "name": "FBC"},
    {"code": "ZIMNAT", "name": "Zimnat"},
    {"code": "CBZ", "name": "CBZ Insurance"},
    {"code": "FIRST", "name": "First Mutual"},
]
INSURER_BY_CODE = {i["code"]: i["name"] for i in INSURERS}
INSURER_ALIASES = {
    "old mutual": "OLD", "old": "OLD", "om": "OLD",
    "aic": "AIC", "ndi": "NDI", "fbc": "FBC", "zimnat": "ZIMNAT",
    "cbz": "CBZ", "first mutual": "FIRST", "first": "FIRST", "fm": "FIRST",
}

CLAIM_STATUSES = ["DRAFT", "ASSESSOR_BOOKED", "SUBMITTED", "APPROVED", "PARTIAL", "REPUDIATED", "SETTLED"]
CLAIM_STATUS_LABELS = {
    "DRAFT": "Draft",
    "ASSESSOR_BOOKED": "Assessor Booked",
    "SUBMITTED": "Submitted to Insurer",
    "APPROVED": "Approved",
    "PARTIAL": "Partially Approved",
    "REPUDIATED": "Repudiated",
    "SETTLED": "Settled",
}

# ── Estimator: labour matrix (hours per panel / operation) ───────────────────
# Derived from typical Zimbabwean panel shop norm times. Tune per workshop.
LABOUR_MATRIX = {
    "Front Bumper": {"panel": 3.0, "paint": 2.0},
    "Rear Bumper": {"panel": 3.0, "paint": 2.0},
    "Bonnet": {"panel": 4.5, "paint": 3.0},
    "Boot Lid / Tailgate": {"panel": 4.0, "paint": 2.5},
    "Front Door": {"panel": 5.0, "paint": 3.0},
    "Rear Door": {"panel": 5.0, "paint": 3.0},
    "Front Fender / Wing": {"panel": 4.0, "paint": 2.5},
    "Rear Quarter Panel": {"panel": 7.0, "paint": 4.0},
    "Roof": {"panel": 6.0, "paint": 3.5},
    "Sill / Rocker": {"panel": 3.5, "paint": 2.0},
    "Mirror / Housing": {"panel": 1.0, "paint": 0.8},
    "Grille": {"panel": 1.5, "paint": 1.0},
    "Headlamp Surround": {"panel": 1.5, "paint": 1.0},
    "Windscreen Frame / A-Pillar": {"panel": 2.5, "paint": 1.5},
    "Chassis / Jig Alignment": {"panel": 12.0, "paint": 0.0},
    "Cut & Weld Section": {"panel": 6.0, "paint": 1.0},
    "Full Respray (per panel)": {"panel": 0.0, "paint": 4.0},
}

# Charge-out rates (USD / hour).
PANEL_RATE = Decimal("25.00")
PAINT_RATE = Decimal("28.00")
METAL_RATE = Decimal("32.00")
DETAIL_RATE = Decimal("20.00")

# Materials & consumables.
PAINT_MATERIAL_PER_PANEL = Decimal("18.00")   # paint + thinners + clear coat
CONSUMABLES_PCT = Decimal("0.08")              # % of labour, shop rags/tape/etc.
METAL_CONSUMABLE_PER_HOUR = Decimal("4.50")    # gas, wire, grinding discs

DEFAULT_PARTS_MARKUP = Decimal("0.25")         # 25% on parts
INSURER_PARTS_MARKUP = Decimal("0.15")         # insurers pay a tighter markup
INSURER_LABOUR_DISCOUNT = Decimal("0.10")      # 10% off retail labour on panel rates
EXCESS_DEFAULT = Decimal("150.00")

# ── Parts ────────────────────────────────────────────────────────────────────
PART_CATEGORIES = ["Body Panels", "Lights & Lamps", "Trim & Interior", "Paint & Consumables",
                   "Mechanical", "Glass", "Tyres & Wheels", "Hardware"]
PART_STATUSES = ["REQUIRED", "ORDERED", "IN_TRANSIT", "RECEIVED", "FITTED", "CANCELLED"]
SUPPLIERS = [
    "Rupes Zimbabwe", "Scangrip SA", "Spies Hecker Zim", "Nano Coatings",
    "Miaz Distributors", "Bulldog Abrasives", "Croco Motor Spares",
]

# ── Invoicing ────────────────────────────────────────────────────────────────
INVOICE_STATUSES = ["DRAFT", "ISSUED", "PART_PAID", "PAID", "OVERDUE", "CANCELLED"]
PAYMENT_METHODS = ["CASH", "ECOCASH", "INNBUCKS", "BANK_TRANSFER", "CARD", "INSURER_SETTLEMENT"]

BOOKING_STATUSES = ["REQUESTED", "CONFIRMED", "ARRIVED", "COMPLETED", "NO_SHOW", "CANCELLED"]
BOOKING_SLOTS = [
    "08:00", "09:00", "10:00", "11:00", "12:00",
    "13:00", "14:00", "15:00", "16:00",
]

QC_CHECKLIST = [
    "Panel gaps and alignment within tolerance",
    "Paint match verified in daylight",
    "No runs, sags, orange peel or dry spray",
    "Overspray removed from glass, trim and rubber",
    "All electrical items tested (lights, windows, wipers)",
    "Fluids topped up and no leaks",
    "Interior and exterior fully cleaned",
    "Wheel torque checked and tyre pressures set",
    "Tools, clips and loose items accounted for",
    "Final photos taken for job card",
]

WARRANTY_TEXT = (
    "Topclass Auto Body warrants workmanship for 12 months. Ceramic coating and paint "
    "protection film carry the manufacturer's warranty subject to the maintenance schedule."
)

# ── Vehicle reference data ───────────────────────────────────────────────────
# Curated for the Zimbabwean market: Japanese imports dominate, but the panel
# handles European marques, light commercials and the trucks the fleets run.
VEHICLE_MAKES = [
    "Toyota", "Nissan", "Honda", "Mazda", "Mitsubishi", "Isuzu", "Subaru", "Suzuki",
    "Daihatsu", "Ford", "Chevrolet", "Opel", "Volkswagen", "Audi", "BMW", "Mercedes-Benz",
    "Land Rover", "Jeep", "Hyundai", "Kia", "Renault", "Peugeot", "Citroën", "Fiat",
    "Volvo", "Lexus", "Hino", "Fuso", "UD Trucks", "Scania", "MAN", "Iveco",
    "Tata", "Mahindra", "Haval", "Chery", "BAIC", "JAC", "Foton", "Sinotruk",
]

# Models for the makes we see most. Anything not listed falls back to
# VEHICLE_MODELS_COMMON, and every dropdown keeps an "Other" escape hatch.
VEHICLE_MODELS = {
    "Toyota": [
        "Corolla", "Corolla Quest", "Corolla Axio", "Corolla Fielder", "Vitz", "Aqua",
        "Prius", "Yaris", "Etios", "Camry", "Mark X", "Premio", "Allion", "Crown",
        "Auris", "Wish", "Avanza", "Rush", "Urban Cruiser", "RAV4", "Hilux Legend",
        "Hilux D4D", "Hilux Revo", "Land Cruiser", "Land Cruiser Prado", "Fortuner",
        "Hiace", "Quantum", "Noah", "Voxy", "Granvia", "Coaster", "Dyna", "Tundra",
    ],
    "Nissan": [
        "NP200", "NP300", "Navara", "Hardbody", "X-Trail", "Qashqai", "Juke", "Murano",
        "Patrol", "Almera", "Sunny", "Tiida", "Note", "Micra", "March", "Serena",
        "Elgrand", "Caravan", "Urvan", "NV200", "Atlas", "Civilian", "Skyline", "370Z",
    ],
    "Honda": [
        "Fit", "Jazz", "Vezel", "HR-V", "CR-V", "Civic", "Accord", "City", "Stepwgn",
        "Odyssey", "Freed", "Mobilio", "Stream", "Airwave", "Insight", "Pilot",
        "BR-V", "WR-V", "Inspire", "Civic Type R",
    ],
    "Mazda": [
        "Demio", "Mazda2", "Axela", "Mazda3", "Atenza", "Mazda6", "CX-3", "CX-5",
        "CX-7", "CX-9", "BT-50", "Bongo", "Premacy", "Verisa", "MPV", "Familia",
        "CX-30", "MX-5",
    ],
    "Mitsubishi": [
        "L200", "Triton", "Pajero", "Pajero Sport", "Outlander", "ASX", "Lancer",
        "Colt", "Galant", "Delica", "Canter", "FVR", "Rosa", "Space Star", "Attrage",
        "Eclipse Cross", "Montero",
    ],
    "Isuzu": [
        "KB", "KB200", "KB250", "KB280", "KB300", "D-Max", "D-Max LX", "MU-X",
        "Rodeo", "Trooper", "FRR", "FSR", "FTR", "FVR", "NQR", "NPR", "NPS",
        "Panther", "Journey", "Elf",
    ],
    "Ford": [
        "Ranger", "Ranger Wildtrak", "Ranger Raptor", "Fiesta", "Focus", "Figo",
        "EcoSport", "Kuga", "Everest", "Transit", "Transit Custom", "F-150",
        "Mustang", "Bantam", "Ikon",
    ],
    "Volkswagen": [
        "Polo", "Polo Vivo", "Golf", "Golf GTI", "Golf R", "Jetta", "Passat", "T-Cross",
        "T-Roc", "Tiguan", "Touareg", "Caddy", "Transporter", "Amarok", "Up!",
    ],
    "Mercedes-Benz": [
        "A-Class", "B-Class", "C-Class", "E-Class", "S-Class", "CLA", "CLS", "GLA",
        "GLB", "GLC", "GLE", "GLS", "G-Class", "V-Class", "Vito", "Sprinter", "Citan",
    ],
    "BMW": [
        "1 Series", "2 Series", "3 Series", "4 Series", "5 Series", "7 Series", "X1",
        "X2", "X3", "X4", "X5", "X6", "X7", "Z4", "i3", "M3", "M5",
    ],
    "Hyundai": [
        "i10", "i20", "i30", "Accent", "Elantra", "Sonata", "Venue", "Creta", "Tucson",
        "Santa Fe", "H-1", "H100", "ix35", "Grand i10",
    ],
    "Kia": [
        "Picanto", "Rio", "Cerato", "Sportage", "Sorento", "Seltos", "Sonet", "K2700",
        "Carnival", "Soul", "Pegas",
    ],
    "Land Rover": [
        "Defender", "Discovery", "Discovery Sport", "Range Rover", "Range Rover Sport",
        "Range Rover Evoque", "Range Rover Velar", "Freelander",
    ],
}

# Used when the chosen make has no curated list of its own.
VEHICLE_MODELS_COMMON = [
    "Sedan", "Hatchback", "Station wagon", "SUV", "Double cab", "Single cab",
    "Panel van", "Minibus", "Truck", "Chassis cab", "Bus", "Other",
]

# name + a CSS background value for the swatch, used by the colour dropdown.
VEHICLE_COLOURS = [
    {"value": "White", "swatch": "#f7f8fa"},
    {"value": "Pearl White", "swatch": "#f2eee6"},
    {"value": "Silver", "swatch": "#c8cdd4"},
    {"value": "Grey", "swatch": "#8b939e"},
    {"value": "Charcoal", "swatch": "#4c525c"},
    {"value": "Black", "swatch": "#1a1d23"},
    {"value": "Blue", "swatch": "#1f5fb0"},
    {"value": "Navy Blue", "swatch": "#1b2f52"},
    {"value": "Light Blue", "swatch": "#8fb8de"},
    {"value": "Red", "swatch": "#c2102e"},
    {"value": "Maroon", "swatch": "#6d1526"},
    {"value": "Orange", "swatch": "#dd6b1a"},
    {"value": "Yellow", "swatch": "#e8c317"},
    {"value": "Gold", "swatch": "#bd9a3f"},
    {"value": "Beige", "swatch": "#ddcfb4"},
    {"value": "Cream", "swatch": "#f3ead2"},
    {"value": "Brown", "swatch": "#6b4a2f"},
    {"value": "Bronze", "swatch": "#8a6a3b"},
    {"value": "Green", "swatch": "#157a45"},
    {"value": "Dark Green", "swatch": "#123d27"},
    {"value": "Teal", "swatch": "#1d7f80"},
    {"value": "Purple", "swatch": "#5b3a86"},
    {"value": "Pink", "swatch": "#d878a4"},
    {"value": "Champagne", "swatch": "#cbb78e"},
    {"value": "Burgundy", "swatch": "#5d1a2c"},
    {"value": "Matte Black", "swatch": "#22262c"},
    {"value": "Two-tone", "swatch": "linear-gradient(135deg, #1a1d23 50%, #f7f8fa 50%)"},
]
