"""The participation facts count the pledge values in force (#2202, part 2).

Option B of the Administrator's decision (2026-10-26): a reviewer's pledge
Edit in force counts on every day from the response it reviewed onward. A
fact generation records the campaign's pledge review watermark (part of its
exact inputs) and the Edits in force when it is created (frozen migration
0185, reports spec "Pledge values in the participation facts"), so the
dashboard's Effective annual pledges, the campaign comparison's day figures
and the campaign archive export agree with Financial stewardship, and
verification recalculates exactly what was built. These tests drive real
decisions through the Pledges service and build generations through the
real allocation and calculation.
"""

# ruff: noqa: F811 -- the imported scenario fixture is injected by pytest name.

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.archive import check
from parishkit.stewardship.archive.export import export_archive
from parishkit.stewardship.archive.workbook import read_archive
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.comparison_reads import current_figures
from parishkit.stewardship.reports.facts import review_watermark
from parishkit.stewardship.reports.materialization import (
    materialize_fact_set,
    verify_fact_set,
)
from parishkit.stewardship.reports.models import CampaignDailyFact
from parishkit.stewardship.reports.selection import current_inputs
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from ..test_financial_answers import CHECK, OPTIONS
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_campaign_comparison_postgresql import (  # noqa: F401
    PLEDGES,
    first_day,
    in_process,
    scenario,
)
from .test_fact_materialization_postgresql import allocation
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_pledge_review_postgresql import admin, again, decide
from .test_pledge_share_methods_postgresql import edit
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

SHARE = {f"value_share_{CHECK}": "on"}
FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0185_series_in_force.sql"
)
WATERMARK = "public.stewardship_pledge_review_watermark_v1(uuid)"
EDITS = "public.stewardship_fact_pledge_edits_v1(uuid)"


