"""An Administrator resolves a census Conflict under the real roles (#528).

Migration 0108 (``schema/migrations/0108_conflict_resolution.sql``) lets a
review row accept ParishSoft's current value as the base publication merges
against ("Use the Family's value anyway", ADM-09.03), approving the change
and moving it from conflict to pending. These tests run the service and the
SQL guards as installed, the planner against the accepted base,
reconciliation and a Family resubmission once the base is stale or belongs
to an earlier row, the worklist over HTTP, and the file's own DO block
against the bodies it replaces.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.accepted_base import with_accepted_base
from parishkit.stewardship.responses.census_review import (
    KeyRebound,
    resolve_census_conflict,
)
from parishkit.stewardship.responses.models import ProposalReview, ProposedChange
from parishkit.stewardship.responses.publication import (
    MEMBER_CONTACT_FIELDS,
    Outcome,
    plan_entity,
)
from parishkit.stewardship.storage import StaleRecordError

from .test_background_grants_postgresql import task_login
from .test_census_bulk_review_postgresql import bulk
from .test_census_edit_postgresql import edit
from .test_census_resolution_postgresql import prepared, resubmit
from .test_census_review_postgresql import fresh, review
from .test_publication_plans_postgresql import owner, ready  # noqa: F401
from .test_publication_tasks_postgresql import world  # noqa: F401 (fixture)
from .test_publication_worker_postgresql import _replaced

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0108_conflict_resolution.sql"
ROUTE = "/admin/reports/census/"
# The Review form's Use the Family's value anyway button.
USE = b"resolve=use_family&amp;"


def conflicted(row, value="Parish value"):
    """Put the change in conflict with ParishSoft now holding ``value``."""
    owner(
        "UPDATE stewardship_proposed_change SET execution='conflict',"
        " current_available=true, current_value=%s::jsonb WHERE id=%s",
        [json.dumps(value), row.pk],
    )
    return fresh(row)


def resolve(harness, actor, proposal, *, version=None, key=None, note=""):
    """Use the Family's value anyway through the service on the web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return resolve_census_conflict(
            harness.service.store,
            actor,
            proposal.pk,
            expected_version=proposal.version if version is None else version,
            request_key=key or uuid4(),
            note=note,
        )


def annotated(row):
    """The proposal as the planner reads it, with its active accepted base."""
    return with_accepted_base(ProposedChange.objects.filter(pk=row.pk)).get()


def contact(first_name):
    """A full Member contact record holding ``first_name``."""
    return dict.fromkeys(MEMBER_CONTACT_FIELDS) | {
        "firstName": first_name,
        "lastName": "Example",
    }


def outcome(row, first_name):
    """The planner's outcome for ``row`` against a read holding ``first_name``."""
    plan = plan_entity("member", row.entity_key, [row], contact(first_name))
    (field,) = plan.fields
    return field.outcome, plan.payload


def test_use_the_familys_value_anyway_approves_over_parishsoft(response_service):
    """The resolution records ParishSoft's value and source, approves the
    change, makes it pending and is audited; a repeated request is one row,
    and the key cannot be reused for another kind of review."""
    harness, admin, _, _, name = prepared(response_service)
    name = conflicted(name)
    key = uuid4()
    first = resolve(harness, admin, name, key=key, note="Family confirmed")
    assert resolve(harness, admin, name, key=key, note="Family confirmed").pk == (
        first.pk
    )
    with pytest.raises(KeyRebound):
        review(harness, admin, name, "ignored", key=key, note="Family confirmed")
    assert (first.accepted_base, first.accepted_value, first.decision) == (
        True,
        "Parish value",
        "approved",
    )
    assert first.accepted_source_id == name.current_source_id
    assert first.note == "Family confirmed" and first.expected_version == name.version
    resolved = fresh(name)
    assert (resolved.decision, resolved.execution, resolved.version) == (
        "approved",
        "pending",
        name.version + 1,
    )
    # The Family's baseline is never rewritten.
    assert resolved.baseline_value == name.baseline_value
    event = AuditEvent.objects.get(
        event_type="census_change_updated", subject_id=first.pk
    )
    assert AuditContext.objects.get(event=event).context == {
        "outcome": "changed",
        "before_version": name.version,
        "after_version": name.version + 1,
    }
    # Afterwards it is an ordinary approved change: it may be ignored or sent
    # back, and a decision review accepts nothing.
    later = review(harness, admin, resolved, "ignored")
    assert (later.accepted_base, later.accepted_value) == (False, None)


