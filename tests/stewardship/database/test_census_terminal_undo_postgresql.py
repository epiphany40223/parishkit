"""Back to review undoes a census change's terminal step (#1840), in PostgreSQL.

The review pipeline (spec, "Undoing a terminal stage") makes no stage final:
Back to review removes only the terminal step, so a change Entered by hand
(also Entered incorrectly or Confirmed), Updated in ParishSoft or Already in
ParishSoft goes back to the stage before it with its decision kept, with a
required note, for Staff and Administrators only. Migration 0146
(provisional number) admits the paired ``reopened`` resolution. These tests
run the real service on the web login, re-evaluate the undone change with
real promotions, check the SQL guards with the service bypassed, and check
that the guards' previous bodies refuse the undo.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.responses.census_resolution import (
    NoteRequired,
    resolve_census_change,
)
from parishkit.stewardship.responses.models import ProposalResolution, ProposedChange
from parishkit.stewardship.responses.publication_models import PublicationField

from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide
from .test_census_refresh_sources_postgresql import answered
from .test_census_refresh_sources_postgresql import refresh as refresh_family
from .test_census_resolution_postgresql import leader
from .test_census_review_postgresql import fresh
from .test_policy_postgresql import user
from .test_publication_plans_postgresql import owner, plan, writing
from .test_published_refresh_postgresql import first_name, published
from .test_refresh_checks_postgresql import (
    census_row,
    checks,
    prepared,
    refresh,
    started,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0146_census_terminal_undo.sql"
# 0216 (#1705) replaces 0146's derived guard last; it only replaces
# functions, so it runs again whole after 0146 to put the latest back.
LATER = MIGRATIONS / "0216_reviewed_census_resubmission.sql"


def undo(harness, actor, change, *, note="Marked by mistake", key=None):
    """Back to review on the worklist, through the service on the web login."""
    return decide(harness, actor, [change], "back_to_review", note=note, key=key)


def forge(change, actor, *, note="Because", assignments=None):
    """A ``reopened`` resolution row and, given ``assignments``, the change's
    UPDATE, as the web login in one work transaction, bypassing the service."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note) "
            "VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),'reopened',%s)",
            [actor, change.pk, change.version, note],
        )
        if assignments:
            cursor.execute(
                f"UPDATE stewardship_proposed_change SET {assignments},"
                " version=version+1 WHERE id=%s",
                [change.pk],
            )


@pytest.mark.usefixtures("started")
def test_undoing_entered_by_hand_keeps_the_decision_and_restarts_not_seen(
    response_service,
):
    """An accepted change marked Entered by hand and checked once goes back
    to To enter by hand, Accepted, at its next version, with its audit
    event (versions only). Open again, the refresh checks nothing; marked
    again, it is judged against its stored ParishSoft value (never the
    undone mark's checks), and Not seen counts from the new mark."""
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "accept")
    decide(harness, staff, [fresh(rows["prefix"])], "entered_by_hand")
    marked = fresh(rows["prefix"])
    refresh(harness)
    assert checks(proposal=marked) == ["unchanged"]
    events = AuditEvent.objects.filter(event_type="census_change_updated").count()
    key = uuid4()
    (resolution,) = undo(harness, staff, marked, key=key, note="Typed it wrong")
    reopened = fresh(marked)
    assert (reopened.decision, reopened.execution) == ("approved", "pending")
    assert reopened.version == marked.version + 1
    assert (resolution.action, resolution.actor_id, resolution.note) == (
        "reopened",
        staff,
        "Typed it wrong",
    )
    row = census_row(harness, reopened)
    assert row["stage"] == Stage.TO_ENTER_BY_HAND
    assert row["placement"].decision is Decision.ACCEPTED
    event = AuditEvent.objects.get(subject_id=resolution.pk)
    assert event.event_type == "census_change_updated"
    assert AuditContext.objects.get(event=event).context == {
        "outcome": "changed",
        "before_version": marked.version,
        "after_version": marked.version + 1,
    }
    assert (
        AuditEvent.objects.filter(event_type="census_change_updated").count()
        == events + 1
    )
    # History keeps the undone mark and its check.
    assert [r.action for r in reopened.resolutions.order_by("expected_version")] == [
        "entered",
        "reopened",
    ]
    assert checks(proposal=reopened) == ["unchanged"]
    # Open again: a refresh at ParishSoft's stored value records no check.
    refresh(harness)
    assert checks(proposal=reopened, item_version=reopened.version) == []
    assert fresh(reopened).execution == "pending"
    # Marked again: unchanged at the stored value, Not seen from this mark.
    decide(harness, staff, [fresh(reopened)], "entered_by_hand")
    again = fresh(reopened)
    refresh(harness)
    assert checks(proposal=again, item_version=again.version) == ["unchanged"]
    assert not census_row(harness, again)["not_seen"]
    refresh(harness)
    assert census_row(harness, again)["not_seen"]


