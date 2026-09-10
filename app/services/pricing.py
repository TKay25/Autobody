"""Estimating engine.

Turns "which panels are damaged" into a priced, VAT-inclusive estimate using the
labour matrix in :mod:`app.constants`. Insurers get a different rate card than
walk-in retail customers, which is the single biggest source of margin leakage in
a panel shop.
"""
from __future__ import annotations

from decimal import Decimal

from ..constants import (
    CONSUMABLES_PCT,
    DEFAULT_PARTS_MARKUP,
    DETAIL_RATE,
    INSURER_LABOUR_DISCOUNT,
    INSURER_PARTS_MARKUP,
    LABOUR_MATRIX,
    METAL_CONSUMABLE_PER_HOUR,
    METAL_RATE,
    PAINT_MATERIAL_PER_PANEL,
    PAINT_RATE,
    PANEL_RATE,
    SERVICE_FROM_PRICE,
)

OPERATION_RATES = {
    "PANEL": PANEL_RATE,
    "PAINT": PAINT_RATE,
    "METAL": METAL_RATE,
    "DETAIL": DETAIL_RATE,
}


def _dec(value) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def panel_hours(panel: str) -> dict:
    return LABOUR_MATRIX.get(panel, {"panel": 0.0, "paint": 0.0})


def available_panels() -> list[str]:
    return list(LABOUR_MATRIX.keys())


def labour_line(panel: str, operation: str, is_insurance: bool = False) -> dict | None:
    """Build one labour estimate line for a panel + operation."""
    hours = panel_hours(panel).get(operation.lower(), 0.0)
    if not hours:
        return None
    rate = OPERATION_RATES.get(operation.upper(), PANEL_RATE)
    if is_insurance and operation.upper() in {"PANEL", "PAINT", "METAL"}:
        rate = _money(rate * (Decimal("1") - INSURER_LABOUR_DISCOUNT))
    label = {
        "PANEL": f"Panel beating — {panel}",
        "PAINT": f"Spray painting — {panel}",
        "METAL": f"Chassis / metalwork — {panel}",
        "DETAIL": f"Detailing — {panel}",
    }.get(operation.upper(), f"{operation} — {panel}")
    return {
        "kind": "LABOUR",
        "panel": panel,
        "operation": operation.upper(),
        "description": label,
        "quantity": round(hours, 2),
        "unit": "hrs",
        "unit_price": rate,
        "markup_pct": Decimal("0"),
    }


def material_line(panel: str, painted: bool = True) -> dict:
    return {
        "kind": "MATERIAL",
        "panel": panel,
        "operation": "PAINT",
        "description": f"Paint & materials — {panel}",
        "quantity": 1,
        "unit": "lot",
        "unit_price": PAINT_MATERIAL_PER_PANEL if painted else Decimal("0"),
        "markup_pct": Decimal("0"),
    }


def consumables_line(labour_total: Decimal) -> dict:
    return {
        "kind": "CONSUMABLE",
        "panel": None,
        "operation": None,
        "description": "Workshop consumables (tape, masking, abrasives, rags)",
        "quantity": 1,
        "unit": "lot",
        "unit_price": _money(labour_total * CONSUMABLES_PCT),
        "markup_pct": Decimal("0"),
    }


def metal_consumables_line(metal_hours: Decimal) -> dict:
    return {
        "kind": "CONSUMABLE",
        "panel": None,
        "operation": "METAL",
        "description": "Welding gas, wire and grinding consumables",
        "quantity": 1,
        "unit": "lot",
        "unit_price": _money(metal_hours * METAL_CONSUMABLE_PER_HOUR),
        "markup_pct": Decimal("0"),
    }