def test_the_planner_merges_against_the_accepted_value(response_service):
    """While ParishSoft still holds the accepted value, the Family's answer is
    written; if ParishSoft changed again it is a conflict, and if it already
    holds the answer there is nothing to write. Without the annotation the
    row plans a conflict (fails safe); an edited value is what is written."""
    harness, admin, _, _, name = prepared(response_service)
    name = conflicted(name)
    resolve(harness, admin, name)
    row = annotated(name)
    assert row.conflict_accepted
    written, payload = outcome(row, "Parish value")
    assert written is Outcome.WRITE and payload["firstName"] == "Requested"
    assert outcome(row, "Someone else")[0] is Outcome.CONFLICT
    assert outcome(row, "Requested")[0] is Outcome.ALREADY_IN_PARISHSOFT
    assert outcome(fresh(name), "Parish value")[0] is Outcome.CONFLICT
    edit(harness, admin, fresh(name), "Corrected")
    written, payload = outcome(annotated(name), "Parish value")
    assert written is Outcome.WRITE and payload["firstName"] == "Corrected"
    # Once the source reference moves on (as reconciliation moves it when
    # ParishSoft changes), the accepted value no longer counts.
    owner(
        "UPDATE stewardship_proposal_review SET accepted_source_id=%s"
        " WHERE proposal_id=%s AND accepted_base",
        [uuid4(), name.pk],
    )
    assert not annotated(name).conflict_accepted


def test_a_resolved_conflict_is_planned_confirmed_and_published(world):  # noqa: F811
    """End to end with a fake ParishSoft still holding the accepted value:
    preflight plans the write, and the worker's own rebuild before the PUT
    agrees with it, so the run publishes the Family's answer."""
    first = conflicted(world.first)
    world.gateway.records[first.entity_key]["firstName"] = "Parish value"
    resolve(world.harness, world.admin, first)
    plan = world.planned(uuid4())
    entity = plan.entities.get()
    assert not entity.blocked and entity.planned_sha256
    _, run = world.confirm(plan)
    assert world.execute(run.run_id)
    ((_, written),) = world.gateway.writes
    assert written["firstName"] == "Requested"
    assert fresh(first).execution == "published"


@pytest.mark.parametrize("undo", ["unreviewed", "ignored"])
@pytest.mark.parametrize("in_bulk", [False, True])
def test_undoing_a_resolution_brings_the_conflict_back(response_service, undo, in_bulk):
    """Back to To review or Ignore (one by one or in bulk) on a resolved
    change puts it back to Conflict and ends the accepted base: neither a
    plain nor a bulk Approve can then approve it, so nothing publishes over
    ParishSoft's value, and Use the Family's value anyway can be chosen
    again, which makes the base count once more."""
    harness, admin, _, _, name = prepared(response_service)
    resolve(harness, admin, conflicted(name))
    resolved = fresh(name)
    if in_bulk:
        bulk(harness, admin, [resolved], undo)
    else:
        review(harness, admin, resolved, undo)
    undone = fresh(name)
    assert (undone.execution, undone.decision) == ("conflict", undo)
    assert not annotated(undone).conflict_accepted
    if undo == "ignored":
        review(harness, admin, undone, "unreviewed")
        undone = fresh(name)
        assert undone.execution == "conflict"
    with pytest.raises(ValueError):
        review(harness, admin, undone, "approved")
    with pytest.raises(ValueError):
        bulk(harness, admin, [undone], "approved")
    # Even if it were approved, the planner would see a conflict: the row is
    # in conflict and the undone base no longer counts.
    forged = annotated(undone)
    forged.decision = "approved"
    assert outcome(forged, "Parish value")[0] is Outcome.CONFLICT
    forged.execution = "pending"
    assert outcome(forged, "Parish value")[0] is Outcome.CONFLICT
    again = resolve(harness, admin, undone)
    assert again.accepted_base and annotated(name).conflict_accepted
    assert (fresh(name).execution, fresh(name).decision) == ("pending", "approved")
    assert outcome(annotated(name), "Parish value")[0] is Outcome.WRITE


