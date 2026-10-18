"""The publication check marks Already in ParishSoft under the real roles (#528).

Migration 0112 (``schema/migrations/0112_upstream_at_check.sql``) lets a
Production-mode preflight mark an approved change whose value ParishSoft
already holds ``resolved_upstream`` in the transaction that records its plan
(the data spec's preflight step 4), lets such a plan still be confirmed, and
the worker's pre-PUT check leaves exactly those changes out. These tests run
the preflight and the run through the real dispatcher on the fake gateway,
the derived guard's same-transaction check, the old guard definitions, and
the file's own DO block against the bodies it replaces. Nothing here calls
ParishSoft.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses import publication_plans
from parishkit.stewardship.responses.models import (
    ProposedChange,
    PublicationEntity,
    PublicationField,
    PublicationPlan,
)
from parishkit.stewardship.responses.publication import Outcome, plan_entity
from parishkit.stewardship.responses.publication_plans import confirmation_counts

from .test_background_grants_postgresql import task_login
from .test_publication_plans_postgresql import (
    confirm,
    owner,
    plan,
    ready,  # noqa: F401 (fixture)
)
from .test_publication_tasks_postgresql import (
    state,
    world,  # noqa: F401 (fixture)
)
from .test_publication_worker_postgresql import _replaced, contact, worker_update

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0112_upstream_at_check.sql"
DERIVED = "stewardship_response_derived_guard_v1"
CONFIRMATION = "stewardship_publication_confirmation_guard_v1"


def earlier(name):
    """The replaced function's body just before 0112, as a whole statement."""
    source = (
        "0108_conflict_resolution.sql"
        if name == DERIVED
        else ("0090_publication_worker.sql")
    )
    return _replaced((MIGRATIONS / "migrations" / source).read_text("utf-8"), name)


def install(text):
    """Run SQL text as the schema owner, unparameterized (it holds ``%``)."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(text)


def live(name):
    """The installed definition of ``public.name()``, as one statement.

    A later migration (0114, #1547) replaces 0112's confirmation guard, so a
    test that runs 0112's file puts this back, not 0112's body.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef(%s::regprocedure)", [f"public.{name}()"]
        )
        return cursor.fetchone()[0] + ";"


def matching(first):
    """Add an approved last-name change the fake ParishSoft already holds.

    It is a copy of the first-name change's row (same response, Member and
    version) for ``last_name``, whose Family answer is the record's
    ``lastName`` ("Example", ``contact``), so the check finds it already in
    ParishSoft. Inserted as the schema owner with triggers off, as the other
    publication tests set up their approvals.
    """
    pk = uuid4()
    owner(
        "INSERT INTO stewardship_proposed_change SELECT (jsonb_populate_record("
        "NULL::stewardship_proposed_change, to_jsonb(p) || jsonb_build_object("
        "'id', %s::uuid, 'field', 'last_name', 'submitted_value', %s::jsonb,"
        " 'baseline_value', %s::jsonb, 'current_value', %s::jsonb))).*"
        " FROM stewardship_proposed_change p WHERE p.id=%s",
        [
            str(pk),
            json.dumps("Example"),
            json.dumps("Old"),
            json.dumps("Old"),
            first.pk,
        ],
    )
    return ProposedChange.objects.get(pk=pk)


def now(row):
    """The proposal's current execution, decision and version."""
    fresh = ProposedChange.objects.get(pk=row.pk)
    return fresh.execution, fresh.decision, fresh.version


def outcomes(record):
    """Each field row of a plan: proposal, recorded version and outcome."""
    return set(
        PublicationField.objects.filter(entity__plan=record).values_list(
            "proposal_id", "proposal_version", "outcome"
        )
    )


