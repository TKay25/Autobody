"""Smoke-test the quotation / invoice / receipt document pipeline.

Run:  python tools/smoke_docs.py
Generates real PDFs into ``instance/smoke/`` and exercises the simulator-mode
WhatsApp delivery so the whole chain is proven without touching Meta.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WA_MODE", "simulator")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Estimate, Invoice, Payment  # noqa: E402
from app.services import documents, job_flow, notifications  # noqa: E402

OUT = ROOT / "instance" / "smoke"


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    return condition


def main() -> int:
    app = create_app()
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0

    with app.app_context():
        estimate = Estimate.query.filter(Estimate.job_id.isnot(None)).first()
        if not estimate:
            print("No seeded estimate found — run `python bootstrap.py --reset`.")
            return 1
        invoice = Invoice.query.filter(Invoice.job_id == estimate.job_id).first() \
            or Invoice.query.first() \
            or job_flow.ensure_invoice(estimate.job)
        if not invoice:
            print("Could not obtain an invoice for the smoke test.")
            return 1
        db.session.commit()

        print("PDF generation")
        for kind, record in (("quote", estimate), ("invoice", invoice)):
            payload, filename = documents.build_for(kind, record)
            path = OUT / filename
            path.write_bytes(payload)
            ok = payload[:4] == b"%PDF" and len(payload) > 2000
            failures += not check(f"{kind}: {filename} ({len(payload):,} bytes)", ok)
            failures += not check(f"{kind}: saved to {path.name}", path.exists())

        print("Receipt + payment")
        before = invoice.balance
        payment = job_flow.record_payment(
            invoice, amount=min(50, before) or 50, method="ECOCASH",
            reference="SMOKE-1",
        )
        db.session.refresh(invoice)
        failures += not check("receipt_no assigned", bool(payment.receipt_no),
                              payment.receipt_no or "")
        failures += not check("balance reduced", invoice.balance < before,
                              f"{before} -> {invoice.balance}")
        payload, filename = documents.build_for("receipt", payment)
        (OUT / filename).write_bytes(payload)
        failures += not check(f"receipt PDF {filename} ({len(payload):,} bytes)",
                              payload[:4] == b"%PDF")
        failures += not check("public_token present", bool(payment.public_token))

        print("WhatsApp delivery (simulator)")
        result = notifications.send_quotation(estimate.job, estimate)
        failures += not check("quotation sent", bool(result.get("sent")), str(result))
        failures += not check("estimate marked SENT", estimate.status == "SENT",
                              estimate.status)
        failures += not check("pdf link built", (result.get("pdf") or "").endswith(".pdf"),
                              result.get("pdf") or "")

        result = notifications.send_invoice(invoice.job, invoice)
        failures += not check("invoice sent", bool(result.get("sent")), str(result))

        result = notifications.send_receipt(payment)
        failures += not check("receipt sent", bool(result.get("sent")), str(result))
        failures += not check("receipt link built",
                              (result.get("link") or "").startswith("http"),
                              result.get("link") or "")

        print("Public documents")
        client = app.test_client()
        for kind, record in (("quote", estimate), ("invoice", invoice), ("receipt", payment)):
            token = record.public_token
            page = client.get(f"/doc/{kind}/{token}")
            failures += not check(f"/doc/{kind}/<token> -> {page.status_code}",
                                  page.status_code == 200)
            pdf = client.get(f"/doc/{kind}/{token}.pdf")
            failures += not check(
                f"/doc/{kind}/<token>.pdf -> {pdf.status_code} {pdf.headers.get('Content-Type')}",
                pdf.status_code == 200
                and pdf.headers.get("Content-Type") == "application/pdf"
                and pdf.data[:4] == b"%PDF",
            )
        failures += not check("bad token 404s",
                              client.get("/doc/quote/deadbeefdeadbeef").status_code == 404)
        failures += not check("unknown kind 404s",
                              client.get(f"/doc/wibble/{estimate.public_token}").status_code == 404)

        db.session.rollback()

    print()
    print("FAILURES:" if failures else "All document checks passed.", failures or "")
    print(f"Artifacts in {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
