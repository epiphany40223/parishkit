"""Entered incorrectly's two fixes (#1737), in PostgreSQL.

A census change Entered by hand that a refresh finds at a third value is
Entered incorrectly (#1689). Staff fix it on its row (review pipeline spec,
"Entered incorrectly stage" and "Refresh checks"): **Entered by hand
again** records a new mark whose value is ParishSoft's in the check that
found it, so the next refreshes compare with that value; **Back to review**
undoes the mark (#1840), back to To enter by hand with its decision kept,
and once more sends it to To review for an Edit to ParishSoft's value.
Migration 0133 (provisional number) admits Entered by hand again only while
the latest check says Entered incorrectly, and checks every census refresh
check against the value recorded at the mark; 0210 (provisional) makes Back
to review the undo of any terminal step. The changes are made through the
real service on the web login and re-evaluated by real promotions.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.responses.models import ProposalResolution, ProposedChange
from parishkit.stewardship.workflows.models import RefreshCheck

from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide
from .test_census_review_postgresql import fresh
from .test_refresh_checks_postgresql import (
    census_row,
    checks,
    prepared,
    refresh,
    started,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)


def incorrect(response_service):
    """Member 3's prefix marked Entered by hand ("Dr."), then found "Rev."."""
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    refresh(harness, member={"salutation": "Rev."})
    proposal = fresh(rows["prefix"])
    assert census_row(harness, proposal)["stage"] == Stage.ENTERED_INCORRECTLY
    return harness, staff, proposal


@pytest.mark.usefixtures("started")
def test_entered_by_hand_again_compares_with_parishsofts_value(response_service):
    """Marking it again keeps the change entered at its next version, so it
    is Entered by hand; ParishSoft still at the value the check found is
    unchanged (and Not seen after two full refreshes, counted from the new
    mark), yet another value is Entered incorrectly again, and the
    reviewer's value confirms it."""
    harness, staff, proposal = incorrect(response_service)
    decide(harness, staff, [proposal], "entered_by_hand", note="Fixed")
    again = fresh(proposal)
    assert (again.decision, again.execution) == ("unreviewed", "resolved_external")
    assert again.version == proposal.version + 1
    assert ProposalResolution.objects.filter(proposal=again).count() == 2
    row = census_row(harness, again)
    assert row["stage"] == Stage.ENTERED_BY_HAND and not row["not_seen"]
    refresh(harness, member={"salutation": "Rev."})
    assert checks(proposal=again, item_version=again.version) == ["unchanged"]
    assert census_row(harness, again)["stage"] == Stage.ENTERED_BY_HAND
    refresh(harness, member={"salutation": "Rev."})
    row = census_row(harness, again)
    assert row["stage"] == Stage.ENTERED_BY_HAND and row["not_seen"]
    refresh(harness, member={"salutation": "Sir"})
    assert census_row(harness, again)["stage"] == Stage.ENTERED_INCORRECTLY
    assert census_row(harness, again)["parishsoft_now"] == "Sir"
    refresh(harness, member={"salutation": "Dr."})
    assert checks(proposal=again, item_version=again.version) == [
        "unchanged",
        "unchanged",
        "entered_incorrectly",
        "confirmed",
    ]
    assert census_row(harness, again)["stage"] == Stage.CONFIRMED


def test_the_first_marks_value_no_longer_counts_after_a_new_mark(response_service):
    """After a mark made again over "Rev.", ParishSoft back at the value the
    first mark recorded ("Mr.") is a third value: Entered incorrectly."""
    harness, staff, proposal = incorrect(response_service)
    decide(harness, staff, [proposal], "entered_by_hand")
    again = fresh(proposal)
    refresh(harness)
    assert checks(proposal=again, item_version=again.version) == ["entered_incorrectly"]


