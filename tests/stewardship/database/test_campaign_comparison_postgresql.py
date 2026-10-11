"""The Campaign comparison report (#1018, slice 3), on PostgreSQL.

A real campaign with Financial stewardship gets live pledges from four
households, a materialized "Historical as of day" fact set and synthetic
imported archives (``archive_factory``; every value invented). The SQL
functions are called as the restricted web login, where their grants
apply; the page and ``pk-admin report comparison`` read the same figures.
"""

import json
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.archive import imports
from parishkit.stewardship.archive.check_worker import document
from parishkit.stewardship.archive.workbook import (
    BAND_EDGES,
    pledge_distribution,
    quartiles,
)
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.comparison_reads import current_figures
from parishkit.stewardship.reports.materialization import materialize_fact_set
from parishkit.stewardship.reports.models import CampaignDailyFact
from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from ..archive_factory import at, campaign, set_metadata, submission, workbook_bytes
from ..test_financial_answers import CHECK, OPTIONS
from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_fact_materialization_postgresql import allocation
from .test_financial_report_postgresql import family_session, head, pledge
from .test_financial_source_postgresql import financial_source
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[3]
FROZEN = (
    ROOT / "src/parishkit/stewardship/schema/migrations/0067_campaign_comparison.sql"
)
# The current campaign's stewardship year in these tests; the archives are
# the years before it.
CURRENT_YEAR = 2055
# The live pledges, by Family DUID (6 pledges zero, so it is left out of the
# distribution and quartiles).
PLEDGES = {1: "1234.50", 2: "500", 6: "0", 7: "250"}


def in_process(data, on_timeout=None):
    """The archive helper's document, computed here, as JSON would carry it."""
    return json.loads(json.dumps(document(data)))


@pytest.fixture(autouse=True)
def stewardship_year(monkeypatch):
    """Check workbooks in this process, with a fixed current year."""
    monkeypatch.setattr(imports, "check_archive", in_process)
    monkeypatch.setattr(imports, "stewardship_year", lambda: CURRENT_YEAR)


def archive_bytes(year, ids, *, system="parishsoft_duid", final=True):
    """A synthetic archive for ``year`` whose Families have these ids.

    The first id responds on day 1 and each next one a day later; each
    pledges $100 times its position. ``system`` "pds_fid" rewrites the keys
    as old-system Family ids.
    """
    prefix = "duid" if system == "parishsoft_duid" else "pds_fid"
    rows = [
        submission(
            f"{prefix}:{value}", "combined", at(index + 1), str(100 * (index + 1))
        )
        for index, value in enumerate(ids)
    ]
    metadata, sheets = campaign(rows)
    for family in sheets["families"]:
        family["family_id_system"] = system
    metadata = set_metadata(metadata, "campaign_year", year)
    metadata = set_metadata(metadata, "campaign_name", f"Stewardship {year}")
    metadata = set_metadata(metadata, "is_final", final)
    return workbook_bytes(metadata, sheets)


def import_archive(store, actor, data):
    """Review and apply one archive as the web login; return its row."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        review = imports.review(store, actor, data)
        imports.apply(store, actor, review.archive.pk)
    return review.archive


@pytest.fixture
def scenario(response_service, google):
    """A financial campaign with four households' live pledges on day 1.

    Returns (harness, ready fact set, signed-in browser, Administrator id).
    """
    harness = response_service
    financial_source(
        harness,
        modules=["financial"],
        options=map(asdict, OPTIONS),
        extra_families={
            duid: dict(familyDUID=duid, registeredOrganizationID=5, lastName="Zeta")
            for duid in (6, 7)
        },
        extra_members={21: head(21, 2), 22: head(22, 6), 23: head(23, 7)},
    )
    harness = activate_response_service(harness)
    opened = harness.campaign.active_configuration.starts_at
    for hour, (duid, amount) in enumerate(PLEDGES.items(), start=1):
        with campaign_clock(opened + timedelta(hours=hour)), web_login():
            family = harness if duid == 1 else family_session(harness, duid)
            positive = amount != "0"
            pledge(
                family,
                load_form(family),
                annual_pledge=amount,
                frequency="annual" if positive else "",
                shares={CHECK: ""} if positive else {},
            )
    # The fact set reads the newest load, which made households 2, 6 and 7
    # eligible.
    newest = SourceSnapshot.objects.filter(state="promoted").latest("generation")
    record, claim = allocation(harness, watermark=len(PLEDGES), source=newest)
    ready = materialize_fact_set(record.pk, claim, admit=permit, interactive=True)
    browser, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(email="admin@example.org").pk
    return harness, ready, browser, actor


def first_day(ready):
    """The fact set's day 1 row."""
    return CampaignDailyFact.objects.filter(fact_set=ready).order_by("local_date")[0]


