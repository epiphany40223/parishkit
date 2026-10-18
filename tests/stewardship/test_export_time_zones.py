"""Downloads ask for a time zone only when their rows hold action times (#1127).

The ui-conventions "Downloaded files" rule: a download whose rows hold exact
times of actions (submissions, log entries, staff actions, deliveries) asks
for a time zone; any other download asks nothing, its form sends the
browser's zone unasked (``data-browser-zone``), and the server falls back to
the parish's time zone rather than refusing a missing or unknown one. The
template guard below reviews every export form; a new form with a zone field
fails it until it is listed here.
"""

import re
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.http import HttpResponse, QueryDict
from django.test import RequestFactory

import parishkit.stewardship.accounts as accounts
from parishkit.stewardship.reports import (
    directory_export_views,
    export_ui,
    ministry_export_views,
)
from parishkit.stewardship.reports.directories import DirectoryQuery
from parishkit.stewardship.reports.workspace import ReportQuery
from parishkit.stewardship.web.dates import unasked_zone

# The parish's time zone the views fall back to, standing in for the
# active configuration's (web.dates.parish_zone).
PARISH = "America/Indiana/Indianapolis"
TEMPLATES = Path(accounts.__file__).parent / "templates" / "stewardship"

# Every export form with a time zone field, by template and form action:
# True asks (a visible menu), False sends the browser's zone unasked.
ASKS = {
    ("logs.html", "admin:logs_export"): True,
    ("financial-report.html", "admin:financial_export"): True,
    ("limitations-report.html", "admin:limitations_export"): True,
    ("census-changes.html", "admin:census_changes_export"): True,
    ("information.html", "admin:information_export"): True,
    # Export these requests (#1157), which replaced one Ministry's joining
    # and leaving exports: Last contact is an action time.
    ("ministry-followup.html", "admin:ministry_followup_export"): True,
    ("ministry-roster.html", "admin:ministry_roster_export"): True,
    ("timeline.html", "export.url"): True,
    ("ministry-packet.html", "admin:ministry_packet"): False,
    ("response-dashboard.html", "admin:report_export_create"): False,
    # Campaign days and dates, no action times (#1161).
    ("campaign-comparison.html", "admin:campaign_comparison_export"): False,
}
# The Family directory asks only for the code list with response columns.
CONDITIONAL = ("directory.html", "admin:family_directory_export")
ZONE_MENU = re.compile(r'<select\b[^>]*\bname="(?:browser_)?timezone"')


def export_forms():
    """Each template form with a time zone field: {(template, action): body}."""
    forms = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        for body in re.findall(r"<form\b.*?</form>", path.read_text(), re.S):
            if not (ZONE_MENU.search(body) or 'name="browser_timezone"' in body):
                continue
            action = re.search(r'action="(?:\{%\s*url\s*\'|\{\{\s*)([^\' }]+)', body)
            forms[(path.name, action.group(1))] = body
    return forms


def test_every_export_form_is_reviewed():
    """A new zone field fails until it is listed as asking or not."""
    assert set(export_forms()) == set(ASKS) | {CONDITIONAL}


@pytest.mark.parametrize("form", sorted(ASKS), ids="{0[0]}:{0[1]}".format)
def test_a_form_asks_for_a_zone_only_with_action_times(form):
    """The listed forms show a zone menu; the rest send the browser's zone."""
    body = export_forms()[form]
    if ASKS[form]:
        assert ZONE_MENU.search(body)
    else:
        assert not ZONE_MENU.search(body)
        # The browser's zone: filled by ui-v1.js, or (participation) the
        # zone report-v1.js drew the page in.
        assert re.search(
            r'<input type="hidden" name="browser_timezone" '
            r'(?:data-browser-zone|value="\{\{ query\.timezone \}\}")',
            body,
        )


def test_the_response_dashboard_asks_for_no_zone():
    """Its region carries the zone the page was drawn in (#1535, #1537)."""
    page = (TEMPLATES / "response-dashboard.html").read_text()
    assert not ZONE_MENU.search(page)
    assert re.search(
        r'data-report-options data-report-zone="\{\{ query\.timezone \}\}"', page
    )


def test_the_directory_asks_only_with_response_columns():
    """The zone slot is a menu with response columns, else the browser's zone.

    It is its own data-table-sync node, so each applied page redraws it.
    """
    body = export_forms()[CONDITIONAL]
    slot = re.search(
        r'<div id="directory-export-zone" data-table-sync[^>]*>'
        r"\{% if export_asks_zone %\}(.*?)\{% else %\}(.*?)\{% endif %\}</div>",
        body,
        re.S,
    )
    asked, unasked = slot.groups()
    assert ZONE_MENU.search(asked)
    assert unasked == '<input type="hidden" name="browser_timezone" data-browser-zone>'
    assert len(ZONE_MENU.findall(body)) == 1


@pytest.mark.parametrize(
    "responses, postal, asks",
    [("yes", False, True), ("no", False, False), ("yes", True, False)],
)
def test_the_directory_view_asks_by_its_file(responses, postal, asks):
    """Only the code list with response columns has times in its rows."""
    from parishkit.stewardship.reports.directory_views import export_asks_zone

    assert export_asks_zone(DirectoryQuery(responses=responses), postal) is asks


