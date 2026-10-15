"""The fenced ParishSoft publish worker under the real roles (#528, BG-09).

``responses.publication_worker`` writes a confirmed plan through a
``ContactGateway``; here it is an in-memory fake, so nothing calls
ParishSoft. The worker runs on the restricted worker login, with migration
0090's derived guard admitting each proposal state change only as the
confirmed plan and its outcomes say. The file's own DO block is run against
a database holding the bodies it replaces.
"""

from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection, transaction
from psycopg import sql

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import (
    ProposedChange,
    PublicationEntity,
    PublicationOutcome,
)
from parishkit.stewardship.responses.publication import (
    MEMBER_CONTACT_FIELDS,
    encode_member_value,
    plan_entity,
)
from parishkit.stewardship.responses.publication_gateway import ContactGatewayError
from parishkit.stewardship.responses.publication_worker import (
    ATTEMPTS,
    PublicationRefused,
    publish_plan,
)
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease

from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_publication_plans_postgresql import (
    approve,
    confirm,
    context,
    owner,
    plan,
    ready,  # noqa: F401 (fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0090_publication_worker.sql"


class Crash(BaseException):
    """A worker killed mid-run (not an ordinary failure)."""


class FakeGateway:
    """An in-memory ParishSoft: one contact record per Member id.

    ``read_errors`` and ``write_errors`` are failure words raised, in order,
    before the store is touched; ``on_read`` runs before each read (to
    change the world mid-run) and ``crash_after_write`` stores a write and
    then dies before the worker can record it.
    """

    def __init__(self, records):
        """Start from ``records`` and no calls."""
        self.records = {key: dict(value) for key, value in records.items()}
        self.reads, self.writes = [], []
        self.read_errors, self.write_errors = [], []
        self.on_read = None
        self.crash_after_write = False

    def read_member_contact(self, member_id, *, timeout):
        """Return a copy of the stored record, as an uncached read would."""
        self.reads.append(member_id)
        if self.on_read is not None:
            self.on_read()
        if self.read_errors:
            raise ContactGatewayError(self.read_errors.pop(0))
        record = self.records.get(member_id)
        return None if record is None else dict(record)

    def write_member_contact(self, member_id, payload, *, timeout):
        """Store the full payload, as the contact PUT replaces the record."""
        self.writes.append((member_id, dict(payload)))
        if self.write_errors:
            raise ContactGatewayError(self.write_errors.pop(0))
        self.records[member_id] = dict(payload)
        if self.crash_after_write:
            raise Crash


class FakeExecution:
    """A running publish task's execution: its claim and ordered effects."""

    def __init__(self):
        """Admit one synthetic running task."""
        task = running_source_task()
        self.claim = SimpleNamespace(
            run_id=task["task_id"],
            fence=task["task_fence"],
            worker_id=task["worker_id"],
        )

    def check(self):
        """The task is never stopped here."""

    @contextmanager
    def effect(self):
        """One ordered short transaction, as the dispatcher's effect is."""
        with work_transaction():
            yield

    def maintain_source(self, claim):
        """No renewal thread is needed within these short tests."""
        return nullcontext()


def as_owner(statement):
    """Run ``owner(statement)`` from inside a restricted login, then go back."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        (role,) = cursor.fetchone()
        cursor.execute("RESET SESSION AUTHORIZATION")
    try:
        owner(statement)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role))
            )


def contact(first):
    """A full contact record whose first name is the change's baseline."""
    return dict.fromkeys(MEMBER_CONTACT_FIELDS) | {
        "firstName": first.baseline_value,
        "lastName": "Example",
    }


@pytest.fixture
def confirmed(ready):  # noqa: F811
    """A confirmed plan writing the first-name change, and its fake ParishSoft."""
    harness, admin, _, home, first, session = ready
    record = contact(first)
    entity = plan_entity(first.entity_kind, first.entity_key, [first], record)
    assert entity.payload is not None
    plan_row = plan(admin, [first, home], [entity])
    confirm(harness, admin, plan_row, session)
    gateway = FakeGateway({first.entity_key: record})
    return SimpleNamespace(
        harness=harness,
        admin=admin,
        session=session,
        first=first,
        home=home,
        plan=plan_row,
        entity=PublicationEntity.objects.get(plan=plan_row, entity_kind="member"),
        gateway=gateway,
        planned=entity.payload,
    )


def run(state, gateway=None):
    """Run the worker on the restricted worker login (its task made first)."""
    execution = FakeExecution()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return publish_plan(
            execution, state.plan.pk, gateway or state.gateway, enabled=True
        )


def drained():
    """End the last run's request safety window, as time would."""
    owner("UPDATE stewardship_source_lease SET external_deadline=NULL")


def results(entity):
    """The entity's outcomes, in attempt order."""
    return list(
        PublicationOutcome.objects.filter(entity=entity)
        .order_by("attempt")
        .values_list("result", "failure_kind")
    )


def proposal(row):
    """The proposal's current execution and version."""
    row.refresh_from_db()
    return row.execution, row.version


def test_publish_writes_verifies_and_marks_published(confirmed):
    """One PUT of the planned full payload, verified by a fresh read, records
    published and moves the change pending -> queued -> published; the lease
    is released and the audit holds a word and a count only."""
    state = confirmed
    before = state.first.version
    result = run(state)
    assert (result.published, result.invalidated, result.failed_attempts) == (1, 0, 0)
    (written,) = state.gateway.writes
    assert written == (state.first.entity_key, state.planned)
    assert written[1]["firstName"] == encode_member_value(
        "first_name", state.first.submitted_value
    )
    # Read before the PUT, then once more to verify it.
    assert state.gateway.reads == [state.first.entity_key] * 2
    assert results(state.entity) == [("published", "")]
    assert proposal(state.first) == ("published", before + 2)
    assert context("census_publication_recorded") == [
        {"outcome": "succeeded", "count": 1}
    ]
    assert SourceMutationLease.objects.get(singleton=True).phase == "idle"
    # A published entity is never written again.
    drained()
    assert run(state).published == 0 and len(state.gateway.writes) == 1


def test_a_stale_read_invalidates_without_writing(confirmed):
    """ParishSoft changed since preflight: no PUT, and the change goes back
    to pending (it was never queued) for a new preflight."""
    state = confirmed
    before = proposal(state.first)
    state.gateway.records[state.first.entity_key]["fax"] = "555-0100"
    assert run(state).invalidated == 1
    assert state.gateway.writes == []
    assert results(state.entity) == [("invalidated", "")]
    assert proposal(state.first) == before


def test_a_changed_proposal_invalidates_without_writing(confirmed):
    """A decision or edit since confirmation changes the version: no PUT."""
    state = confirmed
    approve(state.first)
    assert run(state).invalidated == 1
    assert state.gateway.writes == []
    assert results(state.entity) == [("invalidated", "")]


def test_testing_mode_refuses_and_a_switch_stops_every_write(confirmed):
    """In Testing mode the worker refuses before the lease; a switch to
    Testing after the read stops the run before any PUT."""
    state = confirmed
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    with pytest.raises(PublicationRefused):
        run(state)
    assert state.gateway.reads == [] and results(state.entity) == []
    owner("UPDATE stewardship_system_configuration SET mode='production'")
    state.gateway.on_read = lambda: as_owner(
        "UPDATE stewardship_system_configuration SET mode='testing'"
    )
    assert run(state).invalidated == 1
    assert state.gateway.writes == []
    assert proposal(state.first)[0] == "pending"


def test_a_failed_write_is_retried_and_then_published(confirmed):
    """A sanitized failure leaves the change queued; the next attempt (the
    same plan) writes it and verifies it."""
    state = confirmed
    before = state.first.version
    state.gateway.write_errors = ["write_unavailable"]
    result = run(state)
    assert (result.published, result.failed_attempts) == (1, 1)
    assert results(state.entity) == [
        ("failed", "write_unavailable"),
        ("published", ""),
    ]
    assert len(state.gateway.writes) == 2
    assert proposal(state.first) == ("published", before + 2)


def test_exhausted_attempts_invalidate_and_leave_the_change_failed(confirmed):
    """After ATTEMPTS failures the entity is invalidated and the change goes
    queued -> failed, so a new preflight plans it and confirmation of the new
    plan is no longer blocked by this one."""
    state = confirmed
    before = state.first.version
    state.gateway.write_errors = ["write_rejected"] * ATTEMPTS
    result = run(state)
    assert (result.invalidated, result.failed_attempts) == (1, ATTEMPTS)
    assert results(state.entity) == [
        *[("failed", "write_rejected")] * ATTEMPTS,
        ("invalidated", ""),
    ]
    assert proposal(state.first) == ("failed", before + 2)
    state.first.refresh_from_db()
    record = contact(state.first)
    again = plan_entity("member", state.first.entity_key, [state.first], record)
    second = plan(state.admin, [state.first], [again])
    confirm(state.harness, state.admin, second, state.session)


def test_a_verify_mismatch_is_a_failure_then_the_next_read_invalidates(confirmed):
    """ParishSoft holding something else after the PUT is never published."""
    state = confirmed
    original = state.gateway.write_member_contact

    def normalizing(member_id, payload, *, timeout):
        """Store the payload with one field altered, as a provider might."""
        original(member_id, payload | {"lastName": "EXAMPLE"}, timeout=timeout)

    state.gateway.write_member_contact = normalizing
    assert run(state).invalidated == 1
    assert results(state.entity) == [
        ("failed", "verify_mismatch"),
        ("invalidated", ""),
    ]
    assert proposal(state.first)[0] == "failed"


def test_a_crash_after_the_put_is_recovered_without_a_second_put(confirmed):
    """The change stays queued with no outcome; the next run sees the planned
    payload already in ParishSoft and records published without writing."""
    state = confirmed
    before = state.first.version
    state.gateway.crash_after_write = True
    with pytest.raises(Crash):
        run(state)
    assert proposal(state.first) == ("queued", before + 1)
    assert results(state.entity) == []
    state.gateway.crash_after_write = False
    drained()
    assert run(state).published == 1
    assert len(state.gateway.writes) == 1
    assert results(state.entity) == [("published", "")]
    assert proposal(state.first) == ("published", before + 2)


def test_plans_past_their_window_are_invalidated(confirmed):
    """A confirmation older than the execution window is abandoned work: it
    is invalidated without a read, and returns the change for preflight."""
    state = confirmed
    owner(
        "UPDATE stewardship_publication_confirmation "
        "SET created_at=created_at-interval '25 hours' WHERE plan_id=%s",
        [state.plan.pk],
    )
    assert run(state).invalidated == 1
    assert state.gateway.reads == [] and state.gateway.writes == []
    assert results(state.entity) == [("invalidated", "")]


def worker_update(row, **values):
    """Update one proposal directly on the worker login, under the order."""
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        work_transaction(),
    ):
        return ProposedChange.objects.filter(pk=row.pk).update(
            version=row.version + 1, **values
        )


