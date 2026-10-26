"""An ended Admin sign-in goes to the sign-in page with a short note (#2058)."""

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.accounts import authentication
from parishkit.stewardship.accounts.admin_editing import error_response, signed_out
from parishkit.stewardship.web.error_pages import (
    BrowserErrorMiddleware,
    sign_in_address,
)

PAGE = {"HTTP_ACCEPT": "text/html,*/*;q=0.8", "HTTP_SEC_FETCH_MODE": "navigate"}
SCRIPT = {"HTTP_ACCEPT": "application/json", "HTTP_SEC_FETCH_MODE": "cors"}
NOTICE = (
    "You were signed out, for example after an hour without activity or after "
    "signing out in another tab. Sign in to continue."
)


def through(request, *, had_session=True):
    """Answer ``request`` with the signed-out refusal, through the middleware.

    ``had_session`` sends the Admin session cookie, as a browser whose
    session ended still does.
    """
    if had_session:
        request.COOKIES["pk_admin"] = "ended-session-key"
    return BrowserErrorMiddleware(lambda _: error_response(signed_out()))(request)


def location_query(response):
    """The redirect's query, parsed."""
    return parse_qs(urlsplit(response["Location"]).query)


@pytest.mark.parametrize("method", ["get", "head"])
def test_a_page_load_goes_to_sign_in_with_the_page_as_next(method):
    """A GET (or HEAD) page load is redirected, never shown an error page."""
    request = getattr(RequestFactory(), method)("/admin/users", {"page": "2"}, **PAGE)
    response = through(request)
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == "/admin/login"
    assert location_query(response) == {"ended": ["1"], "next": ["/admin/users?page=2"]}
    assert "Accept" in response["Vary"]


@pytest.mark.parametrize(
    "path",
    [
        "/admin/",
        "/admin/login",
        "/admin/logout",
        "/admin/oauth/callback",
        "/admin/a%2F..%2Fb",
        "/admin/a%20b",
        "/admin/a/../b",
    ],
)
def test_an_unsafe_or_home_next_is_dropped(path):
    """Only a same-origin Admin page other than home is kept as ``next``."""
    request = RequestFactory().get(path, **PAGE)
    response = through(request)
    assert response.status_code == 302
    assert location_query(response) == {"ended": ["1"]}


def test_an_overlong_next_is_never_kept():
    """A next too long for the return-path check falls back to home."""
    request = RequestFactory().get("/admin/users", {"q": "x" * 2000}, **PAGE)
    request.COOKIES["pk_admin"] = "ended-session-key"
    assert sign_in_address(request, ended=True) == "/admin/login?ended=1"
    assert sign_in_address(request, ended=False) == "/admin/login"


def test_a_visitor_without_a_session_is_not_told_they_were_signed_out():
    """No Admin session cookie (a new browser, a bookmark): no ended flag."""
    request = RequestFactory().get("/admin/users", **PAGE)
    response = through(request, had_session=False)
    assert response.status_code == 302
    assert response["Location"] == "/admin/login?next=%2Fadmin%2Fusers"


@pytest.mark.parametrize("method", ["get", "post"])
def test_scripts_and_in_place_requests_keep_the_typed_refusal(method):
    """Fetch/JSON callers get the 403 the session-ended dialog reads."""
    request = getattr(RequestFactory(), method)("/admin/users", **SCRIPT)
    response = through(request)
    assert response.status_code == 403
    data = json.loads(response.content)
    assert data["errors"][0]["code"] == "denied"
    assert data["refusal"]["link"] == {"url": "/admin/login", "label": "Sign in"}


def _section(response):
    """The error page's own content.

    The Admin base also carries a hidden session-ended dialog with its own
    sign-in button.
    """
    body = response.content.decode()
    return re.search(r'<section class="flow panel">.*?</section>', body, re.S)[0]


def test_a_form_post_shows_one_sign_in_link_and_no_dead_ends():
    """A POST is never replayed: its error page offers sign-in, nothing else.

    The link returns to the page the form was posted from (its Referer),
    not to the POST's own address, which may answer only POST.
    """
    request = RequestFactory().post(
        "/admin/files/delete", HTTP_REFERER="http://testserver/admin/files", **PAGE
    )
    response = through(request)
    assert response.status_code == 403
    section = _section(response)
    assert section.count("href=") == 1
    assert 'href="/admin/login?next=%2Fadmin%2Ffiles">Sign in</a>' in section
    assert ">Sign in again</a>" not in section
    assert "Return to the previous page" not in section
    assert "Administration home" not in section


