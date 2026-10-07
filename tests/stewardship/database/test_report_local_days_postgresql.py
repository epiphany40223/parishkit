"""Report date filters are days in the viewer's browser zone (#558, migration 0017).

The Additional information and Ministry reports, and their export captures,
place each From and To day in the browser zone the page sends, not the
campaign's zone. The v2 SQL functions compute the day bounds themselves, so
these tests check them against the Python rule (``browser_day_start``), filter
real submissions across a day boundary that two zones see differently, and
keep the v1 behavior for captures from before the migration (a rollback).
"""

import json
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.information import InformationQuery, information_page
from parishkit.stewardship.reports.information_exports import create_information_export
from parishkit.stewardship.reports.ministries import MinistryQuery
from parishkit.stewardship.responses.models import AdditionalInformationItem
from parishkit.stewardship.web.dates import browser_day_start

from .test_background_grants_postgresql import task_login
from .test_ministry_exports_postgresql import create
from .test_ministry_reports_postgresql import page, setup
from .test_policy_postgresql import user
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

# UTC+14 and UTC-11, with no daylight saving: for any instant their local
# dates differ, so a day in one never covers an instant the other's same day
# covers.
EAST, WEST = "Pacific/Kiritimati", "Pacific/Pago_Pago"


@pytest.mark.parametrize(
    "day,zone",
    [
        (date(2026, 3, 8), "America/New_York"),  # 23-hour day
        (date(2026, 11, 1), "America/New_York"),  # 25-hour day
        (date(2026, 11, 2), "America/New_York"),
        (date(2026, 9, 6), "America/Santiago"),  # no 00:00 that day
        (date(2026, 4, 5), "America/Santiago"),
        (date(2026, 7, 1), "Asia/Kathmandu"),  # +05:45
        (date(2026, 9, 27), "Pacific/Auckland"),  # east of UTC, 23-hour day
        (date(2026, 1, 1), "US/Eastern"),  # a catalog alias PostgreSQL lacks
    ],
)
def test_sql_day_bounds_equal_the_python_rule(day, zone):
    """SQL and Python place a browser-local day at the same two instants."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (%s::date::timestamp"
            " AT TIME ZONE stewardship_timezone_name_v1(%s)),"
            "((%s::date+1)::timestamp"
            " AT TIME ZONE stewardship_timezone_name_v1(%s))",
            [day, zone, day, zone],
        )
        start, end = cursor.fetchone()
    assert start == browser_day_start(day, zone)
    assert end == browser_day_start(date.fromordinal(day.toordinal() + 1), zone)


def _information(harness, **filters):
    """The queue's total for these filters, read under the real web login."""
    query = InformationQuery.parse({"disposition": "all", **filters})
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return information_page(harness.campaign.pk, query)["total"]


def _capture(harness, query):
    """One complete information export capture's row count, as the web role."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_information_export(
            harness.service.store,
            user("admin@example.org").pk,
            campaign_id=harness.campaign.pk,
            query=query,
            history=False,
            format="csv",
            browser_timezone="Etc/UTC",
            request_key=uuid4(),
        )
        return request.information_snapshot


def test_information_days_are_the_browsers_zone(live_response_service):
    """A request's local day is where it is listed, on the page and in exports."""
    harness = live_response_service
    respond(harness, "Please call about the new choir")
    submitted = AdditionalInformationItem.objects.get().submission.submitted_at
    east = submitted.astimezone(ZoneInfo(EAST)).date().isoformat()
    assert _information(harness, start=east, end=east, zone=EAST) == 1
    assert _information(harness, start=east, end=east, zone=WEST) == 0
    assert _information(harness, start=east, zone=WEST) == 0
    assert _information(harness, end=east, zone=WEST) == 1
    zoned = InformationQuery.parse(
        {"disposition": "all", "start": east, "end": east, "zone": EAST}
    )
    snapshot = _capture(harness, zoned)
    assert snapshot.row_count == 1 and snapshot.parameters["filters"]["zone"] == EAST
    west = InformationQuery.parse(zoned.form_values() | {"zone": WEST})
    assert _capture(harness, west).row_count == 0