def test_back_to_review_undoes_the_mark_then_reviews_it_for_an_edit(
    response_service,
):
    """Back to review undoes the mark (#1840): the change is open again
    (pending) with its decision kept, so it is To enter by hand, Accepted
    (it was accepted and marked in one step); it needs a note. Back to
    review again sends it to To review, Edit then accepts ParishSoft's
    value, and the next refresh finds that value and confirms it."""
    from parishkit.stewardship.responses.census_resolution import NoteRequired

    harness, staff, proposal = incorrect(response_service)
    with pytest.raises(NoteRequired):
        decide(harness, staff, [proposal], "back_to_review", note="  ")
    key = uuid4()
    decide(harness, staff, [proposal], "back_to_review", key=key, note="Wrong")
    reopened = fresh(proposal)
    assert (reopened.decision, reopened.execution) == ("approved", "pending")
    assert reopened.version == proposal.version + 1
    resolution = ProposalResolution.objects.get(request_key=key)
    assert (resolution.action, resolution.actor_id) == ("reopened", staff)
    assert resolution.note == "Wrong"
    assert census_row(harness, reopened)["stage"] == Stage.TO_ENTER_BY_HAND
    # The check that found it is history: it no longer places the change.
    assert checks(proposal=reopened) == ["entered_incorrectly"]
    # The same post again is its first answer, not a stale refusal.
    (replayed,) = decide(
        harness, staff, [proposal], "back_to_review", key=key, note="Wrong"
    )
    assert replayed.pk == resolution.pk and fresh(proposal).version == reopened.version
    decide(harness, staff, [reopened], "back_to_review")
    reviewed = fresh(proposal)
    assert census_row(harness, reviewed)["stage"] == Stage.TO_REVIEW
    decide(harness, staff, [reviewed], "edit", value="Rev.")
    edited = fresh(proposal)
    assert (edited.decision, edited.admin_value) == ("approved", "Rev.")
    refresh(harness, member={"salutation": "Rev."})
    assert fresh(proposal).execution == "resolved_upstream"
    assert census_row(harness, proposal)["stage"] == Stage.CONFIRMED


def test_marking_again_is_refused_unless_it_is_entered_incorrectly(response_service):
    """Entered by hand (no check yet), and Confirmed, take no mark made
    again, nor Back to review without a note; nothing is recorded. (Back to
    review with a note undoes either, #1840.)"""
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    entered = fresh(rows["prefix"])
    for action in ("entered_by_hand", "back_to_review"):
        with pytest.raises(ValueError):
            decide(harness, staff, [entered], action)
    refresh(harness, member={"salutation": "Dr."})
    for action in ("entered_by_hand", "back_to_review"):
        with pytest.raises(ValueError):
            decide(harness, staff, [fresh(entered)], action)
    assert ProposalResolution.objects.filter(proposal=entered).count() == 1
    assert fresh(entered).version == entered.version


def forge(proposal, actor, action, assignments=None, note=""):
    """A resolution row (with ``note``) and, given ``assignments``, the
    change's UPDATE, as the web login in one work transaction, bypassing
    the service."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        if action:
            cursor.execute(
                "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
                "actor_id,proposal_id,expected_version,request_key,action,note) "
                "VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
                "gen_random_uuid(),%s,%s)",
                [actor, proposal.pk, proposal.version, action, note],
            )
        if assignments:
            cursor.execute(
                f"UPDATE stewardship_proposed_change SET {assignments},"
                " version=version+1 WHERE id=%s",
                [proposal.pk],
            )


@pytest.mark.parametrize(
    ("entered_incorrectly", "action", "assignments", "words"),
    [
        # No mark made again on a change only Entered by hand, and no undo
        # without a note (#1840), there or on Entered incorrectly.
        (False, "reopened", None, "lacks current authority"),
        (True, "reopened", None, "lacks current authority"),
        (False, "entered", None, "lacks current authority"),
        # Ignore is no fix.
        (True, "ignored", None, "lacks current authority"),
        # No update of an entered change without its resolution.
        (True, None, "execution='pending'", "new final Family response"),
        (True, None, "decision='ignored'", "new final Family response"),
        (True, None, "execution='cancelled'", "new final Family response"),
        # A resolution's update must be exactly its result: an undo keeps
        # the change's decision, Accepted (#1840), and makes it pending.
        (True, "reopened", "decision='unreviewed'", "new final Family response"),
        (
            True,
            "reopened",
            "decision='unreviewed', execution='conflict'",
            "new final Family response",
        ),
        # #1737's old result (pending, no decision) is no longer it.
        (True, "reopened", "execution='pending'", "new final Family response"),
        (
            True,
            "reopened",
            "decision='approved', execution='conflict'",
            "new final Family response",
        ),
        (True, "entered", "execution='pending'", "new final Family response"),
        (
            True,
            "entered",
            "execution='resolved_external', decision='ignored'",
            "new final Family response",
        ),
    ],
)
def test_sql_refuses_a_forged_fix(
    response_service, entered_incorrectly, action, assignments, words
):
    """The resolution guard and the census guard hold with the service
    bypassed: nothing is recorded."""
    if entered_incorrectly:
        _, staff, proposal = incorrect(response_service)
    else:
        harness, staff, rows = prepared(response_service)
        decide(harness, staff, [rows["prefix"]], "entered_by_hand")
        proposal = fresh(rows["prefix"])
    before = ProposalResolution.objects.count()
    # An undo with its result carries a note, so only its result is wrong.
    note = "Because" if action == "reopened" and assignments else ""
    with pytest.raises((IntegrityError, DatabaseError), match=words):
        forge(proposal, staff, action, assignments, note=note)
    assert ProposalResolution.objects.count() == before
    assert fresh(proposal).version == proposal.version


def test_the_worker_cannot_reopen_an_entered_change(response_service):
    """The census guard keeps an entered change final for every login but
    web, which needs its resolution: the worker's update is refused."""
    _, _, proposal = incorrect(response_service)
    with (
        pytest.raises(DatabaseError, match="terminal outcomes are immutable"),
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_proposed_change SET execution='pending',"
            " version=version+1 WHERE id=%s",
            [proposal.pk],
        )


