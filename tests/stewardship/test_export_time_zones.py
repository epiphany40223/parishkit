"""No download asks for a time zone (#1679, replacing #1127's rule).

The ui-conventions "Downloaded files" rule (Time zones): every export row
posts the browser's zone unasked (``browser_timezone``), and the server
falls back to the parish's time zone rather than refusing a missing or
unknown one, since the reader was never asked. An explicit ``timezone``
(System logs' "Times in", a tab opened before #1679) is still accepted and
checked as before. The template side (which rows post which zone, and the
one page with a zone choice) is guarded in test_export_actions.py.
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
    census_change_views,
    directory_export_views,
    export_ui,
    financial_export_views,
    information_export_views,
    ministry_export_views,
    ministry_followup_exports,
    timeline_views,
)
from parishkit.stewardship.reports.directories import DirectoryQuery
from parishkit.stewardship.reports.financial import FinancialQuery
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.workspace import ReportQuery
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.dates import download_zone, unasked_zone

# The parish's time zone the views fall back to, standing in for the
# active configuration's (web.dates.parish_zone).
PARISH = "America/Indiana/Indianapolis"
TEMPLATES = Path(accounts.__file__).parent / "templates" / "stewardship"


def test_the_response_dashboard_asks_for_no_zone():
    """Its region carries the zone the page was drawn in (#1535, #1537)."""
    page = (TEMPLATES / "response-dashboard.html").read_text()
    assert not re.search(r'<select\b[^>]*\bname="(?:browser_)?timezone"', page)
    assert re.search(
        r'data-report-options data-report-zone="\{\{ query\.timezone \}\}"', page
    )
    assert "zone_value=query.timezone" in page


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


@pytest.mark.parametrize("zone", [None, "", "Etc/Unknown"])
def test_a_directory_with_response_columns_needs_no_zone(monkeypatch, zone):
    """Its rows hold action times, but it no longer asks either (#1679)."""
    response, calls = _directory(monkeypatch, responses="yes", zone=zone)
    assert response.status_code == 303 and calls[0]["browser_timezone"] == PARISH
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


@pytest.mark.parametrize(
    "chosen, browser, expected",
    [
        (None, "Asia/Tokyo", "Asia/Tokyo"),
        (None, None, PARISH),
        (None, "Etc/Unknown", PARISH),
        ("UTC", "Asia/Tokyo", "UTC"),
        # An explicit choice is returned as given for the caller's own check.
        ("Mars/Olympus", "Asia/Tokyo", "Mars/Olympus"),
    ],
)
def test_a_download_zone_is_the_choice_else_the_browsers(
    monkeypatch, chosen, browser, expected
):
    """``timezone`` wins when posted; otherwise the browser's, else the parish's."""
    monkeypatch.setattr(dates, "parish_zone", lambda: PARISH)
    assert download_zone(chosen, browser) == expected


def _form(values):
    """``values`` as the posted form a download view reads."""
    data = QueryDict(mutable=True)
    for key, value in values.items():
        data[key] = value
    return data


def _census(values):
    """The Census changes download's parsed format and zone for ``values``."""
    return census_change_views._choices(_form(values), export=True)


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"browser_timezone": "Asia/Tokyo"}, "Asia/Tokyo"),
        ({}, PARISH),
        ({"browser_timezone": "Etc/Unknown"}, PARISH),
        ({"timezone": "UTC", "browser_timezone": "Asia/Tokyo"}, "UTC"),
    ],
)
def test_the_direct_downloads_take_the_browsers_zone(monkeypatch, values, expected):
    """Census changes and Ministry requests: never refused for a zone the
    reader was not asked for; an explicit one still wins."""
    monkeypatch.setattr(dates, "parish_zone", lambda: PARISH)
    assert str(_census({"format": "csv"} | values)["zone"]) == expected
    _query, _fmt, zone = ministry_followup_exports._choices(
        _form({"format": "csv"} | values)
    )
    assert str(zone) == expected


def test_an_explicit_unknown_zone_is_still_refused(monkeypatch):
    """A posted ``timezone`` keeps its strict check (an old tab, pk-admin)."""
    monkeypatch.setattr(dates, "parish_zone", lambda: PARISH)
    with pytest.raises(ValueError):
        _census({"format": "csv", "timezone": "Mars/Olympus"})
    with pytest.raises(ValueError):
        ministry_followup_exports._choices(
            _form({"format": "csv", "timezone": "Mars/Olympus"})
        )


@pytest.mark.parametrize(
    "module, service, query",
    [
        (financial_export_views, "create_financial_export", FinancialQuery),
        (information_export_views, "create_information_export", InformationQuery),
    ],
)
@pytest.mark.parametrize(
    "zone, expected", [(None, PARISH), ("bad/zone", PARISH), ("Asia/Tokyo", None)]
)
def test_the_queued_report_exports_need_no_zone(
    monkeypatch, module, service, query, zone, expected
):
    """Financial stewardship and Additional information no longer ask (#1679)."""
    calls = _queued(monkeypatch, module, service)
    monkeypatch.setattr(module, "admit_report_read", Mock(), raising=False)
    values = query().form_values() | {"format": "csv", "request_key": str(uuid4())}
    values.pop("page", None)
    if zone is not None:
        values["browser_timezone"] = zone
    response = module.create(_post(values), uuid4())
    assert response.status_code == 303
    assert calls[0]["browser_timezone"] == (expected or zone)


@pytest.mark.parametrize(
    "zone, expected", [(None, PARISH), ("bad/zone", PARISH), ("Asia/Tokyo", None)]
)
def test_the_timeline_export_needs_no_zone(monkeypatch, zone, expected):
    """The Family timeline no longer asks for its zone either (#1679)."""
    from parishkit.stewardship.reports import timeline_exports

    calls = []
    monkeypatch.setattr(dates, "parish_zone", lambda: PARISH)
    monkeypatch.setattr(timeline_views, "runtime", Mock())
    monkeypatch.setattr(timeline_views, "_principal", Mock())
    monkeypatch.setattr(timeline_views, "_full", lambda principal: True)
    monkeypatch.setattr(timeline_views, "admit_report_read", Mock())
    monkeypatch.setattr(
        timeline_exports,
        "create_timeline_export",
        lambda *args, **values: calls.append(values) or Mock(),
    )
    monkeypatch.setattr(export_ui, "_redirect", lambda *args: HttpResponse(status=303))
    values = {"format": "csv", "request_key": str(uuid4())}
    if zone is not None:
        values["browser_timezone"] = zone
    response = timeline_views.export(_post(values), uuid4(), uuid4())
    assert response.status_code == 303
    assert calls[0]["browser_timezone"] == (expected or zone)
