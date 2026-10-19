"""The upcoming-year pledge a Family answered against (#1753).

Frozen migration 0145 (provisional number,
``schema/migrations/0145_submission_upcoming_pledge.sql``) records on each
response ParishSoft's pledge for the campaign's upcoming period in the
snapshot the submit validates against (only with its proven giving read),
guards it in SQL, and reads it as the financial stream's A in the shared
stage selection. These tests submit through the real Family form on the web
login and promote real refreshes of synthetic giving
(``test_pledge_refresh_postgresql.giving``; review pipeline spec, "Financial
stream" and "Resubmission while the setting is off").
"""

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.reports.pledge_models import PledgeOpenCheck, PledgeReview
from parishkit.stewardship.reports.pledge_review_views import ALREADY, NOTHING
from parishkit.stewardship.responses import submission as submit_module
from parishkit.stewardship.responses.models import Submission

from ..test_financial_answers import CHECK, OPTIONS
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_ministry_responses_postgresql import revisit
from .test_pledge_refresh_postgresql import ANSWER, checks, refresh, row
from .test_pledge_review_postgresql import admin, again, decide, forge, pledged
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FILE = SCHEMA / "migrations/0145_submission_upcoming_pledge.sql"


def answered(response_service, *amounts, covered=True, **answer):
    """A live pledge answered while ParishSoft held ``amounts`` (2055 pledges).

    ``covered`` False promotes that refresh with no giving proof, as one
    whose giving read was incomplete.
    """
    harness = response_service
    financial_source(harness, options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    refresh(harness, *amounts, covered=covered)
    with web_login():
        found = pledge(
            harness, load_form(harness), **({"shares": {CHECK: ""}} | answer)
        )
    return harness, Submission.objects.get(pk=found.pk)


def stage_row(harness, submission):
    """The stage selection's (stage, check, observed, baseline, next_baseline)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stage, check_outcome, observed, baseline, next_baseline "
            "FROM stewardship_pledge_stages_v1(%s, %s)",
            [harness.campaign.pk, submission.family_id],
        )
        return cursor.fetchone()


def line(harness, submission):
    """The Pledges to enter row's line under its Stage, as text."""
    from parishkit.stewardship.reports.pledge_review_views import _stage_line

    found = row(harness, submission)
    return str(_stage_line(found, None))


def test_the_submit_records_parishsoft_amount_and_reads_it_as_a(response_service):
    """The response records ParishSoft's upcoming-year pledge at the submit;
    a refresh still finding it is unchanged (A), not the Conflict a $0.00 A
    made it, and a decision records that A."""
    harness, submission = answered(response_service, "300.00")
    assert submission.upcoming_pledge == Decimal("300.00")
    assert stage_row(harness, submission) == (
        "to_review",
        None,
        None,
        Decimal("300.00"),
        Decimal("300.00"),
    )
    refresh(harness, "300.00")
    assert not PledgeOpenCheck.objects.exists()
    assert row(harness, submission)["placement"].stage is Stage.TO_REVIEW
    decide(harness, admin(), "accept", [submission])
    assert PledgeReview.objects.get().baseline_pledge == Decimal("300.00")
    refresh(harness, "300.00")
    assert checks() == []
    refresh(harness, "350.00")
    assert checks() == [("conflict", Decimal("350.00"))]


def test_an_unproven_giving_read_records_none_and_the_submit_goes_on(
    response_service,
):
    """Without the snapshot's giving proof the response records no amount,
    the submit still succeeds, and A reads as $0.00, as before."""
    harness, submission = answered(response_service, "300.00", covered=False)
    assert submission.upcoming_pledge is None
    assert stage_row(harness, submission) == (
        "to_review",
        None,
        None,
        Decimal("0.00"),
        None,
    )


def test_a_new_pledge_parishsoft_already_holds_is_confirmed_at_submit(
    response_service,
):
    """Rule 4 with A known: a first answer ParishSoft already holds is
    Confirmed at once ("Already in ParishSoft."), nothing is offered, the
    guard refuses a decision and no refresh checks it again."""
    harness, submission = answered(response_service, "1000.00", "234.50")
    assert submission.upcoming_pledge == Decimal(ANSWER)
    assert stage_row(harness, submission)[:3] == (
        "confirmed",
        "confirmed",
        Decimal(ANSWER),
    )
    found = row(harness, submission)
    assert (found["placement"].stage, found["placement"].decision) == (
        Stage.CONFIRMED,
        None,
    )
    assert ALREADY in line(harness, submission)
    with pytest.raises(ValueError):
        decide(harness, admin(), "accept", [submission])
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="authority, version or state"),
        work_transaction(),
    ):
        forge(submission, admin())
    refresh(harness, "1.00")
    assert not PledgeOpenCheck.objects.exists() and checks() == []


