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


def test_quick_quote_is_none_when_the_service_has_no_published_price():
    """Auto body and panel work are priced off the damage, not off a number.

    These used to fall back to a flat USD 75, which the service menu then
    rendered as a plausible-looking "from USD 75" quote.
    """
    assert pricing.quick_quote("Auto Body") is None
    assert pricing.quick_quote("Panel Beating & Spray Painting") is None
    assert pricing.quick_quote("Not A Service At All") is None


def test_quoted_from_is_empty_rather_than_zero_when_unpriced():
    """Holding no figure has to stay distinct from having quoted zero."""
    assert pricing.quoted_from("Ceramic Coating") == Decimal("350.00")
    assert pricing.quoted_from("Auto Body") is None
