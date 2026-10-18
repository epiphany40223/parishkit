"""Typed errors: HTML pages for browser requests, JSON for scripts."""

import json

import pytest
from django.db import DatabaseError
from django.test import RequestFactory, override_settings

from parishkit.stewardship.accounts.admin_editing import error_response
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


def test_a_denial_offers_sign_in_and_no_step_up():
    """A refused Admin request links to sign-in; no page asks to confirm (#1547)."""
    request = RequestFactory().post("/admin/setup/credentials/parishsoft", **PAGE)
    response = through(request, error_response(PermissionError("private")))
    body = response.content.decode()
    assert response.status_code == 403
    assert 'href="/admin/login"' in body
    assert "Confirm with Google" not in body
    assert "For security" not in body
    assert 'name="next"' not in body
    assert "private" not in body


@pytest.mark.parametrize("outage", [False, True])
def test_local_denial_carries_the_local_flag(monkeypatch, outage):
    """LOCAL's error page keeps its LOCAL chrome even in the outage fallback.

    The outage fallback renders without context processors, so the page's
    own context must carry LOCAL's flag (#619).
    """
    real = error_pages.render_to_string

    def render(name, context, request=None):
        """Fail the chrome render, as a database outage would."""
        if outage and request is not None:
            raise DatabaseError("outage")
        return real(name, context, request=request)

    monkeypatch.setattr(error_pages, "render_to_string", render)
    request = RequestFactory().get("/admin/setup/credentials/parishsoft", **PAGE)
    with override_settings(STEWARDSHIP_DEPLOYMENT_PROFILE=DeploymentProfile.LOCAL):
        response = through(request, error_response(PermissionError()))
    body = response.content.decode()
    assert response.status_code == 403
    assert "This is the local laptop environment" in body
    assert "Google" not in body


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
    """Outside /admin/ no Admin link or sign-in is offered."""
    request = RequestFactory().get("/family/section", **PAGE)
    response = error_response(PermissionError())
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


def test_a_refusal_title_replaces_the_status_heading():
    """A refused page load whose status does not describe it (a 409 for the
    deployment's state, not an out-of-date form) is headed by its own title;
    scripts see no title."""
    from parishkit.stewardship.web.refusals import UserFacingStale

    titled = UserFacingStale("The campaign exists.", title="Creation is unavailable")
    response = error_response(titled)
    assert "title" not in json.loads(response.content)["refusal"]
    body = through(RequestFactory().get("/admin/x", **PAGE), response).content
    assert b"<h1>Creation is unavailable</h1>" in body
    assert b"<h1>This information changed</h1>" not in body
    plain = through(
        RequestFactory().get("/admin/x", **PAGE),
        error_response(UserFacingStale("The campaign exists.")),
    ).content
    assert b"<h1>This information changed</h1>" in plain


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
    assert invalid["refusal"]["fix"] == "Reload the page, then try again."
    assert invalid["refusal"]["link"] is None
    for link, label in (
        ("https://example.org/", "Elsewhere"),
        ("//example.org/", "Elsewhere"),
        ("/admin/setup", None),
        (None, "Label"),
    ):
        with pytest.raises(TypeError):
            UserFacingError("Message", link=link, link_label=label)


def test_unexpected_fields_say_the_page_is_out_of_date():
    """A form posted with fields it does not have (#1401): the page says what
    happened and to reload, not "Check your entries" as if a value were
    wrong; the refusal itself (400, code ``invalid``) is unchanged."""
    from parishkit.stewardship.web.refusals import unexpected_fields

    response = error_response(unexpected_fields())
    assert response.status_code == 400
    data = json.loads(response.content)
    assert [error["code"] for error in data["errors"]] == ["invalid"]
    assert data["refusal"]["message"] == (
        "Nothing was done: this page sent something the server did not "
        "expect, which usually means the page changed after you opened it."
    )
    # A dialog or in-place region, still on the page, is told to reload.
    assert data["refusal"]["fix"] == "Reload the page, then try again."
    # The full page answering the POST is told to open the page again, by
    # its link: Reload there would send the same refused form again.
    page = through(RequestFactory().post("/admin/setup/branding", **PAGE), response)
    body = page.content.decode()
    assert page.status_code == 400 and page.stewardship_safe_error
    assert "<h1>This page is out of date</h1>" in body
    assert "the page changed after you opened it" in body
    assert "Open the page again, then try again." in body
    assert '<a href="/admin/setup/branding">Return to the previous page</a>' in body
    for generic in (
        "Reload the page",
        "Check your entries",
        "Check this value.",
        "correct the entries",
        "unexpected data",
    ):
        assert generic not in body