def test_current_figures_match_the_graph_and_the_python_rules(scenario):
    """The definer function's aggregates equal the graph's and Python's."""
    harness, ready, _, actor = scenario
    store = harness.service.store
    last_year = import_archive(
        store, actor, archive_bytes(CURRENT_YEAR - 1, [1, 2, 99])
    )
    fact = first_day(ready)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        found = current_figures(ready.pk, fact, last_year.pk)
    amounts = [Decimal(value) for value in PLEDGES.values() if value != "0"]
    assert found["responded"] == fact.cumulative_responses == len(PLEDGES)
    assert Decimal(found["pledge_total"]) == fact.pledge_total == sum(amounts)
    assert found["pledging"] == 3
    assert [Decimal(v) for v in found["quartiles"]] == list(quartiles(amounts))
    assert [
        (row["band"], row["families"], Decimal(row["total"])) for row in found["bands"]
    ] == [
        (row["band"], row["families"], row["total"])
        for row in pledge_distribution(amounts)
    ]
    # Families 1 and 2 responded again; 99 did not.
    assert (found["renewed"], found["prior_families"]) == (2, 3)


COMPARISON = "public.stewardship_campaign_comparison_v1(%s,%s,%s::numeric[],%s)"
RENEWAL = "public.stewardship_campaign_archive_renewal_v1(%s,%s,1)"


def calls(ready):
    """Each definer function's call, with arguments, for one fact set."""
    fact = first_day(ready)
    return (
        (COMPARISON, [ready.pk, fact.local_date, list(BAND_EDGES), None]),
        (RENEWAL, [ready.pk, ready.pk]),
    )


