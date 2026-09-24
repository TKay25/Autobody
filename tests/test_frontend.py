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


def _problems_for(tmp_path: Path, source: str):
    (tmp_path / "sample.js").write_text(source, encoding="utf-8")
    return check_all(tmp_path)


def test_a_name_declared_twice_in_one_block_is_reported(tmp_path):
    """A duplicate `const` is a SyntaxError, and it blanks the entire screen.

    This happened on the dashboard: two `const flow` lines in one function took
    the whole route down with "Page not found", and the cause was nowhere near
    the symptom. A bracket counter cannot see it, so it is checked explicitly.
    """
    problems = _problems_for(tmp_path, "const flow = 1;\nconst flow = 2;\n")
    assert problems, "a duplicate const was not reported"
    assert "twice" in problems[0].message
    assert problems[0].line == 2


def test_the_same_name_in_two_sibling_blocks_is_allowed(tmp_path):
    """Block scoping means this is legal, so it must not be reported."""
    source = "if (a) { const x = 1; }\nif (b) { const x = 2; }\n"
    assert _problems_for(tmp_path, source) == []


def test_a_shadowed_declaration_in_an_inner_block_is_allowed(tmp_path):
    source = "const x = 1;\nfunction f() { const x = 2; return x; }\n"
    assert _problems_for(tmp_path, source) == []


def test_a_duplicate_inside_a_template_interpolation_is_reported(tmp_path):
    source = "const s = `${(() => { const q = 1; const q = 2; return q; })()}`;\n"
    problems = _problems_for(tmp_path, source)
    assert problems, "a duplicate inside a ${ } interpolation was missed"


def test_a_declaration_keyword_inside_a_string_is_not_a_declaration(tmp_path):
    """`const` in a string or a template is just text, not a binding."""
    source = 'const a = "const b = 1";\nconst c = `let d = 2`;\nconst e = /const f/;\n'
    assert _problems_for(tmp_path, source) == []


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


def test_form_modal_submit_button_is_associated_with_its_form():
    """The footer sits outside the <form>, so the button has to name it.

    Without that association the button is orphaned and clicking it does nothing
    at all — every formModal in the app (payments, staff, customers, parts) was
    silently unsubmittable, which no browser-free test would otherwise catch.
    `form` is a read-only property on a button, so it must go on as an attribute.
    """
    src = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "submit.setAttribute('form', formId)" in src, (
        "formModal's submit button is not associated with its form — "
        "clicking Save will do nothing"
    )


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


def test_identity_lives_in_the_top_bar():
    """The user menu and sign out belong in the top bar, not the sidebar foot."""
    appjs = (JS_DIR / "app.js").read_text(encoding="utf-8")
    assert "userWrap" in appjs, "the top bar should render the user menu"
    assert "tc-topbar-sep" in appjs
    assert "userRow" not in appjs, "the sidebar user row was replaced by the top-bar menu"


def test_quick_actions_strip_is_wired():
    """Quick actions render as cards under the top bar, styled by the kit."""
    appjs = (JS_DIR / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "QUICK_ACTIONS" in appjs
    assert "tc-quickbar" in appjs, "the layout must mount the strip"
    for cls in (".tc-quickbar", ".tc-quick-card", ".tc-quick-icon", ".tc-quick-label"):
        assert cls in css, f"{cls} is missing from the stylesheet"


@pytest.mark.parametrize("colour", ["brand", "primary", "success", "warning", "danger"])
def test_stage_colours_are_known_bootstrap_slots(colour):
    """Stage badges must map to a real utility class, not a made-up colour."""
    from app.constants import STAGE_COLOURS

    allowed = {
        "primary", "secondary", "success", "danger", "warning", "info", "light",
        "dark", "brand",
    }
    assert set(STAGE_COLOURS.values()) <= allowed