def two_days(response_service):
    """A financial campaign whose Family pledges on day 1 and again on day 2.

    Returns (harness, day-1 response, day-2 response): 1,234.50 and then 900.
    """
    harness = response_service
    financial_source(harness, options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    opened = harness.campaign.active_configuration.starts_at
    with campaign_clock(opened + timedelta(hours=1)), web_login():
        first = pledge(harness, load_form(harness), shares={CHECK: ""})
    with campaign_clock(opened + timedelta(days=1, hours=1)):
        second = again(harness, annual_pledge="900")
    return harness, first, second


def build(harness, *, source=None):
    """A ready historical generation at the current submission and review
    watermarks, through day 2, built as the worker builds one."""
    record, claim = allocation(
        harness,
        watermark=Submission.objects.filter(
            campaign=harness.campaign, mode="live"
        ).count(),
        source=source,
        reviews=review_watermark(harness.campaign.pk),
    )
    return materialize_fact_set(record.pk, claim, admit=permit)


def totals(generation):
    """The generation's pledge total on each day, in date order."""
    return [
        row.pledge_total
        for row in CampaignDailyFact.objects.filter(fact_set=generation).order_by(
            "local_date"
        )
    ]


def run(statement, params=()):
    """Run one statement on the test connection; return its first row, if any."""
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchone() if cursor.description else None


def verified(generation):
    """Recalculation reproduces every stored day (no differing date)."""
    return verify_fact_set(generation.pk, admit=permit) == ()


def test_an_edit_counts_from_the_response_it_reviewed_onward(response_service):
    """Day 1 keeps the Family's first answer; from the reviewed day-2
    response on, the Edit's amount counts. The generation records the Edit
    and its watermark, and an older generation stays as it was built."""
    harness, _first, second = two_days(response_service)
    before = build(harness)
    assert (before.review_watermark, before.pledge_edits) == (0, [])
    assert totals(before) == [Decimal("1234.50"), Decimal("900.00")]
    edit(harness, admin(), second, amount="2000", frequency="annual", **SHARE)
    assert review_watermark(harness.campaign.pk) == 1
    after = build(harness)
    assert after.pk != before.pk and after.review_watermark == 1
    assert after.pledge_edits == [
        {
            "family_id": str(second.family_id),
            "from_sequence": second.campaign_sequence,
            "annual_pledge": "2000.00",
            "frequency": "annual",
        }
    ]
    assert totals(after) == [Decimal("1234.50"), Decimal("2000.00")]
    # Published generations are immutable; both verify as built.
    before.refresh_from_db()
    assert totals(before) == [Decimal("1234.50"), Decimal("900.00")]
    assert verified(before) and verified(after)


def test_an_edit_of_a_repeated_answer_counts_from_the_reviewed_response(
    response_service,
):
    """The Family repeats its answer after the Edit, and the pledge is
    entered by hand on that newer response: the Edit stays in force and
    still counts from the response it reviewed, not from the newer one."""
    harness, _first, second = two_days(response_service)
    actor = admin()
    edit(harness, actor, second, amount="1500", frequency="annual", **SHARE)
    opened = harness.campaign.active_configuration.starts_at
    with campaign_clock(opened + timedelta(days=1, hours=2)):
        third = again(harness, annual_pledge="900")
    decide(harness, actor, "entered_by_hand", [third])
    generation = build(harness)
    [recorded] = generation.pledge_edits
    assert recorded["from_sequence"] == second.campaign_sequence
    assert recorded["from_sequence"] < third.campaign_sequence
    assert totals(generation) == [Decimal("1234.50"), Decimal("1500.00")]
    assert verified(generation)


def test_undoing_an_edit_or_a_newer_answer_puts_the_familys_answer_back(
    response_service,
):
    """Back to review, then a newer, different answer: each review or
    response makes new inputs, and every generation verifies as built
    however later reviews change."""
    harness, _first, second = two_days(response_service)
    actor = admin()
    edit(harness, actor, second, amount="2000", frequency="annual", **SHARE)
    edited = build(harness)
    decide(harness, actor, "back_to_review", [second])
    assert review_watermark(harness.campaign.pk) == 2
    undone = build(harness)
    assert undone.pledge_edits == []
    assert totals(undone) == [Decimal("1234.50"), Decimal("900.00")]
    edit(harness, actor, second, amount="0", frequency="")
    zero = build(harness)
    assert zero.pledge_edits[0]["frequency"] is None
    assert totals(zero) == [Decimal("1234.50"), Decimal("0.00")]
    opened = harness.campaign.active_configuration.starts_at
    with campaign_clock(opened + timedelta(days=1, hours=3)):
        again(harness, annual_pledge="700")
    newer = build(harness)
    assert newer.pledge_edits == []
    assert totals(newer) == [Decimal("1234.50"), Decimal("700.00")]
    assert all(verified(g) for g in (edited, undone, zero, newer))


def test_the_dashboard_shows_updating_until_the_generation_of_a_review(
    response_service,
):
    """A review makes the current inputs differ (the dashboard's Updating)
    until a generation of that watermark exists."""
    harness, _first, second = two_days(response_service)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        expected, _ = current_inputs(harness.campaign.pk, "historical")
    assert expected.review_watermark == 0
    edit(harness, admin(), second, amount="2000", frequency="annual", **SHARE)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        expected, _ = current_inputs(harness.campaign.pk, "historical")
    assert expected.review_watermark == 1


def test_the_comparison_counts_the_generations_edits(scenario):
    """Day N's annual pledges, pledging Families, quartiles and bands count
    the Edit, and equal the fact row they are checked against; the
    generation built before it keeps the Family's answer."""
    harness, ready, _browser, actor = scenario
    household = Submission.objects.filter(family__family_duid=1, mode="live").latest(
        "campaign_sequence"
    )
    edit(harness, actor, household, amount="3000", frequency="annual", **SHARE)
    newest = SourceSnapshot.objects.filter(state="promoted").latest("generation")
    record, claim = allocation(
        harness,
        watermark=len(PLEDGES),
        source=newest,
        reviews=review_watermark(harness.campaign.pk),
    )
    edited = materialize_fact_set(record.pk, claim, admit=permit)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        found = current_figures(edited.pk, first_day(edited), None)
        old = current_figures(ready.pk, first_day(ready), None)
    amounts = [Decimal("3000"), Decimal("500"), Decimal("250")]
    assert Decimal(found["pledge_total"]) == first_day(edited).pledge_total
    assert Decimal(found["pledge_total"]) == sum(amounts)
    assert found["pledging"] == 3
    # Quartiles of 250, 500 and 3,000 (inclusive method).
    assert [Decimal(value) for value in found["quartiles"]] == [
        Decimal("375.00"),
        Decimal("500.00"),
        Decimal("1750.00"),
    ]
    assert Decimal(old["pledge_total"]) == first_day(ready).pledge_total
    assert Decimal(old["pledge_total"]) == Decimal("1984.50")


def test_the_archive_export_carries_the_edit_and_matches_its_facts(
    scenario, monkeypatch
):
    """The families, submissions and daily sheets carry the value in force,
    the Edit's amount and frequency, and the file still matches its facts."""
    monkeypatch.setattr(check, "check_archive", in_process)
    harness, _ready, _browser, actor = scenario
    household = Submission.objects.filter(family__family_duid=2, mode="live").latest(
        "campaign_sequence"
    )
    edit(harness, actor, household, amount="1200", frequency="monthly", **SHARE)
    newest = SourceSnapshot.objects.filter(state="promoted").latest("generation")
    record, claim = allocation(
        harness,
        watermark=len(PLEDGES),
        source=newest,
        reviews=review_watermark(harness.campaign.pk),
    )
    edited = materialize_fact_set(record.pk, claim, admit=permit)
    written = export_archive(
        edited.pk,
        cutoff_date=first_day(edited).local_date,
        final=False,
        converted_at=datetime(2054, 10, 20, 9, 0, tzinfo=UTC),
    )
    archive = read_archive(written.data)
    family = {f["family_id"]: f for f in archive.families}[2]
    assert (family["annual_pledge"], family["frequency"]) == (
        Decimal("1200"),
        "monthly",
    )
    assert family["installment_amount"] == Decimal("100")
    [day] = archive.daily
    assert day["cumulative_annual_pledged"] == first_day(edited).pledge_total
    assert day["cumulative_annual_pledged"] == Decimal("2684.50")


@pytest.mark.parametrize(
    "service,allowed",
    [
        (ServiceRole.WEB, True),
        (ServiceRole.WORKER, True),
        (ServiceRole.SCHEDULER, True),
        (ServiceRole.MAIL_DISPATCH, False),
    ],
)
def test_only_the_named_logins_read_the_watermark(response_service, service, allowed):
    """The count-only definer serves web, the worker and the scheduler."""
    harness, _first, _second = two_days(response_service)
    with task_login(service, exact=True, reconnect=True), connection.cursor() as cursor:
        if allowed:
            cursor.execute(
                f"SELECT {WATERMARK.split('(')[0]}(%s)", [harness.campaign.pk]
            )
            assert cursor.fetchone()[0] == 0
        else:
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(
                    f"SELECT {WATERMARK.split('(')[0]}(%s)", [harness.campaign.pk]
                )


def test_only_the_worker_reads_a_generations_edits(response_service):
    """The Edits read is the worker's; web is refused, even granted EXECUTE."""
    harness, _first, second = two_days(response_service)
    edit(harness, admin(), second, amount="2000", frequency="annual", **SHARE)
    name = EDITS.split("(")[0]
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        [found] = run(f"SELECT {name}(%s)::text", [harness.campaign.pk])
    assert '"annual_pledge": "2000.00"' in found
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="permission denied"),
    ):
        run(f"SELECT {name}(%s)", [harness.campaign.pk])
    # A login granted EXECUTE is still refused by the body.
    with transaction.atomic():
        run("CREATE ROLE pk_t2202_probe LOGIN NOINHERIT")
        run(f"GRANT EXECUTE ON FUNCTION {EDITS} TO pk_t2202_probe")
        run("SET SESSION AUTHORIZATION pk_t2202_probe")
        with (
            pytest.raises(DatabaseError, match="Only the worker"),
            transaction.atomic(),
        ):
            run(f"SELECT {name}(%s)", [harness.campaign.pk])
        run("RESET SESSION AUTHORIZATION")
        transaction.set_rollback(True)


