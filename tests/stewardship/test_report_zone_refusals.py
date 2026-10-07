"""Report dates without the browser's zone are refused with a plain reason (#558).

The Additional information queue, one Ministry's requests and both exports
take submitted dates only as days in the viewer's browser zone. A dated POST
without a known zone (a tab opened before the release, or a browser that
reports none) is a 400 that says so, never a guess and never a generic form
error.
"""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.test import RequestFactory

from parishkit.stewardship.reports import (
    information_export_views,
    information_views,
    ministry_export_views,
    ministry_views,
)
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.ministries import MinistryQuery

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


def _ministry(zone):
    """A dated one-Ministry filter with this zone (None leaves the field out)."""
    values = MinistryQuery(end="2026-11-01").form_values()
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


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_ministry_requests_refuse_dates_without_a_zone(monkeypatch, zone):
    """One Ministry's filter POST answers 400 with the zone message."""
    _signed_in(monkeypatch, ministry_views)
    request = RequestFactory().post("/", _ministry(zone) | {"ministry": "9"})
    response = ministry_views.report(request, uuid4(), action="join")
    assert response.status_code == 400 and response.content == ZONE + b"\n"


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", None])
def test_ministry_export_refuses_dates_without_a_zone(monkeypatch, zone):
    """So does its export, including a form from a page loaded before #558."""
    _signed_in(monkeypatch, ministry_export_views)
    values = _ministry(zone) | EXPORT | {"action": "join", "ministry": "9"}
    response = ministry_export_views.create(RequestFactory().post("/", values), uuid4())
    assert response.status_code == 400 and response.content == ZONE + b"\n"


def test_an_export_form_without_a_zone_or_dates_is_still_invalid(monkeypatch):
    """A zone-less form with no dates has nothing to place, so no zone message."""
    _signed_in(monkeypatch, ministry_export_views)
    values = _ministry(None) | EXPORT | {"action": "join", "ministry": "9", "end": ""}
    response = ministry_export_views.create(RequestFactory().post("/", values), uuid4())
    assert response.status_code == 400
    assert response.content == b"Invalid Ministry export selection.\n"


@pytest.mark.parametrize("module", [information_export_views, ministry_export_views])
def test_an_export_with_a_query_string_is_invalid_before_any_zone_check(
    monkeypatch, module
):
    """A query string is refused as invalid, never answered with the zone hint."""
    _signed_in(monkeypatch, module)
    if module is information_export_views:
        values = _information(None) | EXPORT
    else:
        values = _ministry(None) | EXPORT | {"action": "join", "ministry": "9"}
    response = module.create(RequestFactory().post("/?x=1", values), uuid4())
    assert response.status_code == 400 and ZONE not in response.content
