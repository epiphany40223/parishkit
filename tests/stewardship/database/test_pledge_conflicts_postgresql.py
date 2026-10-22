"""The refresh re-evaluation of To review and conflicting pledges (#1747).

Frozen migration 0135 (provisional number,
``schema/migrations/0135_pledge_conflicts.sql``) checks To review pledges
(``stewardship_pledge_open_check``), adds a Conflict outcome for an accepted
pledge not yet marked, records the item's A on the decision taken on a
Conflict, and places every pledge through one stage selection the page, the
counts and the pledge review guard share. These tests promote real refreshes
of synthetic giving (``test_pledge_refresh_postgresql.giving``) and read the
real page rows (review pipeline spec, "Financial stream", Pledge
confirmation).
"""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.reports.pledge_models import (
    PledgeOpenCheck,
    PledgeRefreshCheck,
    PledgeReview,
)

from .test_background_grants_postgresql import task_login
from .test_pledge_refresh_postgresql import (
    ANSWER,
    accepted,
    checks,
    giving,
    refresh,
    row,
)
from .test_pledge_review_postgresql import admin, again, decide, pledged
from .test_source_families_postgresql import promote

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"


def opened():
    """Every To review pledge check's (outcome, amount), oldest first."""
    return list(
        PledgeOpenCheck.objects.order_by("created_at", "id").values_list(
            "outcome", "observed"
        )
    )


def latest():
    """The latest decision row."""
    return PledgeReview.objects.order_by("-sequence").first()


def test_a_to_review_pledge_already_in_parishsoft_stays_to_review(
    response_service,
):
    """Rule 1 on To review, in strict phase order (#1890): the check that
    finds the pledge in ParishSoft leaves it To review with no reviewer,
    offering the decisions; the same amount again records nothing, another
    amount is checked again (at A: unchanged), and an Accept on it is
    recorded."""
    harness, submission = pledged(response_service)
    refresh(harness, "1000.00", "234.50")
    assert opened() == [("confirmed", Decimal(ANSWER))]
    check = PledgeOpenCheck.objects.get()
    assert (check.submission_id, check.item_version) == (submission.pk, 0)
    assert check.full_refresh and check.family_id == submission.family_id
    found = row(harness, submission)
    assert found["placement"].stage is Stage.TO_REVIEW
    assert found["placement"].decision is None
    with pytest.raises(ValueError):
        decide(harness, admin(), "entered_by_hand", [submission])
    refresh(harness, "1000.00", "234.50")
    assert opened() == [("confirmed", Decimal(ANSWER))]
    refresh(harness)
    assert opened() == [
        ("confirmed", Decimal(ANSWER)),
        ("unchanged", Decimal("0.00")),
    ]
    assert row(harness, submission)["placement"].stage is Stage.TO_REVIEW
    decide(harness, admin(), "accept", [submission])
    assert row(harness, submission)["placement"].stage is Stage.TO_ENTER_BY_HAND
    assert checks() == []


def test_a_to_review_conflict_is_decided_against_parishsoft(response_service):
    """A third amount on To review is a Conflict; the Accept taken on it
    records ParishSoft's amount as the new A, so the same amount is no new
    Conflict, and the pledge then moves on as any accepted one does."""
    harness, submission = pledged(response_service)
    refresh(harness, "900.00")
    assert opened() == [("conflict", Decimal("900.00"))]
    found = row(harness, submission)
    assert found["placement"].stage is Stage.CONFLICT
    assert found["item"].observed == Decimal("900.00")
    decide(harness, admin(), "accept", [submission])
    assert latest().baseline_pledge == Decimal("900.00")
    assert row(harness, submission)["placement"].stage is Stage.TO_ENTER_BY_HAND
    refresh(harness, "900.00")
    assert checks() == [] and len(opened()) == 1
    decide(harness, admin(), "entered_by_hand", [submission])
    mark = latest()
    assert (mark.baseline_pledge, mark.marked_pledge) == (
        Decimal("900.00"),
        Decimal("900.00"),
    )
    refresh(harness, ANSWER)
    assert checks() == [("confirmed", Decimal(ANSWER))]
    assert row(harness, submission)["placement"].stage is Stage.CONFIRMED


def test_a_conflict_follows_parishsoft_and_ends_back_at_a(response_service):
    """A Conflict is written again only when ParishSoft's amount moves, and a
    refresh finding A again returns the pledge to To review."""
    harness, submission = pledged(response_service)
    refresh(harness, "900.00")
    refresh(harness, "900.00")
    refresh(harness, "950.00")
    refresh(harness)
    assert opened() == [
        ("conflict", Decimal("900.00")),
        ("conflict", Decimal("950.00")),
        ("unchanged", Decimal("0.00")),
    ]
    assert row(harness, submission)["placement"].stage is Stage.TO_REVIEW