def test_undoing_a_one_step_mark_returns_it_accepted(response_service):
    """A To review change Entered by hand in one step was shown Accepted, so
    the undo leaves it To enter by hand, Accepted (approved); Back to review
    again sends it to To review. Without a note nothing is recorded."""
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    marked = fresh(rows["prefix"])
    assert marked.decision == "unreviewed"
    for note in ("", " \t"):
        with pytest.raises(NoteRequired):
            undo(harness, staff, marked, note=note)
    assert fresh(marked).version == marked.version
    undo(harness, staff, marked)
    assert (fresh(marked).decision, fresh(marked).execution) == ("approved", "pending")
    assert census_row(harness, marked)["stage"] == Stage.TO_ENTER_BY_HAND
    decide(harness, staff, [fresh(marked)], "back_to_review")
    assert census_row(harness, marked)["stage"] == Stage.TO_REVIEW


def test_undoing_confirmed_reopens_it_for_the_next_refresh(response_service):
    """A Confirmed entered change goes back to To enter by hand; its
    confirmed check no longer places it, and the next refresh, finding the
    value in ParishSoft, resolves it as an open change: Confirmed again."""
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    refresh(harness, member={"salutation": "Dr."})
    confirmed = fresh(rows["prefix"])
    assert census_row(harness, confirmed)["stage"] == Stage.CONFIRMED
    undo(harness, staff, confirmed)
    assert census_row(harness, confirmed)["stage"] == Stage.TO_ENTER_BY_HAND
    assert checks(proposal=confirmed) == ["confirmed"]
    refresh(harness, member={"salutation": "Dr."})
    resolved = fresh(confirmed)
    assert resolved.execution == "resolved_upstream"
    assert census_row(harness, resolved)["stage"] == Stage.CONFIRMED


def test_an_administrator_undoes_updated_in_parishsoft_and_it_plans_again(
    response_service,
):
    """Updated in ParishSoft (and Confirmed after it) goes back to Ready to
    update, nothing written to ParishSoft and the publication history kept;
    a later update run plans it again at its new version."""
    harness, staff, rows = answered(response_service, first_name)
    admin = user("admin@example.org").pk
    decide(harness, staff, [rows["first_name"]], "accept")
    accepted = fresh(rows["first_name"])
    plan(admin, [accepted], [writing(accepted)])
    planned = PublicationField.objects.filter(proposal=accepted).count()
    assert planned == 1
    # Forged Updated in ParishSoft, as a verified run leaves it.
    owner(
        "UPDATE stewardship_proposed_change SET execution='published',"
        " version=version+1 WHERE id=%s",
        [accepted.pk],
    )
    change = fresh(accepted)
    assert census_row(harness, change)["stage"] == Stage.UPDATED
    undo(harness, admin, change)
    back = fresh(change)
    assert (back.decision, back.execution) == ("approved", "pending")
    assert census_row(harness, back)["stage"] == Stage.READY_TO_UPDATE
    assert PublicationField.objects.filter(proposal=back).count() == planned
    plan(admin, [back], [writing(back)])
    assert set(
        PublicationField.objects.filter(proposal=back).values_list(
            "proposal_version", flat=True
        )
    ) == {accepted.version, back.version}