def test_the_database_records_the_edits_and_keeps_them(response_service):
    """Whatever the INSERT names, the generation holds the Edits in force
    as it was created, and they can never change."""
    harness, _first, second = two_days(response_service)
    edit(harness, admin(), second, amount="2000", frequency="annual", **SHARE)
    record, _claim = allocation(
        harness, watermark=2, reviews=review_watermark(harness.campaign.pk)
    )
    record.refresh_from_db()
    assert record.pledge_edits[0]["annual_pledge"] == "2000.00"
    for change in ("pledge_edits='[]'", "review_watermark=9"):
        with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
            run(
                f"UPDATE stewardship_daily_fact_set SET {change}, "
                "version=version+1 WHERE id=%s",
                [record.pk],
            )


def test_demand_never_regresses_its_review_watermark(response_service):
    """A Family's response keeps the demand's watermark; a lower one is
    refused by the demand guard."""
    from parishkit.stewardship.reports.models import CampaignFactRebuildDemand

    harness, _first, second = two_days(response_service)
    demand = CampaignFactRebuildDemand.objects.get(
        campaign=harness.campaign, population_scope="historical"
    )
    assert demand.requested_review_watermark == 0
    run(
        "UPDATE stewardship_fact_demand SET requested_review_watermark=5, "
        "pending_revision=pending_revision+1, version=version+1, "
        "pending_first_at=coalesce(pending_first_at,pending_last_at) WHERE id=%s",
        [demand.pk],
    )
    opened = harness.campaign.active_configuration.starts_at
    with campaign_clock(opened + timedelta(days=1, hours=4)):
        again(harness, annual_pledge="800")
    demand.refresh_from_db()
    assert demand.requested_review_watermark == 5
    with pytest.raises(DatabaseError, match="preserve"), transaction.atomic():
        run(
            "UPDATE stewardship_fact_demand SET requested_review_watermark=4, "
            "pending_revision=pending_revision+1, version=version+1 WHERE id=%s",
            [demand.pk],
        )


