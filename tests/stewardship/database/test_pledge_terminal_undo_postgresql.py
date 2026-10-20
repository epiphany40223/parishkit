"""Back to review undoes a pledge's terminal stage (#1840), in PostgreSQL.

No review pipeline stage is final (review pipeline spec, "Undoing a terminal
stage"): Back to review on an Entered by hand (or Not seen), Entered
incorrectly or Confirmed pledge removes only that step, with a required
note, for Staff and Administrators only. The decision behind it stays in
force with its values, so the pledge returns to To enter by hand; a pledge
ParishSoft already held, with no decision behind it, returns to To review.
Frozen migration 0213 (provisional number) widens the review rows' state
check and the pledge review guard. These tests run the real service on the
web login and real promotions, so the next refresh is seen to judge the
undone pledge as an open one, and the guard's refusals of rows the service
never writes, and the old guard's refusal of the undo.
"""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Action, Decision, Stage
from parishkit.stewardship.reports.pledge_models import (
    PledgeOpenCheck,
    PledgeRefreshCheck,
    PledgeReview,
)
from parishkit.stewardship.reports.pledge_review import (
    UNDO_NEEDS_NOTE,
    PledgeDecisions,
)
from parishkit.stewardship.web.review_worklist import StaleDecision, WorklistDecision

from .test_background_grants_postgresql import task_login
from .test_ministry_exports_postgresql import leader
from .test_pledge_refresh_postgresql import ANSWER, accepted, checks, refresh, row
from .test_pledge_review_postgresql import (
    GUARD,
    Actor,
    admin,
    decide,
    forge,
    pledged,
    state,
)
from .test_refresh_checks_postgresql import started  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0213_pledge_terminal_undo.sql"


def latest():
    """The pledge's latest review row."""
    return PledgeReview.objects.order_by("-sequence").first()


def undo(harness, submission, note="Marked by mistake"):
    """Back to review on ``submission`` by the Administrator, with ``note``."""
    return decide(harness, admin(), "back_to_review", [submission], note=note)


def placed(harness, submission):
    """The pledge's (stage, decision) as the page places it."""
    found = row(harness, submission)["placement"]
    return found.stage, found.decision


def test_undoing_a_mark_keeps_the_edit_and_needs_a_note(response_service):
    """The mark's undo needs a note; it keeps Accepted (edited) with its
    amount and frequency, records no ParishSoft amount, advances the
    version with its audit event, and the pledge is To enter by hand: it can
    be marked again, and a plain Back to review then sends it to To review."""
    harness, submission = pledged(response_service)
    actor = admin()
    decide(
        harness,
        actor,
        "edit",
        [submission],
        value_amount="1,300",
        value_frequency="quarterly",
    )
    decide(harness, actor, "entered_by_hand", [submission])
    with pytest.raises(ValueError, match=UNDO_NEEDS_NOTE):
        undo(harness, submission, note="")
    assert PledgeReview.objects.count() == 2
    assert undo(harness, submission) == "The pledge sent back to review."
    undone = latest()
    assert (undone.sequence, undone.action, undone.decision, undone.entered) == (
        3,
        "back_to_review",
        "accepted_edited",
        False,
    )
    assert (undone.reviewed_annual_pledge, undone.reviewed_frequency) == (
        Decimal("1300.00"),
        "quarterly",
    )
    assert not undone.marked_pledge_known and undone.marked_pledge is None
    assert undone.note == "Marked by mistake"
    assert state(harness, submission).stage == "to_enter_by_hand"
    context = AuditContext.objects.get(
        event=AuditEvent.objects.get(subject_id=undone.pk)
    ).context
    assert context == {"outcome": "changed", "before_version": 2, "after_version": 3}
    # The decision is still in force: mark it again, then undo the decision.
    decide(harness, actor, "entered_by_hand", [submission])
    assert state(harness, submission).stage == "entered_by_hand"
    undo(harness, submission)
    decide(harness, actor, "back_to_review", [submission])
    assert (latest().decision, state(harness, submission).stage) == (None, "to_review")


def test_a_leader_and_a_stale_version_cannot_undo(response_service, google):
    """A Ministry leader never undoes a mark, and an undo against a version
    someone else moved first is stale; neither records anything."""
    harness, submission = accepted(response_service)
    # The Accept's version: the mark has moved the pledge on since.
    stale = WorklistDecision(
        Action.BACK_TO_REVIEW, {str(submission.pk): 1}, "Late", uuid4()
    )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(StaleDecision),
    ):
        PledgeDecisions(harness.service.store, harness.campaign.pk).decide(
            Actor(admin()), stale
        )
    _browser, chair, _rule, _ = leader(harness, google)
    with pytest.raises(PermissionError):
        decide(harness, chair, "back_to_review", [submission], note="Chair")
    assert PledgeReview.objects.count() == 2


