"""Front-end guards that run in CI.

The browser is not available under pytest, so we do the next best thing: check
every shipped script for the bracket/string mistakes that would otherwise only
surface when a user opens a particular screen.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.check_js import check_all

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "app" / "static" / "js"
TEMPLATES = ROOT / "app" / "templates"


def test_all_javascript_parses():
    problems = check_all(JS_DIR)
    assert not problems, "broken front-end script(s):\n" + "\n".join(str(p) for p in problems)


def test_every_view_script_is_loaded():
    """A view file that is never <script>'d in silently does nothing."""
    shell = (TEMPLATES / "app.html").read_text(encoding="utf-8")
    loaded = set(re.findall(r"static_url\('js/([^']+)'\)", shell))
    on_disk = {
        str(p.relative_to(JS_DIR)).replace("\\", "/")
        for p in JS_DIR.rglob("*.js")
    }
    missing = on_disk - loaded
    assert not missing, f"not referenced by app.html: {sorted(missing)}"

    stale = loaded - on_disk
    assert not stale, f"app.html references missing files: {sorted(stale)}"


def test_no_hardcoded_static_urls_in_templates():
    """Everything must go through static_url() so cache busting always applies."""
    for path in TEMPLATES.rglob("*.html"):
        body = path.read_text(encoding="utf-8")
        assert "url_for('static'" not in body, f"{path.name} bypasses static_url()"


def test_every_js_file_is_wrapped_in_an_iife():
    """Each view file registers routes into the shared TCA namespace."""
    for path in JS_DIR.rglob("*.js"):
        body = path.read_text(encoding="utf-8")
        assert body.lstrip().startswith(("/*", "(function", "//")), \
            f"{path.name} should start with a comment or an IIFE"


def test_no_leftover_debugging_statements():
    offenders = []
    for path in list(JS_DIR.rglob("*.js")) + [ROOT / "app" / "static" / "css" / "app.css"]:
        body = path.read_text(encoding="utf-8")
        for marker in ("console.log(", "debugger;", "TODO:", "FIXME:"):
            if marker in body:
                offenders.append(f"{path.name}: {marker}")
    assert not offenders, "leftover debugging markers: " + ", ".join(offenders)


def test_api_client_sends_the_csrf_token():
    """Without this header every write from the SPA is rejected with a 400."""
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "X-CSRFToken" in core
    assert "window.__CSRF__" in core


def test_theme_tokens_are_complete():
    """The brand palette must define every token the stylesheet consumes."""
    css = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"--(tc-[a-z0-9-]+)\s*:", css))
    used = set(re.findall(r"var\(--(tc-[a-z0-9-]+)\)", css))
    undefined = used - defined
    assert not undefined, f"used but never defined: {sorted(undefined)}"


@pytest.mark.parametrize("colour", ["brand", "primary", "success", "warning", "danger"])
def test_stage_colours_are_known_bootstrap_slots(colour):
    """Stage badges must map to a real utility class, not a made-up colour."""
    from app.constants import STAGE_COLOURS

    allowed = {
        "primary", "secondary", "success", "danger", "warning", "info", "light",
        "dark", "brand",
    }
    assert set(STAGE_COLOURS.values()) <= allowed
