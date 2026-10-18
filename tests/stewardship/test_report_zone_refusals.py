"""Report dates without the browser's zone are refused with a plain reason (#558).

The Additional information queue and its export take submitted dates only
as days in the viewer's browser zone (one Ministry's requests, which also
did, were retired in #1157), and so do Financial stewardship and its export
for the first and latest response dates (migration 0110). A dated POST
without a known zone (a tab opened before the release, or a browser that
reports none) is a 400 that says so, never a guess and never a generic form
error.
"""

from unittest.mock import Mock
from uuid import uuid4

import pytest
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
    """A dated queue filter with this zone (None leaves the field out)."""
    values = InformationQuery(disposition="all", start="2026-11-01").form_values()
    return {key: value for key, value in values.items() if key != "zone"} | (
        {} if zone is None else {"zone": zone}
    )


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_information_queue_refuses_dates_without_a_zone(monkeypatch, zone):
    """The queue's filter POST answers 400 with the zone message."""
    _signed_in(monkeypatch, information_views)
    request = RequestFactory().post("/", _information(zone))
    response = information_views.queue(request, uuid4())
    assert response.status_code == 400 and ZONE in response.content


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_information_export_refuses_dates_without_a_zone(monkeypatch, zone):
    """So does its export, including a form from a page loaded before #558."""
    _signed_in(monkeypatch, information_export_views)
    request = RequestFactory().post("/", _information(zone) | EXPORT)
    response = information_export_views.create(request, uuid4())
    assert response.status_code == 400 and ZONE in response.content


def test_an_export_with_a_query_string_is_invalid_before_any_zone_check(monkeypatch):
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