def test_an_entered_incorrectly_pledge_is_undone_and_judged_open_again(
    response_service,
    started,  # noqa: F811
):
    """Back to review on Entered incorrectly returns it to To enter by hand
    with its Accept; the check of the undone mark stays in history, and the
    next refresh judges the pledge as not marked: the same wrong amount is
    now a Conflict on the new row, and the Family's amount Confirms it."""
    harness, submission = accepted(response_service)
    refresh(harness, "1243.50")
    assert placed(harness, submission) == (Stage.ENTERED_INCORRECTLY, Decision.ACCEPTED)
    mark = latest()
    undo(harness, submission)
    assert placed(harness, submission) == (Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)
    refresh(harness, "1243.50")
    assert checks() == [
        ("entered_incorrectly", Decimal("1243.50")),
        ("conflict", Decimal("1243.50")),
    ]
    first, second = PledgeRefreshCheck.objects.order_by("created_at", "id")
    assert first.review_id == mark.pk and second.review_id == latest().pk
    assert second.item_version == latest().sequence
    assert placed(harness, submission)[0] is Stage.CONFLICT
    refresh(harness, ANSWER)
    assert placed(harness, submission) == (Stage.CONFIRMED, Decision.ACCEPTED)


@pytest.mark.parametrize("marked", [True, False])
def test_a_confirmed_accepted_pledge_is_undone_and_checked_again(
    response_service, marked
):
    """Confirmed (marked or not) is undone to To enter by hand, Accepted;
    the confirmed check no longer places it, and the next refresh, which
    skipped it while Confirmed, checks the new row and Confirms it again."""
    harness, submission = accepted(response_service, marked=marked)
    refresh(harness, ANSWER)
    assert placed(harness, submission) == (Stage.CONFIRMED, Decision.ACCEPTED)
    undo(harness, submission)
    assert placed(harness, submission) == (Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)
    assert (latest().decision, latest().entered) == ("accepted", False)
    refresh(harness, ANSWER)
    assert checks() == [("confirmed", Decimal(ANSWER))] * 2
    assert PledgeRefreshCheck.objects.filter(review=latest()).count() == 1
    assert placed(harness, submission) == (Stage.CONFIRMED, Decision.ACCEPTED)


def test_already_in_parishsoft_is_undone_to_to_review(response_service):
    """A To review pledge Confirmed with no reviewer goes back to To review,
    with no decision; the open check at the old version no longer counts,
    and the next refresh writes one at the new version (here a Conflict)."""
    harness, submission = pledged(response_service)
    refresh(harness, ANSWER)
    assert placed(harness, submission) == (Stage.CONFIRMED, None)
    undo(harness, submission, note="Not this year's pledge")
    undone = latest()
    assert (undone.sequence, undone.decision) == (1, None)
    assert placed(harness, submission) == (Stage.TO_REVIEW, None)
    refresh(harness, "99.00")
    assert list(
        PledgeOpenCheck.objects.order_by("created_at", "id").values_list(
            "outcome", "item_version"
        )
    ) == [("confirmed", 0), ("conflict", 1)]
    assert placed(harness, submission) == (Stage.CONFLICT, None)


def test_not_seen_counts_again_from_a_later_mark(
    response_service,
    started,  # noqa: F811
):
    """A Not seen pledge undone is To enter by hand, unflagged; marked again,
    one more unchanged full refresh does not flag it: Not seen counts from
    the new mark, whose checks start afresh."""
    harness, submission = accepted(response_service)
    refresh(harness)
    refresh(harness)
    assert row(harness, submission)["not_seen"]
    undo(harness, submission)
    found = row(harness, submission)
    assert found["placement"].stage is Stage.TO_ENTER_BY_HAND
    assert not found["not_seen"]
    decide(harness, admin(), "entered_by_hand", [submission])
    refresh(harness)
    found = row(harness, submission)
    assert found["placement"].stage is Stage.ENTERED_BY_HAND
    assert not found["not_seen"]
    refresh(harness)
    assert row(harness, submission)["not_seen"]