def test_only_the_web_login_may_call_the_definer_functions(scenario):
    """EXECUTE is the web login's alone; PUBLIC has none."""
    _, ready, _, _ = scenario
    for role in (ServiceRole.WORKER, ServiceRole.SCHEDULER):
        for function, arguments in calls(ready):
            with (
                pytest.raises(DatabaseError, match="permission denied"),
                task_login(role, exact=True, reconnect=True),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT " + function, arguments)


def test_a_login_granted_execute_is_still_refused_in_the_body(scenario):
    """The body's own check refuses a session that is not the web login's."""
    _, ready, _, _ = scenario
    probes = calls(ready)  # Read before the session becomes the probe role.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("CREATE ROLE p1018c_probe LOGIN NOINHERIT")
        for name in (
            "stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid)",
            "stewardship_campaign_archive_renewal_v1(uuid, uuid, integer)",
        ):
            cursor.execute(f"GRANT EXECUTE ON FUNCTION public.{name} TO p1018c_probe")
        cursor.execute("SET LOCAL SESSION AUTHORIZATION p1018c_probe")
        for function, arguments in probes:
            with (
                pytest.raises(DatabaseError, match="Only the web login"),
                transaction.atomic(),
            ):
                cursor.execute("SELECT " + function, arguments)
        # Roll back the probe role with everything else.
        transaction.set_rollback(True)


def test_the_cutoff_is_derived_in_sql_and_checked_against_the_fact_row(scenario):
    """No caller-supplied day end exists, and a disagreeing fact row refuses.

    Moving the cutoff could reveal whether one Family is among last year's
    by a change in the renewal count. The function takes no day end, and
    it refuses unless its Families responded equal the fact row's, here
    forced apart as only a superuser could.
    """
    from .auth_builders import unguarded

    _, ready, _, _ = scenario
    fact = first_day(ready)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_function_identity_arguments("
            "'public.stewardship_campaign_comparison_v1"
            "(uuid, date, numeric[], uuid)'::regprocedure)"
        )
        assert "time" not in cursor.fetchone()[0]
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_daily_fact SET cumulative_responses=%s,"
            "cohort_denominator=%s WHERE id=%s",
            [fact.cumulative_responses + 1, fact.cohort_denominator + 1, fact.pk],
        )
    with (
        pytest.raises(DatabaseError, match="disagree with their fact row"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT " + COMPARISON, calls(ready)[0][1])


@pytest.mark.parametrize(
    "edges", [["500", "250"], ["250", "250"], ["0", "250"], ["-1"], []]
)
def test_band_edges_must_be_positive_and_increasing(scenario, edges):
    """Unsorted, repeated, non-positive or missing edges are refused."""
    _, ready, _, _ = scenario
    fact = first_day(ready)
    with (
        pytest.raises(DatabaseError, match="increasing band edges"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT " + COMPARISON, [ready.pk, fact.local_date, edges, None])


def test_page_tiles_tables_notes_and_audit(scenario):
    """The page compares day 2 with last year and the average, no-store."""
    harness, ready, browser, actor = scenario
    store = harness.service.store
    import_archive(store, actor, archive_bytes(CURRENT_YEAR - 1, [1, 2, 99]))
    import_archive(store, actor, archive_bytes(CURRENT_YEAR - 2, [1, 99, 500]))
    # An old-system year: its Families cannot be matched with DUIDs.
    import_archive(
        store, actor, archive_bytes(CURRENT_YEAR - 3, [4, 5], system="pds_fid")
    )
    start = harness.campaign.active_configuration.start_date
    opened = harness.campaign.active_configuration.starts_at
    route = reverse("admin:campaign_comparison")
    assert route == "/admin/reports/comparison/"
    with (
        campaign_clock(opened + timedelta(days=2, hours=12)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        response, body = get(browser, route)
        assert response.status_code == 200, body
        assert response["Cache-Control"] == "no-store"
        text = body.decode()
        assert "<h1>Campaign comparison</h1>" in text
        assert "Day 2 of" in text and "2052–2054 average" in text
        # Four Families responded; each imported year had two by day 2.
        assert "Last year on day 2: 2 (↑ 100% more)" in text
        assert "2052–2054 average: 2 (↑ 100% more)" in text
        # 4 of this cohort's 4 against 2 of an archive's 40 active Families.
        assert "Last year on day 2: 5% (↑ 95 points more)" in text
        assert "Average pledge" in text and "Median so far: $500.00" in text
        # Renewal: 2 of last year's 3 responders; last year renewed 1 of
        # 2053's 3 by day 2. 2053 cannot renew the old-system 2052, so
        # renewal has no average (it needs two years).
        assert "66.7%" in text and "(↑ 33.3 points more)" in text
        assert "average: 33.3%" not in text
        # The pledge distribution shows this campaign's bands so far.
        assert "$1,000 to under $1,500" in text and "$1,984.50" in text
        # Data notes, each year's provenance.
        assert "Invented test cohort" in text and "Synthetic data for tests" in text
    contexts = list(
        AuditContext.objects.filter(
            event__event_type="campaign_comparison_viewed"
        ).values_list("context", flat=True)
    )
    assert sorted(c["outcome"] for c in contexts) == ["started", "succeeded"]
    assert {c.get("count") for c in contexts} == {None, 2}
    assert start  # The campaign's day 1 is its start date.


def chart_spec(text, key):
    """The Vega-Lite spec the page embeds for chart ``key``."""
    marker = f'<script id="comparison-{key}-spec" type="application/json">'
    start = text.index(marker) + len(marker)
    return json.loads(text[start : text.index("</script>", start)])


def test_charts_and_compare_on_day(scenario):
    """The page draws the stored years and live facts; Compare on day moves N.

    Day 1 is a day the campaign has, so the page answers with day 1's
    figures; day 3 is past the last complete day, and any value but a whole
    day number is refused at the field, with the latest day's page, 400
    and a denied view. Any other parameter is the plain options error.
    """
    harness, ready, browser, actor = scenario
    store = harness.service.store
    import_archive(store, actor, archive_bytes(CURRENT_YEAR - 1, [1, 2, 99]))
    import_archive(store, actor, archive_bytes(CURRENT_YEAR - 2, [1, 99, 500, 501]))
    opened = harness.campaign.active_configuration.starts_at
    route = reverse("admin:campaign_comparison")
    with (
        campaign_clock(opened + timedelta(days=2, hours=12)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        response, body = get(browser, route)
        assert response.status_code == 200, body
        text = body.decode()
        assert 'max="2"' in text and 'value="2"' in text
        families = chart_spec(text, "families")
        lines = {row["line"] for row in families["data"]["values"]}
        assert lines == {
            "current",
            f"year-{CURRENT_YEAR - 1}",
            f"year-{CURRENT_YEAR - 2}",
            "average",
        }
        # The current line is the fact rows: four Families on days 1 and 2.
        current = [r for r in families["data"]["values"] if r["line"] == "current"]
        assert [(r["day"], r["shown"]) for r in current] == [(1, "4"), (2, "4")]
        # Last year (three Families, one a day) ended on day 3 and is
        # carried to day 4, the last day of the year before it.
        last = [
            (r["day"], r["shown"])
            for r in families["data"]["values"]
            if r["line"] == f"year-{CURRENT_YEAR - 1}"
        ]
        assert last == [(1, "1"), (2, "2"), (3, "3"), (4, "3 (final)")]
        pledged = chart_spec(text, "pledged")
        assert {r["line"] for r in pledged["data"]["values"]} == lines
        for key in ("final-families", "final-pledged", "bands-families"):
            chart_spec(text, key)
        # Compare on day 1: the tiles and the rule move to day 1.
        response, body = get(browser, route + "?day=1")
        assert response.status_code == 200, body
        text = body.decode()
        assert "Day 1 of" in text and 'value="1"' in text
        rule = [
            layer
            for layer in chart_spec(text, "families")["layer"]
            if layer["mark"]["type"] == "rule"
        ]
        assert rule[0]["data"]["values"][0]["day"] == 1
        for refused in ("?day=3", "?day=0", "?day=abc"):
            response, body = get(browser, route + refused)
            text = body.decode()
            assert response.status_code == 400, refused
            assert response["Cache-Control"] == "no-store"
            assert "Enter a whole number from 1 to 2." in text
            assert 'aria-invalid="true"' in text and "Day 2 of" in text
        # The plain refusal, whose text the security boundary seals.
        response, body = get(browser, route + "?day=1&scope=current")
        assert response.status_code == 400 and b"Day 1 of" not in body
    outcomes = sorted(
        (c["outcome"], c.get("count"))
        for c in AuditContext.objects.filter(
            event__event_type="campaign_comparison_viewed"
        ).values_list("context", flat=True)
        if c["outcome"] != "started"
    )
    assert outcomes == [
        ("denied", 2),
        ("denied", 2),
        ("denied", 2),
        ("succeeded", 1),
        ("succeeded", 2),
    ]


def test_command_prints_aggregates_with_the_small_count_protection(scenario, cli):  # noqa: F811
    """``report comparison`` prints the page's figures, protected."""
    harness, _, _, actor = scenario
    store = harness.service.store
    import_archive(store, actor, archive_bytes(CURRENT_YEAR - 1, [1, 2, 99]))
    opened = harness.campaign.active_configuration.starts_at
    run = cli()
    with campaign_clock(opened + timedelta(days=2, hours=12)):
        code, result = run("report", "comparison")
        assert code == 0, result
        figures = result["result"]
        assert figures["day"] == 2 and figures["last_year"] == CURRENT_YEAR - 1
        current = figures["current"]
        assert current["at_day"]["families"] == 4
        assert current["renewal"]["renewed"] == 2
        # No current distribution, no note text, no Family value.
        assert "distribution" not in current
        printed = json.dumps(figures)
        assert "Invented test cohort" not in printed and "duid" not in printed
        (year,) = figures["years"]
        # Three pledging Families: under 5, so no bands or quartiles.
        assert year["distribution"]["bands"] is None
        assert year["distribution"]["pledging_families"] == "fewer than 5"
        code, result = run("report", "comparison", "--day", "1")
        assert code == 0 and result["result"]["day"] == 1
        code, result = run("report", "comparison", "--day", "9")
        assert code == 1 and result["error"]["code"] == "not_available"


def test_migration_check_refuses_a_missing_or_widened_function():
    """The frozen file's DO block fails when a function is absent or public."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)  # The installed functions pass.
        for change in (
            "DROP FUNCTION public.stewardship_campaign_archive_renewal_v1"
            "(uuid, uuid, integer)",
            "GRANT EXECUTE ON FUNCTION public.stewardship_campaign_comparison_v1"
            "(uuid, date, numeric[], uuid) TO PUBLIC",
            "ALTER FUNCTION public.stewardship_campaign_comparison_v1"
            "(uuid, date, numeric[], uuid) SECURITY INVOKER",
        ):
            with (
                pytest.raises(DatabaseError, match="Migration 0067"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
        cursor.execute(check)


def test_band_edges_reach_the_function_as_the_workbook_defines_them():
    """A pledge on an edge falls in the higher band, as in Python."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT width_bucket(x, %s::numeric[]) + 1 FROM unnest(%s::numeric[]) x",
            [list(BAND_EDGES), ["249.99", "250", "9999.99", "10000"]],
        )
        assert [row[0] for row in cursor.fetchall()] == [1, 2, 7, 8]