@pytest.mark.parametrize(
    "change_sql,message",
    [
        (
            f"ALTER FUNCTION {WATERMARK} SECURITY INVOKER",
            "did not install the definers",
        ),
        (
            f"GRANT EXECUTE ON FUNCTION {EDITS} TO PUBLIC",
            "unexpected owners or grants",
        ),
        (
            "ALTER FUNCTION public.stewardship_campaign_comparison_v1"
            "(uuid,date,numeric[],uuid) SECURITY INVOKER",
            "did not replace the comparison",
        ),
        (
            "ALTER TABLE stewardship_daily_fact_set "
            "DISABLE TRIGGER stewardship_fact_pledge_edits",
            "recording trigger",
        ),
    ],
)
def test_the_migration_check_refuses_a_changed_install(change_sql, message):
    """The frozen file's closing check passes as installed (with the grants
    database-grants adds removed) and refuses a definer that lost SECURITY
    DEFINER or gained a PUBLIC grant, a comparison run as invoker and a
    disabled recording trigger."""
    check_sql = FROZEN.read_text(encoding="utf-8")
    check_sql = check_sql[check_sql.index("DO $check$") :]
    # The captured settings the check compares with: as they are now.
    capture = FROZEN.read_text(encoding="utf-8")
    capture = capture[
        capture.index("SELECT set_config('stewardship.migration_0185_acl'") :
    ]
    capture = capture[: capture.index("), true);") + len("), true);")]
    with transaction.atomic(), connection.cursor() as cursor:
        for function in (WATERMARK, EDITS):
            cursor.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
            cursor.execute(
                "SELECT string_agg(quote_ident(r.rolname),',') FROM pg_proc p "
                "CROSS JOIN LATERAL aclexplode(p.proacl) a JOIN pg_roles r "
                "ON r.oid=a.grantee WHERE p.oid=%s::regprocedure "
                "AND a.grantee<>p.proowner",
                [function],
            )
            holders = cursor.fetchone()[0]
            if holders:
                cursor.execute(f"REVOKE ALL ON FUNCTION {function} FROM {holders}")
        cursor.execute(capture)
        cursor.execute(check_sql)
        cursor.execute(change_sql)
        with pytest.raises(DatabaseError, match=message), transaction.atomic():
            cursor.execute(check_sql)
        transaction.set_rollback(True)


def test_accept_advances_the_watermark_and_changes_no_figure(response_service):
    """Every review action makes new inputs; Accept leaves the figures."""
    harness, _first, second = two_days(response_service)
    before = build(harness)
    decide(harness, admin(), "accept", [second])
    after = build(harness)
    assert (before.review_watermark, after.review_watermark) == (0, 1)
    assert after.pledge_edits == [] and totals(after) == totals(before)


def test_an_edit_past_the_submission_watermark_counts_nowhere(response_service):
    """A generation counts no version past its submission watermark, so an
    Edit of such a response changes none of its days."""
    harness, first, second = two_days(response_service)
    edit(harness, admin(), second, amount="2000", frequency="annual", **SHARE)
    record, claim = allocation(
        harness,
        watermark=first.campaign_sequence,
        reviews=review_watermark(harness.campaign.pk),
    )
    generation = materialize_fact_set(record.pk, claim, admit=permit)
    assert generation.pledge_edits[0]["from_sequence"] == second.campaign_sequence
    assert totals(generation) == [Decimal("1234.50"), Decimal("1234.50")]
    assert verified(generation)


def test_a_watermark_ahead_of_the_campaigns_is_refused(response_service):
    """No generation claims reviews the campaign has not had."""
    harness, _first, _second = two_days(response_service)
    with pytest.raises(DatabaseError, match="ahead of the campaign"):
        allocation(harness, watermark=2, reviews=1)