@pytest.mark.parametrize(
    "zone, expected",
    [
        ("America/Chicago", "America/Chicago"),
        ("UTC", "UTC"),
        ("", PARISH),
        ("Etc/Unknown", PARISH),
        ("../etc/passwd", PARISH),
        (None, PARISH),
    ],
)
def test_an_unasked_zone_falls_back(zone, expected):
    """A zone the reader was never asked for is never refused."""
    assert unasked_zone(zone, PARISH) == expected


def _post(values):
    """A POST request carrying ``values`` (lists for repeated names)."""
    data = QueryDict(mutable=True)
    for key, value in values.items():
        data.setlist(key, value if isinstance(value, list) else [value])
    return RequestFactory().post("/", data.urlencode(), content_type=_FORM)


_FORM = "application/x-www-form-urlencoded"


def _queued(monkeypatch, module, service):
    """Stand in for the runtime, principal, parish and ``service``; record calls.

    The parish's time zone (``parish_zone``, a database read) is ``PARISH``.
    """
    calls = []
    monkeypatch.setattr(module, "parish_zone", lambda: PARISH)
    monkeypatch.setattr(module, "runtime", Mock())
    monkeypatch.setattr(module, "_principal", Mock())
    monkeypatch.setattr(
        module, service, lambda *args, **values: calls.append(values) or Mock()
    )
    monkeypatch.setattr(module, "_redirect", lambda *args: HttpResponse(status=303))
    return calls


@pytest.mark.parametrize(
    "zone, expected",
    [(None, PARISH), ("", PARISH), ("Etc/Unknown", PARISH), ("America/Detroit", None)],
)
def test_the_packet_takes_the_browsers_zone_unasked(monkeypatch, zone, expected):
    """The follow-up packet has no action times: no zone is required."""
    calls = _queued(monkeypatch, ministry_export_views, "create_ministry_export")
    values = {"format": "pdf", "request_key": str(uuid4())}
    if zone is not None:
        values["browser_timezone"] = zone
    response = ministry_export_views.create_packet(_post(values), uuid4())
    assert response.status_code == 303
    assert calls[0]["browser_timezone"] == (expected or zone)


def _directory(monkeypatch, *, responses, zone):
    """Post a directory export with these response columns and ``zone``."""
    calls = _queued(monkeypatch, directory_export_views, "create_directory_export")
    monkeypatch.setattr(directory_export_views, "family_runtime", Mock())
    monkeypatch.setattr(
        directory_export_views,
        "_error",
        lambda campaign_id, status: HttpResponse(status=status),
    )
    values = DirectoryQuery(responses=responses).form_values() | {
        "mailing": "no",
        "format": "csv",
        "request_key": str(uuid4()),
    }
    if zone is not None:
        values["browser_timezone"] = zone
    response = directory_export_views.create(_post(values), uuid4())
    return response, calls


@pytest.mark.parametrize("zone", [None, "", "Etc/Unknown"])
def test_a_directory_without_response_columns_needs_no_zone(monkeypatch, zone):
    """The code list without response columns falls back to the parish's zone."""
    response, calls = _directory(monkeypatch, responses="no", zone=zone)
    assert response.status_code == 303 and calls[0]["browser_timezone"] == PARISH
    response, calls = _directory(monkeypatch, responses="no", zone="Asia/Tokyo")
    assert calls[0]["browser_timezone"] == "Asia/Tokyo"


def test_a_directory_with_response_columns_asks_for_its_zone(monkeypatch):
    """With response columns the zone is required and passed on as chosen."""
    response, calls = _directory(monkeypatch, responses="yes", zone=None)
    assert response.status_code == 400 and not calls
    response, calls = _directory(monkeypatch, responses="yes", zone="Asia/Tokyo")
    assert response.status_code == 303
    assert calls[0]["browser_timezone"] == "Asia/Tokyo"


@pytest.mark.parametrize(
    "module, service, field",
    [
        (export_ui, "create_export", ("fact_set_id", str(uuid4()))),
    ],
)
@pytest.mark.parametrize(
    "zone, expected", [(None, PARISH), ("bad/zone", PARISH), ("Asia/Tokyo", None)]
)
def test_participation_exports_need_no_zone(
    monkeypatch, module, service, field, zone, expected
):
    """The participation exports hold campaign dates, not action times."""
    calls = _queued(monkeypatch, module, service)
    values = {field[0]: field[1], "format": "csv", "request_key": str(uuid4())}
    if zone is not None:
        values["browser_timezone"] = zone
    response = module.create(_post(values), uuid4())
    assert response.status_code == 303
    assert calls[0]["browser_timezone"] == (expected or zone)


def test_the_participation_page_falls_back_to_utc():
    """An unknown zone in the address draws the page in UTC, not a refusal."""
    query = ReportQuery.parse(QueryDict("timezone=Not/AZone"))
    assert query.timezone == "UTC" and query.timezone_explicit
    assert ReportQuery.parse(QueryDict("timezone=Asia/Tokyo")).timezone == "Asia/Tokyo"
