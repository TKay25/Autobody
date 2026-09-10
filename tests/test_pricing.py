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


def test_summarise_applies_vat_for_retail():
    lines = pricing.build_lines(["Front Bumper"], is_insurance=False)
    summary = pricing.summarise(lines, is_insurance=False, vat_rate=Decimal("0.15"))
    assert summary["vat"] > 0
    assert summary["total"] == summary["subtotal"] + summary["vat"]


def test_insurance_is_vat_exempt_and_labour_discounted():
    retail = pricing.summarise(
        pricing.build_lines(["Bonnet"], is_insurance=False), False, Decimal("0.15")
    )
    insured = pricing.summarise(
        pricing.build_lines(["Bonnet"], is_insurance=True), True, Decimal("0.15")
    )
    assert insured["vat"] == 0
    assert insured["labour_total"] < retail["labour_total"]


def test_parts_markup_is_applied():
    lines = pricing.build_lines([], parts=[{"description": "Bumper", "quantity": 1, "unit_price": 100}])
    summary = pricing.summarise(lines, False, Decimal("0.15"))
    assert summary["parts_total"] == Decimal("125.00")  # 25% retail markup


def test_quick_quote_returns_indicative_price():
    quote = pricing.quick_quote("Ceramic Coating")
    assert quote["from_price"] == 350.0
    assert "Indicative" in quote["note"]


def test_insurer_vs_customer_split():
    split = pricing.insurer_vs_customer(Decimal("1000"), Decimal("150"))
    assert split == {"insurer_pays": 850.0, "customer_pays": 150.0}
