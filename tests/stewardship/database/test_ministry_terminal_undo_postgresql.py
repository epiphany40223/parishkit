"""Back to review undoes a Ministry request's terminal step (#1840).

Entered by hand and Confirmed are undone by a revision that keeps the
decision and advances the request's version (the tick, if set, is cleared
in the same transaction); a request the refresh resolved goes back to To
review (review pipeline spec, "Undoing a terminal stage"). Each takes a
required note, from Staff or an Administrator only. Migration 0212's guards
are tested here with the service bypassed too, against the old bodies.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.reports.timeline_reviews import BACK_TO_REVIEW, steps
from parishkit.stewardship.workflows.models import (
    MinistryRosterEntry,
    MinistryWorkflowRevision,
)

from .test_background_grants_postgresql import task_login
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import actor, setup
from .test_ministry_review_postgresql import decide, requests
from .test_refresh_checks_postgresql import checks, refresh, view
from .test_refresh_checks_postgresql import started as started  # the fixture

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0212_ministry_terminal_undo.sql"
OLD = MIGRATIONS / "0131_ministry_review_pipeline.sql"


def accepted(response_service):
    """The harness's join accepted by Staff; (harness, staff, join)."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    decide(harness, staff, "accept", join)
    return harness, staff, join


def stage(harness, staff, join):
    """The join's worklist placement for Staff."""
    return view(harness, staff)[str(join.pk)]["placement"]


def test_back_to_review_undoes_entered_by_hand_keeping_the_accept(response_service):
    """The undo clears the tick and keeps the request joined, one version
    on, with its note, as one audited revision; without a note it is
    refused; the next Back to review sends it to To review."""
    harness, staff, join = accepted(response_service)
    decide(harness, staff, "entered_by_hand", join)
    assert stage(harness, staff, join).stage is Stage.ENTERED_BY_HAND
    version = join.version
    with pytest.raises(ValueError, match="needs a note"):
        decide(harness, staff, "back_to_review", join)
    key = uuid4()
    decide(harness, staff, "back_to_review", join, note="Not typed yet", key=key)
    assert (join.state, join.outcome, join.version) == (
        "resolved",
        "joined",
        version + 1,
    )
    undo = MinistryWorkflowRevision.objects.get(request_key=key)
    assert (undo.state, undo.outcome, undo.decision_note) == (
        "resolved",
        "joined",
        "Not typed yet",
    )
    cleared = MinistryRosterEntry.objects.get(request_key=key)
    assert (cleared.sequence, cleared.entered) == (2, False)
    assert AuditEvent.objects.filter(
        event_type="ministry_request_updated", subject_id=undo.pk
    ).exists()
    placement = stage(harness, staff, join)
    assert (placement.stage, placement.decision) == (
        Stage.TO_ENTER_BY_HAND,
        Decision.ACCEPTED,
    )
    decide(harness, staff, "back_to_review", join)
    assert (join.state, join.outcome) == ("in_progress", None)


def test_back_to_review_undoes_confirmed_and_the_next_refresh_confirms_again(
    response_service,
):
    """A Confirmed request goes back to To enter by hand, its version past
    the check, which stays in its history; the next refresh finds it open
    and Confirms it again at the new version."""
    harness, staff, join = accepted(response_service)
    refresh(harness, joins=(9,))
    assert stage(harness, staff, join).stage is Stage.CONFIRMED
    join.refresh_from_db()
    decide(harness, staff, "back_to_review", join, note="Wrong person")
    assert stage(harness, staff, join).stage is Stage.TO_ENTER_BY_HAND
    assert checks(request=join) == ["confirmed"]
    assert not MinistryRosterEntry.objects.filter(request=join).exists()
    refresh(harness, joins=(9,))
    assert checks(request=join) == ["confirmed", "confirmed"]
    assert stage(harness, staff, join).stage is Stage.CONFIRMED