def test_an_accepted_pledge_conflict_keeps_its_decision_visible(response_service):
    """An accepted pledge not yet marked at a third amount is a Conflict
    holding its Accept; an Edit to ParishSoft's amount records it as A; the
    next refresh leaves it To enter by hand (ParishSoft already holds it,
    #1890), and once marked the refresh after Confirms the reviewer's
    value."""
    harness, submission = accepted(response_service, marked=False)
    refresh(harness, "900.00")
    assert checks() == [("conflict", Decimal("900.00"))]
    found = row(harness, submission)["placement"]
    assert (found.stage, found.decision, found.held_decision) == (
        Stage.CONFLICT,
        None,
        Decision.ACCEPTED,
    )
    for action in ("entered_by_hand", "back_to_review"):
        with pytest.raises(ValueError):
            decide(harness, admin(), action, [submission])
    decide(
        harness,
        admin(),
        "edit",
        [submission],
        value_amount="900",
        value_frequency="annual",
    )
    edit = latest()
    assert (edit.decision, edit.baseline_pledge) == (
        "accepted_edited",
        Decimal("900.00"),
    )
    refresh(harness, "900.00")
    found = row(harness, submission)["placement"]
    assert (found.stage, found.decision) == (
        Stage.TO_ENTER_BY_HAND,
        Decision.ACCEPTED_EDITED,
    )
    decide(harness, admin(), "entered_by_hand", [submission])
    refresh(harness, "900.00")
    found = row(harness, submission)["placement"]
    assert (found.stage, found.decision) == (
        Stage.CONFIRMED,
        Decision.ACCEPTED_EDITED,
    )


def test_an_accepted_conflict_back_at_a_is_to_enter_by_hand_again(
    response_service,
):
    """ParishSoft back at A ends the Conflict: unchanged, and the Accept is
    in force again (To enter by hand)."""
    harness, submission = accepted(response_service, marked=False)
    refresh(harness, "900.00")
    refresh(harness)
    assert checks() == [
        ("conflict", Decimal("900.00")),
        ("unchanged", Decimal("0.00")),
    ]
    found = row(harness, submission)["placement"]
    assert (found.stage, found.decision) == (Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)


def test_back_to_review_carries_the_conflicts_baseline(response_service):
    """A decided Conflict's A survives Back to review while the pledge is
    unchanged, so the same amount is no new Conflict; a changed pledge
    starts again at ParishSoft's amount at its submit (#1753), so that
    amount is no Conflict either, and a new one is."""
    harness, submission = pledged(response_service)
    refresh(harness, "900.00")
    decide(harness, admin(), "accept", [submission])
    decide(harness, admin(), "back_to_review", [submission])
    assert latest().baseline_pledge == Decimal("900.00")
    refresh(harness, "900.00")
    assert len(opened()) == 1
    assert row(harness, submission)["placement"].stage is Stage.TO_REVIEW
    changed = again(harness, annual_pledge="1000")
    assert changed.upcoming_pledge == Decimal("900.00")
    refresh(harness, "900.00")
    assert len(opened()) == 1
    assert row(harness, changed)["placement"].stage is Stage.TO_REVIEW
    refresh(harness, "950.00")
    assert opened()[-1] == ("conflict", Decimal("950.00"))
    assert PledgeOpenCheck.objects.latest("created_at").submission_id == changed.pk


def test_an_unchanged_answer_keeps_the_check_a_changed_one_leaves_it(
    response_service,
):
    """Rule 1 keeps a To review pledge's check when the Family answers the
    same again; rule 2 leaves it behind when the pledge changes."""
    harness, submission = pledged(response_service)
    refresh(harness, ANSWER)
    same = again(harness)
    # The check (ParishSoft holds it) still counts: To review, saying so.
    found = row(harness, same)
    assert found["placement"].stage is Stage.TO_REVIEW
    assert found["item"].check == "confirmed"
    changed = again(harness, annual_pledge="99")
    found = row(harness, changed)
    assert found["placement"].stage is Stage.TO_REVIEW
    assert found["item"].check is None


