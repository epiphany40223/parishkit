"""Typed errors: HTML pages for browser requests, JSON for scripts."""

import json

import pytest
from django.test import RequestFactory

from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.storage import StaleRecordError
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
    """Outside /admin/ only the Family home and sign-in are offered."""
    request = RequestFactory().get("/family/section", **PAGE)
    response = error_response(PermissionError())
    body = through(request, response).content.decode()
    assert "/admin/" not in body
    assert 'href="/"' in body
