"""Strict phase order in the review pipeline (#1890), in PostgreSQL.

The Administrator (2026-10-21) requires every item to go through review,
then its step (Updated in ParishSoft, Entered by hand), then confirmation,
in order (review pipeline spec, "Baseline refresh re-evaluation" rule 1 and
decision 2, reversed): a refresh confirms only an item already past its
step, and Entered by hand follows an Accept. Frozen migration 0159
(provisional number, ``schema/migrations/0159_strict_phase_order.sql``)
enforces both in the database. These tests promote real refreshes and read
the real pages' rows for census changes and Ministry requests (pledges are
in the pledge refresh and conflict tests), force the old behaviour past the
code to show the guards refuse it, and check the file's DO block against
the bodies it replaces.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.reports import ministry_worklist
from parishkit.stewardship.reports.census_worklist import worklist_rows
from parishkit.stewardship.responses import reconciliation, refresh_checks
from parishkit.stewardship.responses.baselines import _pin_admission
from parishkit.stewardship.source.pins import pin_snapshot
from parishkit.stewardship.web.review_worklist import ALREADY_HELD
from parishkit.stewardship.workflows.models import MinistryRequest, RefreshCheck

from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide as census_decide
from .test_census_review_postgresql import fresh
from .test_ministry_reports_postgresql import actor, setup
from .test_ministry_responses_postgresql import ministry_source
from .test_ministry_review_postgresql import decide as ministry_decide
from .test_ministry_review_postgresql import requests
from .test_refresh_checks_postgresql import (
    BASE,
    census_row,
    checks,
    prepared,
    refresh,
    view,
)
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0159_strict_phase_order.sql"
# Each function 0159 replaces, and the file holding the body it replaces.
EARLIER = {
    "stewardship_proposal_resolution_guard_v1": "0158_ignored_note_optional.sql",
    "stewardship_response_derived_guard_v1": "0216_reviewed_census_resubmission.sql",
    "stewardship_publication_confirmation_guard_v1": "0124_guard_wording.sql",
    "stewardship_ministry_request_guard_v1": "0217_reviewed_ministry_resubmission.sql",
    "stewardship_refresh_check_guard_v1": "0138_published_refresh_conflicts.sql",
    "stewardship_pledge_stages_v1": "0136_submission_upcoming_pledge.sql",
    "stewardship_pledge_refresh_check_guard_v1": "0135_pledge_conflicts.sql",
}
# The bodies before the sibling PRs that 0159's three shared functions were
# copied onto (#1889's optional Ignored note, #1705's reviewed view rules):
# a 0159 written on these would lose those rules, so its DO block refuses
# each of them too.
WITHOUT_SIBLINGS = {
    "stewardship_proposal_resolution_guard_v1": "0210_census_terminal_undo.sql",
    "stewardship_response_derived_guard_v1": "0210_census_terminal_undo.sql",
    "stewardship_ministry_request_guard_v1": "0212_ministry_terminal_undo.sql",
}


def worker_refresh(harness, *, member=None, joins=()):
    """``refresh``'s promotion on the source worker's own login, whose
    writes the census and Ministry guards check (staged as the owner)."""
    data = ministry_source()
    data.members[3].update(BASE | (member or {}))
    for ministry in joins:
        data.ministry_type_memberships.setdefault(ministry, {"membership": []})[
            "membership"
        ].append(
            {
                "memberId": 3,
                "ministryRoleId": 7,
                "ministryRoleName": "Member",
                "startDate": "2026-01-01",
                "endDate": None,
            }
        )
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return promote(snapshot, claim, harness.campaign, harness.rings)


def stage_note(harness, proposal):
    """The line the Census changes worklist draws under ``proposal``'s stage."""
    (row,) = worklist_rows([census_row(harness, proposal)])
    return row["stage_note"]


def test_a_census_change_parishsoft_already_holds_waits_for_its_steps(
    response_service,
):
    """Rule 1 before the step (#1890): a To review change ParishSoft already
    holds stays To review, then Ready to update or To enter by hand once
    accepted, each saying ParishSoft already holds the value; a refresh
    resolves nothing and checks nothing. Marked Entered by hand, the next
    refresh confirms it."""
    harness, staff, rows = prepared(response_service)
    refresh(harness, member={"firstName": "Requested", "salutation": "Dr."})
    for field in ("first_name", "prefix"):
        held = fresh(rows[field])
        assert (held.decision, held.execution) == ("unreviewed", "pending")
        row = census_row(harness, held)
        assert row["stage"] == Stage.TO_REVIEW and row["already_held"]
        assert stage_note(harness, held) == ALREADY_HELD
    census_decide(harness, staff, [fresh(rows["first_name"])], "accept")
    census_decide(harness, staff, [fresh(rows["prefix"])], "accept")
    refresh(harness, member={"firstName": "Requested", "salutation": "Dr."})
    for field, stage in (
        ("first_name", Stage.READY_TO_UPDATE),
        ("prefix", Stage.TO_ENTER_BY_HAND),
    ):
        accepted = fresh(rows[field])
        assert (accepted.decision, accepted.execution) == ("approved", "pending")
        row = census_row(harness, accepted)
        assert row["stage"] == stage and row["already_held"]
        assert row["placement"].decision is Decision.ACCEPTED
        assert stage_note(harness, accepted) == ALREADY_HELD
        assert checks(proposal=accepted) == []
    census_decide(harness, staff, [fresh(rows["prefix"])], "entered_by_hand")
    marked = fresh(rows["prefix"])
    assert census_row(harness, marked)["stage"] == Stage.ENTERED_BY_HAND
    assert not census_row(harness, marked)["already_held"]
    refresh(harness, member={"firstName": "Requested", "salutation": "Dr."})
    assert checks(proposal=marked) == ["confirmed"]
    assert census_row(harness, marked)["stage"] == Stage.CONFIRMED