def test_a_confirmed_ticked_request_is_unticked_too(response_service):
    """Ticked, then Confirmed: the undo also clears the tick."""
    harness, staff, join = accepted(response_service)
    decide(harness, staff, "entered_by_hand", join)
    refresh(harness, joins=(9,))
    join.refresh_from_db()
    assert stage(harness, staff, join).stage is Stage.CONFIRMED
    decide(harness, staff, "back_to_review", join, note="Typed the wrong one")
    latest = MinistryRosterEntry.objects.filter(request=join).order_by("-sequence")
    assert not latest.first().entered
    assert stage(harness, staff, join).stage is Stage.TO_ENTER_BY_HAND


def test_a_refresh_resolved_request_goes_back_to_review(response_service):
    """A request the refresh resolved, with no decision behind it, goes back
    to To review: In progress, its resolution cleared, with the note. The
    next refresh may resolve it again, as expected."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    refresh(harness, joins=(9,))
    join.refresh_from_db()
    assert join.resolution_source_id is not None
    placement = stage(harness, staff, join)
    assert (placement.stage, placement.decision) == (Stage.CONFIRMED, None)
    with pytest.raises(ValueError, match="needs a note"):
        decide(harness, staff, "back_to_review", join)
    decide(harness, staff, "back_to_review", join, note="Roster was wrong")
    assert (join.state, join.outcome, join.resolution_source_id) == (
        "in_progress",
        None,
        None,
    )
    assert stage(harness, staff, join).stage is Stage.TO_REVIEW
    refresh(harness, joins=(9,))
    join.refresh_from_db()
    assert join.state == "resolved" and join.resolution_source_id is not None


def test_not_seen_counts_again_from_the_next_mark(response_service, started):
    """A ticked request Not seen after two full refreshes is undone; marked
    again, it is not Not seen until two more refreshes after the new mark."""
    harness, staff, join = accepted(response_service)
    decide(harness, staff, "entered_by_hand", join)
    refresh(harness)
    refresh(harness)
    assert view(harness, staff)[str(join.pk)]["not_seen"]
    decide(harness, staff, "back_to_review", join, note="Retyping it")
    decide(harness, staff, "entered_by_hand", join)
    assert not view(harness, staff)[str(join.pk)]["not_seen"]
    refresh(harness)
    assert not view(harness, staff)[str(join.pk)]["not_seen"]


def test_a_leader_never_undoes_a_terminal_step(response_service, google):
    """A Ministry leader is never offered the undo; the database refuses
    their revision too."""
    harness, staff, join = accepted(response_service)
    decide(harness, staff, "entered_by_hand", join)
    _, head, _, _ = leader(harness, google)
    with pytest.raises(DatabaseError, match="not valid for this request"):
        revise(join, head, state="resolved", outcome="joined", note="Mine")


def revise(target, actor_id, *, state, outcome, note, version=None):
    """Insert one revision as the web login, bypassing the service."""
    target.refresh_from_db()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), work_transaction():
        MinistryWorkflowRevision.objects.create(
            request=target,
            actor_id=getattr(actor_id, "identity", actor_id),
            request_key=uuid4(),
            expected_version=target.version if version is None else version,
            state=state,
            outcome=outcome,
            decision_note=note,
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        # The note is required in the database too.
        ({"note": " "}, "not valid for this request"),
        # The decision is kept: the same outcome, never another.
        ({"outcome": "declined"}, "not valid for this request"),
        # Only at the request's version.
        ({"version": 1}, "lacks current authority/version"),
    ],
)
def test_sql_holds_the_undo_to_its_rules(response_service, values, message):
    """The 0212 revision guard admits the undo only with a note, keeping
    the decision, at the request's version."""
    harness, staff, join = accepted(response_service)
    decide(harness, staff, "entered_by_hand", join)
    shape = {"state": "resolved", "outcome": "joined", "note": "Typo"} | values
    with pytest.raises(DatabaseError, match=message):
        revise(join, staff, **shape)


