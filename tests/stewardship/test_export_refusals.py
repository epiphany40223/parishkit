"""Export refusals say what happened, not a sign-in denial (#388 L3 and L4).

A reused one-time export form is a 409 on every export page, and an authorized
download of an expired file is a 410 that says it is no longer available.
"""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from parishkit.stewardship.reports import (
    directory_export_views,
    export_ui,
    financial_export_views,
    information_export_views,
    ministry_export_views,
)
from parishkit.stewardship.reports.export_services import (
    ExportExpired,
    ExportRequestBound,
)

BOUND = b"already used for a different export"
# Every create view that allocates an export from a form key.
CREATE_VIEWS = {
    "participation": (export_ui, export_ui.create),
    "directory": (directory_export_views, directory_export_views.create),
    "financial": (financial_export_views, financial_export_views.create),
    "information": (information_export_views, information_export_views.create),
    "ministry-packet": (ministry_export_views, ministry_export_views.create_packet),
}


def _failing(monkeypatch, module, error):
    """Make the view's first call raise ``error``, before any form parsing."""
    monkeypatch.setattr(module, "runtime", Mock(side_effect=error))


@pytest.mark.parametrize("name", sorted(CREATE_VIEWS))
def test_every_export_form_reuse_is_a_conflict(monkeypatch, name):
    """A reused request key is 409 with recovery text, never 400 invalid filters."""
    module, view = CREATE_VIEWS[name]
    _failing(monkeypatch, module, ExportRequestBound("private detail"))
    response = view(RequestFactory().post("/"), uuid4())
    assert response.status_code == 409
    assert response["Cache-Control"] == "no-store"
    assert BOUND in response.content
    assert b"private detail" not in response.content


def test_native_expired_download_says_the_file_is_gone(monkeypatch):
    """The requester sees the fixed "no longer available" page (410) with a
    way back to the report, not the sign-in denial (#1398)."""
    identifier = uuid4()
    _failing(monkeypatch, export_ui, ExportExpired("This export has expired."))
    # The window ended between the check and the download: the row is read
    # once more for the page's link back.
    row = object()
    rows = Mock()
    rows.objects.get.return_value = row
    monkeypatch.setattr(export_ui, "ExportRequest", rows)
    gone = Mock(return_value=HttpResponse("gone", status=410))
    monkeypatch.setattr(export_ui, "_gone", gone)
    response = export_ui.command(
        RequestFactory().post("/"), identifier, action="download"
    )
    assert response.status_code == 410
    rows.objects.get.assert_called_once_with(pk=identifier)
    assert gone.call_args.args[1] is row


def test_an_outage_reading_an_expired_export_is_the_503_page(monkeypatch):
    """A database failure while drawing the "no longer available" page is the
    export error page's temporary outage, never a 500."""
    from django.db import DatabaseError

    _failing(monkeypatch, export_ui, ExportExpired("This export has expired."))
    rows = Mock()
    rows.objects.get.side_effect = DatabaseError("private detail")
    monkeypatch.setattr(export_ui, "ExportRequest", rows)
    response = export_ui.command(RequestFactory().post("/"), uuid4(), action="download")
    assert response.status_code == 503 and b"private detail" not in response.content


def test_the_gone_page_links_back_to_its_report():
    """The fixed page's one sentence names the report and links to it."""
    from django.template.loader import render_to_string

    page = render_to_string(
        "stewardship/report-export-gone.html",
        {"report_url": "/admin/reports/", "report_name": "Participation"},
    )
    assert (
        'This export is no longer available. Export it again from <a href="'
        '/admin/reports/">Participation</a>.'
    ) in page
    assert "Regenerate" not in page


def test_the_gone_page_records_the_gone_code(monkeypatch):
    """The 410 page carries ``gone`` for the refusal log (#1459's strict
    check), and is never cached."""
    monkeypatch.setattr(export_ui.admin_navigation, "place", Mock())
    request = RequestFactory().get("/")
    response = export_ui.gone_page(request, "response_dashboard", "/admin/reports/")
    assert response.status_code == 410
    assert response.stewardship_codes == ("gone",)
    assert response["Cache-Control"] == "no-store"


def test_native_download_denial_stays_uniform(monkeypatch):
    """Every refusal other than expiry is the uniform denial, without detail."""
    _failing(monkeypatch, export_ui, PermissionError("private detail"))
    response = export_ui.command(RequestFactory().post("/"), uuid4(), action="download")
    assert response.status_code == 403
    assert b"private detail" not in response.content


@pytest.mark.parametrize(
    "report, parameters, source, url",
    [
        ("ministry", {}, "ministry_report", "/admin/reports/ministries/"),
        ("financial", {}, "financial_report", None),
        ("campaign_archive", {}, "past_campaigns", None),
        ("campaign_comparison", {"day": 2}, "campaign_comparison", None),
        (
            "postal_outreach",
            {"postal": True, "filters": {"reach": "mail"}},
            "family_directory",
            "?mailing=yes",
        ),
        (
            "family_directory",
            {"postal": False, "filters": {"reach": "email"}},
            "family_directory",
            "?reach=email",
        ),
        (
            # An old Testing export opens the (Production only) Timeline, #1570.
            "family_timeline",
            {"family_id": "00000000-0000-0000-0000-000000000007", "mode": "testing"},
            "timeline",
            "/00000000-0000-0000-0000-000000000007/",
        ),
    ],
)
def test_an_ended_exports_link_back_comes_from_its_row(report, parameters, source, url):
    """The "no longer available" page links back to the export's own report,
    with the presets it used, from the request row alone (never its
    capture, which goes when the export ends; #1398)."""
    from types import SimpleNamespace

    job = SimpleNamespace(report=report, parameters=parameters, browser_timezone="UTC")
    _, origin, _, _, report_url = export_ui._origin(job)
    assert origin == source
    if url is not None:
        assert report_url.endswith(url) or report_url == url, report_url