def test_a_zero_pledge_parishsoft_holds_none_of_has_nothing_to_enter(
    response_service,
):
    """A $0.00 answer with no ParishSoft pledge is Confirmed at submit with
    the $0.00 words."""
    harness, submission = answered(
        response_service, annual_pledge="0", frequency="", shares={}
    )
    assert submission.upcoming_pledge == Decimal("0.00")
    assert row(harness, submission)["placement"].stage is Stage.CONFIRMED
    assert NOTHING in line(harness, submission)


def test_a_pledge_set_back_to_parishsoft_value_is_withdrawn_as_confirmed(
    response_service,
):
    """Rule 3: a Family changing its pledge to the amount ParishSoft holds
    at the submit leaves To enter by hand (the earlier Accept is no longer
    in force) and is Confirmed with no decision."""
    harness, submission = pledged(response_service)
    decide(harness, admin(), "accept", [submission])
    refresh(harness, "500.00")
    assert row(harness, submission)["placement"].stage is Stage.CONFLICT
    changed = again(harness, annual_pledge="500")
    found = row(harness, changed)
    assert (found["placement"].stage, found["placement"].decision) == (
        Stage.CONFIRMED,
        None,
    )
    assert found["placement"].held_decision is None


def test_an_entered_earlier_version_keeps_the_new_answer_to_review(
    response_service,
):
    """An earlier version already marked Entered by hand is not undone: a
    new answer equal to ParishSoft's stays To review for Staff to see."""
    harness, submission = pledged(response_service)
    decide(harness, admin(), "accept", [submission])
    decide(harness, admin(), "entered_by_hand", [submission])
    with web_login():
        changed = pledge(
            harness, revisit(harness), annual_pledge="0", frequency="", shares={}
        )
    assert changed.upcoming_pledge == Decimal("0.00")
    assert row(harness, changed)["placement"].stage is Stage.TO_REVIEW


def test_an_unchanged_answer_keeps_a_a_changed_one_takes_the_new_amount(
    response_service,
):
    """Rule 1 keeps A (the amount recorded where the answer began), though
    the unchanged response records ParishSoft's newer amount; rule 2 takes
    the changed response's own."""
    harness, submission = pledged(response_service)
    assert submission.upcoming_pledge == Decimal("0.00")
    refresh(harness, "900.00")
    assert row(harness, submission)["placement"].stage is Stage.CONFLICT
    same = again(harness)
    assert same.upcoming_pledge == Decimal("900.00")
    assert stage_row(harness, same)[0] == "conflict"
    assert stage_row(harness, same)[3] == Decimal("0.00")
    changed = again(harness, annual_pledge="99")
    assert stage_row(harness, changed)[0] == "to_review"
    assert stage_row(harness, changed)[3] == Decimal("900.00")


def test_an_accepted_pledge_keeps_its_decision_and_a(response_service):
    """A decision taken on a To review pledge records the item's A, which
    the stage selection reads back while the answer is unchanged."""
    harness, submission = answered(response_service, "300.00")
    decide(harness, admin(), "accept", [submission])
    found = row(harness, submission)["placement"]
    assert (found.stage, found.decision) == (Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)
    assert stage_row(harness, submission)[3:] == (
        Decimal("300.00"),
        Decimal("300.00"),
    )