MARKED = {"sequence": 3, "action": "back_to_review", "decision": "accepted"}
# Each forged undo of a mark the service never writes, and why it is refused.
FORGED = {
    "no_note": ({**MARKED}, GUARD),
    "blank_note": ({**MARKED, "note": "   "}, GUARD),
    "cleared": ({**MARKED, "decision": None, "note": "Why"}, GUARD),
    "other_values": (
        {
            **MARKED,
            "decision": "accepted_edited",
            "amount": "500.00",
            "frequency": "weekly",
            "note": "Why",
        },
        GUARD,
    ),
    "stale": ({**MARKED, "sequence": 2, "note": "Why"}, GUARD),
    # The guard (a BEFORE trigger) refuses it first; the state check would
    # refuse a kept Ignore too.
    "keeps_ignored": ({**MARKED, "decision": "ignored", "note": "Why"}, GUARD),
    "leader": ({**MARKED, "note": "Why"}, GUARD),
    # Admitted by the guard: refused only at commit, for its missing audit.
    "admitted": ({**MARKED, "note": "Why"}, "Pledge review requires its audit"),
}


@pytest.mark.parametrize("case", sorted(FORGED))
def test_the_guard_admits_only_a_noted_undo_keeping_the_decision(
    response_service, google, case
):
    """On an Entered by hand pledge, an undo with no (or a blank) note, one
    that clears or changes the decision, at an old version, keeping an
    Ignore or from a Ministry leader is refused at the INSERT; the noted
    undo keeping the Accept passes the guard."""
    harness, submission = accepted(response_service)
    actor = admin()
    if case == "leader":
        _browser, actor, _rule, _ = leader(harness, google)
    values, reason = FORGED[case]
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match=reason),
        work_transaction(),
    ):
        try:
            forge(submission, actor, **values)
        except DatabaseError:
            assert case != "admitted"
            raise
        assert case == "admitted"
    assert PledgeReview.objects.count() == 2


def test_a_decision_s_undo_still_clears_it(response_service):
    """Back to review of a decision not yet marked must clear it: keeping
    the Accept there is refused, even with a note."""
    harness, submission = accepted(response_service, marked=False)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match=GUARD),
        work_transaction(),
    ):
        forge(submission, admin(), **{**MARKED, "sequence": 2, "note": "Why"})


def guard_body(name, text, created="CREATE OR REPLACE FUNCTION"):
    """One function's statement as ``text`` installs it, replacing the current."""
    start = text.index(f"{created} public.{name}()")
    body = text[start : text.index("END $$;", start) + len("END $$;")]
    return body.replace(created, "CREATE OR REPLACE FUNCTION")


def test_the_old_guard_refuses_the_undo_and_the_files_check_refuses_it(
    response_service,
):
    """0135's guard refuses the undo the new one admits, and 0213's DO block
    refuses to commit over 0135's guard or 0127's state check."""
    harness, submission = accepted(response_service)
    old = (SCHEMA / "migrations/0135_pledge_conflicts.sql").read_text(encoding="utf-8")
    new = FROZEN.read_text(encoding="utf-8")
    block = new[new.index("DO $check$") :]
    name = "stewardship_pledge_review_guard_v1"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(guard_body(name, old))
        with pytest.raises(DatabaseError, match=GUARD), work_transaction():
            forge(submission, admin(), **{**MARKED, "note": "Why"})
        with (
            pytest.raises(DatabaseError, match="undo a terminal step"),
            transaction.atomic(),
        ):
            cursor.execute(block)
        cursor.execute(guard_body(name, new))
        with work_transaction():
            forge(submission, admin(), **{**MARKED, "note": "Why"})
        transaction.set_rollback(True)
    earlier = (SCHEMA / "migrations/0127_pledge_review.sql").read_text(encoding="utf-8")
    start = earlier.index('CONSTRAINT "pledge_review_action_state" CHECK')
    check = earlier[start : earlier.index(', CONSTRAINT "pledge_review_mark_shape"')]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_pledge_review "
            "DROP CONSTRAINT pledge_review_action_state"
        )
        cursor.execute(f"ALTER TABLE stewardship_pledge_review ADD {check}")
        with (
            pytest.raises(DatabaseError, match="kept decision"),
            transaction.atomic(),
        ):
            cursor.execute(block)
        transaction.set_rollback(True)
    with connection.cursor() as cursor:
        cursor.execute(block)


def test_the_state_check_keeps_only_an_accepted_decision_on_back_to_review(
    response_service,
):
    """With the guard set aside, the state check alone admits a Back to
    review keeping an accepted decision and refuses one keeping an Ignore."""
    harness, submission = accepted(response_service)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_pledge_review "
            "DISABLE TRIGGER stewardship_pledge_review_guard"
        )
        forge(submission, admin(), **{**MARKED, "note": "Why"})
        with (
            pytest.raises(DatabaseError, match="pledge_review_action_state"),
            transaction.atomic(),
        ):
            forge(submission, admin(), **{**MARKED, "decision": "ignored"})
        transaction.set_rollback(True)