def test_sql_refuses_an_undo_with_nothing_to_undo(response_service):
    """An accepted request neither ticked nor Confirmed has no terminal step
    for a kept-decision revision to undo."""
    harness, staff, join = accepted(response_service)
    with pytest.raises(DatabaseError, match="not valid for this request"):
        revise(join, staff, state="resolved", outcome="joined", note="Typo")


def test_sql_refuses_a_bare_clear_of_the_tick(response_service):
    """Clearing the tick needs its Back to review revision (the same actor
    and request key) with a note."""
    harness, staff, join = accepted(response_service)
    decide(harness, staff, "entered_by_hand", join)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="needs its Back to review"),
    ):
        cursor.execute(
            "INSERT INTO stewardship_ministry_roster_entry (id,correlation_id,"
            "actor_id,request_id,sequence,entered,request_key) VALUES "
            "(gen_random_uuid(),gen_random_uuid(),%s,%s,2,false,gen_random_uuid())",
            [staff.identity, join.pk],
        )


def old_body(name):
    """0131's statement of ``name``, as it was before 0212."""
    text = OLD.read_text(encoding="utf-8")
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}()")
    return text[start : text.index("END $$;", start) + len("END $$;")]


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def test_the_old_guards_refused_the_undo(response_service):
    """Before 0212, the revision guard refused any reopen of a ticked
    request, so the undo was refused; 0212's admits it. Each is tried in a
    rolled-back transaction (before the deferred audit check)."""
    harness, staff, join = accepted(response_service)
    decide(harness, staff, "entered_by_hand", join)
    join.refresh_from_db()

    def attempt():
        """The undo's revision, inserted directly."""
        MinistryWorkflowRevision.objects.create(
            request=join,
            actor_id=staff.identity,
            request_key=uuid4(),
            expected_version=join.version,
            state="resolved",
            outcome="joined",
            decision_note="Typo",
        )

    with pytest.raises(Rollback), work_transaction():
        attempt()
        raise Rollback
    with (
        pytest.raises(DatabaseError, match="not valid for this request"),
        work_transaction(),
    ):
        with connection.cursor() as cursor:
            cursor.execute(old_body("stewardship_ministry_revision_guard_v1"))
        attempt()


def test_do_block_passes_installed_and_refuses_an_old_body():
    """0212's closing check passes as installed, and raises once the
    revision guard is 0131's again."""
    text = FROZEN.read_text(encoding="utf-8")
    prefix = text[: text.index("CREATE OR REPLACE FUNCTION")]
    check = text[text.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(prefix + check)
    with (
        pytest.raises(DatabaseError, match="did not replace the guards"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(prefix)
        cursor.execute(old_body("stewardship_ministry_revision_guard_v1"))
        cursor.execute(check)


def test_the_timeline_reads_the_undo_as_back_to_review():
    """The undo's revision keeps the decision, so the Timeline names it
    Back to review (then the cleared tick); a refresh resolution's undo,
    with its note, too."""
    base = dict(
        item="r1",
        actor="staff@example.org",
        entity_kind="member",
        entity_key="3",
        subject="9",
        c="join",
        seed_decision=None,
        seed_set=None,
        seed_value=None,
        answer_first=None,
        answer_last=None,
        source_first=None,
        source_last=None,
    )

    def row(stream, step, a, b=None, d=None):
        """One _HISTORY row of request r1."""
        return base | dict(stream=stream, step=step, at=step, a=a, b=b, d=d)

    lines = steps(
        [
            row("ministry_revision", 1, "resolved", "joined", "false"),
            row("ministry_roster", 2, "true"),
            row("ministry_revision", 3, "resolved", "joined", "true"),
            row("ministry_roster", 4, "false"),
        ],
        {9: "Choir"},
    )
    assert [str(what) for _, what, _ in lines] == [
        "Accepted",
        "Entered by hand",
        str(BACK_TO_REVIEW),
        "Entered by hand cleared",
    ]
    (line,) = steps([row("ministry_revision", 1, "in_progress", None, "true")])
    assert str(line[1]) == str(BACK_TO_REVIEW)