def test_v2_equals_v1_when_the_zone_is_the_campaigns(live_response_service):
    """Only the zone moved: with the campaign's own zone both versions agree.

    Both run in one statement, so their observation instants are equal too.
    """
    harness = live_response_service
    respond(harness, "Same answer either way")
    zone = harness.campaign.active_configuration.timezone
    submitted = AdditionalInformationItem.objects.get().submission.submitted_at
    day = submitted.astimezone(ZoneInfo(zone)).date().isoformat()
    legacy = InformationQuery(disposition="all", start=day, end=day, zone=None)
    zoned = InformationQuery(disposition="all", start=day, end=day, zone=zone)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_information_report_v1(%s,%s::jsonb)::text,"
            "stewardship_information_report_v2(%s,%s::jsonb)::text",
            [
                harness.campaign.pk,
                json.dumps({"filters": legacy.form_values(), "history": True}),
                harness.campaign.pk,
                json.dumps({"filters": zoned.form_values(), "history": True}),
            ],
        )
        v1, v2 = (json.loads(value) for value in cursor.fetchone())
    assert v1 == v2 and v2["total"] == 1


def test_a_capture_from_before_the_migration_keeps_campaign_days(
    live_response_service,
):
    """Filters without a zone (an older release) are still v1 campaign days."""
    harness = live_response_service
    respond(harness, "Rollback keeps working")
    zone = harness.campaign.active_configuration.timezone
    submitted = AdditionalInformationItem.objects.get().submission.submitted_at
    day = submitted.astimezone(ZoneInfo(zone)).date().isoformat()
    legacy = InformationQuery.retained(
        InformationQuery(disposition="all", start=day, end=day, zone=None).form_values()
    )
    snapshot = _capture(harness, legacy)
    assert "zone" not in snapshot.parameters["filters"] and snapshot.row_count == 1


@pytest.mark.parametrize(
    "zone,message",
    [("", "information report interval"), ("Mars/Base", "information report")],
)
def test_sql_refuses_dates_without_a_known_zone(live_response_service, zone, message):
    """The SQL owner repeats Python's rule for any direct caller."""
    harness = live_response_service
    filters = InformationQuery(disposition="all", start="2026-01-01").form_values()
    with (
        pytest.raises(DatabaseError, match="(?i)" + message),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_information_report_v2(%s,%s::jsonb)",
            [
                harness.campaign.pk,
                json.dumps({"filters": filters | {"zone": zone}, "history": False}),
            ],
        )


def _instant(row):
    """A report row's submission instant, whether detached as text or not."""
    value = row["submitted_at"]
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def test_ministry_days_are_the_browsers_zone(response_service):
    """One Ministry's rows and its export capture use the browser's days."""
    harness = setup(response_service)
    (row,) = page(harness, ministry=9)["rows"]
    east = _instant(row).astimezone(ZoneInfo(EAST)).date().isoformat()
    window = {"start": east, "end": east}
    assert len(page(harness, ministry=9, zone=EAST, **window)["rows"]) == 1
    assert page(harness, ministry=9, zone=WEST, **window)["rows"] == []
    actor = user("admin@example.org").pk
    query = MinistryQuery(zone=EAST, **window)
    captured = create(harness, actor, query=query).ministry_snapshot
    assert captured.row_count == 1 and captured.parameters["filters"]["zone"] == EAST
    query = MinistryQuery(zone=WEST, **window)
    assert create(harness, actor, query=query).ministry_snapshot.row_count == 0


def test_a_ministry_capture_without_a_zone_keeps_campaign_days(
    response_service, monkeypatch
):
    """An older release's filters (no zone key) still capture through v1."""
    harness = setup(response_service)
    (row,) = page(harness, ministry=9)["rows"]
    zone = harness.campaign.active_configuration.timezone
    day = _instant(row).astimezone(ZoneInfo(zone)).date().isoformat()
    # An older release's capture: its filters have no zone key, which this
    # release's own parsing would never produce.
    original = MinistryQuery.form_values
    monkeypatch.setattr(
        MinistryQuery,
        "form_values",
        lambda self: {k: v for k, v in original(self).items() if k != "zone"},
    )

    def unchecked(cls, values, detail=False):
        """Build the query as an older release did, without a zone."""
        return cls(**values)

    monkeypatch.setattr(MinistryQuery, "parse", classmethod(unchecked))
    query = MinistryQuery(start=day, end=day)
    captured = create(harness, user("admin@example.org").pk, query=query)
    assert "zone" not in captured.ministry_snapshot.parameters["filters"]
    assert captured.ministry_snapshot.row_count == 1
