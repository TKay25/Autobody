"""The WIP board's own workflow: adding a card, moving it with a note, and the
closing sheet for the day — or for a month.

Three promises are pinned here. The first is that a stage move says *why* it
happened (the note) and *whether the customer hears about it* (the toggle), which
used to be an unconditional message nobody could stop. The second is that the
closing sheet answers for any range the shop asks for, with the notes still in
it — a summary of a month that has lost the month's own words is not a handover.
The third is that the service chosen when the card is opened decides the stages
that card walks: a car booked in for a valet is never sent through the spray
booth, and a column it never visits is a decision the desk has to make on
purpose rather than a slip of the finger.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from app import tz
from app.constants import SERVICE_NAMES, SERVICE_STAGES, SERVICES, STAGES, stages_for_service
from app.extensions import db
from app.models import JobCard, JobStageEvent
from app.services import reporting

ROOT = Path(__file__).resolve().parent.parent
BOARD_JS = ROOT / "app" / "static" / "js" / "views" / "board.js"
APP_CSS = ROOT / "app" / "static" / "css" / "app.css"


# ── helpers ──────────────────────────────────────────────────────────────────
def _card(auth_client, *, reg="WIP001", name="Board Client",
          whatsapp="+263772000001", description="Right rear quarter panel",
          **extra):
    """What the board's Add job card dialog posts."""
    body = {
        "reg_no": reg, "customer_name": name, "customer_whatsapp": whatsapp,
        "description": description,
    }
    body.update(extra)
    res = auth_client.post("/api/jobs", json=body)
    assert res.status_code == 201, res.get_data(as_text=True)
    return res.get_json()["job"]


def _move(auth_client, job_id, stage="STRIP", **body):
    return auth_client.post(f"/api/jobs/{job_id}/stage", json={"stage": stage, **body})


def _outbound_kinds(job_id):
    """Message kinds sent to this job's customer, in order.

    Scoped to that customer's own thread rather than to every outbound row: the
    seed can plant a demo conversation, so a bare count measures the wrong thing.
    """
    from app.models import WaConversation

    job = db.session.get(JobCard, job_id)
    number = job.customer.wa_number if job.customer else None
    conversation = WaConversation.query.filter_by(wa_id=number).first()
    if conversation is None:
        return []
    return [m.msg_type for m in sorted(conversation.messages, key=lambda m: m.id)
            if m.direction == "outbound"]


def _column(board, stage):
    return next(c for c in board["columns"] if c["stage"] == stage)


# ── adding a card from the board ─────────────────────────────────────────────
def test_a_card_opened_on_the_board_lands_in_intake(auth_client, app):
    job = _card(auth_client, reg="WIP002", whatsapp="+263772000002")
    assert job["stage"] == "INTAKE"
    assert job["reg_no"] == "WIP002"
    assert job["description"] == "Right rear quarter panel"

    board = auth_client.get("/api/dashboard").get_json()["board"]
    card = next(j for j in _column(board, "INTAKE")["jobs"] if j["id"] == job["id"])
    assert card["customer_name"] == "Board Client"

    # The move dialog names the number an update would go to, so the card has to
    # carry the dialable form of it — not the one typed at the counter.
    with app.app_context():
        stored = db.session.get(JobCard, job["id"]).customer.wa_number
    assert card["customer_whatsapp"] == stored


# ── the note, and who hears about it ─────────────────────────────────────────
def test_a_move_keeps_the_note_on_the_card(auth_client):
    job = _card(auth_client)
    res = _move(auth_client, job["id"], "STRIP", note="Doors off, glass out", notify=False)
    assert res.status_code == 200, res.get_json()

    history = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]["stage_history"]
    assert history[-1]["stage"] == "STRIP"
    assert history[-1]["note"] == "Doors off, glass out"
    assert history[-1]["user"], "a note that does not say who wrote it is not a record"


def test_unticking_the_box_keeps_the_customer_quiet(auth_client, app):
    job = _card(auth_client, reg="QUIET1", whatsapp="+263772000010")
    res = _move(auth_client, job["id"], "STRIP", note="Stripped", notify=False)

    assert res.get_json()["notified"] is False
    with app.app_context():
        assert _outbound_kinds(job["id"]) == []