def test_a_decision_over_an_unseen_check_is_stale(response_service):
    """The page's version counts the checks: an Accept posted from a page
    drawn before a refresh found a Conflict is refused as someone else's
    change, so A never absorbs an amount nobody was shown."""
    from parishkit.stewardship.pipeline import Action
    from parishkit.stewardship.reports.pledge_review import (
        PledgeDecisions,
        read_items,
        version_of,
    )
    from parishkit.stewardship.web.review_worklist import (
        StaleDecision,
        WorklistDecision,
    )

    from .test_pledge_review_postgresql import Actor

    harness, submission = pledged(response_service)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        seen = version_of(read_items(harness.campaign.pk)[submission.pk])
    refresh(harness, "900.00")
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(StaleDecision),
    ):
        PledgeDecisions(harness.service.store, harness.campaign.pk).decide(
            Actor(admin()),
            WorklistDecision(Action.ACCEPT, {str(submission.pk): seen}, "", uuid4()),
        )
    assert not PledgeReview.objects.exists()
    decide(harness, admin(), "accept", [submission])
    assert latest().baseline_pledge == Decimal("900.00")


def test_a_mark_without_an_amount_reads_as_a(response_service):
    """A mark made while the giving read is unproven records no amount; the
    refresh reads it as A (here a Conflict's amount), so ParishSoft still at
    A is unchanged, not Entered incorrectly."""
    harness, submission = pledged(response_service)
    refresh(harness, "500.00")
    decide(harness, admin(), "accept", [submission])
    refresh(harness, "500.00", covered=False)
    decide(harness, admin(), "entered_by_hand", [submission])
    assert not latest().marked_pledge_known
    refresh(harness, "500.00")
    # Unchanged with no Not seen start owes no check; read as $0.00, the
    # mark would have been Entered incorrectly.
    assert checks() == []
    assert row(harness, submission)["placement"].stage is Stage.ENTERED_BY_HAND


def test_the_worker_login_records_open_checks(response_service):
    """Promotion on the source worker's own login reads To review pledges
    through its definer selection and inserts their checks."""
    harness, submission = pledged(response_service)
    snapshot, claim = giving(harness, "900.00")
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    assert opened() == [("conflict", Decimal("900.00"))]


def test_the_page_counts_and_shows_a_conflict(response_service):
    """The Stage filter counts Conflict; the row shows ParishSoft now."""
    from parishkit.stewardship.reports.pledge_review_views import (
        decorate,
        stage_table,
    )

    harness, submission = pledged(response_service)
    refresh(harness, "900.00")
    found = row(harness, submission)
    _table, counts = stage_table(
        [found], Stage.CONFLICT.value, {"paging": {}}, action="/x/"
    )
    assert counts == {Stage.CONFLICT.value: 1}
    (drawn,) = decorate([found])
    assert drawn["parishsoft_now"] == "$900.00"


# Each forged To review check, as a promotion of ParishSoft holding
# ``amounts`` would write it: its fields, and the guard's words.
FORGERIES = {
    "amount not the snapshot's": (
        {"outcome": "conflict", "observed": Decimal("900.00")},
        (),
        "snapshot's pledge",
    ),
    "confirmed at another amount": (
        {"outcome": "confirmed", "observed": Decimal("5.00")},
        ("5.00",),
        "differs from its",
    ),
    "conflict at the Family's amount": (
        {"outcome": "conflict", "observed": Decimal(ANSWER)},
        (ANSWER,),
        "differs from its",
    ),
    "conflict at A": (
        {"outcome": "conflict", "observed": Decimal("0.00")},
        (),
        "differs from its",
    ),
    "unchanged off A": (
        {"outcome": "unchanged", "observed": Decimal("5.00")},
        ("5.00",),
        "differs from its",
    ),
    "another version": (
        {"outcome": "unchanged", "observed": Decimal("0.00"), "item_version": 1},
        (),
        "requires a To review pledge",
    ),
    "a quick refresh called full": (
        {"outcome": "unchanged", "observed": Decimal("0.00"), "full_refresh": False},
        (),
        "promoting source worker",
    ),
}


@pytest.mark.parametrize("case", sorted(FORGERIES))
def test_the_open_guard_refuses_each_forgery_inside_a_promotion(
    response_service, monkeypatch, case
):
    """Each of the open check guard's refusals, met inside a real promotion:
    the refresh is rolled back and no forged check is written."""
    from parishkit.stewardship.responses import refresh_checks

    fields, amounts, words = FORGERIES[case]
    harness, submission = pledged(response_service)

    def forged(snapshot, corpus, campaign_id, full):
        """One check that the snapshot does not bear out."""
        yield PledgeOpenCheck(
            **(
                {
                    "snapshot_id": snapshot.pk,
                    "full_refresh": full,
                    "family_id": submission.family_id,
                    "submission_id": submission.pk,
                    "item_version": 0,
                }
                | fields
            )
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match=words):
        refresh(harness, *amounts)
    assert not PledgeOpenCheck.objects.exists()


