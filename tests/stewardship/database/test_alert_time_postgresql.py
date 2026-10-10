"""Alert times in SQL match the Python alert text in every zone and format (#967).

Operational and security alert emails show their times in the parish zone
with the zone named. The outbox admission recompiles each alert in SQL
(``stewardship_alert_time_v1``, frozen migration 0043) and compares it byte
for byte with ``jobs/operational_content.alert_time``, so the two must agree
for every date format and across daylight-saving changes.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.configuration_snapshots import prepare_snapshot
from parishkit.stewardship.jobs.operational_content import alert_time
from parishkit.stewardship.web import dates

from ..configuration_factory import configuration_version, successor_document

pytestmark = pytest.mark.django_db(transaction=True)

INSTANTS = (
    # US fall back, November 1, 2026: 1:30 AM EDT, then 1:30 AM EST.
    datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
    datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
    # US spring forward, March 8, 2026: 1:59 AM EST, then 3:00 AM EDT.
    datetime(2026, 3, 8, 6, 59, tzinfo=UTC),
    datetime(2026, 3, 8, 7, 0, tzinfo=UTC),
    # EU changes (March 29 and October 25, 2026), midnight, noon and a
    # single-digit day and month.
    datetime(2026, 3, 29, 0, 59, tzinfo=UTC),
    datetime(2026, 3, 29, 1, 0, tzinfo=UTC),
    datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
    datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
    datetime(2027, 1, 5, 4, 0, tzinfo=UTC),
    datetime(2027, 1, 5, 17, 0, tzinfo=UTC),
)
# Each format in the US zone (None is the unset default), then other zones:
# EU daylight saving, a quarter-hour offset, a mixed-case abbreviation,
# numeric abbreviations, a half-hour daylight change and a catalog alias.
PROFILES = (
    *(("America/New_York", style) for style in (None, *dates.FORMATS)),
    ("Europe/Berlin", "eu_long"),
    ("Asia/Kathmandu", "iso"),
    ("Pacific/Guam", "us_long"),
    ("America/Sao_Paulo", "eu_numeric"),
    ("Australia/Lord_Howe", "eu_medium"),
    ("Asia/Calcutta", "us_medium"),
)


def test_sql_alert_time_matches_python_in_every_zone_and_format():
    """Every profile and instant renders identically and leaves the session zone."""
    version = configuration_version()
    prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())
    with connection.cursor() as cursor:
        cursor.execute("SHOW TimeZone")
        (session_zone,) = cursor.fetchone()
    for zone, style in PROFILES:
        document = successor_document(version)
        values = document["sections"]["parish"][0]["values"]
        values["timezone"] = zone
        values.pop("date_format", None)
        if style is not None:
            values["date_format"] = style
        version = configuration_version(document)
        snapshot = prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())
        clock = SimpleNamespace(timezone=zone, date_format=dates.normalized(style))
        for instant in INSTANTS:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT stewardship_alert_time_v1(%s,%s)", [instant, snapshot.pk]
                )
                (shown,) = cursor.fetchone()
                cursor.execute("SHOW TimeZone")
                assert cursor.fetchone() == (session_zone,)
            assert shown == alert_time(clock, instant), (zone, style, instant)
    # The two fall-back hours read differently in the parish's own words.
    assert (
        alert_time(
            SimpleNamespace(timezone="America/New_York", date_format="us_long"),
            INSTANTS[0],
        )
        == "November 1, 2026 at 1:30 AM EDT"
    )


def test_sql_alert_time_refuses_a_configuration_without_a_parish():
    """No profile means no zone; the admission fails instead of guessing UTC."""
    with connection.cursor() as cursor, pytest.raises(DatabaseError, match="no rows"):
        cursor.execute(
            "SELECT stewardship_alert_time_v1(%s,%s)", [INSTANTS[0], uuid4()]
        )
