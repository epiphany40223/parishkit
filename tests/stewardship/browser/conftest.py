"""Serve actual shared templates/assets locally; never contact a real provider."""

import os
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string

from parishkit.stewardship.web.security import CSP

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def browser_engine(request):
    """An explicitly enabled browser job fails if tools/engines are missing."""
    if os.environ.get("PARISHKIT_RUN_BROWSER_TESTS") != "1":
        pytest.skip("Browser component tests require PARISHKIT_RUN_BROWSER_TESTS=1.")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runner:
        browser = getattr(runner, request.param).launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser_engine):
    """Each scenario has isolated browser state and an explicit non-parish zone."""
    context = browser_engine.new_context(
        timezone_id="America/Los_Angeles", reduced_motion="reduce"
    )
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture(scope="module")
def component_origin():
    """An exact response allowlist avoids exposing source files through the server."""
    context = {
        "server_now": NOW,
        "deadline": NOW + timedelta(hours=1),
        "absolute_deadline": NOW + timedelta(hours=4),
        "csrf_token": "a" * 64,
    }
    responses = {
        "/login": ("text/html", render_to_string("stewardship/login.html", context)),
        "/family-login": (
            "text/html",
            render_to_string("stewardship/family-login.html", context),
        ),
        "/family": ("text/html", render_to_string("stewardship/family.html", context)),
        "/errors": (
            "text/html",
            render_to_string(
                "stewardship/family-login.html",
                {
                    **context,
                    "errors": [
                        {"field_id": "family-code", "message": "Check the Family code."}
                    ],
                },
            ),
        ),
    }
    for name, kind in (("css", "text/css"), ("js", "application/javascript")):
        asset = f"stewardship/ui-v1.{name}"
        responses[f"/static/{asset}"] = (kind, Path(finders.find(asset)).read_text())

    class Handler(BaseHTTPRequestHandler):
        """Suppress raw request logging; unknown routes are intentionally empty."""

        def do_GET(self):
            """Serve only exact pre-rendered component fixtures with actual CSP."""
            kind, body = responses.get(self.path, ("text/plain", ""))
            self.send_response(200 if self.path in responses else 404)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            """Fixture HTTP traffic must not generate private request diagnostics."""

        def do_POST(self):
            """Fixtures never issue external redirects, even to synthetic identities."""
            self.send_response(405)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def axe_source():
    """Pinned test-only accessibility scanner, not a browser-delivered dependency."""
    path = Path(__file__).parent / "node_modules/axe-core/axe.min.js"
    assert path.is_file(), "Run npm ci --prefix tests/stewardship/browser."
    return path.read_text()
