"""The WIP board's own workflow: adding a card, moving it with a note, and the
closing sheet for the day — or for a month.

Four promises are pinned here. The first is that a stage move says *why* it
happened (the note) and *whether the customer hears about it* (the toggle), which
used to be an unconditional message nobody could stop. The second is that the
closing sheet answers for any range the shop asks for, with the notes still in
it — a summary of a month that has lost the month's own words is not a handover.
The third is that the service chosen when the card is opened decides the stages
that card walks: a car booked in for a valet is never sent through the spray
booth, and a column it never visits is a decision the desk has to make on
purpose rather than a slip of the finger.

A fourth is pinned with them: the card opens *itself*. Tapping one used to
navigate away to the job's own screen, which cost the board its place thirteen
columns in to answer a question the board is already holding — where is this car
and what is next. So the dialog carries the facts, the walk, the move and the
note, and the full job card is one button inside it.

Two more, both about what the desk is asked when a car arrives. A card can carry
the **TMS's own reference**, and where there is one it *is* the card number,
because a shop running alongside the insurer's system cannot quote two numbers
for one car. And "What is the car in for" takes **more than one answer**: a car in
for a panel repair and a valet is one card whose walk is the *union* of both
lines, so the second service is a stage the shop goes through rather than an
override somebody has to stamp.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from app import tz
from app.constants import (
    SERVICE_NAMES,
    SERVICE_STAGES,
    SERVICES,
    STAGES,
    stages_for_service,
    stages_for_services,
)
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


def test_a_note_can_be_kept_on_the_stage_the_card_is_already_in(auth_client):
    """The dialog's note field, saved with the stage picker left alone.

    A foreman who has just found something worth writing down should not have to
    move the car to be allowed to say it. Posting the stage it is already in is
    how the board does it: the note lands on the card's history, the card does
    not move, and nobody is messaged about a move that never happened.
    """
    job = _card(auth_client, reg="NOTE1", whatsapp="+263772000015")
    note = "Odometer read, keys and spare wheel handed over"
    res = _move(auth_client, job["id"], "INTAKE", note=note, notify=True)

    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["job"]["stage"] == "INTAKE", "the card moved when it was asked not to"
    assert body["notified"] is False, "a note alone told the customer something"

    history = auth_client.get(f"/api/jobs/{job['id']}").get_json()["job"]["stage_history"]
    assert note in [e["note"] for e in history], (
        "the note was not kept on the card's history")


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


def test_several_services_walk_the_union_of_their_stages():
    """A car in for two things goes through every door either of them needs."""
    valet = stages_for_service("Car Detailing")
    wrap = stages_for_service("Car Vinyl Wrapping")

    # A wrap already covers the valet's stages; the valet's walk alone does not
    # cover the wrap's, so the union has to come out as the wrap's.
    assert stages_for_services(["Car Detailing", "Car Vinyl Wrapping"]) == wrap
    assert stages_for_services(["Car Vinyl Wrapping", "Car Detailing"]) == wrap
    assert valet != wrap, "the two lines are meant to differ for this to prove anything"

    # Repeats and blanks are noise, not extra stages.
    assert stages_for_services(["Car Detailing", "Car Detailing"]) == valet
    assert stages_for_services(["", None, "Car Detailing", " "]) == valet

    # Still a walk like any other: the board's own order, and nothing the board
    # has no column for.
    for combo in (["Car Detailing", "Ceramic Coating"],
                  ["Rebuilds & Performance Upgrades", "Car Detailing"],
                  [s["name"] for s in SERVICES]):
        walk = stages_for_services(combo)
        assert walk[0] == "INTAKE" and walk[-1] == "COLLECTED", combo
        assert walk == [s for s in STAGES if s in walk], combo
        assert set(walk) <= set(STAGES), combo

    # A line nobody recognises contributes the whole workshop, exactly as it does
    # on its own — the union cannot strand a card either.
    assert stages_for_services(["Car Detailing", "Diesel Tuning"]) == STAGES
    assert stages_for_services([]) == STAGES
    assert stages_for_services(None) == STAGES


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


# ── the TMS's own number ─────────────────────────────────────────────────────
def test_a_card_can_be_numbered_by_the_tms(auth_client):
    """Where the insurer issued a reference, that reference *is* the number.

    A shop running alongside the TMS's own system cannot quote two numbers for
    one car, so the ID is not a note beside the card number — it becomes the card
    number, in the same upper-cased, space-collapsed form any other key gets.
    """
    job = _card(auth_client, reg="TMS1", whatsapp="+263772000046",
                tms_id=" tms 88231 ")

    assert job["job_no"] == "TMS 88231"
    assert job["tms_id"] == "TMS 88231"

    # The search answers to it, because it is not a second field to look in.
    found = auth_client.get("/api/jobs?q=TMS 88231").get_json()["items"]
    assert [j["job_no"] for j in found] == ["TMS 88231"]


def test_a_card_with_no_tms_id_still_gets_the_shops_own_number(auth_client):
    """Most cars have no TMS paper behind them, and those are numbered as before."""
    job = _card(auth_client, reg="TMS2", whatsapp="+263772000047")
    assert job["job_no"].startswith(f"TC-{tz.today().year}-")
    assert job["tms_id"] is None


def test_the_same_tms_number_cannot_open_two_cards(auth_client):
    """Two cards answering to one reference is the thing this number avoids."""
    first = _card(auth_client, reg="TMS3", whatsapp="+263772000048", tms_id="TMS 9001")

    res = auth_client.post("/api/jobs", json={
        "reg_no": "TMS4", "customer_name": "Second Car", "tms_id": "tms 9001",
    })
    assert res.status_code == 409, res.get_json()
    assert "TMS 9001" in res.get_json()["message"]

    # Refused means no card: nothing was left behind against that number.
    jobs = auth_client.get("/api/jobs?q=TMS 9001").get_json()["items"]
    assert [j["id"] for j in jobs] == [first["id"]]


def test_a_tms_number_too_long_to_be_a_card_number_is_refused(auth_client):
    res = auth_client.post("/api/jobs", json={
        "reg_no": "TMS5", "customer_name": "Long Number", "tms_id": "X" * 40,
    })
    assert res.status_code == 409, res.get_json()
    assert "TMS ID" in res.get_json()["message"]
    assert not auth_client.get("/api/jobs?q=XXXX").get_json()["items"]


# ── more than one service on one card ────────────────────────────────────────
def test_a_car_can_be_in_for_more_than_one_service(auth_client):
    job = _card(auth_client, reg="MULTI1", whatsapp="+263772000049",
                services=["Car Detailing", "Ceramic Coating"])

    # The first line picked leads the card; the rest ride with it.
    assert job["service"] == "Car Detailing"
    assert job["services"] == ["Car Detailing", "Ceramic Coating"]
    assert job["service_codes"] == ["DETAIL", "CERAMIC"]

    detail = auth_client.get(f"/api/jobs/{job['id']}").get_json()
    assert detail["walk"] == stages_for_service("Car Detailing")
    assert detail["next_stage"] == "DETAILING"


def test_a_second_service_adds_its_stages_to_the_cards_walk(auth_client):
    """The walk is the union of the lines — not the leading line's alone.

    A valet that also wants a wrap has to reach the trim and the prep bay, or the
    shop would have to move the card off its own walk to get the wrapping done and
    have that stamped as an override.
    """
    job = _card(auth_client, reg="MULTI2", whatsapp="+263772000050",
                services=["Car Detailing", "Car Vinyl Wrapping"])

    detail = auth_client.get(f"/api/jobs/{job['id']}").get_json()
    assert detail["walk"] == stages_for_service("Car Vinyl Wrapping")
    # A union, not everything: a wrap is never painted, so the booth stays off it.
    assert "PAINT" not in detail["walk"]
    assert detail["next_stage"] == "STRIP"

    moved = auth_client.post(f"/api/jobs/{job['id']}/advance", json={"notify": False})
    assert moved.status_code == 200, moved.get_json()
    assert moved.get_json()["job"]["stage"] == "STRIP"


def test_the_board_carries_every_line_so_it_can_union_the_walks(auth_client):
    job = _card(auth_client, reg="MULTI3", whatsapp="+263772000051",
                services=["Car Detailing", "Ceramic Coating"])

    board = auth_client.get("/api/dashboard").get_json()["board"]
    card = next(j for j in _column(board, "INTAKE")["jobs"] if j["id"] == job["id"])
    assert card["services"] == ["Car Detailing", "Ceramic Coating"]
    assert card["service_codes"] == ["DETAIL", "CERAMIC"]
    # One honest position on one walk, whichever way the shop reads the column.
    assert card["stage_steps"] == len(stages_for_service("Car Detailing"))
    assert card["stage_step"] == 1


def test_the_card_names_its_tms_number_on_the_same_payload(auth_client):
    job = _card(auth_client, reg="TMS6", whatsapp="+263772000052", tms_id="TMS-77")
    board = auth_client.get("/api/dashboard").get_json()["board"]
    card = next(j for j in _column(board, "INTAKE")["jobs"] if j["id"] == job["id"])
    assert card["job_no"] == "TMS-77"
    assert card["tms_id"] == "TMS-77"


def test_a_line_the_shop_does_not_offer_is_refused_even_as_a_second_one(auth_client):
    res = auth_client.post("/api/jobs", json={
        "reg_no": "BAD901", "customer_name": "Two Lines",
        "services": ["Car Detailing", "Rocket Science"],
    })
    assert res.status_code == 400, res.get_json()
    assert "Rocket Science" in res.get_json()["message"]


def test_the_card_leads_with_one_line_for_the_reports(auth_client, app):
    """The reports, the bot and the quotations all still speak one service name.

    `service` is the line that leads the card, so nothing written before a card
    could carry a second one reads differently now.
    """
    job = _card(auth_client, reg="MULTI4", whatsapp="+263772000053",
                services=["Car Detailing", "Ceramic Coating"])

    with app.app_context():
        card = db.session.get(JobCard, job["id"])
        assert card.service == "Car Detailing"
        assert card.service_names == ["Car Detailing", "Ceramic Coating"]
        assert card.service_code == "DETAIL"
        assert card.stages() == stages_for_service("Car Detailing")

        # A value nobody can parse is "no extra lines", never a card that cannot
        # be read — the column is free text as far as the database is concerned.
        card.extra_services = "not json at all"
        assert card.service_names == ["Car Detailing"]
        assert card.stages() == stages_for_service("Car Detailing")


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
    """The stages come from the service, so the board has to ask for it.

    It asks for more than one: a car is often in for two things, and a picker
    that took a single answer dropped the second service the desk was told
    about. The TMS's number is asked for in the same breath, because where the
    insurer issued one it *is* the card number.
    """
    src = BOARD_JS.read_text(encoding="utf-8")
    block = src[src.index("async function quickJob()"):]
    block = block[:block.index("submitLabel: 'Open the card'")]
    assert "name: 'services'" in block, "the card is opened without a service"
    assert "type: 'checks'" in block, "the picker still takes a single answer"
    assert "services.map" in block, "the service list is not offered"
    assert "name: 'tms_id'" in block, "the popup never asks for the TMS ID"

    assert "services: res.services" in src, "the chosen services never reach the server"
    assert "tms_id: res.tms_id" in src, "the TMS ID never reaches the server"

    # And the board draws the card's walk from every line, not from one code.
    assert "job.service_codes" in src, "the board only reads one service code"
    assert "walks[code]" in src, "the board does not look a code's walk up"


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


# ── the card opens itself ────────────────────────────────────────────────────
def _card_markup(src: str) -> str:
    """The job card as the board builds it, up to its drag wiring."""
    block = src[src.index("const card = h('div.job-card'"):]
    return block[:block.index("card.addEventListener('dragstart'")]


def _job_dialog(src: str) -> str:
    """The job dialog: `openJob`, and none of the screens declared after it."""
    block = src[src.index("function openJob("):]
    return block[:block.index("async function quickJob(")]


def test_a_tapped_card_opens_the_job_rather_than_leaving_the_board():
    """The board already holds the answer, so the card answers in place.

    Tapping a card used to navigate to the job's own screen — a page load and a
    lost place thirteen columns in — when the question being asked of it was
    "where is this car, and what is next".
    """
    src = BOARD_JS.read_text(encoding="utf-8")
    card = _card_markup(src)

    assert "openJob(job, to)" in card, "tapping a card does not open the job dialog"
    assert "T.navigate(" not in card, "a tapped card still leaves the board"

    # A card only a mouse can open is a card half the shop cannot open.
    assert "card.setAttribute('tabindex', '0')" in card, (
        "the job card cannot be reached from a keyboard")
    assert "addEventListener('keydown'" in card, (
        "a keyboard cannot open a job card")


def test_the_dialog_shows_the_car_not_only_the_move():
    """The dialog is the job card now: the facts, the move, the note, the way on."""
    src = BOARD_JS.read_text(encoding="utf-8")
    dialog = _job_dialog(src)

    for marker in ("job.reg_no", "job.customer_phone", "job.technician", "job.bay",
                   "job.promised_date", "job.days_in_shop", "job.description"):
        assert marker in dialog, f"the job dialog never shows {marker}"
    assert "T.navigate(`/jobs/${job.id}`)" in dialog, (
        "the dialog is a dead end — nothing leads on to the full job card")


def test_the_dialog_keeps_a_note_without_moving_the_card():
    """A foreman writing down what was found should not have to move the car.

    The stage endpoint records a note whether or not the stage changed, so what
    the board has to get right is not drawing a move that did not happen — and
    not announcing one either.
    """
    src = BOARD_JS.read_text(encoding="utf-8")
    move = src[src.index("async function moveTo("):]
    move = move[:move.index("function openJob(")]

    assert "const moved = stage !== currentStage" in move, (
        "a note-only save is swallowed: the endpoint is never called")
    assert "if (moved) placeCard(jobId, stage)" in move, (
        "a note-only save still draws the card in a new column")
    assert "Note saved on" in move, (
        "a note-only save reports itself as a stage move")


def test_the_save_button_says_what_it_will_do():
    """A button promising a move when only a note will be kept is a lie."""
    src = BOARD_JS.read_text(encoding="utf-8")
    dialog = _job_dialog(src)

    assert "=== currentStage ? 'Save note' : 'Move card'" in dialog, (
        "the save button does not follow the stage picker")
    assert "controls.stage.addEventListener('change'" in dialog, (
        "the save button never hears the stage picker change")


def test_the_job_dialogs_save_button_is_associated_with_its_form():
    """The footer is outside the <form>, so the button has to name it.

    `form` is a read-only property on a button, so the association has to be an
    attribute — the same trap formModal was in, and the same fix.
    """
    src = BOARD_JS.read_text(encoding="utf-8")
    dialog = _job_dialog(src)

    assert "const form = h('form.row.g-3', { id: formId, novalidate: true }" in dialog
    assert "save.setAttribute('form', formId)" in dialog, (
        "the job dialog's Save button is orphaned — clicking it does nothing")
