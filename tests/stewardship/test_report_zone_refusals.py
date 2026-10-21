"""Report dates without the browser's zone are refused with a plain reason (#558).

Financial stewardship and its export take the first and latest response
dates only as days in the viewer's browser zone (migration 0110). A dated
POST without a known zone (a tab opened before the release, or a browser
that reports none) is a 400 that says so, never a guess and never a generic
form error. The Additional information queue and its export no longer have
a submitted-date filter (#1888): a page drawn before then still posts its
dates and zone, which are dropped unread, never refused.
"""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from parishkit.stewardship.reports import (
    financial_export_views,
    financial_views,
    information_export_views,
    information_views,
)
from parishkit.stewardship.reports.financial import DATES, FinancialQuery
from parishkit.stewardship.reports.information import InformationQuery

ZONE = (
    b"The dates came without your computer's time zone. Go back, reload the page"
    b" and apply the filters again; if this repeats, check your computer's time"
    b" zone setting."
)
EXPORT = {"format": "csv", "browser_timezone": "UTC", "request_key": str(uuid4())}


def _signed_in(monkeypatch, module):
    """Stand in for the runtime and the signed-in principal, which need a DB."""
    monkeypatch.setattr(module, "runtime", Mock())
    monkeypatch.setattr(module, "_principal", Mock())


def _information(zone):
    """A dated queue filter with this zone (None leaves the field out), as a
    page drawn before #1888 posts it."""
    values = InformationQuery(disposition="all", start="2026-11-01").form_values()
    return {key: value for key, value in values.items() if key != "zone"} | (
        {} if zone is None else {"zone": zone}
    )


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_information_queue_ignores_the_retired_dates(monkeypatch, zone):
    """The worklist's filter POST (#1685) reads the view without the dates."""
    _signed_in(monkeypatch, information_views)
    parse = Mock(wraps=information_views.WorklistQuery.parse)
    monkeypatch.setattr(information_views.WorklistQuery, "parse", parse)
    for name in ("admit_report_read", "_audit"):
        monkeypatch.setattr(information_views, name, Mock())
    page = HttpResponse("page")
    monkeypatch.setattr(information_views, "campaign_response", Mock(return_value=page))
    values = {"stage": "all", "search": "", "start": "2026-11-01", "end": "x"}
    request = RequestFactory().post(
        "/", values | ({} if zone is None else {"zone": zone})
    )
    assert information_views.queue(request, uuid4()) is page
    assert set(parse.call_args.args[0]) == {"stage", "search"}


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_information_export_ignores_the_retired_dates(monkeypatch, zone):
    """So does its export: a pane drawn before #1888 still queues one, with
    no date range captured."""
    _signed_in(monkeypatch, information_export_views)
    create = Mock(return_value=Mock(pk=uuid4()))
    monkeypatch.setattr(information_export_views, "create_information_export", create)
    monkeypatch.setattr(information_export_views, "_redirect", Mock(return_value=302))
    request = RequestFactory().post("/", _information(zone) | EXPORT)
    assert information_export_views.create(request, uuid4()) == 302
    query = create.call_args.kwargs["query"]
    assert (query.start, query.end, query.zone) == ("", "", "")


def test_information_export_needs_no_dates(monkeypatch):
    """The current pane posts no dates, zone or page, and queues the export."""
    _signed_in(monkeypatch, information_export_views)
    create = Mock(return_value=Mock(pk=uuid4()))
    monkeypatch.setattr(information_export_views, "create_information_export", create)
    monkeypatch.setattr(information_export_views, "_redirect", Mock(return_value=302))
    values = {
        key: value
        for key, value in InformationQuery(disposition="all").form_values().items()
        if key not in {"start", "end", "zone"}
    }
    request = RequestFactory().post("/", values | EXPORT)
    assert information_export_views.create(request, uuid4()) == 302
    assert create.call_args.kwargs["query"] == InformationQuery(disposition="all")


def test_an_export_with_a_query_string_is_invalid(monkeypatch):
    """A query string is refused as invalid, never answered with the zone hint."""
    _signed_in(monkeypatch, information_export_views)
    values = _information(None) | EXPORT
    response = information_export_views.create(
        RequestFactory().post("/?x=1", values), uuid4()
    )
    assert response.status_code == 400 and ZONE not in response.content


def _financial(zone, name):
    """A Financial stewardship filter with one response date and this zone
    (None leaves the field out, as a page loaded before #558 does)."""
    values = FinancialQuery(**{name: "2026-11-01"}).form_values()
    return {key: value for key, value in values.items() if key != "zone"} | (
        {} if zone is None else {"zone": zone}
    )


def _financial_signed_in(monkeypatch, module):
    """As ``_signed_in``, and admit the campaign, which also needs a DB."""
    _signed_in(monkeypatch, module)
    monkeypatch.setattr(module, "admit_report_read", Mock())


@pytest.mark.parametrize("name", DATES)
@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_financial_report_refuses_dates_without_a_zone(monkeypatch, zone, name):
    """The report's filter POST answers 400 with the zone message."""
    _financial_signed_in(monkeypatch, financial_views)
    request = RequestFactory().post("/", _financial(zone, name))
    response = financial_views.report(request, uuid4())
    assert response.status_code == 400 and ZONE in response.content


@pytest.mark.parametrize("name", DATES)
@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_financial_export_refuses_dates_without_a_zone(monkeypatch, zone, name):
    """So does its export, including a form from a page loaded before #558."""
    _financial_signed_in(monkeypatch, financial_export_views)
    request = RequestFactory().post("/", _financial(zone, name) | EXPORT)
    response = financial_export_views.create(request, uuid4())
    assert response.status_code == 400 and ZONE in response.content


def test_a_financial_filter_without_dates_needs_no_zone(monkeypatch):
    """A form from a page loaded before #558 without dates is not a zone
    problem: its missing field is refused as any malformed export is, and
    the report itself reads it (the zone is unused)."""
    _financial_signed_in(monkeypatch, financial_export_views)
    values = {
        key: value
        for key, value in FinancialQuery().form_values().items()
        if key != "zone"
    }
    response = financial_export_views.create(
        RequestFactory().post("/", values | EXPORT), uuid4()
    )
    assert response.status_code == 400 and ZONE not in response.content
    assert FinancialQuery.parse(values) == FinancialQuery()


def test_a_financial_export_with_a_query_string_is_invalid_first(monkeypatch):
    """A query string is refused as invalid, never answered with the zone hint."""
    _financial_signed_in(monkeypatch, financial_export_views)
    values = _financial(None, "first_start") | EXPORT
    response = financial_export_views.create(
        RequestFactory().post("/?x=1", values), uuid4()
    )
    assert response.status_code == 400 and ZONE not in response.content
