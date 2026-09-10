"""Building a job card from an existing quotation, and attaching paperwork."""
from __future__ import annotations

from io import BytesIO

from app.extensions import db
from app.models import Estimate, JobPhoto

PDF = b"%PDF-1.4\n% test quotation\n%%EOF\n"


def _job(client, *, reg="QTE111", customer="Quote Tester", panels=("Front Bumper", "Bonnet"),
         insurance=False, phone=None):
    res = client.post("/api/jobs", json={
        "customer_name": customer,
        # Distinct numbers keep customers apart — the API matches on phone too.
        "customer_phone": phone or f"+26377{abs(hash(customer)) % 10_000_000:07d}",
        "reg_no": reg,
        "panels": list(panels),
        "is_insurance": insurance,
    })
    assert res.status_code == 201, res.get_json()
    return res.get_json()["job"]


def _estimate_id(client, job_id):
    estimate = client.get(f"/api/jobs/{job_id}").get_json()["job"]["estimate"]
    assert estimate, f"job {job_id} has no estimate"
    return estimate["id"]


# ── the quotation picker ─────────────────────────────────────────────────────
def test_quotations_list_includes_job_and_vehicle_context(auth_client):
    job = _job(auth_client, reg="LST222")
    res = auth_client.get("/api/quotations")
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] >= 1

    row = next(q for q in body["items"] if q["job_id"] == job["id"])
    assert row["reg_no"] == "LST222"          # stored without the space
    assert row["customer_name"] == "Quote Tester"
    assert row["item_count"] > 0
    assert row["total"] > 0
    assert row["job_no"] == job["job_no"]


def test_quotations_filter_by_registration_ignores_spaces(auth_client):
    _job(auth_client, reg="REG333")
    _job(auth_client, reg="OTH444")

    for probe in ("REG333", "REG 333", "reg333"):
        body = auth_client.get(f"/api/quotations?reg_no={probe}").get_json()
        assert body["count"] == 1, probe
        assert body["items"][0]["reg_no"] == "REG333"


def test_quotations_filter_by_customer(auth_client):
    _job(auth_client, reg="CUS555", customer="Alpha Fleet")
    _job(auth_client, reg="CUS666", customer="Beta Motors")

    cust = auth_client.get("/api/customers?q=Alpha").get_json()["items"][0]
    body = auth_client.get(f"/api/quotations?customer_id={cust['id']}").get_json()
    assert body["count"] == 1
    assert body["items"][0]["customer_name"] == "Alpha Fleet"


def test_quotations_search_matches_reference_and_customer(auth_client):
    job = _job(auth_client, reg="SRCH77", customer="Searchable Motors")
    ref = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]["estimate"]["reference"]
    assert auth_client.get(f"/api/quotations?q={ref}").get_json()["count"] == 1
    assert auth_client.get("/api/quotations?q=Searchable").get_json()["count"] == 1


def test_quotations_requires_login(client):
    assert client.get("/api/quotations").status_code == 401


# ── building a job card from a quotation ─────────────────────────────────────
def test_new_job_card_copies_the_attached_quotation(auth_client, app):
    source_job = _job(auth_client, reg="SRC111", panels=("Front Bumper", "Bonnet", "Roof"))
    source_id = _estimate_id(auth_client, source_job["id"])
    source = auth_client.get(f"/api/estimates/{source_id}").get_json()["estimate"]

    res = auth_client.post("/api/jobs", json={
        "customer_name": "Second Visit",
        "reg_no": "SRC111",
        "source_estimate_id": source_id,
    })
    assert res.status_code == 201, res.get_json()
    new_job = res.get_json()["job"]

    detail = auth_client.get(f"/api/jobs/{new_job['id']}").get_json()["job"]
    copied = detail["estimate"]
    assert copied["id"] != source_id
    assert len(copied["items"]) == len(source["items"])
    assert copied["subtotal"] == source["subtotal"]
    assert copied["total"] == source["total"]
    assert "Copied from quotation" in (copied["notes"] or "")
    assert source["reference"] in copied["notes"]


def test_copied_lines_keep_their_kind_and_price(auth_client):
    source_job = _job(auth_client, reg="KND222", panels=("Front Door",))
    source = auth_client.get(
        f"/api/estimates/{_estimate_id(auth_client, source_job['id'])}").get_json()["estimate"]

    new_job = auth_client.post("/api/jobs", json={
        "customer_name": "Kind Check", "reg_no": "KND222",
        "source_estimate_id": source["id"],
    }).get_json()["job"]

    copied = auth_client.get(f"/api/jobs/{new_job['id']}").get_json()["job"]["estimate"]
    for before, after in zip(source["items"], copied["items"]):
        assert after["kind"] == before["kind"]
        assert after["description"] == before["description"]
        assert after["quantity"] == before["quantity"]
        assert after["unit_price"] == before["unit_price"]
        assert after["line_total"] == before["line_total"]


def test_copied_estimate_inherits_the_excess_sent_by_the_form(auth_client):
    source_job = _job(auth_client, reg="EXC333", insurance=True)
    source_id = _estimate_id(auth_client, source_job["id"])

    new_job = auth_client.post("/api/jobs", json={
        "customer_name": "Excess Check", "reg_no": "EXC333",
        "source_estimate_id": source_id, "is_insurance": True, "excess": 250,
    }).get_json()["job"]

    copied = auth_client.get(f"/api/jobs/{new_job['id']}").get_json()["job"]["estimate"]
    assert copied["excess"] == 250.0
    assert copied["is_insurance"] is True