# Forged census checks the tightened refresh check guard refuses inside a
# real promotion: (the check's outcome and observed value, the refresh's
# ParishSoft prefix, whether the change is marked again first).
MARK_FORGERIES = {
    "unchanged at a third value": ("unchanged", "Rev.", False),
    "entered incorrectly at the marked value": ("entered_incorrectly", "Mr.", False),
    "unchanged at the first mark after a new one": ("unchanged", "Mr.", True),
    "entered incorrectly at the new mark's value": (
        "entered_incorrectly",
        "Rev.",
        True,
    ),
}


@pytest.mark.parametrize("case", sorted(MARK_FORGERIES))
def test_the_check_guard_compares_with_the_value_at_the_mark(
    response_service, monkeypatch, case
):
    """Each check that disagrees with the value recorded at the mark is
    refused inside a real promotion, which rolls back."""
    from parishkit.stewardship.responses import refresh_checks

    outcome, value, again = MARK_FORGERIES[case]
    if again:
        harness, staff, proposal = incorrect(response_service)
        decide(harness, staff, [proposal], "entered_by_hand")
    else:
        harness, staff, rows = prepared(response_service)
        decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    target = ProposedChange.objects.get(field="prefix")
    before = RefreshCheck.objects.count()

    def forged(snapshot, corpus, campaign_id, full):
        """One check the value at the mark does not bear out."""
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            proposal_id=target.pk,
            item_version=target.version,
            outcome=outcome,
            observed={"available": True, "value": value},
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match="value recorded at its mark"):
        refresh(harness, member={"salutation": value})
    assert RefreshCheck.objects.count() == before


def test_a_refresh_before_the_next_mark_makes_it_a_conflict(response_service):
    """Back to review, then a refresh before the change is marked again:
    ParishSoft differs from the stored value and the reviewer's, so the
    change is a Conflict, and an Edit to ParishSoft's value accepts it
    against that value (the dialog promises no confirmation)."""
    harness, staff, proposal = incorrect(response_service)
    decide(harness, staff, [proposal], "back_to_review", note="Wrong")
    refresh(harness, member={"salutation": "Rev."})
    conflict = fresh(proposal)
    assert (conflict.execution, conflict.current_value) == ("conflict", "Rev.")
    assert census_row(harness, conflict)["stage"] == Stage.CONFLICT
    decide(harness, staff, [conflict], "edit", value="Rev.")
    edited = fresh(proposal)
    assert (edited.decision, edited.execution, edited.admin_value) == (
        "approved",
        "pending",
        "Rev.",
    )