def test_a_conflict_parishsoft_moves_to_the_answer_returns_to_review(
    response_service,
):
    """A Conflict whose ParishSoft value then reaches the Family's answer
    returns to the stage its decision gives (To review), saying ParishSoft
    already holds it, as one back at A does; never Confirmed. Another value
    takes the words away. An Ignored change ParishSoft reaches is only
    noted: it stays Ignored, with no already-held words."""
    harness, staff, rows = prepared(response_service)
    refresh(harness, member={"firstName": "Someone else"})
    assert fresh(rows["first_name"]).execution == "conflict"
    refresh(harness, member={"firstName": "Requested"})
    back = fresh(rows["first_name"])
    assert (back.decision, back.execution) == ("unreviewed", "pending")
    row = census_row(harness, back)
    assert row["stage"] == Stage.TO_REVIEW and row["already_held"]
    # Another value takes the words away; an Ignored change is only noted.
    refresh(harness, member={"firstName": "Pat"})
    assert not census_row(harness, fresh(back))["already_held"]
    census_decide(harness, staff, [fresh(back)], "ignore")
    refresh(harness, member={"firstName": "Requested"})
    ignored = fresh(back)
    assert (ignored.decision, ignored.execution) == ("ignored", "pending")
    row = census_row(harness, ignored)
    assert row["stage"] == Stage.IGNORED and row["now_matches"]
    assert not row["already_held"]