def test_a_matching_change_is_marked_at_the_check_and_survives_the_run(world):  # noqa: F811
    """The check marks the matching change Already in ParishSoft at the next
    version, in the plan's own transaction; the plan is still confirmed, the
    run writes only the other change, and the mark is left as it is."""
    last = matching(world.first)
    plan_row = world.planned(uuid4())
    assert outcomes(plan_row) == {
        (world.first.pk, world.first.version, "write"),
        (last.pk, last.version, "already_in_parishsoft"),
    }
    assert now(last) == ("resolved_upstream", "approved", last.version + 1)
    assert now(world.first) == ("pending", "approved", world.first.version)
    assert confirmation_counts(plan_row.pk) == {
        "write_entities": 1,
        "write_fields": 1,
        "blocked_entities": 0,
    }
    _, run = world.confirm(plan_row)
    assert world.execute(run.run_id) and state(run) == "succeeded"
    ((_, written),) = world.gateway.writes
    # The PUT is a partial update (#1600): the body holds only the written
    # change, never the matching one or any field as read.
    assert written == {"firstName": "Requested"}
    assert now(world.first)[0] == "published"
    assert now(last) == ("resolved_upstream", "approved", last.version + 1)
    # The mark is final: the derived guard keeps resolved_upstream immutable.
    with pytest.raises(DatabaseError, match="immutable"):
        worker_update(ProposedChange.objects.get(pk=last.pk), execution="pending")


def test_a_non_matching_change_still_conflicts(world):  # noqa: F811
    """A change whose ParishSoft value moved since the Family answered is a
    conflict: it blocks the Member, is not marked and stays open; only the
    matching change on the same Member is marked."""
    last = matching(world.first)
    world.gateway.records[world.first.entity_key]["firstName"] = "Someone else"
    plan_row = world.planned(uuid4())
    assert outcomes(plan_row) == {
        (world.first.pk, world.first.version, "conflict"),
        (last.pk, last.version, "already_in_parishsoft"),
    }
    assert PublicationEntity.objects.get(plan=plan_row).blocked
    assert now(world.first) == ("pending", "approved", world.first.version)
    assert now(last)[0] == "resolved_upstream"
    assert confirmation_counts(plan_row.pk)["write_entities"] == 0


def test_a_testing_mode_check_marks_nothing(ready):  # noqa: F811
    """A Testing-mode plan records the outcome but leaves the change open."""
    _, admin, _, home, first, _ = ready
    last = matching(first)
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    record = contact(first)
    entity = plan_entity("member", first.entity_key, [first, last], record)
    plan_row = plan(admin, [first, last, home], [entity])
    assert plan_row.testing
    assert (last.pk, last.version, "already_in_parishsoft") in outcomes(plan_row)
    assert now(last) == ("pending", "approved", last.version)


def test_a_change_that_failed_earlier_is_marked(world):  # noqa: F811
    """A change an earlier run left failed is still marked once ParishSoft
    holds its value, and the plan is confirmed."""
    last = matching(world.first)
    owner(
        "UPDATE stewardship_proposed_change SET execution='failed' WHERE id=%s",
        [last.pk],
    )
    plan_row = world.planned(uuid4())
    assert (last.pk, last.version, "already_in_parishsoft") in outcomes(plan_row)
    assert now(last) == ("resolved_upstream", "approved", last.version + 1)
    world.confirm(plan_row)


def test_an_administrator_edited_value_already_there_is_marked(world):  # noqa: F811
    """An Administrator's edit that ParishSoft already holds is marked, though
    the Family's own answer differs."""
    last = matching(world.first)
    owner(
        "UPDATE stewardship_proposed_change SET submitted_value=%s::jsonb,"
        " admin_value_set=true, admin_value=%s::jsonb WHERE id=%s",
        [json.dumps("Family answer"), json.dumps("Example"), last.pk],
    )
    plan_row = world.planned(uuid4())
    assert (last.pk, last.version, "already_in_parishsoft") in outcomes(plan_row)
    assert now(last) == ("resolved_upstream", "approved", last.version + 1)


def unmarked(monkeypatch):
    """Record plans without the check's mark, as code bypassing it would."""
    monkeypatch.setattr(publication_plans, "_mark_upstream", lambda *args: None)


