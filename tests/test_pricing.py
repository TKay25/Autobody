"""Estimating engine tests — the money side must be exact."""
from __future__ import annotations

from decimal import Decimal

from app.services import pricing


def test_labour_matrix_has_expected_panels():
    panels = pricing.available_panels()
    assert "Front Bumper" in panels
    assert "Rear Quarter Panel" in panels
    assert len(panels) >= 15


def test_build_lines_produces_labour_paint_and_materials():
    lines = pricing.build_lines(["Front Door"])
    kinds = {l["kind"] for l in lines}
    assert "LABOUR" in kinds
    assert "MATERIAL" in kinds
    assert "CONSUMABLE" in kinds
    labour = [l for l in lines if l["kind"] == "LABOUR"]
    assert len(labour) == 2  # panel beating + spray painting


def test_summarise_applies_vat():
    lines = pricing.build_lines(["Front Bumper"])
    summary = pricing.summarise(lines, Decimal("0.15"))
    assert summary["vat"] > 0
    assert summary["total"] == summary["subtotal"] + summary["vat"]


def test_parts_markup_is_applied():
    lines = pricing.build_lines([], parts=[{"description": "Bumper", "quantity": 1, "unit_price": 100}])
    summary = pricing.summarise(lines, Decimal("0.15"))
    assert summary["parts_total"] == Decimal("125.00")  # 25% markup


def test_quick_quote_returns_indicative_price():
    quote = pricing.quick_quote("Ceramic Coating")
    assert quote["from_price"] == 350.0
    assert "Indicative" in quote["note"]