def test_the_customer_hears_about_a_move_when_the_box_is_ticked(auth_client, app):
    job = _card(auth_client, reg="TELL1", whatsapp="+263772000011")
    res = _move(auth_client, job["id"], "STRIP", note="Stripped", notify=True)

    assert res.get_json()["notified"] is True
    with app.app_context():
        kinds = _outbound_kinds(job["id"])
    assert kinds, "the customer was told nothing at all"


def test_a_client_that_says_nothing_still_tells_the_customer(auth_client, app):
    """An older page drags a card and posts the stage alone.

    It has no toggle to send, so the default has to stay the behaviour it always
    had — quietly going quiet instead would be a worse bug than the one being
    fixed.
    """
    job = _card(auth_client, reg="OLD1", whatsapp="+263772000012")
    res = _move(auth_client, job["id"], "STRIP", note="Stripped")

    assert res.get_json()["notified"] is True
    with app.app_context():
        assert _outbound_kinds(job["id"]), "the customer was not told at all"


def test_a_string_false_is_read_as_a_decision_not_as_truthiness(auth_client, app):
    """`"false"` is a truthy string, and an offline queue can replay one."""
    job = _card(auth_client, reg="STR1", whatsapp="+263772000013")
    res = _move(auth_client, job["id"], "STRIP", note="Stripped", notify="false")

    assert res.get_json()["notified"] is False
    with app.app_context():
        assert _outbound_kinds(job["id"]) == []


def test_a_note_longer_than_the_column_is_refused(auth_client):
    """A note that silently truncates on the closing sheet is a note that lies."""
    job = _card(auth_client)
    res = _move(auth_client, job["id"], "STRIP", note="x" * 241)

    assert res.status_code == 400
    assert "240" in res.get_json()["message"]


def test_the_advance_button_takes_a_note_and_a_quiet_customer_too(auth_client, app):
    """Both ways of moving a card answer the same two questions."""
    job = _card(auth_client, reg="ADV1", whatsapp="+263772000014")
    res = auth_client.post(f"/api/jobs/{job['id']}/advance", json={
        "note": "Booked in, keys handed over", "notify": False,
    })
    assert res.status_code == 200, res.get_json()

    body = res.get_json()
    assert body["job"]["stage"] == "PARTS_ORDER"
    assert body["notified"] is False
    with app.app_context():
        assert _outbound_kinds(job["id"]) == []
        assert db.session.get(JobCard, job["id"]).stage_history()[-1]["note"] \
            == "Booked in, keys handed over"


# ── the closing sheet, over a day or over a span ─────────────────────────────
def test_the_closing_sheet_covers_today_by_default(auth_client):
    job = _card(auth_client, reg="SHEET1", whatsapp="+263772000020")
    _move(auth_client, job["id"], "STRIP", note="Both doors stripped", notify=False)

    sheet = auth_client.get("/api/reports/range?preset=today").get_json()
    assert sheet["preset"] == "today"
    assert sheet["range"]["days"] == 1
    assert sheet["range"]["from"] == tz.today().isoformat()
    assert sheet["date"] == tz.today().isoformat()

    assert sheet["notes"]["moves"] >= 1
    assert "Both doors stripped" in [r["note"] for r in sheet["notes"]["list"]]
    assert sheet["notes"]["with_note"] >= 1


def test_the_sheet_can_be_asked_for_a_month_or_a_year(auth_client):
    """Month and year run up to today — a sheet of days that have not happened yet
    is a sheet full of zeros."""
    today = tz.today()

    month = auth_client.get("/api/reports/range?preset=month").get_json()
    assert month["range"]["from"] == today.replace(day=1).isoformat()
    assert month["range"]["to"] == today.isoformat()
    assert month["range"]["days"] == today.day

    year = auth_client.get("/api/reports/range?preset=year").get_json()
    assert year["range"]["from"] == date(today.year, 1, 1).isoformat()
    assert year["range"]["to"] == today.isoformat()

    yesterday = auth_client.get("/api/reports/range?preset=yesterday").get_json()
    assert yesterday["range"]["from"] == (today - timedelta(days=1)).isoformat()
    assert yesterday["range"]["days"] == 1


def test_a_custom_range_needs_its_dates(auth_client):
    """Answering "custom" with today's numbers would be a silent lie."""
    res = auth_client.get("/api/reports/range?preset=custom")
    assert res.status_code == 400
    assert "dates" in res.get_json()["message"].lower()