def test_the_guard_admits_publication_states_only_as_the_plan_says(ready):  # noqa: F811
    """Without a confirmation, without the live publication lease, from web,
    in Testing mode, or without the matching outcome, the guard refuses."""
    harness, admin, _, _, first, session = ready
    record = contact(first)
    entity = plan_entity(first.entity_kind, first.entity_key, [first], record)
    plan_row = plan(admin, [first], [entity])
    claim = acquire_source(**running_source_task(), phase="publication")
    with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
        worker_update(first, execution="queued")
    confirm(harness, admin, plan_row, session)
    release_source(claim)
    with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
        worker_update(first, execution="queued")  # no live publication lease
    claim = acquire_source(**running_source_task(), phase="publication")
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        ProposedChange.objects.filter(pk=first.pk).update(
            execution="queued", version=first.version + 1
        )
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
        worker_update(first, execution="queued")
    owner("UPDATE stewardship_system_configuration SET mode='production'")
    assert worker_update(first, execution="queued") == 1
    first.refresh_from_db()
    for target in ("published", "pending", "failed"):
        with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
            worker_update(first, execution=target)
    release_source(claim)


def test_only_the_queueing_plan_settles_a_change(confirmed):
    """An earlier plan invalidated at the same proposal version (a stale read
    queues nothing) cannot settle a change a later plan queued: only the
    latest confirmed plan writing it at that version, once finished, can."""
    state = confirmed
    state.gateway.records[state.first.entity_key]["fax"] = "555-0100"
    assert run(state).invalidated == 1
    state.gateway.records[state.first.entity_key]["fax"] = None
    state.first.refresh_from_db()
    entity = plan_entity(
        "member", state.first.entity_key, [state.first], contact(state.first)
    )
    later = plan(state.admin, [state.first], [entity])
    confirm(state.harness, state.admin, later, state.session)
    drained()
    claim = acquire_source(**running_source_task(), phase="publication")
    assert worker_update(state.first, execution="queued") == 1
    state.first.refresh_from_db()
    for target in ("published", "pending", "failed"):
        with pytest.raises(DatabaseError, match="confirmed plan and outcome"):
            worker_update(state.first, execution=target)
    release_source(claim)