def test_the_notice_says_again_only_for_a_mark_made_again(response_service):
    """The worklist words a mark "again" from the recorded resolution, so a
    replayed first mark is not called one."""
    from parishkit.stewardship.reports.census_worklist import _made_again

    harness, staff, proposal = incorrect(response_service)
    decide(harness, staff, [proposal], "entered_by_hand")
    first, second = ProposalResolution.objects.filter(proposal=proposal).order_by(
        "expected_version"
    )
    assert not _made_again(first) and _made_again(second)


def answer_again(harness, **member):
    """The Family answers again, with Member 3's census answers changed."""
    from .test_ministry_responses_postgresql import respond, revisit
    from .test_response_http_postgresql import answers_for
    from .test_runtime_auth_grants_postgresql import web_login

    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"].update(member)
        respond(harness, form, answers)


def test_a_replaced_change_takes_no_check_and_no_fix(response_service, monkeypatch):
    """The Family answering the field again makes a change of its own; the
    earlier entered one is history: it is placed by its execution alone,
    takes neither fix (the service and SQL refuse), and gets no new check,
    while the new change is confirmed by the refresh."""
    from parishkit.stewardship.responses import refresh_checks

    harness, staff, old = incorrect(response_service)
    # ParishSoft holds "Rev." now, so the new answer is another value.
    answer_again(harness, prefix="Sir")
    new = ProposedChange.objects.get(field="prefix", execution="pending")
    assert census_row(harness, old)["stage"] == Stage.ENTERED_BY_HAND
    for action in ("entered_by_hand", "back_to_review"):
        with pytest.raises(ValueError):
            decide(harness, staff, [fresh(old)], action)
    for action in ("entered", "reopened"):
        with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
            forge(fresh(old), staff, action)
    with pytest.raises((IntegrityError, DatabaseError), match="new final Family"):
        forge(fresh(old), staff, None, "execution='cancelled'")
    decide(harness, staff, [new], "entered_by_hand")
    refresh(harness, member={"salutation": "Sir"})
    assert checks(proposal=old) == ["entered_incorrectly"]
    assert checks(proposal=new) == ["confirmed"]
    assert census_row(harness, old)["stage"] == Stage.ENTERED_BY_HAND
    assert census_row(harness, new)["stage"] == Stage.CONFIRMED

    # A check of the replaced change is refused inside a real promotion.
    def forged(snapshot, corpus, campaign_id, full):
        """One check of the replaced change."""
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            proposal_id=old.pk,
            item_version=old.version,
            outcome="unchanged",
            observed={"available": True, "value": "Sir"},
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match="replaced census change"):
        refresh(harness, member={"salutation": "Sir"})


def test_both_fixes_in_bulk_and_their_replay(response_service):
    """One bulk Entered by hand marks every chosen change again, and the same
    bulk key posted again records nothing more; a bulk Back to review
    undoes each mark, with its one note, keeping each decision (#1840)."""
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    # Member 3 has a ParishSoft suffix before the Family answers one, so the
    # mark records it (an unknown one would stand in, never incorrectly).
    refresh(harness, member={"salutation": "Rev.", "suffix": "Sr."})
    answer_again(harness, suffix="Jr.")
    suffix = ProposedChange.objects.get(field="suffix")
    decide(harness, staff, [suffix], "entered_by_hand")
    refresh(harness, member={"salutation": "Rev.", "suffix": "III"})
    chosen = [fresh(rows["prefix"]), fresh(suffix)]
    for change in chosen:
        assert census_row(harness, change)["stage"] == Stage.ENTERED_INCORRECTLY
    key = uuid4()
    first = decide(harness, staff, chosen, "entered_by_hand", bulk=True, key=key)
    again = decide(harness, staff, chosen, "entered_by_hand", bulk=True, key=key)
    assert [row.pk for row in again] == [row.pk for row in first]
    for change in chosen:
        assert fresh(change).version == change.version + 1
        assert census_row(harness, change)["stage"] == Stage.ENTERED_BY_HAND
    refresh(harness, member={"salutation": "Sir", "suffix": "IV"})
    chosen = [fresh(change) for change in chosen]
    decide(harness, staff, chosen, "back_to_review", bulk=True, note="Both wrong")
    for change in chosen:
        assert (fresh(change).decision, fresh(change).execution) == (
            "approved",
            "pending",
        )
        assert census_row(harness, change)["stage"] == Stage.TO_ENTER_BY_HAND
