"""Financial response dates are days in the viewer's browser zone (#558, 0110).

Financial stewardship filters pledges by the day of each Family's first and
latest response. Since migration 0110 those are days in the browser zone the
page sends (``zone``), not the campaign's zone. These tests submit real
responses around a daylight-saving change in a browser zone other than the
campaign's (Europe/London's clocks fall back on 2054-10-25, inside the
fixture campaign, whose zone is America/New_York), and assert the exact
Families each filter keeps, on the page, in the export capture and for the
zone-less filters of a release from before 0110 (a rollback), which keep
their campaign-zone days. They also run 0110's DO block against the old
definition.
"""

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import FinancialExportSnapshot
from parishkit.stewardship.reports.financial import FinancialQuery

from ..test_financial_answers import CHECK, OPTIONS
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_financial_exports_postgresql import capture
from .test_financial_report_postgresql import family_session, head, pledge, report
from .test_financial_source_postgresql import financial_source
from .test_policy_postgresql import user
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

LONDON = "Europe/London"
CAMPAIGN = "America/New_York"
# London's 25-hour day: clocks fall back from 02:00 BST to 01:00 GMT, so it
# runs from 2054-10-24 23:00 UTC to 2054-10-26 00:00 UTC.
DAY = "2054-10-25"
# Family 1 first responds at 00:30 BST on London's DAY (19:30 the day before
# in New York), then changes its response three days later. Family 2
# responds at 00:30 the day after in London (20:30 on DAY in New York).
# Family 6 responds at 23:30 GMT on DAY, in London's 25th hour: a 24-hour
# day from London's midnight would have ended at 23:00 UTC.
FIRST_1 = datetime(2054, 10, 24, 23, 30, tzinfo=UTC)
LATEST_1 = datetime(2054, 10, 28, 12, 0, tzinfo=UTC)
ONLY_2 = datetime(2054, 10, 26, 0, 30, tzinfo=UTC)
ONLY_6 = datetime(2054, 10, 25, 23, 30, tzinfo=UTC)
MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)


def families(harness):
    """Three real households whose responses straddle London's DST change."""
    financial_source(
        harness,
        options=map(asdict, OPTIONS),
        extra_families={
            6: dict(familyDUID=6, registeredOrganizationID=5, lastName="Zeta")
        },
        extra_members={21: head(21, 2), 22: head(22, 6)},
    )
    harness = activate_response_service(harness)
    assert harness.campaign.active_configuration.timezone == CAMPAIGN
    with campaign_clock(FIRST_1), web_login():
        pledge(harness, load_form(harness), shares={CHECK: ""})
    with campaign_clock(ONLY_2), web_login():
        second = family_session(harness, 2)
        pledge(second, load_form(second), annual_pledge="500", frequency="weekly")
    with campaign_clock(ONLY_6), web_login():
        third = family_session(harness, 6)
        pledge(third, load_form(third), annual_pledge="0", frequency="")
    # Days later, so Family 1 signs in again for its changed response.
    with campaign_clock(LATEST_1), web_login():
        again = family_session(harness, 1)
        pledge(again, load_form(again), annual_pledge="900", shares={CHECK: ""})
    return harness


def shown(page):
    """The Family DUIDs a report page lists."""
    return {row["family_duid"] for row in page["rows"]}


def selection(harness, filters):
    """The Family DUIDs SQL itself selects for raw filters, under the web role."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_financial_report_v1(%s,%s::jsonb,NULL,NULL)::text",
            [harness.campaign.pk, json.dumps({"filters": filters, "proof": None})],
        )
        document = json.loads(cursor.fetchone()[0])
    return {row["family_duid"] for row in document["rows"]}


# (filters, the Families kept in London, the Families kept in New York).
CASES = [
    ({"first_start": DAY, "first_end": DAY}, {1, 6}, {2, 6}),
    ({"latest_start": DAY, "latest_end": DAY}, {6}, {2, 6}),
    ({"latest_start": DAY}, {1, 2, 6}, {1, 2, 6}),
    ({"latest_end": DAY}, {6}, {2, 6}),
    ({"first_end": "2054-10-24"}, set(), {1}),
    ({"first_start": "2054-10-26"}, {2}, set()),
]


def test_response_days_are_the_browsers_zone(response_service):
    """Each filter keeps exactly the Families whose response falls on those
    days in the zone the page sent, across London's 25-hour day; the
    campaign's own zone gives its own days, and the summary follows."""
    harness = families(response_service)
    assert shown(report(harness)) == {1, 2, 6}
    for filters, london, new_york in CASES:
        page = report(harness, zone=LONDON, **filters)
        assert shown(page) == london, filters
        assert page["total"] == page["summary"]["families"] == len(london)
        assert shown(report(harness, zone=CAMPAIGN, **filters)) == new_york, filters