def test_an_edit_after_a_resolution_keeps_it(response_service):
    """Edit value keeps the decision, so it keeps the accepted base."""
    harness, admin, _, _, name = prepared(response_service)
    resolve(harness, admin, conflicted(name))
    edit(harness, admin, fresh(name), "Corrected")
    edited = annotated(name)
    assert edited.conflict_accepted and edited.execution == "pending"


def test_sql_refuses_an_undo_that_leaves_the_change_pending(response_service):
    """A decision review away from approved on a resolved change must put it
    back to Conflict; a forged one keeping it pending is refused."""
    harness, admin, _, _, name = prepared(response_service)
    resolve(harness, admin, conflicted(name))
    resolved = fresh(name)
    with (
        pytest.raises((IntegrityError, DatabaseError), match="replacement requires"),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_review (id,correlation_id,actor_id,"
            "proposal_id,expected_version,request_key,note,decision,"
            "admin_value_set) VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,"
            "%s,gen_random_uuid(),'','unreviewed',false)",
            [admin, resolved.pk, resolved.version],
        )
        cursor.execute(
            "UPDATE stewardship_proposed_change SET decision='unreviewed',"
            " version=version+1 WHERE id=%s",
            [resolved.pk],
        )
    assert (fresh(name).execution, fresh(name).decision) == ("pending", "approved")


def test_an_edited_value_can_be_used_anyway(response_service):
    """A Conflict carrying an edit may be resolved; the edit stays."""
    harness, admin, _, _, name = prepared(response_service)
    name = conflicted(name)
    edit(harness, admin, name, "Corrected")
    resolve(harness, admin, fresh(name))
    resolved = fresh(name)
    assert (resolved.execution, resolved.decision, resolved.admin_value) == (
        "pending",
        "approved",
        "Corrected",
    )


def test_only_an_administrators_conflict_with_a_known_value_is_resolved(
    response_service,
):
    """Staff never resolve; a stale version, a By-hand change, a change not in
    conflict, a queued one and a conflict whose ParishSoft value is unknown
    are refused, and nothing is recorded."""
    harness, admin, staff, home, name = prepared(response_service)
    with pytest.raises(ValueError):
        resolve(harness, admin, name)  # pending, not a conflict
    name = conflicted(name)
    with pytest.raises(PermissionError):
        resolve(harness, staff, name)
    with pytest.raises(StaleRecordError):
        resolve(harness, admin, name, version=name.version + 1)
    with pytest.raises(ValueError):
        resolve(harness, admin, conflicted(home))
    for statement in (
        "UPDATE stewardship_proposed_change SET execution='queued' WHERE id=%s",
        "UPDATE stewardship_proposed_change SET execution='conflict',"
        " current_available=false, current_value=NULL WHERE id=%s",
    ):
        owner(statement, [name.pk])
        with pytest.raises(ValueError):
            resolve(harness, admin, fresh(name))
    assert ProposalReview.objects.count() == 0


def forge(proposal, actor, *, review=None, update=None):
    """Insert a resolution row and apply it directly as the web login,
    bypassing the service and its audit, inside one transaction.

    The row accepts the proposal's own current value and source, approved,
    and the update approves the proposal and makes it pending; ``review``
    and ``update`` override columns of either.
    """
    row = {
        "decision": "approved",
        "admin_value_set": proposal.admin_value_set,
        "admin_value": json.dumps(proposal.admin_value)
        if proposal.admin_value_set
        else None,
        "accepted_base": True,
        "accepted_value": json.dumps(proposal.current_value),
        "accepted_source_id": proposal.current_source_id,
    } | (review or {})
    changes = {"decision": row["decision"], "execution": "pending"} | (update or {})
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_review (id,correlation_id,actor_id,"
            "proposal_id,expected_version,request_key,note,decision,admin_value_set,"
            "admin_value,accepted_base,accepted_value,accepted_source_id)"
            " VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),'',%s,%s,%s::jsonb,%s,%s::jsonb,%s)",
            [actor, proposal.pk, proposal.version, *row.values()],
        )
        assignments = ", ".join(f"{column}=%s" for column in changes)
        cursor.execute(
            f"UPDATE stewardship_proposed_change SET {assignments},"
            " version=version+1 WHERE id=%s",
            [*changes.values(), proposal.pk],
        )


@pytest.mark.parametrize(
    ("review", "update", "message"),
    [
        # Not the value or the source the proposal holds.
        ({"accepted_value": '"Something else"'}, None, "current authority"),
        ({"accepted_source_id": uuid4()}, None, "current authority"),
        # A resolution approves.
        ({"decision": "ignored"}, None, "current authority"),
        # With an edit at once.
        (
            {"admin_value_set": True, "admin_value": '"Corrected"'},
            {"admin_value_set": True, "admin_value": '"Corrected"'},
            "current authority",
        ),
        # A decision review that also carries an accepted value.
        (
            {"accepted_base": False, "accepted_source_id": None},
            None,
            "current authority",
        ),
        # Conflict to pending with a plain approval.
        (
            {
                "accepted_base": False,
                "accepted_value": None,
                "accepted_source_id": None,
            },
            None,
            "current authority",
        ),
        # The proposal left in conflict: no web branch admits that.
        (None, {"execution": "conflict"}, "replacement requires"),
        # Well formed, but with no audit event.
        (None, None, "result and audit"),
    ],
)
def test_sql_refuses_a_forged_resolution(response_service, review, update, message):
    """The review guard, the derived guard and the effect hold every writer
    to the resolution's rules, with or without the service."""
    harness, admin, _, _, name = prepared(response_service)
    name = conflicted(name)
    with pytest.raises((IntegrityError, DatabaseError), match=message):
        forge(name, admin, review=review, update=update)
    name = fresh(name)
    assert (name.execution, name.decision) == ("conflict", "unreviewed")


@pytest.mark.parametrize(
    "columns",
    [
        "accepted_base=true, decision='ignored'",
        "accepted_base=true, accepted_source_id=NULL",
        "accepted_value='\"x\"'::jsonb",
    ],
)
def test_the_table_check_holds_the_accepted_base_shape(response_service, columns):
    """Even with every trigger off, a base needs a source and an approval,
    and a row without one accepts no value."""
    harness, admin, _, _, name = prepared(response_service)
    resolve(harness, admin, conflicted(name))
    with pytest.raises(IntegrityError, match="proposal_review_accepted_shape"):
        owner(
            f"UPDATE stewardship_proposal_review SET {columns}"
            if "accepted_base=true" in columns
            else "UPDATE stewardship_proposal_review SET accepted_base=false,"
            f" accepted_source_id=NULL, {columns}"
        )


def test_sql_refuses_a_resolution_outside_a_known_conflict(response_service):
    """A pending change, or a conflict ParishSoft could not be read for,
    cannot be resolved even by a forged row."""
    harness, admin, _, _, name = prepared(response_service)
    with pytest.raises((IntegrityError, DatabaseError), match="current authority"):
        forge(name, admin)
    owner(
        "UPDATE stewardship_proposed_change SET execution='conflict',"
        " current_available=false, current_value=NULL WHERE id=%s",
        [name.pk],
    )
    with pytest.raises((IntegrityError, DatabaseError), match="current authority"):
        forge(fresh(name), admin)


def test_web_cannot_leave_a_conflict_without_a_resolution(response_service):
    """No other web write moves a conflict to pending: not a decision review
    (which keeps the execution), and not a bare update."""
    harness, admin, _, _, name = prepared(response_service)
    name = conflicted(name)
    with (
        pytest.raises((IntegrityError, DatabaseError), match="replacement requires"),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_proposed_change SET execution='pending',"
            " version=version+1 WHERE id=%s",
            [name.pk],
        )
    assert fresh(name).execution == "conflict"


def accept(row, *, decision="approved", execution="pending"):
    """Record an accepted base at the row's current source, as the schema
    owner (the service's own path is tested above), and approve the row."""
    owner(
        "INSERT INTO stewardship_proposal_review (id,correlation_id,actor_id,"
        "proposal_id,expected_version,request_key,decision,admin_value_set,"
        "note,accepted_base,accepted_value,accepted_source_id) VALUES"
        " (gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),%s,%s,"
        "gen_random_uuid(),'approved',false,'',true,"
        "(SELECT current_value FROM stewardship_proposed_change WHERE id=%s),%s)",
        [row.pk, row.version, row.pk, row.current_source_id],
    )
    owner(
        "UPDATE stewardship_proposed_change SET decision=%s, execution=%s,"
        " version=version+1 WHERE id=%s",
        [decision, execution, row.pk],
    )
    return fresh(row)


def test_a_new_parishsoft_value_withdraws_an_accepted_approval(
    live_response_service, monkeypatch
):
    """Reconciliation moves the source reference when ParishSoft changes, so
    the accepted value is stale: the approval goes back to To review (and
    the row is a conflict again). The database refuses a reconciliation
    that keeps the approval."""
    from parishkit.stewardship.responses import reconciliation

    from .response_builders import response_source
    from .test_response_submission_postgresql import form_and_answers, submit
    from .test_source_families_postgresql import prepare, promote

    harness = live_response_service
    earlier, claim = prepare(response_source())
    promote(earlier, claim, harness.campaign, harness.rings)
    form, answers = form_and_answers(harness)
    answers["members"]["3"]["first_name"] = "Requested name"
    response = submit(harness, form, answers).submission
    data = response_source()
    data.members[3]["firstName"] = "Updated source"
    current, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(current, claim, harness.campaign, harness.rings)
    row = ProposedChange.objects.get(submission=response)
    assert (row.execution, row.current_value) == ("conflict", "Updated source")
    row = accept(row)
    assert annotated(row).conflict_accepted
    data.members[3]["firstName"] = "Changed again"
    third, claim = prepare(data)
    monkeypatch.setattr(reconciliation, "resets_approval", lambda row, execution: False)
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(IntegrityError, match="stale accepted approval"),
    ):
        promote(third, claim, harness.campaign, harness.rings)
    assert fresh(row).decision == "approved"
    monkeypatch.undo()
    # The refused promotion rolled back and still holds its claim.
    with task_login(ServiceRole.WORKER, exact=True):
        promote(third, claim, harness.campaign, harness.rings)
    row = fresh(row)
    assert (row.execution, row.decision, row.current_value) == (
        "conflict",
        "unreviewed",
        "Changed again",
    )
    assert not annotated(row).conflict_accepted


