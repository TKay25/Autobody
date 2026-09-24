"""The payments register behind the Payments tab.

Every job is paid for by the customer — cash, EcoCash, InnBucks, bank transfer
or card. There is no insurer settlement, so the register has to stand on its own
as the banking summary: what came in today, what came in this month, and how it
splits across the ways the shop actually takes money.
"""
from __future__ import annotations

from datetime import date

from app.constants import PAYMENT_METHODS


def _invoice_with_payment(auth_client, *, reg, amount=None, method="CASH"):
    """Open a job card, raise its invoice and take a payment against it."""
    res = auth_client.post("/api/jobs", json={
        "customer_name": f"Payer {reg}", "reg_no": reg, "panels": ["Bonnet"],
    })
    assert res.status_code == 201, res.get_json()
    job_id = res.get_json()["job"]["id"]

    res = auth_client.post(f"/api/jobs/{job_id}/invoice", json={})
    assert res.status_code == 201, res.get_json()
    invoice = res.get_json()["invoice"]

    paid = invoice["total"] if amount is None else amount
    res = auth_client.post(f"/api/invoices/{invoice['id']}/payment",
                           json={"amount": paid, "method": method})
    assert res.status_code == 200, res.get_json()
    return invoice, res.get_json()


def test_register_lists_receipts_with_a_total(auth_client):
    invoice, _ = _invoice_with_payment(auth_client, reg="REG101", method="ECOCASH")

    body = auth_client.get("/api/payments").get_json()
    assert body["count"] == 1
    assert body["total"] == invoice["total"]
    receipt = body["items"][0]
    assert receipt["invoice_id"] == invoice["id"]
    assert receipt["method"] == "ECOCASH"


def test_every_payment_method_is_offered(auth_client):
    body = auth_client.get("/api/payments").get_json()
    assert body["methods"] == PAYMENT_METHODS
    # The shop takes money directly from the customer — nothing else.
    assert "INSURER_SETTLEMENT" not in body["methods"]


def test_register_breaks_the_takings_down_by_method(auth_client):
    _invoice_with_payment(auth_client, reg="REG201", amount=50, method="CASH")
    _invoice_with_payment(auth_client, reg="REG202", amount=120, method="ECOCASH")
    _invoice_with_payment(auth_client, reg="REG203", amount=90, method="ECOCASH")

    by_method = auth_client.get("/api/payments").get_json()["by_method"]
    # Largest first, so the busiest channel reads at a glance.
    assert by_method[0]["method"] == "ECOCASH"
    assert by_method[0]["count"] == 2
    assert by_method[0]["total"] == 210.0
    assert by_method[1] == {"method": "CASH", "count": 1, "total": 50.0}


def test_register_can_be_filtered_by_method(auth_client):
    _invoice_with_payment(auth_client, reg="REG301", amount=40, method="CASH")
    _invoice_with_payment(auth_client, reg="REG302", amount=60, method="BANK_TRANSFER")

    body = auth_client.get("/api/payments?method=BANK_TRANSFER").get_json()
    assert body["count"] == 1
    assert body["total"] == 60.0
    # Filtered view: the breakdown reflects only what is in it.
    assert [row["method"] for row in body["by_method"]] == ["BANK_TRANSFER"]


def test_register_can_be_filtered_by_invoice(auth_client):
    invoice, _ = _invoice_with_payment(auth_client, reg="REG401", amount=25)
    _invoice_with_payment(auth_client, reg="REG402", amount=80)

    body = auth_client.get(f"/api/payments?invoice_id={invoice['id']}").get_json()
    assert body["count"] == 1
    assert body["items"][0]["invoice_id"] == invoice["id"]
    assert body["total"] == 25.0


def test_register_reports_todays_and_this_months_takings(auth_client):
    _invoice_with_payment(auth_client, reg="REG501", amount=75, method="CASH")

    body = auth_client.get("/api/payments").get_json()
    # Taken a moment ago, so it lands in both windows.
    assert body["received_today"] == 75.0
    assert body["received_month"] == 75.0


def test_a_day_window_that_excludes_today_is_empty(auth_client):
    _invoice_with_payment(auth_client, reg="REG601", amount=30)

    # `until` is exclusive of the day itself, so yesterday's window misses it.
    yesterday = date.today().isoformat()
    body = auth_client.get(
        f"/api/payments?since=2020-01-01&until={(date.fromordinal(date.today().toordinal() - 1)).isoformat()}"
    ).get_json()
    assert body["count"] == 0
    assert body["total"] == 0
    assert yesterday  # the payment is dated today, which the window excludes


def test_register_requires_login(client):
    assert client.get("/api/payments").status_code in (401, 302)


def test_the_retired_claim_endpoints_are_gone(auth_client):
    """Insurance claims were removed from the product, endpoints included."""
    assert auth_client.get("/api/claims").status_code == 404
    assert auth_client.patch("/api/claims/1", json={"status": "APPROVED"}).status_code == 404