def build_lines(
    panels: list[str],
    is_insurance: bool = False,
    include_paint: bool = True,
    parts: list[dict] | None = None,
    extra_labour: list[dict] | None = None,
    include_consumables: bool = True,
) -> list[dict]:
    """Assemble the full set of estimate lines.

    ``panels``  – panel names from :data:`LABOUR_MATRIX`.
    ``parts``   – ``[{"description": str, "quantity": n, "unit_price": x}]``.
    ``extra_labour`` – ``[{"description": str, "quantity": hrs, "unit_price": rate}]``.
    """
    lines: list[dict] = []
    panel_hours_total = Decimal("0")
    paint_hours_total = Decimal("0")
    metal_hours_total = Decimal("0")

    for panel in panels:
        hours = panel_hours(panel)
        panel_h = _dec(hours.get("panel", 0))
        paint_h = _dec(hours.get("paint", 0))
        panel_hours_total += panel_h
        paint_hours_total += paint_h

        if panel_h:
            op = "METAL" if "Chassis" in panel or "Weld" in panel else "PANEL"
            if op == "METAL":
                metal_hours_total += panel_h
            line = labour_line(panel, op, is_insurance)
            if line:
                lines.append(line)
        if paint_h and include_paint:
            line = labour_line(panel, "PAINT", is_insurance)
            if line:
                lines.append(line)
            lines.append(material_line(panel))

    for extra in extra_labour or []:
        lines.append({
            "kind": "LABOUR",
            "panel": extra.get("panel"),
            "operation": (extra.get("operation") or "PANEL").upper(),
            "description": extra.get("description") or "Additional labour",
            "quantity": _dec(extra.get("quantity", 1)),
            "unit": extra.get("unit", "hrs"),
            "unit_price": _dec(extra.get("unit_price", PANEL_RATE)),
            "markup_pct": Decimal("0"),
        })

    markup = INSURER_PARTS_MARKUP if is_insurance else DEFAULT_PARTS_MARKUP
    for part in parts or []:
        lines.append({
            "kind": "PART",
            "panel": None,
            "operation": None,
            "description": part.get("description") or "Replacement part",
            "quantity": _dec(part.get("quantity", 1)),
            "unit": part.get("unit", "ea"),
            "unit_price": _dec(part.get("unit_price", 0)),
            "markup_pct": _dec(part.get("markup_pct", markup)),
        })

    if include_consumables:
        labour_total = sum(
            (_dec(l["quantity"]) * _dec(l["unit_price"]) for l in lines if l["kind"] == "LABOUR"),
            Decimal("0"),
        )
        if labour_total:
            lines.append(consumables_line(labour_total))
        if metal_hours_total:
            lines.append(metal_consumables_line(metal_hours_total))

    return lines


def summarise(lines: list[dict], is_insurance: bool, vat_rate: Decimal) -> dict:
    """Price up a set of lines. Labour/materials VAT-able, insurers exempt at source."""
    labour = sum(
        (_dec(l["quantity"]) * _dec(l["unit_price"]) for l in lines if l["kind"] == "LABOUR"),
        Decimal("0"),
    )
    materials = sum(
        (_dec(l["quantity"]) * _dec(l["unit_price"]) for l in lines if l["kind"] in {"MATERIAL", "CONSUMABLE"}),
        Decimal("0"),
    )
    parts = sum(
        (
            _dec(l["quantity"]) * _dec(l["unit_price"]) * (Decimal("1") + _dec(l.get("markup_pct", 0)))
            for l in lines
            if l["kind"] == "PART"
        ),
        Decimal("0"),
    )
    subtotal = labour + materials + parts
    vat = Decimal("0") if is_insurance else subtotal * vat_rate
    return {
        "labour_total": _money(labour),
        "materials_total": _money(materials),
        "parts_total": _money(parts),
        "subtotal": _money(subtotal),
        "vat": _money(vat),
        "total": _money(subtotal + vat),
        "line_count": len(lines),
    }


def quick_quote(service: str) -> dict:
    """Indicative "from" price for the quick-quote widget and the WhatsApp bot."""
    base = SERVICE_FROM_PRICE.get(service)
    if base is None:
        base = Decimal("75.00")
    return {
        "service": service,
        "from_price": float(_money(base)),
        "currency": "USD",
        "note": "Indicative only. A firm quotation follows a physical assessment.",
    }


def insurer_vs_customer(estimate_total: Decimal, excess: Decimal) -> dict:
    """Split the bill between insurer and customer."""
    total = _dec(estimate_total)
    excess = _dec(excess)
    return {
        "insurer_pays": float(_money(total - excess)),
        "customer_pays": float(_money(excess)),
    }