def test_a_resubmission_does_not_carry_an_accepted_approval(
    live_response_service, monkeypatch
):
    """The accepted value belongs to the row it was recorded on, so a Family's
    same-intent resubmission is compared with their baseline again and its
    approval goes back to To review; SQL refuses carrying it."""
    from parishkit.stewardship.responses import proposals

    from .test_response_revisit_postgresql import revisit
    from .test_response_submission_postgresql import form_and_answers, submit
    from .test_runtime_auth_grants_postgresql import web_login

    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["members"]["3"]["first_name"] = "Requested name"
    first = submit(harness, form, answers).submission
    accept(ProposedChange.objects.get(submission=first))
    harness, form, answers, _ = revisit(harness)
    with monkeypatch.context() as patch:
        patch.setattr(proposals, "has_active_base", lambda row: False)
        with web_login(), pytest.raises(IntegrityError, match="same prior intent"):
            submit(harness, form, answers)
    with web_login():
        second = submit(harness, form, answers).submission
    carried = ProposedChange.objects.get(submission=second)
    assert (carried.decision, carried.execution) == ("unreviewed", "pending")
    assert not annotated(carried).conflict_accepted


def test_a_later_family_response_supersedes_a_resolved_change(response_service):
    """A different later answer supersedes the resolved row as any other."""
    harness, admin, _, _, name = prepared(response_service)
    resolve(harness, admin, conflicted(name))
    resubmit(harness)
    assert fresh(name).execution == "superseded"


