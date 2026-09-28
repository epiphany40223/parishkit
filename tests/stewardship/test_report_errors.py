"""Report outages answer with an Admin page and leave a debug trace (#132)."""

import json
import logging

from django.test import RequestFactory

from parishkit.stewardship.observability import (
    DEBUG_LOGGING_VARIABLE,
    debug_swallowed,
)
from parishkit.stewardship.web.error_pages import error_page
from parishkit.stewardship.web.report_errors import report_unavailable


def _unavailable_inside_except():
    """Build the response the way a view does: while handling a failure."""
    try:
        raise ConnectionError("synthetic private detail")
    except ConnectionError:
        return report_unavailable()


def test_scripts_receive_a_typed_503_with_retry_and_the_report_refusal():
    """Enhanced clients keep the JSON contract; nothing raised is echoed."""
    response = _unavailable_inside_except()
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert response["Cache-Control"] == "no-store"
    body = json.loads(response.content)
    assert body["errors"][0]["code"] == "unavailable"
    assert body["refusal"]["message"] == "This report is temporarily unavailable."
    assert "System logs" in body["refusal"]["fix"]
    assert b"synthetic private detail" not in response.content


def test_people_see_an_admin_page_not_the_family_sign_in_denial():
    """The browser page names the report outage, never sign-in trouble."""
    request = RequestFactory().get(
        "/admin/reports/ministry-reports/", HTTP_SEC_FETCH_MODE="navigate"
    )
    page = error_page(request, _unavailable_inside_except())
    assert page.status_code == 503
    assert page["Retry-After"] == "5"
    assert b"This report is temporarily unavailable." in page.content
    assert b"Sign-in" not in page.content
    assert b"synthetic private detail" not in page.content


def test_debug_swallowed_logs_the_handled_exception_only_when_enabled(
    monkeypatch, caplog
):
    """Debug logging names the real failure; off by default and outside except."""
    caplog.set_level(logging.DEBUG, logger="parishkit.stewardship.debug")
    monkeypatch.delenv(DEBUG_LOGGING_VARIABLE, raising=False)
    _unavailable_inside_except()
    assert not caplog.records

    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    debug_swallowed("no exception is being handled")
    assert not caplog.records

    _unavailable_inside_except()
    (record,) = caplog.records
    assert record.getMessage() == "report request unavailable"
    assert record.exc_info[0] is ConnectionError