def test_staff_undo_a_confirmed_update(response_service):
    """A refresh that finds the written value confirms an Updated in
    ParishSoft change; Staff's Back to review returns it to Ready to update."""
    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    refresh_family(harness, member={"firstName": "Requested"})
    confirmed = fresh(change)
    assert confirmed.execution == "published"
    assert census_row(harness, confirmed)["stage"] == Stage.CONFIRMED
    undo(harness, staff, confirmed)
    assert census_row(harness, confirmed)["stage"] == Stage.READY_TO_UPDATE


def test_already_in_parishsoft_goes_back_to_the_stage_before_it(response_service):
    """A change ParishSoft already held that nobody decided is To review
    after the undo; one that was accepted (an Edit ParishSoft reached) is
    Ready to update, its value kept."""
    harness, staff, rows = prepared(response_service)
    refresh(harness, member={"firstName": "Requested"})
    held = fresh(rows["first_name"])
    assert (held.decision, held.execution) == ("unreviewed", "resolved_upstream")
    assert census_row(harness, held)["placement"].decision is None
    undo(harness, staff, held)
    assert (fresh(held).decision, fresh(held).execution) == ("unreviewed", "pending")
    assert census_row(harness, held)["stage"] == Stage.TO_REVIEW


def test_an_accepted_already_in_parishsoft_keeps_its_edit(response_service):
    """An Edit ParishSoft reached is Confirmed, Accepted (edited); the undo
    leaves it Ready to update with the Reviewer's value."""
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "edit", value="Corrected")
    refresh(harness, member={"firstName": "Corrected"})
    held = fresh(rows["first_name"])
    assert (held.decision, held.execution) == ("approved", "resolved_upstream")
    undo(harness, staff, held)
    back = fresh(held)
    assert (back.decision, back.execution, back.admin_value) == (
        "approved",
        "pending",
        "Corrected",
    )
    row = census_row(harness, back)
    assert row["stage"] == Stage.READY_TO_UPDATE
    assert row["placement"].decision is Decision.ACCEPTED_EDITED