@pytest.mark.parametrize(
    "fields,amounts",
    [
        # A Conflict is never recorded on a marked pledge, an accepted
        # pledge not yet marked is unchanged only at A, and a marked one
        # only at its mark.
        ({"outcome": "conflict", "observed": Decimal("900.00")}, ("900.00",)),
        ({"outcome": "unchanged", "observed": Decimal("900.00")}, ("900.00",)),
        ({"outcome": "unchanged", "observed": Decimal("900.00")}, ("900.00",)),
    ],
    ids=["conflict on a mark", "unchanged off A", "unchanged off the mark"],
)
def test_the_decided_guard_knows_a_conflict(
    response_service, monkeypatch, request, fields, amounts
):
    """0134's check guard, replaced: a Conflict only before the mark, and
    an unmarked pledge unchanged only at A."""
    from parishkit.stewardship.responses import refresh_checks

    marked = request.node.callspec.id != "unchanged off A"
    harness, submission = accepted(response_service, marked=marked)
    review = latest()

    def forged(snapshot, corpus, campaign_id, full):
        """One decided check that the snapshot does not bear out."""
        yield PledgeRefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            review_id=review.pk,
            item_version=review.sequence,
            **fields,
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match="differs from its"):
        refresh(harness, *amounts)
    assert not PledgeRefreshCheck.objects.exists()


def test_open_checks_are_immutable(response_service):
    """Neither the owner nor anyone may change or delete an open check."""
    harness, submission = pledged(response_service)
    refresh(harness, "900.00")
    check = PledgeOpenCheck.objects.get()
    with connection.cursor() as cursor:
        for statement in (
            "UPDATE stewardship_pledge_open_check SET outcome='unchanged' WHERE id=%s",
            "DELETE FROM stewardship_pledge_open_check WHERE id=%s",
        ):
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(statement, [check.pk])


def test_the_migration_check_refuses_the_earlier_guards(response_service):
    """0135's DO block refuses to commit while 0134's review guard (which
    reads no check) or check guard (which knows no Conflict) is installed."""
    earlier = (SCHEMA / "migrations/0134_pledge_refresh_checks.sql").read_text(
        encoding="utf-8"
    )
    new = (SCHEMA / "migrations/0135_pledge_conflicts.sql").read_text(encoding="utf-8")
    block = new[new.index("DO $check$") :]

    def guard(name, created):
        """One of 0134's guard bodies, replacing the current one."""
        start = earlier.index(f"{created} public.{name}()")
        body = earlier[start : earlier.index("END $$;", start) + len("END $$;")]
        return body.replace(created, "CREATE OR REPLACE FUNCTION")

    for name, created, words in (
        (
            "stewardship_pledge_review_guard_v1",
            "CREATE OR REPLACE FUNCTION",
            "by their checks",
        ),
        (
            "stewardship_pledge_refresh_check_guard_v1",
            "CREATE FUNCTION",
            "does not know a Conflict",
        ),
    ):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(guard(name, created))
            with pytest.raises(DatabaseError, match=words), transaction.atomic():
                cursor.execute(block)
            transaction.set_rollback(True)
    with connection.cursor() as cursor:
        cursor.execute(block)


def test_the_migration_revokes_the_earlier_selection_from_the_worker(
    response_service,
):
    """An environment that granted 0134's v1 to the worker loses it in 0135
    (database-grants never revokes, and the worker refuses an undeclared
    definer), and the DO block refuses to commit while the grant remains."""
    new = (SCHEMA / "migrations/0135_pledge_conflicts.sql").read_text(encoding="utf-8")
    revoke = new[new.index("DO $revoke$") : new.index("DO $check$")]
    block = new[new.index("DO $check$") :]
    v1 = "public.stewardship_pledge_refresh_items_v1(uuid, uuid)"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE "
            "rolname='pk_stewardship_worker') THEN CREATE ROLE pk_stewardship_worker; "
            "END IF; END $$"
        )
        cursor.execute(f"GRANT EXECUTE ON FUNCTION {v1} TO pk_stewardship_worker")
        with (
            pytest.raises(DatabaseError, match="earlier pledge selection"),
            transaction.atomic(),
        ):
            cursor.execute(block)
        cursor.execute(revoke)
        cursor.execute(
            "SELECT has_function_privilege('pk_stewardship_worker', %s, 'EXECUTE')",
            [v1],
        )
        assert cursor.fetchone() == (False,)
        cursor.execute(block)
        transaction.set_rollback(True)