def test_the_export_captures_the_zone_and_its_days(response_service):
    """An export captures the filters with their zone and the same Families."""
    harness = families(response_service)
    actor = user("admin@example.org").pk
    for zone, expected in ((LONDON, {1, 6}), (CAMPAIGN, {2, 6})):
        query = FinancialQuery.parse(
            {"first_start": DAY, "first_end": DAY, "zone": zone}
        )
        request = capture(harness, actor, query=query)
        snapshot = FinancialExportSnapshot.objects.get(pk=request.financial_snapshot_id)
        assert snapshot.parameters["filters"]["zone"] == zone
        assert {row["family_duid"] for row in snapshot.document["rows"]} == expected
        assert snapshot.row_count == len(expected)


def test_filters_without_a_zone_keep_the_campaign_days(response_service):
    """An older release sends no zone key: its days stay the campaign's,
    exactly as the same filters with the campaign's zone now select them."""
    harness = families(response_service)
    for filters, _london, new_york in CASES:
        legacy = FinancialQuery().sql_filters() | filters
        del legacy["zone"]
        assert selection(harness, legacy) == new_york, filters
        zoned = legacy | {"zone": CAMPAIGN}
        assert selection(harness, zoned) == new_york, filters


@pytest.mark.parametrize(
    "name", ["first_start", "first_end", "latest_start", "latest_end"]
)
@pytest.mark.parametrize("zone", ["", "Mars/Base", "+05:00"])
def test_sql_refuses_a_date_without_a_known_zone(response_service, name, zone):
    """The selection repeats Python's rule for any direct caller."""
    filters = FinancialQuery().sql_filters() | {name: DAY, "zone": zone}
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="Invalid financial report parameters"),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_financial_report_v1(%s,%s::jsonb,1,50)",
            [uuid4(), json.dumps({"filters": filters, "proof": None})],
        )


def test_sql_refuses_an_unknown_zone_even_without_dates(response_service):
    """A zone the catalog lacks is refused, never carried; a blank one with no
    dates (the page's first view) is read as before."""
    neutral = FinancialQuery().sql_filters()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_financial_report_v1(%s,%s::jsonb,1,50)",
            [uuid4(), json.dumps({"filters": neutral, "proof": None})],
        )
        assert cursor.fetchone()[0] is None
        with (
            pytest.raises(DatabaseError, match="Invalid financial report parameters"),
            transaction.atomic(),
        ):
            cursor.execute(
                "SELECT stewardship_financial_report_v1(%s,%s::jsonb,1,50)",
                [
                    uuid4(),
                    json.dumps(
                        {"filters": neutral | {"zone": "Mars/Base"}, "proof": None}
                    ),
                ],
            )


def _check():
    """0110's DO block, as the frozen file runs it."""
    text = (MIGRATIONS / "0110_financial_local_days.sql").read_text(encoding="utf-8")
    return re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]


def _recorded_acl(cursor):
    """Record the grants, settings and owner as the frozen file does first."""
    text = (MIGRATIONS / "0110_financial_local_days.sql").read_text(encoding="utf-8")
    statement = re.search(
        r"^SELECT set_config\('stewardship\.migration_0110_acl'.*?, true\);",
        text,
        re.M | re.S,
    )[0]
    cursor.execute(statement.replace(", true);", ", false);"))


def _old_body():
    """0094's financial selection, the newest definition before 0110."""
    old = (MIGRATIONS / "0094_source_status_names.sql").read_text(encoding="utf-8")
    start = old.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_financial_report_v1("
    )
    body = old[start : old.index("END $$;", start) + len("END $$;")]
    assert "'zone'" not in body
    return body


def test_migration_self_check_refuses_the_old_financial_body():
    """0110's DO block passes on the installed selection and refuses 0094's.

    The old body runs inside a rolled-back transaction, so the installed
    function is left as the full migration made it.
    """
    with connection.cursor() as cursor:
        _recorded_acl(cursor)
        cursor.execute(_check())
        with transaction.atomic():
            cursor.execute(_old_body())
            with (
                pytest.raises(DatabaseError, match="did not replace the selection"),
                transaction.atomic(),
            ):
                cursor.execute(_check())
            transaction.set_rollback(True)
        cursor.execute(_check())


def test_migration_self_check_refuses_changed_grants_or_definer_rights():
    """The DO block refuses a selection whose grants or rights changed."""
    with connection.cursor() as cursor:
        _recorded_acl(cursor)
        for change, message in (
            (
                "GRANT EXECUTE ON FUNCTION public.stewardship_financial_report_v1"
                "(uuid,jsonb,integer,integer) TO PUBLIC",
                "grants, owner or settings",
            ),
            (
                "ALTER FUNCTION public.stewardship_financial_report_v1"
                "(uuid,jsonb,integer,integer) SECURITY DEFINER",
                "did not replace the selection",
            ),
            (
                "ALTER FUNCTION public.stewardship_financial_report_v1"
                "(uuid,jsonb,integer,integer) RESET jit",
                "grants, owner or settings",
            ),
        ):
            with transaction.atomic():
                cursor.execute(change)
                with pytest.raises(DatabaseError, match=message), transaction.atomic():
                    cursor.execute(_check())
                transaction.set_rollback(True)
        cursor.execute(_check())