def test_job_card_records_where_the_estimate_came_from(auth_client, app):
    source_job = _job(auth_client, reg="LOG444")
    source_id = _estimate_id(auth_client, source_job["id"])
    new_job = auth_client.post("/api/jobs", json={
        "customer_name": "Audit Trail", "reg_no": "LOG444",
        "source_estimate_id": source_id,
    }).get_json()["job"]

    with app.app_context():
        from app.models import ActivityLog

        entry = ActivityLog.query.filter_by(action="estimate.copied").first()
        assert entry is not None
        assert entry.job_id == new_job["id"]
        assert entry.entity_ref  # the source quotation reference


def test_attaching_an_unknown_or_empty_quotation_is_rejected(auth_client, app):
    res = auth_client.post("/api/jobs", json={
        "customer_name": "Bad Quote", "reg_no": "BAD555", "source_estimate_id": 999999,
    })
    assert res.status_code == 404

    # A job opened without panels gets no estimate at all.
    plain = _job(auth_client, reg="EMP666", customer="Empty Quote", panels=())
    assert auth_client.get(f"/api/jobs/{plain['id']}").get_json()["job"]["estimate"] is None

    # And an estimate that exists but carries no lines has nothing to copy.
    from app.models import JobCard

    with app.app_context():
        bare = Estimate(job_id=plain["id"], reference="TC-EST-BARE01", currency="USD",
                        subtotal=0, vat=0, total=0, status="SENT")
        db.session.add(bare)
        db.session.commit()
        bare_id = bare.id
        assert db.session.get(JobCard, plain["id"]) is not None

    res = auth_client.post("/api/jobs", json={
        "customer_name": "Bad Quote", "reg_no": "BAD555", "source_estimate_id": bare_id,
    })
    assert res.status_code == 400
    assert "no line items" in res.get_json()["message"]


def test_building_from_panels_still_works(auth_client):
    job = _job(auth_client, reg="PNL777", panels=("Front Bumper",))
    estimate = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]["estimate"]
    assert len(estimate["items"]) > 0
    assert estimate["notes"] is None or "Copied from" not in estimate["notes"]


# ── attaching the quotation document ─────────────────────────────────────────
def _upload(client, job_id, *, filename="assessor-quotation.pdf", body=PDF,
            content_type="application/pdf", kind="QUOTATION"):
    return client.post(
        f"/api/jobs/{job_id}/documents",
        data={"file": (BytesIO(body), filename), "kind": kind},
        content_type="multipart/form-data",
    )


def test_uploading_a_quotation_document(auth_client, app):
    job = _job(auth_client, reg="DOC888")
    res = _upload(auth_client, job["id"])
    assert res.status_code == 201, res.get_json()

    document = res.get_json()["document"]
    assert document["kind"] == "QUOTATION"
    assert document["caption"] == "assessor-quotation.pdf"
    assert document["url"].startswith("/uploads/documents/")
    assert document["url"].endswith("-assessor-quotation.pdf")

    with app.app_context():
        stored = JobPhoto.query.filter_by(kind="QUOTATION").one()
        assert stored.filename == "assessor-quotation.pdf"
        assert stored.source == "upload"

    # The stored URL is served back to signed-in staff.
    served = auth_client.get(document["url"])
    assert served.status_code == 200
    assert served.data.startswith(b"%PDF")


def test_upload_records_an_activity_entry(auth_client, app):
    job = _job(auth_client, reg="DOC777")
    _upload(auth_client, job["id"], kind="JOB_SHEET")
    with app.app_context():
        from app.models import ActivityLog

        entry = ActivityLog.query.filter_by(action="document.attached").one()
        assert entry.job_id == job["id"]
        assert "assessor-quotation.pdf" in (entry.summary or "")


def test_upload_rejects_unsupported_file_types(auth_client):
    job = _job(auth_client, reg="DOC999")
    res = _upload(auth_client, job["id"], filename="virus.exe",
                  body=b"MZ", content_type="application/octet-stream")
    assert res.status_code == 400
    assert "not supported" in res.get_json()["message"]

    res = _upload(auth_client, job["id"], filename="notes.txt", body=b"hello",
                  content_type="text/plain")
    assert res.status_code == 400


def test_upload_needs_a_file_and_a_real_job(auth_client):
    job = _job(auth_client, reg="DOC000")
    res = auth_client.post(f"/api/jobs/{job['id']}/documents", data={},
                           content_type="multipart/form-data")
    assert res.status_code == 400
    assert _upload(auth_client, 999999).status_code == 404


def test_uploads_block_traversal_and_missing_files(auth_client, app):
    job = _job(auth_client, reg="SEC123")
    document = _upload(auth_client, job["id"]).get_json()["document"]

    assert auth_client.get(document["url"]).status_code == 200
    assert auth_client.get("/uploads/../config.py").status_code == 404
    assert auth_client.get("/uploads/nope.pdf").status_code == 404
    assert app.config["ALLOWED_EXTENSIONS"] >= {"pdf", "png", "jpg"}


def test_uploads_need_a_staff_session():
    """Attached paperwork is customer data — a blank session must not read it."""
    from app import create_app
    from config import TestConfig

    fresh = create_app(TestConfig)
    with fresh.app_context():
        db.create_all()
        anon = fresh.test_client()
        assert anon.get("/uploads/documents/anything.pdf").status_code in (302, 401)
        db.drop_all()