def test_a_custom_range_reports_the_days_it_was_given(auth_client):
    today = tz.today()
    first = today - timedelta(days=6)
    sheet = auth_client.get(
        "/api/reports/range?preset=custom"
        f"&from={first.isoformat()}&to={today.isoformat()}"
    ).get_json()

    assert sheet["range"]["days"] == 7
    assert sheet["range"]["from"] == first.isoformat()
    assert sheet["range"]["to"] == today.isoformat()
    assert first.strftime("%d %b %Y") in sheet["range"]["label"]


def test_a_range_that_runs_backwards_is_refused(auth_client):
    today = tz.today()
    res = auth_client.get(
        "/api/reports/range?preset=custom"
        f"&from={today.isoformat()}&to={(today - timedelta(days=1)).isoformat()}"
    )
    assert res.status_code == 400
    assert "before" in res.get_json()["message"].lower()


def test_an_unknown_range_is_refused(auth_client):
    res = auth_client.get("/api/reports/range?preset=fortnight")
    assert res.status_code == 400
    assert "fortnight" in res.get_json()["message"]


def test_the_range_report_requires_login(client):
    assert client.get("/api/reports/range").status_code == 401


def test_a_note_belongs_to_the_window_it_was_made_in_not_to_today(auth_client, app):
    """The window decides, not "now": yesterday's note is yesterday's work."""
    job = _card(auth_client, reg="PAST1", whatsapp="+263772000030")
    _move(auth_client, job["id"], "STRIP", note="Yesterday's work", notify=False)

    with app.app_context():
        event = (JobStageEvent.query.filter_by(job_id=job["id"])
                 .order_by(JobStageEvent.id.desc()).first())
        event.created_at = (tz.start_of_day_utc(tz.today() - timedelta(days=1))
                            + timedelta(hours=9))
        db.session.commit()

    yesterday = auth_client.get("/api/reports/range?preset=yesterday").get_json()
    assert "Yesterday's work" in [r["note"] for r in yesterday["notes"]["list"]]

    today = auth_client.get("/api/reports/range?preset=today").get_json()
    assert "Yesterday's work" not in [r["note"] for r in today["notes"]["list"]]


def test_the_day_sheet_still_answers_with_the_same_sections(auth_client):
    """The day sheet is what the PDF is built from, so it must not have moved."""
    sheet = auth_client.get("/api/reports/end-of-day").get_json()
    assert {"jobs", "bookings", "money", "staff", "notes"} <= set(sheet)
    assert sheet["date"] == tz.today().isoformat()
    assert sheet["date_label"].startswith(tz.today().strftime("%A"))


# ── the front end offers exactly what the server answers ─────────────────────
def test_the_move_dialog_asks_for_a_note_and_a_decision():
    src = BOARD_JS.read_text(encoding="utf-8")
    assert "type: 'textarea'" in src, "the move dialog asks for no note"
    assert "WhatsApp the customer" in src, "the move dialog has no notification toggle"
    # Both ways in — the drag and the phone's Move button — post the same fields,
    # including the answer to "is this a deliberate jump off the card's walk?".
    assert "api.post(`/api/jobs/${jobId}/stage`, { stage, note, notify, force })" in src


def test_every_range_the_screen_offers_is_one_the_server_knows():
    """A tab the API has never heard of is a button that only reports an error."""
    src = BOARD_JS.read_text(encoding="utf-8")
    block = src[src.index("const PRESETS = ["):]
    block = block[:block.index("];")]
    offered = re.findall(r"value: '([a-z]+)'", block)
    assert offered, "the report screen offers no ranges at all"
    assert set(offered) == set(reporting.PRESETS), (
        f"the screen offers {offered}, the API knows {list(reporting.PRESETS)}"
    )


def test_the_board_carries_its_own_controls_and_styles():
    src = BOARD_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    for marker in ("quickJob", "openReport", "sheetNode"):
        assert marker in src, f"the board has no {marker}"
    for cls in (".tc-board-actions", ".tc-report-presets", ".tc-note-cell"):
        assert cls in css, f"{cls} is missing from the stylesheet"


def test_the_board_no_longer_promises_the_customer_is_always_told():
    """It read "Customers are notified automatically", which is now the shop's call."""
    src = BOARD_JS.read_text(encoding="utf-8")
    assert "Customers are notified automatically" not in src
    assert "you decide whether the customer hears about it" in src


