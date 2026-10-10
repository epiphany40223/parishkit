"""Typed errors: HTML pages for browser requests, JSON for scripts."""

import json

import pytest
from django.db import DatabaseError
from django.test import RequestFactory, override_settings

from parishkit.stewardship.accounts.admin_editing import (
    error_response,
    step_up_response,
)
from parishkit.stewardship.accounts.sessions import FreshAuthenticationRequired
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web import error_pages
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.error_pages import BrowserErrorMiddleware, page_request

PAGE = {"HTTP_ACCEPT": "text/html,*/*;q=0.8", "HTTP_SEC_FETCH_MODE": "navigate"}


def through(request, response):
    """Run one response through the middleware as the view's result."""
    return BrowserErrorMiddleware(lambda _: response)(request)


@pytest.mark.parametrize(
    "headers,expected",
    [
        (PAGE, True),
        ({"HTTP_SEC_FETCH_MODE": "navigate"}, True),
        ({"HTTP_ACCEPT": "text/html"}, True),
        ({"HTTP_ACCEPT": "application/json", "HTTP_SEC_FETCH_MODE": "cors"}, False),
        ({"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "same-origin"}, False),
        ({"HTTP_ACCEPT": "application/json"}, False),
        ({"HTTP_ACCEPT": "*/*"}, False),
        ({}, False),
        ({**PAGE, "HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}, False),
    ],
)
def test_page_request_negotiation(headers, expected):
    """Navigations and form posts are pages; fetch/XHR and bare clients are not."""
    assert page_request(RequestFactory().get("/admin/", **headers)) is expected


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_ACCEPT": "application/json", "HTTP_SEC_FETCH_MODE": "cors"},
        {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest", **PAGE},
        {},
    ],
)
def test_scripts_keep_the_json_contract(headers):
    """Script callers receive the unchanged closed JSON, now varying on Accept."""
    request = RequestFactory().post("/admin/rules/autosave", **headers)
    response = through(
        request, validation_response([FieldError(ErrorCode.STALE)], status=409)
    )
    assert response.status_code == 409
    assert json.loads(response.content)["errors"][0]["code"] == "stale_version"
    assert "Accept" in response["Vary"]


@pytest.mark.parametrize(
    "code,status,title",
    [
        (ErrorCode.INVALID, 400, "Check your entries"),
        (ErrorCode.STALE, 409, "This information changed"),
        (ErrorCode.UNAVAILABLE, 503, "Temporarily unavailable"),
        (ErrorCode.DENIED, 403, "Access unavailable"),
        (ErrorCode.UNAVAILABLE, 404, "Page unavailable"),
    ],
)
def test_browser_pages_show_closed_messages_and_next_steps(code, status, title):
    """A person sees a titled page with the safe message and a way onward."""
    request = RequestFactory().get("/admin/integrations", **PAGE)
    original = validation_response([FieldError(code, "name")], status=status)
    response = through(request, original)
    body = response.content.decode()
    assert response.status_code == status
    assert response["Content-Type"].startswith("text/html")
    assert response["Cache-Control"] == "no-store"
    assert response.stewardship_safe_error
    assert title in body
    assert json.loads(original.content)["errors"][0]["message"] in body
    assert 'href="/admin/"' in body
    assert '"errors"' not in body
    assert (response.get("Retry-After") == "5") is (status == 503)
    assert ('href="/admin/login"' in body) is (code == ErrorCode.DENIED)


def test_fresh_authentication_offers_confirm_with_google():
    """The step-up form posts to sign-in with the current page as next."""
    request = RequestFactory().get("/admin/setup/credentials/slack", **PAGE)
    response = through(request, error_response(FreshAuthenticationRequired()))
    body = response.content.decode()
    assert response.status_code == 403
    assert "you with Google before this action" in body
    assert 'action="/admin/login"' in body
    assert 'name="next" value="/admin/setup/credentials/slack"' in body
    assert 'name="csrfmiddlewaretoken"' in body
    assert "Confirm with Google" in body


@pytest.mark.parametrize("outage", [False, True])
def test_local_step_up_never_mentions_google(monkeypatch, outage):
    """LOCAL's step-up page names the laptop command, not Google (#619).

    The outage fallback renders without context processors, so the page's
    own context must carry LOCAL's flag.
    """
    real = error_pages.render_to_string

    def render(name, context, request=None):
        """Fail the chrome render, as a database outage would."""
        if outage and request is not None:
            raise DatabaseError("outage")
        return real(name, context, request=request)

    monkeypatch.setattr(error_pages, "render_to_string", render)
    request = RequestFactory().get("/admin/setup/credentials/slack", **PAGE)
    with override_settings(STEWARDSHIP_DEPLOYMENT_PROFILE=DeploymentProfile.LOCAL):
        response = through(request, error_response(FreshAuthenticationRequired()))
    body = response.content.decode()
    assert response.status_code == 403
    assert "Google" not in body
    assert "confirm your sign-in before this action" in body
    assert "tools/stewardship-local.sh sign-in" in body


@pytest.mark.parametrize("kept", [False, True])
def test_refused_gated_post_says_nothing_was_done(kept):
    """A stale-sign-in refusal of a submitted form never reads like a success.

    It says plainly that nothing happened, and says whether the view kept the
    Admin's choices for after the step-up or they must be entered again.
    """
    request = RequestFactory().post("/admin/campaign/content/test/y/families/", **PAGE)
    response = error_response(FreshAuthenticationRequired())
    if kept:
        response.stewardship_inputs_kept = True
    body = through(request, response).content.decode()
    assert "Nothing was done or sent." in body
    assert ("will be shown again when you return" in body) is kept
    assert ("enter it again after you return" in body) is not kept
    assert 'name="next" value="/admin/campaign/content/test/y/families/"' in body


@pytest.mark.parametrize(
    "named,expected",
    [
        (
            "/admin/reports/c/families/?mailing=yes",
            "/admin/reports/c/families/?mailing=yes",
        ),
        ("https://example.org/admin/", "/admin/"),
        ("/admin/login", "/admin/"),
    ],
)
def test_post_only_routes_return_to_the_form_page(named, expected):
    """A POST-only route's step-up returns to the page its form came from (#547).

    The named page is still revalidated: anything but a same-origin Admin page
    returns to the Admin home, never to the POST URL.
    """
    request = RequestFactory().post("/admin/reports/c/families/export", **PAGE)
    body = through(
        request, step_up_response(named, "Active parishioner family directory")
    ).content
    body = body.decode()
    assert "Nothing was done or sent." in body
    assert f'name="next" value="{expected}"' in body
    # The page names where the step-up returns, since it is not this page.
    assert "You will then return to Active parishioner family directory." in body
    assert "return to this page" not in body


def test_fresh_authentication_for_scripts_stays_a_json_denial():
    """The step-up marker changes nothing for fetch callers."""
    response = error_response(FreshAuthenticationRequired())
    assert response.status_code == 403
    assert response.stewardship_reauthenticate
    request = RequestFactory().post("/admin/x", HTTP_ACCEPT="application/json")
    assert json.loads(through(request, response).content)["errors"][0]["code"] == (
        "denied"
    )


def test_return_links_are_same_origin_admin_pages_only():
    """POSTs return to their form; GETs to a same-origin Admin Referer only."""
    posted = through(
        RequestFactory().post("/admin/parish", **PAGE),
        error_response(ValueError("private submitted text")),
    ).content.decode()
    assert 'href="/admin/parish"' in posted
    assert "private submitted text" not in posted
    for referer, shown in [
        ("http://testserver/admin/integrations", True),
        ("https://attacker.example/admin/integrations", False),
        ("http://testserver/admin/../family", False),
    ]:
        body = through(
            RequestFactory().get("/admin/credentials/x", HTTP_REFERER=referer, **PAGE),
            error_response(StaleRecordError("private")),
        ).content.decode()
        assert ('href="/admin/integrations"' in body) is shown
        assert "attacker" not in body


def test_lookup_errors_render_not_found_pages():
    """Admin LookupError keeps its 404 JSON and becomes a readable 404 page."""
    response = error_response(LookupError("private"))
    assert response.status_code == 404
    assert json.loads(response.content)["errors"][0]["code"] == "unavailable"
    page = through(RequestFactory().get("/admin/x", **PAGE), response)
    assert page.status_code == 404 and "Page unavailable" in page.content.decode()


def test_family_pages_link_only_to_the_family_home():
    """Outside /admin/ no Admin link, sign-in or step-up form is offered."""
    request = RequestFactory().get("/family/section", **PAGE)
    response = error_response(FreshAuthenticationRequired())
    body = through(request, response).content.decode()
    assert "/admin/" not in body
    assert 'href="/"' in body


@pytest.mark.parametrize(
    "kind,status",
    [
        ("UserFacingError", 400),
        ("UserFacingStale", 409),
        ("UserFacingMissing", 404),
        ("UserFacingDenied", 403),
    ],
)
def test_user_facing_refusals_explain_and_link_the_fix(kind, status):
    """A reviewed refusal keeps its status and shows its message, fix and link."""
    from parishkit.stewardship.web import refusals

    error = getattr(refusals, kind)(
        "The template is in use.",
        fix="Change the schedule first.",
        link="/admin/setup/schedules",
        link_label="Go to Mail schedules",
    )
    response = error_response(error)
    assert response.status_code == status
    assert json.loads(response.content)["refusal"] == {
        "message": "The template is in use.",
        "fix": "Change the schedule first.",
        "link": {"url": "/admin/setup/schedules", "label": "Go to Mail schedules"},
    }
    page = through(RequestFactory().post("/admin/setup/content", **PAGE), response)
    body = page.content.decode()
    assert page.status_code == status and page.stewardship_safe_error
    assert "The template is in use." in body and "Change the schedule first." in body
    assert '<a href="/admin/setup/schedules">Go to Mail schedules</a>' in body
    # The closed generic message is replaced, not repeated.
    assert "Check this value." not in body


def test_plain_errors_keep_the_closed_generic_text():
    """An ordinary ValueError never exposes its text and has no refusal field."""
    response = error_response(ValueError("secret detail"))
    assert "refusal" not in json.loads(response.content)
    body = through(RequestFactory().get("/admin/x", **PAGE), response).content
    assert b"secret detail" not in body and b"Check this value." in body


def test_standard_refusals_and_link_validation():
    """Stale and unexpected-field refusals are static; links must be same-origin."""
    from parishkit.stewardship.web.refusals import (
        UserFacingError,
        stale_page,
        unexpected_fields,
    )

    stale = error_response(stale_page())
    assert stale.status_code == 409
    assert "another tab" in json.loads(stale.content)["refusal"]["message"]
    invalid = json.loads(error_response(unexpected_fields()).content)
    assert invalid["refusal"]["fix"] == "Reload the page and try again."
    assert invalid["refusal"]["link"] is None
    for link, label in (
        ("https://example.org/", "Elsewhere"),
        ("//example.org/", "Elsewhere"),
        ("/admin/setup", None),
        (None, "Label"),
    ):
        with pytest.raises(TypeError):
            UserFacingError("Message", link=link, link_label=label)


# Untyped 404s on portal addresses (#927): an unknown address or Http404.
JSON = {"HTTP_ACCEPT": "application/json", "HTTP_SEC_FETCH_MODE": "cors"}
SECRET = "/admin/no-such-page-private?token=private-query"


def _assert_no_page(response, *, admin=True):
    """The styled not-found page: 404, closed text, nothing from the address."""
    body = response.content.decode()
    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")
    assert response["Cache-Control"] == "no-store"
    assert "<h1>Page not found</h1>" in body
    assert "There is no page at this address." in body
    assert "Check the address, or use the links below to continue." in body
    assert "private" not in body
    assert "Check this value." not in body
    assert ('class="js-required"' in body) is admin
    return body


@pytest.mark.parametrize("debug", [False, True])
def test_unknown_admin_address_shows_the_styled_page(client, debug):
    """No URL pattern matches: a person sees the Admin page, never plain text.

    Under DEBUG Django skips handler404 and builds its own trace page; the
    middleware replaces both, so the path and query are never echoed.
    """
    with override_settings(DEBUG=debug):
        response = client.get(SECRET, **PAGE)
    body = _assert_no_page(response)
    assert 'href="/admin/">Administration home</a>' in body
    # Signed out: the minimal Admin layout, with no menu.
    assert "admin-sidebar" not in body
    # The outer security envelope still applies.
    assert response["X-Frame-Options"] == "DENY"


def test_unknown_admin_address_answers_scripts_with_typed_json(client):
    """A script gets the closed JSON refusal instead of plain text."""
    response = client.get(SECRET, **JSON)
    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert "Accept" in response["Vary"]
    body = json.loads(response.content)
    assert body["errors"][0]["code"] == "invalid"
    assert body["refusal"]["message"] == "There is no page at this address."
    assert "private" not in response.content.decode()


def test_raised_http404_becomes_the_styled_page():
    """A view's Http404 (Django's own 404 response) is styled too."""
    from django.http import HttpResponseNotFound

    request = RequestFactory().get("/admin/reports/lists/x/", **PAGE)
    _assert_no_page(through(request, HttpResponseNotFound(b"Not Found")))


def test_typed_not_found_keeps_its_own_title():
    """A view's typed 404 already says something specific; it is not replaced."""
    request = RequestFactory().get("/admin/x", **PAGE)
    response = through(request, error_response(LookupError("private")))
    body = response.content.decode()
    assert "Page unavailable" in body and "Page not found" not in body


@pytest.mark.parametrize(
    "path",
    [
        "/static/stewardship/missing-v1.css",
        "/branding/missing.png",
        "/missing-private",
        "/health/missing",
    ],
)
def test_non_portal_not_found_stays_plain(client, path):
    """Assets, probes and stray addresses keep the plain normalized 404."""
    response = client.get(path, **PAGE)
    assert response.status_code == 404
    assert response.content == b"Not Found\n"


def test_chrome_check_is_read_only_and_needs_a_session(monkeypatch):
    """Only a request with a session cookie is checked, read-only, for chrome.

    A failure of that check leaves the minimal layout, never another error.
    """
    from types import SimpleNamespace

    from django.http import HttpResponseNotFound

    from parishkit.stewardship.accounts import authentication, sessions
    from parishkit.stewardship.accounts.limiting import LimiterUnavailable
    from parishkit.stewardship.web import error_pages

    calls = []
    restored = []
    monkeypatch.setattr(error_pages, "_restore_review", lambda: bool(restored))

    def check(request, *, store, read_only=False):
        """Record the call, then fail like an outage would."""
        calls.append((store, read_only))
        raise LimiterUnavailable("outage")

    monkeypatch.setattr(sessions, "authenticated_admin", check)
    monkeypatch.setattr(
        authentication, "runtime", lambda: SimpleNamespace(store="store")
    )
    signed_out = RequestFactory().get("/admin/missing", **PAGE)
    signed_out.session = SimpleNamespace(session_key=None)
    _assert_no_page(through(signed_out, HttpResponseNotFound()))
    assert calls == []
    signed_in = RequestFactory().get("/admin/missing", **PAGE)
    signed_in.session = SimpleNamespace(session_key="key")
    _assert_no_page(through(signed_in, HttpResponseNotFound()))
    assert calls == [("store", True)]
    # During a restore review the gate closes every real page, so the
    # not-found page skips the check and keeps the menu-less layout.
    restored.append(True)
    under_review = RequestFactory().get("/admin/missing", **PAGE)
    under_review.session = SimpleNamespace(session_key="key")
    _assert_no_page(through(under_review, HttpResponseNotFound()))
    assert calls == [("store", True)]


def test_unknown_family_address_shows_the_family_page(client):
    """A Family address gets the same page in the Family layout, linking home."""
    response = client.get("/family/no-such-page-private?code=private", **PAGE)
    body = _assert_no_page(response, admin=False)
    assert 'href="/">Family portal home</a>' in body
    assert "/admin/" not in body
    script = client.get("/family/no-such-page-private", **JSON)
    assert script.status_code == 404
    assert json.loads(script.content)["refusal"]["message"] == (
        "There is no page at this address."
    )