def forced(monkeypatch, **extra):
    """Record plans that try the mark whatever the system mode says.

    ``extra`` adds more changed fields (such as a decision) to the same
    update, to show the guard refuses a mark that changes more than the
    execution.
    """

    def mark(record, plans):
        ids = [
            item.proposal_id
            for plan in plans
            for item in plan.fields
            if item.outcome is Outcome.ALREADY_IN_PARISHSOFT
        ]
        ProposedChange.objects.filter(pk__in=ids).update(
            execution="resolved_upstream", version=F("version") + 1, **extra
        )

    monkeypatch.setattr(publication_plans, "_mark_upstream", mark)


def test_the_guard_admits_the_mark_only_in_the_plans_own_transaction(
    ready,  # noqa: F811
    monkeypatch,
):
    """Without a plan, after the plan committed, from the web, with a changed
    decision, or in a Testing-mode plan, SQL refuses the mark; and a plan
    whose matching change was never marked cannot be confirmed."""
    harness, admin, _, home, first, session = ready
    last = matching(first)
    with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
        worker_update(last, execution="resolved_upstream")
    unmarked(monkeypatch)
    entity = plan_entity("member", first.entity_key, [first, last], contact(first))
    committed = plan(admin, [first, last, home], [entity])
    assert now(last) == ("pending", "approved", last.version)
    # The plan row exists, but it was committed by an earlier transaction.
    with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
        worker_update(last, execution="resolved_upstream")
    with pytest.raises(DatabaseError, match="changed since preflight"):
        confirm(harness, admin, committed, session)
    # The web login reaches the guard's replacement branch, which refuses.
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(
            DatabaseError,
            match="Proposal replacement requires a new final Family response",
        ),
        work_transaction(),
    ):
        ProposedChange.objects.filter(pk=last.pk).update(
            execution="resolved_upstream", version=last.version + 1
        )
    # A mark that also changes the decision, inside a Production plan.
    forced(monkeypatch, decision="ignored")
    with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
        plan(admin, [first, last, home], [entity])
    forced(monkeypatch)
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
        plan(admin, [first, last, home], [entity])
    owner("UPDATE stewardship_system_configuration SET mode='production'")
    marked = plan(admin, [first, last, home], [entity])
    assert now(last)[0] == "resolved_upstream"
    confirm(harness, admin, marked, session)


def test_the_old_guards_refuse_what_0112_admits(world):  # noqa: F811
    """Before 0112 the mark fails the preflight (no plan is recorded) and a
    plan holding a marked change cannot be confirmed; 0112's file, run
    again, admits both."""
    last = matching(world.first)
    installed = [live(DERIVED), live(CONFIRMATION)]
    try:
        install(earlier(DERIVED))
        task = world.preflight()
        assert world.execute(task.run_id) and state(task) == "failed"
        assert not PublicationPlan.objects.exists()
        install(FROZEN.read_text("utf-8"))
        plan_row = world.planned(uuid4())
        assert now(last)[0] == "resolved_upstream"
        install(earlier(CONFIRMATION))
        with pytest.raises(DatabaseError, match="changed since preflight"):
            world.confirm(plan_row)
    finally:
        for statement in installed:
            install(statement)
    world.confirm(plan_row)


def test_do_block_refuses_the_earlier_bodies():
    """The file's closing check passes as installed and raises once either
    replaced function is put back to its earlier body; on the earlier state
    the file installs both again, neither a SECURITY DEFINER."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    for name in (DERIVED, CONFIRMATION):
        with (
            pytest.raises(DatabaseError, match="lacks the mark at the check"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(earlier(name))
            cursor.execute(check)

    class Rollback(Exception):
        """Ends a check that must leave the database as it found it."""

    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        for name in (DERIVED, CONFIRMATION):
            cursor.execute(earlier(name))
        cursor.execute(text)
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef"
            " AND proname IN (%s, %s)"
            " AND prosrc LIKE '%%already_in_parishsoft%%'"
            " AND prosrc LIKE '%%resolved_upstream%%'",
            [DERIVED, CONFIRMATION],
        )
        assert cursor.fetchone() == (2,)
        raise Rollback
