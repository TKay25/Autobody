"""The offline plumbing: the worker, the manifest, and what they must not do.

The behaviour of a service worker cannot be exercised by pytest — it runs in the
browser, and the embedded browser used during development does not start service
workers at all (it exposes the API, reports the registration, and never executes
the script). So these tests pin the parts that *are* testable and that have
already been got wrong once:

* the worker is served from the ROOT, because a worker's scope is capped at the
  path it is served from and a copy under `/static/` can only control
  `/static/*` — useless for caching the shell or the API;
* it is never cached, because a stale worker is the one asset that survives every
  cache-busting trick and then serves the old code that asks for the old files;
* it never intercepts a write, because the app has its own outbox and two things
  racing to send the same payment is exactly what the idempotency keys exist to
  prevent;
* the manifest parses and every icon it names exists.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import current_app

WANTED_HEADERS = {
    "/sw.js": {
        "cache-control": "no-cache, no-store, must-revalidate",
        "service-worker-allowed": "/",
    },
}


def test_the_worker_is_served_from_the_root_not_from_static(client):
    """Scope is decided by the URL it is served from, not by a header request."""
    served = client.get("/sw.js")
    assert served.status_code == 200
    assert "javascript" in served.headers["Content-Type"]

    for header, expected in WANTED_HEADERS["/sw.js"].items():
        assert served.headers.get(header) == expected, (header, served.headers.get(header))


def test_the_worker_is_never_cached_by_the_browser(client):
    """A cached worker is the one thing that can outlive every cache-buster."""
    cache_control = client.get("/sw.js").headers.get("Cache-Control", "")
    assert "no-store" in cache_control
    assert "no-cache" in cache_control


def test_the_worker_is_reachable_without_signing_in(client):
    """The browser fetches it on page load; a 302 to /login would break install."""
    response = client.get("/sw.js", follow_redirects=False)
    assert response.status_code == 200


def test_the_worker_never_intercepts_a_write(app):
    """The outbox owns writes. This is the guard that keeps it that way.

    Read as source on purpose: the failure mode is silent — a worker that also
    handled POSTs would send every queued change a second time, from the other
    side of the app, and the duplicate would look like a user error.
    """
    source = _strip_comments(
        (Path(app.static_folder) / "sw.js").read_text(encoding="utf-8"))
    # The event handler must bail out before it ever calls respondWith.
    fetch_header = source.split("self.addEventListener('fetch'", 1)[1]
    guard = re.search(r"if \(request\.method !== 'GET'\) return;", fetch_header)
    assert guard, "the fetch handler no longer refuses non-GET requests"

    first_respond = fetch_header.index("respondWith")
    assert fetch_header.index("request.method !== 'GET'") < first_respond, (
        "the non-GET guard must come before the first respondWith"
    )


def _strip_comments(source: str) -> str:
    """Code only — the explanation of a trap often names the trap."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"(?m)//.*$", " ", source)


def test_the_worker_has_a_deadline_on_its_precache(app):
    """A hanging CDN must not stop the app from ever becoming offline-capable.

    This was a real bug: `cache.add()` has no timeout, so a CDN that accepted the
    connection and never answered left `install` pending for ever — the worker
    never activated, `register()` never settled, and the app silently stayed
    online-only with no error anywhere.
    """
    code = _strip_comments((Path(app.static_folder) / "sw.js").read_text(encoding="utf-8"))
    assert "AbortController" in code
    assert "cache.add(" not in code, (
        "cache.add() has no timeout — use the deadline helper instead"
    )


def test_the_manifest_parses_and_names_real_icons(app):
    static = Path(app.static_folder)
    manifest = json.loads((static / "manifest.webmanifest").read_text(encoding="utf-8"))

    assert manifest["start_url"] == "/app"
    # Scope "/" so an installed app covers the whole console, not one subtree.
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"], "the installed window needs a title bar colour"

    assert manifest["icons"], "an installable app needs at least one icon"
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/static/"), icon
        assert (static / icon["src"].replace("/static/", "", 1)).exists(), icon["src"]
    # A maskable icon is the one Android crops; without it the mark gets clipped.
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])


def test_the_shell_links_the_manifest_and_registers_the_worker(auth_client):
    page = auth_client.get("/app").get_data(as_text=True)
    assert 'rel="manifest"' in page
    assert "manifest.webmanifest" in page
    # Registered against /sw.js, not /static/sw.js — see the scope note above.
    assert "serviceWorker.register('/sw.js'" in page


def test_the_shell_still_loads_when_the_worker_cannot_register(auth_client):
    """Registration is wrapped, so a browser with no worker still boots."""
    page = auth_client.get("/app").get_data(as_text=True)
    assert "'serviceWorker' in navigator" in page