def test_do_block_refuses_the_earlier_bodies():
    """The file's closing check passes as installed and raises once any
    replaced function is put back to its earlier body or the new check is
    gone; on the earlier state the file installs all of it again."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    files = MIGRATIONS / "migrations"
    edit_file = (files / "0107_proposal_review_edit.sql").read_text(encoding="utf-8")
    review_file = (files / "0096_proposal_review.sql").read_text(encoding="utf-8")
    effect_start = review_file.index(
        "CREATE FUNCTION public.stewardship_proposal_review_effect_v1()"
    )
    earlier = {
        "review_guard": _replaced(edit_file, "stewardship_proposal_review_guard_v1"),
        "effect": review_file[
            effect_start : review_file.index("$$;", effect_start) + 3
        ].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
        "derived_guard": _replaced(
            review_file, "stewardship_response_derived_guard_v1"
        ),
        "check": "ALTER TABLE stewardship_proposal_review"
        " DROP CONSTRAINT proposal_review_accepted_shape",
    }
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    for statement in earlier.values():
        with (
            pytest.raises(DatabaseError, match="lacks"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(check)

    class Rollback(Exception):
        """Ends a check that must leave the database as it found it."""

    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        for name in ("review_guard", "effect", "derived_guard"):
            cursor.execute(earlier[name])
        cursor.execute(
            "ALTER TABLE stewardship_proposal_review DROP COLUMN accepted_base,"
            " DROP COLUMN accepted_value, DROP COLUMN accepted_source_id"
        )
        cursor.execute(text)
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef AND ("
            "(proname='stewardship_proposal_review_guard_v1'"
            " AND prosrc LIKE '%%resolved := NEW.accepted_base;%%')"
            " OR (proname='stewardship_response_derived_guard_v1'"
            " AND prosrc LIKE '%%stale accepted approval%%'))"
        )
        assert cursor.fetchone() == (2,)
        raise Rollback


def test_the_worklist_resolves_in_place_for_an_administrator_only(
    response_service, google
):
    """An Administrator's Conflict row offers Use the Family's value anyway;
    the answer is the page with its notice and the row To do. Staff see no
    such button and are denied; a pending row offers none."""
    from .auth_builders import signed_in
    from .test_information_followup_postgresql import search

    harness, admin, staff, _, name = prepared(response_service)
    google[0]["email"] = "admin@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    _, body = search(browser, ROUTE, {"status": "all"})
    assert USE not in body
    name = conflicted(name)
    form = {
        "status": "all",
        "resolve": "use_family",
        "proposal": str(name.pk),
        "version": str(name.version),
        "request_key": str(uuid4()),
        "note": "Asked the Family",
    }
    google[0]["email"] = "staff@example.org"
    staff_browser, login = signed_in()
    assert login.status_code == 302
    _, body = search(staff_browser, ROUTE, {"status": "all"})
    assert USE not in body
    response, _ = search(staff_browser, ROUTE, form)
    assert response.status_code == 403
    google[0]["email"] = "admin@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    _, body = search(browser, ROUTE, {"status": "all"})
    assert USE in body and b"Use the Family's value anyway" in body
    response, body = search(browser, ROUTE, form)
    assert response.status_code == 200
    assert b"approved over ParishSoft&#x27;s current value." in body
    assert USE not in body and b"Asked the Family" in body
    resolved = fresh(name)
    assert (resolved.execution, resolved.decision) == ("pending", "approved")
    # The same drawn form again records nothing new.
    response, body = search(browser, ROUTE, form)
    assert response.status_code == 200
    assert ProposalReview.objects.count() == 1