def test_a_leader_a_stale_version_and_a_run_in_flight_are_refused(response_service):
    """A Ministry leader may not undo (service and SQL); a stale version is
    refused; a change queued for an update run (Updating) is not terminal
    and takes no undo. Nothing is recorded."""
    from parishkit.stewardship.responses.census_review import BulkStale

    harness, staff, rows = prepared(response_service)
    admin = user("admin@example.org").pk
    head = leader(harness, admin)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    marked = fresh(rows["prefix"])
    with (
        pytest.raises(PermissionError),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        resolve_census_change(
            harness.service.store,
            head,
            marked.pk,
            expected_version=marked.version,
            request_key=uuid4(),
            action="reopened",
            note="A leader",
        )
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge(marked, head)
    with pytest.raises(BulkStale):
        decide(
            harness,
            staff,
            [ProposedChange(pk=marked.pk, version=marked.version - 1)],
            "back_to_review",
            note="Stale",
        )
    decide(harness, staff, [rows["first_name"]], "accept")
    owner(
        "UPDATE stewardship_proposed_change SET execution='queued',"
        " version=version+1 WHERE id=%s",
        [rows["first_name"].pk],
    )
    queued = fresh(rows["first_name"])
    with pytest.raises(ValueError):
        undo(harness, staff, queued)
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge(queued, staff)
    assert not ProposalResolution.objects.filter(action="reopened").exists()
    assert fresh(marked).version == marked.version


@pytest.mark.parametrize(
    ("execution", "assignments"),
    [
        # The undo's result is pending with the decision kept: nothing else.
        ("published", "execution='pending', decision='unreviewed'"),
        ("published", "execution='conflict'"),
        ("published", "execution='pending', current_available=NOT current_available"),
        ("resolved_upstream", "execution='pending', decision='approved'"),
        ("resolved_external", "execution='pending', decision='unreviewed'"),
        # No change of an updated one without its resolution, by the web login.
        ("published", None),
    ],
)
def test_sql_refuses_a_forged_undo(response_service, execution, assignments):
    """The census guard admits exactly the undo's result, and only with its
    paired resolution; an undo without a note is refused too."""
    harness, staff, rows = prepared(response_service)
    if execution == "resolved_external":
        decide(harness, staff, [rows["prefix"]], "entered_by_hand")
        change = fresh(rows["prefix"])
    elif execution == "published":
        change = published(harness, staff, rows["first_name"])
    else:
        refresh(harness, member={"firstName": "Requested"})
        change = fresh(rows["first_name"])
        assert change.execution == "resolved_upstream"
    before = ProposalResolution.objects.count()
    with pytest.raises((IntegrityError, DatabaseError)):
        if assignments is None:
            with (
                task_login(ServiceRole.WEB, exact=True, reconnect=True),
                work_transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "UPDATE stewardship_proposed_change SET execution='pending',"
                    " version=version+1 WHERE id=%s",
                    [change.pk],
                )
        else:
            forge(change, staff, assignments=assignments)
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge(change, staff, note=" ")
    assert ProposalResolution.objects.count() == before
    assert fresh(change).version == change.version


def test_the_worker_still_finds_an_updated_change_final(response_service):
    """Only the web login's paired undo reopens an updated change: the
    worker's update outside a refresh conflict is refused."""
    harness, staff, rows = answered(response_service, first_name)
    change = published(harness, staff, rows["first_name"])
    with (
        pytest.raises(DatabaseError),
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_proposed_change SET execution='pending',"
            " version=version+1 WHERE id=%s",
            [change.pk],
        )
    assert fresh(change).execution == "published"


def test_a_replaced_terminal_change_takes_no_undo(response_service):
    """A change the Family's later answer replaced is history: the service
    and SQL refuse its undo, note or not."""
    from .test_entered_incorrectly_postgresql import answer_again

    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    old = fresh(rows["prefix"])
    answer_again(harness, prefix="Sir")
    with pytest.raises(ValueError):
        undo(harness, staff, fresh(old))
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge(fresh(old), staff)
    assert fresh(old).execution == "resolved_external"


def function_text(path, name):
    """``name``'s whole definition in the frozen file ``path``, as a replace."""
    text = path.read_text(encoding="utf-8")
    found = re.search(
        rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(\).*?\$\$;",
        text,
        re.M | re.S,
    )[0]
    return found.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


OLD_BODIES = (
    ("0139_new_member_id_correction.sql", "stewardship_proposal_resolution_guard_v1"),
    ("0060_census_resolution.sql", "stewardship_proposal_resolution_effect_v1"),
    ("0138_published_refresh_conflicts.sql", "stewardship_response_derived_guard_v1"),
)


def install_old_bodies(cursor):
    """Install the three functions' bodies from before this migration."""
    for name, function in OLD_BODIES:
        cursor.execute(function_text(MIGRATIONS / name, function))


def test_the_migration_check_refuses_the_old_bodies_which_refuse_the_undo(
    response_service,
):
    """The file's DO block fails on each previous body; with the previous
    bodies installed the undo is refused, and the file installs the new
    ones again."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    with connection.cursor() as cursor:
        cursor.execute(check)
        for name, function in OLD_BODIES:
            with (
                pytest.raises(DatabaseError, match="lacks the terminal undo"),
                transaction.atomic(),
            ):
                cursor.execute(function_text(MIGRATIONS / name, function))
                cursor.execute(check)
    harness, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "accept")
    decide(harness, staff, [fresh(rows["prefix"])], "entered_by_hand")
    marked = fresh(rows["prefix"])
    try:
        with connection.cursor() as cursor:
            install_old_bodies(cursor)
        with pytest.raises((IntegrityError, DatabaseError)):
            forge(marked, staff, assignments="execution='pending'")
    finally:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(text)
            cursor.execute(LATER.read_text(encoding="utf-8"))
    assert fresh(marked).execution == "resolved_external"
    undo(harness, staff, marked)
    assert fresh(marked).execution == "pending"