@pytest.mark.parametrize(
    "referer",
    [None, "https://example.com/admin/files", "http://testserver/admin/login"],
)
def test_a_form_post_without_a_safe_referer_signs_in_to_home(referer):
    """No same-origin Admin Referer: the one link is the plain sign-in."""
    headers = {"HTTP_REFERER": referer} if referer else {}
    request = RequestFactory().post("/admin/files/delete", **headers, **PAGE)
    response = through(request)
    assert response.status_code == 403
    section = _section(response)
    assert section.count("href=") == 1
    assert 'href="/admin/login">Sign in</a>' in section


def test_other_denials_keep_their_links():
    """A plain denial still offers Sign in again and the home link."""
    request = RequestFactory().get("/admin/users", **PAGE)
    response = BrowserErrorMiddleware(
        lambda _: error_response(PermissionError("private"))
    )(request)
    assert response.status_code == 403
    body = response.content.decode()
    assert '<p><a href="/admin/login">Sign in again</a></p>' in body
    assert 'href="/admin/">Administration home</a>' in body


def _login_context(monkeypatch, query):
    """The context the sign-in view renders for a GET with ``query``."""
    seen = {}
    monkeypatch.setattr(authentication, "runtime", lambda: object())

    def render(request, template, context=None):
        """Capture the template context instead of rendering."""
        seen.update(context or {})
        return "rendered"

    monkeypatch.setattr(authentication, "render", render)
    request = RequestFactory().get("/admin/login", query)
    assert authentication.login(request) == "rendered"
    return seen


@pytest.mark.parametrize(
    "query,ended,next_path",
    [
        ({}, False, None),
        ({"ended": "1", "next": "/admin/users?page=2"}, True, "/admin/users?page=2"),
        ({"ended": "yes"}, False, None),
        ({"next": "https://example.com/admin/"}, False, None),
        ({"next": "//example.com/admin/"}, False, None),
        ({"next": "/admin/login"}, False, None),
        ({"next": "/admin/"}, False, None),
    ],
)
def test_the_sign_in_page_reads_only_the_fixed_flag_and_a_checked_next(
    monkeypatch, query, ended, next_path
):
    """The notice needs exactly ``ended=1``; ``next`` must be an Admin page."""
    context = _login_context(monkeypatch, query)
    assert context == {
        "ended": ended,
        "next": next_path,
        "setup_cancelled": False,
        "setup_finishing": False,
    }


@pytest.mark.parametrize(
    "flag,cancelled,finishing",
    [
        ("cancelled", True, False),
        ("cancelled-finishing", True, True),
        ("anything-else", False, False),
    ],
)
def test_the_sign_in_page_reads_only_the_fixed_setup_flags(
    monkeypatch, flag, cancelled, finishing
):
    """A cancelling Sign out (#2272) is shown only for its two fixed flags."""
    context = _login_context(monkeypatch, {"setup": flag})
    assert context["setup_cancelled"] is cancelled
    assert context["setup_finishing"] is finishing


def test_the_setup_cancelled_notice_adds_operator_advice_only_when_finishing():
    """Cancel's advice about the original keys applies to a finishing attempt."""
    plain = render_to_string("stewardship/login.html", {"setup_cancelled": True})
    assert "Setup was cancelled." in plain
    assert "original keys" not in plain
    finishing = render_to_string(
        "stewardship/login.html", {"setup_cancelled": True, "setup_finishing": True}
    )
    assert "original keys are restored" in finishing


def test_the_notice_and_next_show_only_when_given():
    """A plain visit is unchanged; an ended one shows the information notice."""
    plain = render_to_string("stewardship/login.html", {})
    assert NOTICE not in plain
    assert 'name="next"' not in plain
    ended = render_to_string(
        "stewardship/login.html", {"ended": True, "next": "/admin/users?page=2"}
    )
    assert f'<p class="notice notice-info" role="status">{NOTICE}</p>' in ended
    assert ended.index(NOTICE) < ended.index("Sign in with Google")
    assert '<input type="hidden" name="next" value="/admin/users?page=2">' in ended
