"""The campaign statistics count the pledge values in force (#2202).

The Response dashboard's Current annual pledges, its export's Summary and
``pk-admin report participation`` read the campaign statistics capture. It
counts each Family's latest response with a reviewer's Edit of the annual
pledge while that Edit is in force on it, else the Family's own answer, as
Financial stewardship does (reports spec, "Values in force on Financial
stewardship"), so the two totals agree. These tests drive real decisions
through the Pledges service and capture on the restricted web login.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.financial import parse_money
from parishkit.stewardship.reports.statistics import calculate_statistics
from parishkit.stewardship.reports.statistics_selection import capture_statistics

from ..test_financial_answers import CHECK
from .test_background_grants_postgresql import task_login
from .test_financial_report_postgresql import report
from .test_pledge_review_postgresql import admin, again, decide, pledged
from .test_pledge_share_methods_postgresql import edit

pytestmark = pytest.mark.django_db(transaction=True)

# The editor names a share method for a pledge above zero.
SHARE = {f"value_share_{CHECK}": "on"}
FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0184_statistics_in_force.sql"
)
DEFINER = "public.stewardship_pledge_edits_in_force_v1(uuid)"


def current_annual(harness):
    """The statistics' active annual pledge total in cents, on the web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        statistics = calculate_statistics(capture_statistics(harness.campaign.pk))
    return statistics.active.annual_pledge.cents


def financial_annual(harness):
    """Financial stewardship's annual total in cents, for comparison."""
    return report(harness)["summary"]["annual_total"].cents


def test_an_edit_in_force_is_the_statistics_pledge_until_it_is_undone(
    response_service,
):
    """An Edit's amount counts through Entered by hand and its undo; Back to
    review of the decision returns the Family's own answer. Every step
    agrees with Financial stewardship's total."""
    harness, submission = pledged(response_service)
    actor = admin()
    assert current_annual(harness) == 123450 == financial_annual(harness)
    edit(harness, actor, submission, amount="2000", frequency="annual", **SHARE)
    assert current_annual(harness) == 200000 == financial_annual(harness)
    decide(harness, actor, "entered_by_hand", [submission])
    assert current_annual(harness) == 200000 == financial_annual(harness)
    decide(harness, actor, "back_to_review", [submission], note="Typed wrong")
    assert current_annual(harness) == 200000 == financial_annual(harness)
    decide(harness, actor, "back_to_review", [submission])
    assert current_annual(harness) == 123450 == financial_annual(harness)
    # Accept and Ignore of the Family's answer change nothing.
    decide(harness, actor, "accept", [submission])
    assert current_annual(harness) == 123450


def test_a_newer_answer_or_a_zero_edit(response_service):
    """A newer, different pledge answer from the Family sets the Edit aside;
    an Edit to zero counts as zero."""
    harness, submission = pledged(response_service)
    actor = admin()
    edit(harness, actor, submission, amount="2000", frequency="annual", **SHARE)
    newer = again(harness, annual_pledge="900")
    # The capture's latest response is the effective one 0181 names.
    family = FamilyCampaign.objects.get(pk=newer.family_id)
    assert family.effective_submission_id == newer.pk
    assert current_annual(harness) == 90000 == financial_annual(harness)
    edit(harness, actor, newer, amount="0", frequency="")
    assert current_annual(harness) == 0 == financial_annual(harness)
    assert report(harness)["summary"]["annual_total"] == parse_money("0.00")


def test_the_general_worker_captures_the_values_in_force(response_service):
    """The worker reads no answers or pledge reviews, yet its capture counts
    the Edit, through 0184's definer."""
    harness, submission = pledged(response_service)
    edit(harness, admin(), submission, amount="2000", frequency="annual", **SHARE)
    with task_login(ServiceRole.WORKER, reconnect=True):
        statistics = calculate_statistics(capture_statistics(harness.campaign.pk))
    assert statistics.active.annual_pledge.cents == 200000 == financial_annual(harness)


@pytest.mark.parametrize(
    ("change_sql", "message"),
    [
        (f"ALTER FUNCTION {DEFINER} SECURITY INVOKER", "not installed as declared"),
        (
            f"GRANT EXECUTE ON FUNCTION {DEFINER} TO PUBLIC",
            "unexpected owner or grants",
        ),
        (
            f"ALTER FUNCTION {DEFINER} SET search_path TO public",
            "not installed as declared",
        ),
        (
            f"GRANT EXECUTE ON FUNCTION {DEFINER} TO pk_stewardship_web",
            "unexpected owner or grants",
        ),
        (f"ALTER FUNCTION {DEFINER} OWNER TO pk_t2202_other", "unexpected owner"),
        (
            # A replacement without the login check; CREATE OR REPLACE also
            # drops SECURITY DEFINER, as the file's check expects.
            "CREATE OR REPLACE FUNCTION public.stewardship_pledge_edits_in_force_v1("
            "campaign_uuid uuid) RETURNS TABLE(family_id uuid, submission_id uuid,"
            " annual_pledge numeric) LANGUAGE plpgsql STABLE"
            " SET search_path TO pg_catalog,public,pg_temp AS $f$ BEGIN RETURN QUERY"
            " SELECT k.family_id,k.submission_id,v.reviewed_annual_pledge::numeric"
            " FROM public.stewardship_pledge_review_states_v1(campaign_uuid,NULL) k"
            " JOIN public.stewardship_pledge_review v ON v.id=k.review_id"
            " WHERE k.in_force AND v.decision='accepted_edited'; END $f$",
            "not installed as declared",
        ),
    ],
)
def test_the_migration_check_refuses_a_changed_definer(change_sql, message):
    """The frozen file's closing check passes as installed and refuses a
    definer that lost SECURITY DEFINER, gained a grant or a search_path."""
    check = FROZEN.read_text(encoding="utf-8")
    check = check[check.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
        for role in ("pk_stewardship_web", "pk_t2202_other"):
            cursor.execute(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE "
                f"rolname='{role}') THEN CREATE ROLE {role}; END IF; END $$"
            )
        cursor.execute(change_sql)
        with pytest.raises(DatabaseError, match=message), transaction.atomic():
            cursor.execute(check)
        transaction.set_rollback(True)
