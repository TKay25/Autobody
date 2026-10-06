"""The minimal console: the WIP board, the enquiry desk and the WhatsApp chat
manager, and nothing else offered in the rail.

The client asked for three screens rather than fifteen, so the other entries are
deleted from the navigation model outright — hidden behind a flag was only the
first cut, and the flag could always be flipped back. ``LITE_MODE`` is still what
the shell opens on and what the phone shows, so these tests pin both halves: the
switch ships on, it reaches the shell as ``lite``, and the front end acts on it.
"""
from __future__ import annotations

from pathlib import Path

from config import Config

ROOT = Path(__file__).resolve().parent.parent


def test_lite_mode_is_on_by_default():
    """The client asked for the three-screen console, so that is what ships."""
    assert Config.LITE_MODE is True


def test_the_shell_carries_the_lite_flag(auth_client):
    """Sent inside the bootstrap payload, so the rail is right on the first paint
    rather than after a round trip."""
    html = auth_client.get("/app").get_data(as_text=True)
    assert '"lite"' in html


def test_the_front_end_acts_on_the_flag():
    """A flag nothing reads is not a feature: the rail has to know the shipped
    screens and the phone has to get its tab bar."""
    appjs = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "LITE_ROUTES" in appjs, "the shipped screens are not named anywhere"
    assert "'/board', '/inbox', '/bookings'" in appjs
    assert "tc-tabbar" in appjs, "the phone's tab bar is missing"


def test_the_other_nav_items_are_deleted_not_hidden():
    """The rail must not still carry what the client asked to be rid of.

    Reading the model is the only way to tell "hidden from the rail" apart from
    "gone", and only the second one survives somebody flipping a flag back. So the
    entries are checked against the ``NAV`` literal itself, not the rendered rail.
    """
    appjs = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    start = appjs.index("const NAV = [")
    nav = appjs[start:appjs.index("const LITE_ROUTES", start)]

    assert "route: '/board'" in nav and "route: '/inbox'" in nav
    assert "route: '/bookings'" in nav, "the enquiry desk was asked for and is gone"
    for route in ("/dashboard", "/quotations", "/jobs", "/todo",
                  "/customers", "/vehicles", "/payments", "/invoices", "/reports",
                  "/parts", "/activity", "/staff"):
        assert f"route: '{route}'" not in nav, f"{route} is still a rail item"


def test_the_phone_affordances_exist():
    """Screens that only work with a mouse are not an exceptional mobile view.

    LITE_MODE drops the off-canvas toggle, so the tab bar is the only way between
    screens on a phone: every shipped route needs a tab of its own or it is
    stranded there.
    """
    css = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".tc-tabbar" in css and ".job-card-move" in css and ".chat-back" in css

    appjs = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    tabs = appjs[appjs.index("const mobileTabs"):]
    tabs = tabs[:tabs.index(".map(")]
    for route in ("/board", "/inbox", "/bookings"):
        assert f"route: '{route}'" in tabs, f"{route} has no phone tab to reach it"

    board = (ROOT / "app" / "static" / "js" / "views" / "board.js").read_text(encoding="utf-8")
    assert "job-card-move" in board, "the board has no touch way to move a card"

    inbox = (ROOT / "app" / "static" / "js" / "views" / "inbox.js").read_text(encoding="utf-8")
    assert "chat-mobile-open" in inbox, "the inbox does not switch panes on a phone"