# ── the service decides the walk ─────────────────────────────────────────────
def test_every_service_offered_has_a_walk_of_its_own():
    """A service with no walk is a card nobody can move."""
    for svc in SERVICES:
        walk = stages_for_service(svc["name"])
        assert walk, f"{svc['name']} has no stages at all"
        assert walk[0] == "INTAKE", f"{svc['name']} does not start at intake"
        assert walk[-1] == "COLLECTED", f"{svc['name']} does not end at collection"
        assert set(walk) <= set(STAGES), (
            f"{svc['name']} walks {set(walk) - set(STAGES)}, which the board has no column for")
        # A walk is the board's own order, filtered — never a new order.
        assert walk == [s for s in STAGES if s in walk], (
            f"{svc['name']} walks the board out of order")

    # Readable by code as well, because the bot and the reports speak codes.
    assert stages_for_service("DETAIL") == stages_for_service("Car Detailing")


def test_a_service_the_shop_no_longer_offers_walks_the_whole_board():
    """A card must never become un-movable because its service line changed."""
    assert stages_for_service("Diesel Tuning") == STAGES
    assert stages_for_service(None) == STAGES
    assert stages_for_service("") == STAGES


def test_the_card_remembers_the_service_it_was_booked_in_for(auth_client):
    job = _card(auth_client, reg="SVC1", whatsapp="+263772000040",
                service="Car Detailing")

    assert job["service"] == "Car Detailing"
    assert job["service_code"] == "DETAIL"
    assert job["stage_step"] == 1
    assert job["stage_steps"] == len(stages_for_service("Car Detailing"))

    # The board draws the walk from the payload, so the card has to arrive with
    # it rather than being re-derived from the service name on the client.
    board = auth_client.get("/api/dashboard").get_json()["board"]
    card = next(j for j in _column(board, "INTAKE")["jobs"] if j["id"] == job["id"])
    assert card["service_code"] == "DETAIL"
    assert card["stage_steps"] == 5
    assert board["walks"]["DETAIL"] == stages_for_service("Car Detailing")


def test_the_board_publishes_every_walk_and_every_cards_place_on_it(auth_client):
    board = auth_client.get("/api/dashboard").get_json()["board"]
    assert board["walks"]["PANEL_SPRAY"] == STAGES
    for svc in SERVICES:
        assert board["walks"][svc["code"]] == stages_for_service(svc["name"])

    for column in board["columns"]:
        for card in column["jobs"]:
            assert card["stage_steps"], f"{card['job_no']} has no walk to move along"
            assert card["service_code"] in board["walks"] or card["service_code"] == "", (
                f"{card['job_no']} claims a service code the board does not know")


def test_a_service_the_shop_does_not_offer_is_refused_at_the_counter(auth_client):
    res = auth_client.post("/api/jobs", json={
        "reg_no": "BAD900", "customer_name": "Wrong Line",
        "service": "Rocket Science",
    })
    assert res.status_code == 400
    assert "service" in res.get_json()["message"].lower()


def test_a_card_opened_without_a_service_still_walks_the_whole_board(auth_client):
    """The old drag-only client posts no service, and must not be stranded."""
    job = _card(auth_client, reg="NOSVC1", whatsapp="+263772000041")
    assert job["service"] == SERVICE_NAMES[1]
    assert job["stage_steps"] == len(STAGES)


def test_a_valet_card_is_never_sent_through_the_spray_booth(auth_client):
    job = _card(auth_client, reg="VALET1", whatsapp="+263772000042",
                service="Car Detailing")

    detail = auth_client.get(f"/api/jobs/{job['id']}").get_json()
    assert detail["next_stage"] == "DETAILING"
    assert detail["walk"] == stages_for_service("Car Detailing")

    def advance():
        res = auth_client.post(f"/api/jobs/{job['id']}/advance", json={"notify": False})
        assert res.status_code == 200, res.get_json()
        return res.get_json()["job"]["stage"]

    assert advance() == "DETAILING"
    assert advance() == "QC"

    # QC is still a gate, whatever walk the card took to get here.
    blocked = auth_client.post(f"/api/jobs/{job['id']}/advance", json={"notify": False})
    assert blocked.status_code == 409
    qc = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]["qc_results"]
    auth_client.post(f"/api/jobs/{job['id']}/qc", json={
        "results": [{"item": r["item"], "passed": True} for r in qc],
    })

    assert advance() == "READY"
    assert advance() == "COLLECTED"