def test_refusal_text_picks_the_fix_for_where_it_shows():
    """Pages and regions with their own fixed text show a refusal's text
    instead (#1401): a region the in-place fix, a full page the page fix;
    any other error has none, so its closed text stays."""
    from parishkit.stewardship.web.refusals import (
        refusal_text,
        stale_page,
        unexpected_fields,
    )

    message = (
        "Nothing was done: this page sent something the server did not "
        "expect, which usually means the page changed after you opened it."
    )
    assert refusal_text(unexpected_fields()) == (
        message + " Reload the page, then try again."
    )
    assert refusal_text(unexpected_fields(), page=True) == (
        message + " Open the page again, then try again."
    )
    # Without a page fix, a full page shows the ordinary fix.
    assert refusal_text(stale_page(), page=True).endswith(
        "then make your change again."
    )
    assert refusal_text(ValueError("secret detail")) is None


@pytest.mark.parametrize(
    "template,region,extra",
    [
        ("stewardship/components/restore-review-outcome.html", "step", {}),
        # Take a backup now and both debug switches refuse in the review
        # dialog (#1356), so their refusals are drawn by its fragments.
        ("stewardship/system-health-backup-review.html", "backup_now", {}),
        ("stewardship/system-health-accept.html", "accept", {}),
        ("stewardship/system-health-debug-off-review.html", "debug_off", {}),
        (
            "stewardship/system-health-debug-allow-review.html",
            "debug_off",
            {"allow": True},
        ),
    ],
)
def test_in_place_regions_show_a_stale_forms_refusal(template, region, extra):
    """A region's "invalid" answer shows a stale form's refusal text when
    there is one, and its own closed text otherwise (#1401)."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.web.refusals import refusal_text, unexpected_fields

    text = refusal_text(unexpected_fields())

    def render(answer):
        """The region's markup for one refused answer."""
        return render_to_string(template, {region: answer | extra})

    stale = render({"refused": "invalid", "refusal_text": text})
    assert "the page changed after you opened it" in stale
    assert "Reload the page, then try again." in stale
    assert "could not be read" not in stale
    plain = render({"refused": "invalid", "refusal_text": None})
    assert "could not be read" in plain
    assert "the page changed after you opened it" not in plain


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
    assert ('class="admin-document js-required"' in body) is admin
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


def _role_refusal():
    """The 403 a current Staff session gets for an Administrator page."""
    from parishkit.stewardship.accounts.policy import Capability
    from parishkit.stewardship.accounts.role_refusal import response, role_denied

    return response(role_denied(Capability.SYSTEM_LOGS))


def test_a_role_refusal_page_offers_home_and_no_sign_in():
    """The page says the role is the reason; signing in again cannot help."""
    request = RequestFactory().get("/admin/system/logs", **PAGE)
    response = through(request, _role_refusal())
    body = response.content.decode()
    assert response.status_code == 403
    assert "<h1>Access unavailable</h1>" in body
    assert "This page is for Administrators. Your access doesn&#x27;t include it." in (
        body
    )
    assert "Choose a page from the menu" in body
    assert "Your session may have ended" not in body
    # The error page's own sign-in link (the Admin base also carries a hidden
    # signed-out dialog with that text).
    assert '<p><a href="/admin/login">Sign in again</a></p>' not in body
    # One home link, in the actions row, not a second one as the fix.
    assert '<p><a href="/admin/">' not in body
    assert body.count('href="/admin/">Administration home</a>') == 1


def test_a_role_refusal_reaches_scripts_as_the_same_closed_json():
    """In-place regions and scripts get the same refusal and closed code."""
    request = RequestFactory().get(
        "/admin/system/logs",
        HTTP_ACCEPT="application/json",
        HTTP_SEC_FETCH_MODE="cors",
    )
    response = through(request, _role_refusal())
    data = json.loads(response.content)
    assert response.status_code == 403
    assert data["errors"][0]["code"] == "denied"
    assert data["refusal"] == {
        "message": "This page is for Administrators. Your access doesn't include it.",
        "fix": (
            "Choose a page from the menu, or ask a parish administrator if you "
            "need this one."
        ),
        "link": {"url": "/admin/", "label": "Administration home"},
    }


def test_a_denial_linking_home_still_offers_sign_in_unless_it_says_not():
    """Sign in again is dropped by ``Refusal.sign_in``, not by the link (#1924)."""
    from parishkit.stewardship.accounts.role_refusal import response
    from parishkit.stewardship.web.refusals import UserFacingDenied

    error = UserFacingDenied(
        "This action is unavailable.", link="/admin/", link_label="Administration home"
    )
    request = RequestFactory().get("/admin/system/logs", **PAGE)
    body = through(request, response(error)).content.decode()
    assert '<p><a href="/admin/login">Sign in again</a></p>' in body
    # The home link still shows once, in the actions row.
    assert body.count('href="/admin/">Administration home</a>') == 1