def test_confirmation_needs_writes_still_approved(ready):  # noqa: F811
    """Review note 3: a planned write whose decision changed without a version
    bump (no guard forces one) still refuses the confirmation."""
    harness, admin, _, _, first, session = ready
    entity = plan_entity("member", first.entity_key, [first], contact(first))
    record = plan(admin, [first], [entity])
    owner(
        "UPDATE stewardship_proposed_change SET decision='unreviewed' WHERE id=%s",
        [first.pk],
    )
    with pytest.raises(DatabaseError, match="changed since preflight"):
        confirm(harness, admin, record, session)


def test_outcomes_need_the_lease_task_still_running(confirmed):
    """Review note 2: a lease whose task is no longer current is no fence."""
    state = confirmed
    claim = acquire_source(**running_source_task(), phase="publication")
    owner(
        "UPDATE stewardship_task_run "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
        [claim.task_id],
    )
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="attempt or fence"),
        transaction.atomic(),
    ):
        # Bypass the Python fence check: SQL must refuse on its own.
        PublicationOutcome.objects.create(
            entity=state.entity, attempt=1, result="invalidated", fence=claim.fence
        )


def _replaced(text, name):
    """One ``CREATE [OR REPLACE] FUNCTION`` statement of a SQL file, whole."""
    for keyword in ("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION"):
        marker = f"{keyword} public.{name}("
        if marker in text:
            start = text.index(marker)
            end = text.index("$$;", text.index("$$", start) + 2) + len("$$;")
            return text[start:end].replace(
                "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
            )
    raise AssertionError(name)


def test_do_block_refuses_the_bodies_it_replaces(ready):  # noqa: F811
    """The file's closing check passes as installed and raises once any
    replaced function is put back to its earlier body."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    earlier = {
        "stewardship_response_derived_guard_v1": "0060_census_resolution.sql",
        "stewardship_publication_confirmation_guard_v1": "0087_publication_plans.sql",
        "stewardship_publication_outcome_guard_v1": "0087_publication_plans.sql",
    }
    for name, source in earlier.items():
        old = (MIGRATIONS / "migrations" / source).read_text(encoding="utf-8")
        with (
            pytest.raises(DatabaseError, match="0090|publication branch"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(_replaced(old, name))
            cursor.execute(check)
    with (
        pytest.raises(DatabaseError, match="SECURITY DEFINER"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "ALTER FUNCTION public.stewardship_publication_audited_v1"
            "(text, uuid, uuid, jsonb) SECURITY INVOKER"
        )
        cursor.execute(check)