def forge_mark(staff, change):
    """Insert an Entered by hand resolution of ``change`` on the web login,
    bypassing the service, as the resolution guard sees it."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note) "
            "VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),'entered','')",
            [staff, change.pk, change.version],
        )


def test_entered_by_hand_needs_an_accept(response_service):
    """Decision 2 reversed (#1890): Entered by hand on a To review By hand
    change, and on an accepted one ParishSoft has since moved to a third
    value (a Conflict, still approved), is refused by the service, and by
    the resolution guard with the service bypassed; after an Accept, with
    the Conflict over, it is recorded."""
    harness, staff, rows = prepared(response_service)
    with pytest.raises(ValueError, match="cannot be resolved that way"):
        census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    with pytest.raises(DatabaseError, match="authority, version or state"):
        forge_mark(staff, fresh(rows["prefix"]))
    assert fresh(rows["prefix"]).execution == "pending"
    census_decide(harness, staff, [fresh(rows["prefix"])], "accept")
    refresh(harness, member={"salutation": "Rev."})
    conflict = fresh(rows["prefix"])
    assert (conflict.decision, conflict.execution) == ("approved", "conflict")
    with pytest.raises(ValueError, match="cannot be resolved that way"):
        census_decide(harness, staff, [conflict], "entered_by_hand")
    with pytest.raises(DatabaseError, match="authority, version or state"):
        forge_mark(staff, conflict)
    assert fresh(rows["prefix"]).execution == "conflict"
    # ParishSoft back at the baseline ends the Conflict: Ready, then marked.
    refresh(harness)
    assert fresh(rows["prefix"]).execution == "pending"
    census_decide(harness, staff, [fresh(rows["prefix"])], "entered_by_hand")
    assert fresh(rows["prefix"]).execution == "resolved_external"


def test_the_census_guard_refuses_a_refresh_resolution(response_service, monkeypatch):
    """Reconciliation forced to resolve an open change upstream, as before
    #1890, is refused by the census guard inside the real promotion, which
    is rolled back."""
    harness, _, rows = prepared(response_service)
    before = fresh(rows["first_name"])
    found = reconciliation._proposal_updates

    def old(corpus, campaign_id, *, record=True):
        """Today's decisions, with every open change resolved upstream."""
        for row, current, _, decision in found(corpus, campaign_id, record=record):
            yield row, current, "resolved_upstream", decision

    monkeypatch.setattr(reconciliation, "_proposal_updates", old)
    with pytest.raises(DatabaseError, match="fenced protected source"):
        worker_refresh(harness, member={"firstName": "Requested"})
    after = fresh(rows["first_name"])
    assert (after.execution, after.version) == (before.execution, before.version)


def test_a_ministry_request_the_roster_already_shows_waits_for_its_steps(
    response_service,
):
    """A join the roster already lists stays To review (the refresh resolves
    nothing) and, accepted, To enter by hand with no check written, each
    saying ParishSoft already holds it; ticked, the next refresh confirms
    it."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    refresh(harness, joins=(9,))
    join.refresh_from_db()
    assert (join.state, join.resolution_source_id) == ("new", None)
    row = view(harness, staff)[str(join.pk)]
    assert row["placement"].stage is Stage.TO_REVIEW and row["already_held"]
    line = ministry_worklist._decision_line(row, False, None, None, True)
    assert ALREADY_HELD in str(line)
    ministry_decide(harness, staff, "accept", join)
    refresh(harness, joins=(9,))
    row = view(harness, staff)[str(join.pk)]
    assert row["placement"].stage is Stage.TO_ENTER_BY_HAND and row["already_held"]
    assert checks(request=join) == []
    ministry_decide(harness, staff, "entered_by_hand", join)
    refresh(harness, joins=(9,))
    assert checks(request=join) == ["confirmed"]
    row = view(harness, staff)[str(join.pk)]
    assert row["placement"].stage is Stage.CONFIRMED and not row["already_held"]


def test_a_request_the_roster_does_not_show_says_nothing(response_service):
    """No words while the roster does not show the request."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, leave = requests()
    refresh(harness)
    rows = view(harness, staff)
    assert not rows[str(join.pk)]["already_held"]
    assert not rows[str(leave.pk)]["already_held"]
    refresh(harness, leaves=(4,))
    assert view(harness, staff)[str(leave.pk)]["already_held"]


def test_the_ministry_guard_refuses_a_refresh_resolution(response_service, monkeypatch):
    """Inside a real promotion on the worker's login, the Ministry request
    guard refuses a refresh resolving an open request, and the refresh is
    rolled back."""
    harness = setup(response_service)
    join, _ = requests()

    def resolving(snapshot, corpus, *, campaign_id):
        """Resolve the join from the roster, as reconciliation did before."""
        pin_snapshot(
            snapshot.pk,
            parent_kind="submission",
            parent_id=join.submission_id,
            admit=_pin_admission,
        )
        MinistryRequest.objects.filter(pk=join.pk).update(
            state="resolved",
            outcome="joined",
            resolved_at=_now(),
            resolution_source=snapshot,
            version=F("version") + 1,
        )

    with monkeypatch.context() as patched:
        patched.setattr(reconciliation, "reconcile_proposals", resolving)
        with pytest.raises(DatabaseError, match="does not resolve a Ministry request"):
            worker_refresh(harness, joins=(9,))
    join.refresh_from_db()
    assert (join.state, join.resolution_source_id) == ("new", None)


def test_the_refresh_check_guard_refuses_an_early_confirmation(
    response_service, monkeypatch
):
    """Inside a real promotion, the refresh check guard refuses a confirmed
    check on an accepted request not yet ticked, and the refresh is rolled
    back."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    ministry_decide(harness, staff, "accept", join)

    def early(snapshot, corpus, campaign_id, full):
        """Confirm the accepted join before its tick."""
        join.refresh_from_db()
        yield RefreshCheck(
            snapshot_id=snapshot.pk,
            full_refresh=full,
            request_id=join.pk,
            item_version=join.version,
            outcome="confirmed",
        )

    monkeypatch.setattr(refresh_checks, "_checks", early)
    with pytest.raises(DatabaseError, match="requires a decided request"):
        refresh(harness, joins=(9,))
    assert not RefreshCheck.objects.exists()


def body(name, path):
    """``name``'s last definition in ``path``, as a CREATE OR REPLACE."""
    text = path.read_text(encoding="utf-8")
    *_, match = re.finditer(
        rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", text, re.M
    )
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.end())
    end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return (
        "CREATE OR REPLACE FUNCTION" + text[match.start() : end].split("FUNCTION", 1)[1]
    )


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def test_the_do_block_refuses_each_earlier_body():
    """The file's closing check passes as installed and raises once any one
    replaced function is put back to the body it replaced, or to the one
    before a sibling PR's rules (0210's, 0212's); on the earlier bodies the
    whole file installs again, with no definer among them."""
    text = FROZEN.read_text(encoding="utf-8")
    captured = text[
        text.index("SELECT set_config(") : text.index("CREATE OR REPLACE FUNCTION")
    ]
    check = text[text.index("DO $check$") :]
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(captured)
        cursor.execute(check)
        raise Rollback
    for name, earlier in (*EARLIER.items(), *WITHOUT_SIBLINGS.items()):
        with (
            pytest.raises(DatabaseError, match="The |grants"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(captured)
            cursor.execute(body(name, SCHEMA / "migrations" / earlier))
            cursor.execute(check)
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        for name, earlier in EARLIER.items():
            cursor.execute(body(name, SCHEMA / "migrations" / earlier))
        cursor.execute(text)
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef AND proname = ANY(%s)",
            [list(EARLIER)],
        )
        assert cursor.fetchone() == (len(EARLIER),)
        raise Rollback