@pytest.mark.parametrize("fault", ["amount", "unproven"])
def test_the_response_guard_refuses_an_amount_not_the_snapshots(
    response_service, monkeypatch, fault
):
    """A recorded amount that is not the validation snapshot's own is
    refused, so a writer cannot plant an A; nothing of the submit commits.

    The ``unproven`` case is refused because the selection reads no amount
    (NULL) on a snapshot without a valid giving date, so any planted amount,
    even $0.00, differs from it."""
    harness, submission = answered(
        response_service, "300.00", covered=fault == "amount"
    )
    with web_login():
        form = revisit(harness)
    answers = answers_for(form)
    answers["financial"] = {
        "annual_pledge": "10",
        "frequency": "annual",
        "shares": {CHECK: ""},
    }
    planted = Decimal("1.00") if fault == "amount" else Decimal("0.00")
    monkeypatch.setattr(submit_module, "answered_pledge", lambda *args: planted)
    before = Submission.objects.count()
    with (
        web_login(),
        pytest.raises(IntegrityError, match="upcoming pledge must be") as refused,
        transaction.atomic(),
    ):
        submit_module.submit_family(
            harness.request,
            harness.service,
            baseline_id=UUID(form["baseline"]),
            payload=answers,
        )
    assert refused.value.__cause__.sqlstate == "23514"
    assert Submission.objects.count() == before


def test_the_web_login_gains_no_new_authority(response_service):
    """The column is part of the row web already inserts and reads (the
    Family submit's login): no login may update it, its guard is no
    definer, and no login is granted a new routine for it."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.runtime_grants import runtime_functions, runtime_grants

    with task_login(ServiceRole.WEB, exact=True), connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_column_privilege(current_user,"
            "'public.stewardship_submission','upcoming_pledge',p) "
            "FROM unnest(ARRAY['SELECT','INSERT','UPDATE']) p"
        )
        assert [found for (found,) in cursor.fetchall()] == [True, True, False]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT prosecdef FROM pg_proc "
            "WHERE proname='stewardship_submission_pledge_guard_v1'"
        )
        assert cursor.fetchall() == [(False,)]
    for role in [*ServiceRole, "download"]:
        try:
            tables, columns = runtime_grants(role)
        except ConfigError:
            continue
        assert "UPDATE" not in tables.get("stewardship_submission", set()), role
        assert "upcoming_pledge" not in columns.get("stewardship_submission", {}).get(
            "UPDATE", set()
        ), role
        assert not any("submission_pledge" in name for name in runtime_functions(role))


def test_the_migration_check_refuses_the_earlier_selection(response_service):
    """0145's DO block refuses to commit while 0142's stage selection (A read
    as $0.00) or no response guard is installed."""
    earlier = (SCHEMA / "migrations/0142_pledge_conflicts.sql").read_text(
        encoding="utf-8"
    )
    new = FILE.read_text(encoding="utf-8")
    block = new[new.index("DO $check$") :]
    start = earlier.index("CREATE FUNCTION public.stewardship_pledge_stages_v1(")
    body = earlier[start : earlier.index("\n$$;", start) + len("\n$$;")]
    for statement, words in (
        (
            body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
            "does not read the answered pledge",
        ),
        (
            "DROP TRIGGER stewardship_submission_pledge_guard "
            "ON public.stewardship_submission",
            "not guarded as declared",
        ),
    ):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement)
            with pytest.raises(DatabaseError, match=words), transaction.atomic():
                cursor.execute(block)
            transaction.set_rollback(True)
    with connection.cursor() as cursor:
        cursor.execute(block)


def test_a_source_row_the_selection_cannot_read_records_none(
    response_service, monkeypatch
):
    """A database error in the upcoming pledge read is kept in its savepoint:
    the response records no amount and the Family's submit still commits."""
    from parishkit.stewardship.reports import pledge_refresh

    def broken(*args):
        """Fail as a malformed source row would, inside the read."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1/0")

    monkeypatch.setattr(pledge_refresh, "_upcoming", broken)
    harness, submission = answered(response_service, "300.00")
    assert submission.upcoming_pledge is None
    assert stage_row(harness, submission)[0] == "to_review"


def test_a_run_that_began_unrecorded_keeps_a_at_zero(response_service):
    """Rule 1 against a response that recorded nothing (every response
    before 0145): an unchanged resubmission records ParishSoft's newer
    amount but keeps A at $0.00, so the Conflict that amount made stays,
    and the item's A is still read as $0.00."""
    harness, submission = answered(response_service, covered=False)
    assert submission.upcoming_pledge is None
    refresh(harness, "500.00")
    assert row(harness, submission)["placement"].stage is Stage.CONFLICT
    same = again(harness)
    assert same.upcoming_pledge == Decimal("500.00")
    assert stage_row(harness, same) == (
        "conflict",
        "conflict",
        Decimal("500.00"),
        Decimal("0.00"),
        Decimal("500.00"),
    )
    refresh(harness, "500.00")
    assert PledgeOpenCheck.objects.count() == 1
