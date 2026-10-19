"""Entered incorrectly's two fixes (#1737), in PostgreSQL.

A census change Entered by hand that a refresh finds at a third value is
Entered incorrectly (#1689). Staff fix it on its row (review pipeline spec,
"Entered incorrectly stage" and "Refresh checks"): **Entered by hand
again** records a new mark whose value is ParishSoft's in the check that
found it, so the next refreshes compare with that value; **Back to review**
reopens it for an Edit to ParishSoft's value. Migration 0140 (provisional
number) admits both only while the latest check says Entered incorrectly,
and checks every census refresh check against the value recorded at the
mark. The changes are made through the real service on the web login and
re-evaluated by real promotions.
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


def test_back_to_review_reopens_it_for_an_edit_the_refresh_confirms(
    response_service,
):
    """Back to review makes the change open again (pending, no decision): it
    is To review, Edit then accepts ParishSoft's value, and the next refresh
    finds that value and confirms it."""
    harness, staff, proposal = incorrect(response_service)
    key = uuid4()
    decide(harness, staff, [proposal], "back_to_review", key=key)
    reopened = fresh(proposal)
    assert (reopened.decision, reopened.execution) == ("unreviewed", "pending")
    assert reopened.version == proposal.version + 1
    resolution = ProposalResolution.objects.get(request_key=key)
    assert (resolution.action, resolution.actor_id) == ("reopened", staff)
    assert census_row(harness, reopened)["stage"] == Stage.TO_REVIEW
    # The same post again is its first answer, not a stale refusal.
    (replayed,) = decide(harness, staff, [proposal], "back_to_review", key=key)
    assert replayed.pk == resolution.pk and fresh(proposal).version == reopened.version
    decide(harness, staff, [reopened], "edit", value="Rev.")
    edited = fresh(proposal)
    assert (edited.decision, edited.admin_value) == ("approved", "Rev.")
    refresh(harness, member={"salutation": "Rev."})
    assert fresh(proposal).execution == "resolved_upstream"
    assert census_row(harness, proposal)["stage"] == Stage.CONFIRMED


def test_the_fixes_are_refused_unless_it_is_entered_incorrectly(response_service):
    """Entered by hand (no check yet), and Confirmed, take neither fix; nothing
    is recorded."""
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


def forge(proposal, actor, action, assignments=None):
    """A resolution row and, given ``assignments``, the change's UPDATE, as
    the web login in one work transaction, bypassing the service."""
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
                "gen_random_uuid(),%s,'')",
                [actor, proposal.pk, proposal.version, action],
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
        # Neither fix on a change only Entered by hand.
        (False, "reopened", None, "lacks current authority"),
        (False, "entered", None, "lacks current authority"),
        # Ignore is no fix.
        (True, "ignored", None, "lacks current authority"),
        # No update of an entered change without its resolution.
        (True, None, "execution='pending'", "new final Family response"),
        (True, None, "decision='ignored'", "new final Family response"),
        # A resolution's update must be exactly its result.
        (True, "reopened", "decision='unreviewed'", "new final Family response"),
        (
            True,
            "reopened",
            "decision='unreviewed', execution='conflict'",
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
    with pytest.raises((IntegrityError, DatabaseError), match=words):
        forge(proposal, staff, action, assignments)
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
