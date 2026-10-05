"""The Admin portal requires JavaScript; the Family portal does not (#565).

Every Admin page, including the sign-in and setup pages, extends
``admin-base.html``, which renders the page behind a ``js-required`` class and
a "needs JavaScript" panel that ``admin-gate-v1.js`` removes. Family pages
extend the ungated ``base.html``. The browser behavior is checked in
``browser/test_admin_javascript_gate.py``.
"""

import re
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.web.error_pages import BrowserErrorMiddleware
from parishkit.stewardship.web.security import login_denial

STEWARDSHIP = Path(__file__).parents[2] / "src/parishkit/stewardship"
TEMPLATES = STEWARDSHIP / "accounts/templates/stewardship"
STATIC = STEWARDSHIP / "accounts/static/stewardship"

ADMIN_BASE = "{% extends 'stewardship/admin-base.html' %}"
FAMILY_BASE = "{% extends 'stewardship/base.html' %}"
# Pages rendered by both portals pick their base from the server's admin flag.
SHARED_BASE = (
    "{% extends admin|yesno:'stewardship/admin-base.html,stewardship/base.html' %}"
)
# Family and public pages: never gated, parishioners may have no JavaScript.
FAMILY_PAGES = {
    "family.html",
    "family-login.html",
    "family-maintenance.html",
    "family-unavailable.html",
    "hosted-file-unavailable.html",
}
# Pages both portals render: error pages, denials and the availability gate.
SHARED_PAGES = {"availability.html", "denied.html", "error.html"}
# The spec's wording: a strong first sentence (not a heading, so the hidden
# panel never adds a second h1 to a page) and the instruction.
PANEL = (
    "<p><strong>The Admin portal needs JavaScript.</strong></p>",
    "<p>Turn it on in your browser settings, then reload this page.</p>",
)
EXTENDS = re.compile(r"\{% extends [^%]*%\}")


def _extends(path):
    """The template's leading extends tag, or None for a fragment or the base."""
    match = EXTENDS.match(path.read_text(encoding="utf-8"))
    return match.group(0) if match else None


def _pages():
    """Every template that is a full page, by name: it extends a base."""
    return {
        path.name: tag
        for path in sorted(TEMPLATES.rglob("*.html"))
        if (tag := _extends(path)) is not None and path.name != "admin-base.html"
    }


def test_every_admin_template_extends_the_gated_base():
    """A new Admin page cannot skip the gate by extending base.html."""
    pages = _pages()
    assert pages.keys() >= FAMILY_PAGES | SHARED_PAGES
    wrong = {
        name: tag
        for name, tag in pages.items()
        if name not in FAMILY_PAGES | SHARED_PAGES and tag != ADMIN_BASE
    }
    assert not wrong, f"Admin pages must extend admin-base.html: {wrong}"
    # The sign-in, local sign-in and setup pages are gated too.
    for name in ("login.html", "local-sign-in.html", "setup.html"):
        assert pages[name] == ADMIN_BASE


def test_family_templates_are_not_gated():
    """Family pages keep the ungated base; shared pages choose by portal."""
    pages = _pages()
    assert {name: pages[name] for name in FAMILY_PAGES} == dict.fromkeys(
        FAMILY_PAGES, FAMILY_BASE
    )
    assert {name: pages[name] for name in SHARED_PAGES} == dict.fromkeys(
        SHARED_PAGES, SHARED_BASE
    )
    # Only the gated base and the full pages wrap the root <html> element.
    roots = {
        path.name
        for path in TEMPLATES.rglob("*.html")
        if "<html" in path.read_text(encoding="utf-8")
    }
    assert roots == {"base.html"}
    assert "js-required" not in (TEMPLATES / "base.html").read_text()


def _gated(html):
    """Whether rendered HTML carries every part of the JavaScript gate."""
    parts = (
        '<html lang="en" class="js-required">' in html,
        '<script src="/static/stewardship/admin-gate-v1.js"></script>' in html,
        # hidden keeps the panel out of a page whose stylesheet failed.
        '<div class="js-required-panel" hidden>' in html,
        *(part in html for part in PANEL),
    )
    assert all(parts) or not any(parts), parts
    return all(parts)


@pytest.mark.parametrize("name", sorted(SHARED_PAGES))
def test_shared_pages_gate_only_admin_responses(name):
    """An Admin error, denial or availability page is gated; a Family one is not."""
    context = {"title": "Error", "kind": "", "retry_path": "/", "setup": True}
    assert _gated(render_to_string(f"stewardship/{name}", context | {"admin": True}))
    assert not _gated(
        render_to_string(f"stewardship/{name}", context | {"admin": False})
    )
    assert not _gated(render_to_string(f"stewardship/{name}", context))


def test_servers_pass_the_portal_to_shared_pages():
    """Error pages and denials are gated under /admin/ and nowhere else."""
    page = {"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "navigate"}

    def error(path):
        """The browser error page the middleware renders for a lookup failure."""
        request = RequestFactory().get(path, **page)
        response = error_response(LookupError("private"))
        return BrowserErrorMiddleware(lambda _: response)(request).content.decode()

    assert _gated(error("/admin/x"))
    assert not _gated(error("/family/section"))
    assert _gated(login_denial(admin=True).content.decode())
    assert not _gated(login_denial(kind="code").content.decode())


def test_rendered_admin_page_is_gated_and_family_page_is_not():
    """The gate script loads in <head> before the body, without defer or async."""
    html = render_to_string("stewardship/login.html", {})
    assert _gated(html)
    # The always-present panel adds no heading: the page keeps its one h1.
    assert html.count("<h1") == 1
    head, body = html.split("<body", 1)
    assert "admin-gate-v1.js" in head and "admin-gate-v1.js" not in body
    assert body.index("js-required-panel") < body.index('class="site-header"')
    # The content security policy forbids inline script and style.
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "style=" not in html
    assert not _gated(render_to_string("stewardship/family-login.html", {}))


def test_gate_script_and_stylesheet_rules():
    """The script removes the class; the stylesheet hides all but the panel."""
    script = (STATIC / "admin-gate-v1.js").read_text(encoding="utf-8")
    assert 'document.documentElement.classList.remove("js-required");' in script
    css = (STATIC / "ui-v1.css").read_text(encoding="utf-8")
    assert ".js-required-panel { display: none; }" in css
    assert (
        "html.js-required body > :not(.js-required-panel) { display: none !important; }"
    ) in css
    # !important outranks the stylesheet's [hidden] rule while the class is on.
    assert "[hidden] { display: none !important; }" in css
    assert "html.js-required .js-required-panel { display: block !important;" in css