def test_a_panel_card_still_walks_the_whole_shop_floor(auth_client):
    job = _card(auth_client, reg="PANEL1", whatsapp="+263772000043",
                service="Panel Beating & Spray Painting")
    detail = auth_client.get(f"/api/jobs/{job['id']}").get_json()
    assert detail["walk"] == STAGES
    assert detail["next_stage"] == "PARTS_ORDER"


def test_a_drop_off_the_cards_walk_is_refused_and_names_the_walk(auth_client):
    """The board is one set of columns for every card; the card is not."""
    job = _card(auth_client, reg="OFF1", whatsapp="+263772000044",
                service="Car Detailing")
    res = _move(auth_client, job["id"], "PAINT", note="Nothing to paint")

    assert res.status_code == 409, res.get_json()
    body = res.get_json()
    assert body["walk"] == stages_for_service("Car Detailing")
    assert body["next_stage"] == "DETAILING"
    assert "Detailing / Valet" in body["message"], body["message"]

    # Refused means refused: the card is where it was.
    after = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]
    assert after["stage"] == "INTAKE"


def test_the_override_moves_it_and_the_audit_says_so(auth_client):
    job = _card(auth_client, reg="OVR1", whatsapp="+263772000045",
                service="Car Detailing")
    res = _move(auth_client, job["id"], "PAINT", force=True,
                note="Customer asked for a full respray")

    assert res.status_code == 200, res.get_json()
    card = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]
    assert card["stage"] == "PAINT"
    # Off its own walk, so the board says so instead of counting it as progress.
    assert card["stage_step"] == 0
    assert card["stage_steps"] == len(stages_for_service("Car Detailing"))

    entries = auth_client.get("/api/activity").get_json()["items"]
    moved = [e for e in entries if e["action"] == "job.stage_changed"]
    assert moved, "an override that leaves no trace is not an audit trail"
    assert "overridden" in moved[0]["summary"]
    assert moved[0]["meta"]["overridden"] is True


def test_a_card_off_its_walk_is_pointed_back_onto_it(auth_client):
    """A service corrected after check-in must not strand the card."""
    job = _card(auth_client, reg="BACK1", whatsapp="+263772000046",
                service="Car Detailing")
    _move(auth_client, job["id"], "PAINT", force=True)

    detail = auth_client.get(f"/api/jobs/{job['id']}").get_json()
    assert detail["job"]["stage_step"] == 0
    assert detail["next_stage"] == "DETAILING", (
        "a valet in the spray booth is not 'nearly finished'")

    res = auth_client.post(f"/api/jobs/{job['id']}/advance", json={"notify": False})
    assert res.status_code == 200
    assert res.get_json()["job"]["stage"] == "DETAILING"


# ── the phone is a device the shop actually uses ─────────────────────────────
def test_the_add_card_dialog_asks_which_service():
    """The stages come from the service, so the board has to ask for it."""
    src = BOARD_JS.read_text(encoding="utf-8")
    block = src[src.index("async function quickJob()"):]
    block = block[:block.index("submitLabel: 'Open the card'")]
    assert "name: 'service'" in block, "the card is opened without a service"
    assert "services.map" in block, "the service list is not offered"
    assert "service: res.service" in src, "the chosen service never reaches the server"


def test_the_move_dialog_offers_the_cards_own_walk_first():
    src = BOARD_JS.read_text(encoding="utf-8")
    assert "data.board.walks" in src, "the walks are not read off the board payload"
    assert "walkFor(" in src, "the dialog does not know the card's walk"
    assert "optgroups:" in src, "the card's own stages are not grouped"
    assert "Override the walk" in src, "an off-walk move cannot be declared"
    assert "job.stage_step" in src, "the card does not say where it is on its walk"


def test_the_phone_gets_a_rail_and_a_thumb_sized_way_to_move():
    """Drag-and-drop never fires on a touchscreen.

    So the phone needs both ways out: the rail to travel across eleven columns,
    and a Move button big enough to hit with a thumb.
    """
    src = BOARD_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert "tc-stage-rail" in src, "the phone has no way across eleven columns"
    assert "scrollIntoView" in src, "the rail does not travel"
    assert "job-card-move" in src, "the touch path to move a card is gone"

    assert ".tc-stage-rail { display: none; }" in css, (
        "the rail is not put away where the columns are the rail")
    assert ".tc-stage-rail-chip" in css
    assert "min-height: 40px" in css, "the rail chips are not a thumb target"
    assert "width: 40px; height: 40px" in css, "the Move button is not a thumb target"
