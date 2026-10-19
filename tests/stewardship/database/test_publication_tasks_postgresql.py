"""The census publication tasks and their dispatch under the real roles (#528).

``responses.publication_tasks`` queues a preflight and a publish run from
the web login and runs both on the restricted worker login through the
real dispatcher (``execute_hint`` and ``recover_hint``), with migration
0101's task-type maps admitting exactly those logins. The gateway is the
in-memory fake from the worker's tests, so nothing calls ParishSoft. The
file's own DO block is run against the maps it replaces. These tests also
carry the #1245 review notes the dispatch must honour (notes 2, 4 and 5;
notes 1 and 3 are the worker's, in test_publication_worker_postgresql).
"""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.responses import publication_tasks as tasks
from parishkit.stewardship.responses.census_resolution import resolve_census_change
from parishkit.stewardship.responses.models import (
    ProposalResolution,
    ProposalReview,
    ProposedChange,
    PublicationConfirmation,
    PublicationEntity,
    PublicationField,
    PublicationPlan,
)
from parishkit.stewardship.responses.publication_plans import confirmation_counts
from parishkit.stewardship.responses.publication_worker import PublicationDisabled

from .test_background_grants_postgresql import task_login
from .test_census_review_postgresql import forge as forge_review
from .test_census_review_postgresql import review
from .test_publication_plans_postgresql import owner, ready  # noqa: F401
from .test_publication_worker_postgresql import Crash, FakeGateway, as_owner, contact

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0101_publication_tasks.sql"


class World:
    """One test's Administrator, the approved change and a fake ParishSoft."""

    def __init__(self, prepared):
        """Unpack the ``ready`` fixture and give the Member a full record."""
        self.harness, self.admin, self.staff, self.home, self.first, self.session = (
            prepared
        )
        self.gateway = FakeGateway({self.first.entity_key: contact(self.first)})
        self.built = []

    def build(self, execution):
        """The handlers' gateway factory: the fake, recording each build."""
        self.built.append(execution.claim.run_id)
        return self.gateway

    @property
    def store(self):
        """The configuration store the web calls check authority with."""
        return self.harness.service.store

    def preflight(self, *, key=None, actor=None, enabled=True):
        """Queue a preflight on the restricted web login."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return tasks.request_preflight(
                self.store,
                actor or self.admin,
                request_key=key or uuid4(),
                correlation_id=uuid4(),
                enabled=enabled,
            )

    def confirm(self, plan, *, key=None, enabled=True):
        """Confirm ``plan`` and queue its run on the restricted web login."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return tasks.confirm_and_publish(
                self.store,
                self.admin,
                plan.pk,
                correlation_id=uuid4(),
                enabled=enabled,
                session_id=self.session.pk,
                authenticated_at=self.session.authenticated_at,
                request_key=key or uuid4(),
                **confirmation_counts(plan.pk),
            )

    def rerun(self, plan, *, command=None):
        """Retry ``plan``'s failed run on the restricted web login."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return tasks.rerun_publication(
                self.store,
                self.admin,
                plan.pk,
                command_id=command or uuid4(),
                correlation_id=uuid4(),
                enabled=True,
            )

    def handlers(self, *, enabled=True):
        """The worker's two compiled handlers, on the fake gateway."""
        return {
            tasks.PREFLIGHT_TASK_TYPE: tasks.preflight_handler(
                enabled=enabled, gateway=self.build
            ),
            tasks.PUBLICATION_TASK_TYPE: tasks.publication_handler(
                enabled=enabled, gateway=self.build
            ),
        }

    def execute(self, run_id, **options):
        """Run one task through the real dispatcher on the worker login."""
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            return execute_hint(
                run_id,
                queue=WorkQueue.SOURCE,
                worker_id=uuid4(),
                handlers=self.handlers(**options),
            )

    def recover(self, run_id):
        """Fence an expired claim and apply its recovery, on the worker login."""
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            return recover_hint(
                run_id,
                queue=WorkQueue.SOURCE,
                worker_id=uuid4(),
                handlers=self.handlers(),
            )

    def planned(self, key):
        """Run a preflight for ``key`` to its plan."""
        task = self.preflight(key=key)
        assert self.execute(task.run_id)
        assert state(task) == "succeeded"
        return PublicationPlan.objects.get(actor_id=self.admin, request_key=key)


@pytest.fixture
def world(ready):  # noqa: F811
    """A ready census change, its Administrator and a fake ParishSoft."""
    return World(ready)


def state(task):
    """The task's current state."""
    return TaskRun.objects.get(pk=task.run_id).state


def due(task):
    """Make the task's run due now and its lease and request window over."""
    owner(
        "UPDATE stewardship_task_run SET not_before=clock_timestamp(), "
        "lease_expires_at=CASE WHEN state='running' "
        "THEN clock_timestamp()-interval '1 second' END WHERE root_id=%s",
        [task.root_id],
    )
    owner(
        "UPDATE stewardship_source_lease SET external_deadline=NULL, "
        "expires_at=CASE WHEN owner_id IS NULL THEN NULL "
        "ELSE clock_timestamp()-interval '1 second' END"
    )


def audited(event_type):
    """Each audit event of one type, oldest first: actor, subject, outcome."""
    return [
        (
            event.actor_id,
            event.subject_id,
            AuditContext.objects.get(event=event).context["outcome"],
        )
        for event in AuditEvent.objects.filter(event_type=event_type).order_by(
            "created_at"
        )
    ]


def proposal(row):
    """The proposal's current execution."""
    return ProposedChange.objects.get(pk=row.pk).execution


def test_preflight_plans_and_a_confirmed_run_publishes(world):
    """A preflight reads the Member and records the plan as its Administrator;
    the same key is the same task and plan. Confirming queues exactly one
    run, which writes, verifies and completes."""
    key = uuid4()
    task = world.preflight(key=key)
    assert world.preflight(key=key).run_id == task.run_id
    # Safety review L3: the request is audited once, a replay not again.
    assert audited("census_publication_preflight_requested") == [
        (world.admin, task.run_id, "started")
    ]
    assert world.execute(task.run_id) and state(task) == "succeeded"
    assert world.gateway.reads == [world.first.entity_key]
    assert world.gateway.writes == [] and world.gateway.closed
    plan = PublicationPlan.objects.get(actor_id=world.admin, request_key=key)
    entity = PublicationEntity.objects.get(plan=plan)
    assert entity.entity_key == world.first.entity_key and not entity.blocked
    assert list(
        PublicationField.objects.filter(entity=entity).values_list("outcome", flat=True)
    ) == ["write"]
    confirmation_key = uuid4()
    confirmation, run = world.confirm(plan, key=confirmation_key)
    again, rerun = world.confirm(plan, key=confirmation_key)
    assert (again.pk, rerun.run_id) == (confirmation.pk, run.run_id)
    assert TaskRun.objects.filter(task_type=tasks.PUBLICATION_TASK_TYPE).count() == 1
    assert world.execute(run.run_id) and state(run) == "succeeded"
    assert len(world.gateway.writes) == 1
    assert proposal(world.first) == "published"


def test_requests_need_the_switch_and_an_administrator(world):
    """Off, the web refuses to queue a preflight or confirm a plan; Staff
    cannot ask for one. Nothing is queued or confirmed."""
    with pytest.raises(PublicationDisabled):
        world.preflight(enabled=False)
    with pytest.raises(PermissionError):
        world.preflight(actor=world.staff)
    plan = world.planned(uuid4())
    with pytest.raises(PublicationDisabled):
        world.confirm(plan, enabled=False)
    assert not PublicationConfirmation.objects.filter(plan=plan).exists()
    assert TaskRun.objects.filter(task_type=tasks.PUBLICATION_TASK_TYPE).count() == 0


def test_tasks_refuse_while_switched_off(world):
    """Both handlers built with the real setting off fail their run before
    building a gateway: no read, no plan, no write."""
    task = world.preflight()
    assert world.execute(task.run_id, enabled=False)
    assert state(task) == "failed" and world.built == []
    assert not PublicationPlan.objects.exists()
    plan = world.planned(uuid4())
    _, run = world.confirm(plan)
    built = len(world.built)
    assert world.execute(run.run_id, enabled=False)
    assert state(run) == "failed" and len(world.built) == built
    assert world.gateway.writes == []
    # Safety review L3: each failure records the switch as its reason.
    assert audited("census_publication_switched_off") == [
        (None, task.run_id, "failed"),
        (None, run.run_id, "failed"),
    ]
    assert audited("census_publication_read_unconfirmed") == []
    assert audited("census_publication_refused") == []


def test_an_unconfirmed_read_fails_the_preflight_without_a_plan(world):
    """The real gateway's refusal (read_unconfirmed) means no plan at all."""
    world.gateway.read_errors = ["read_unconfirmed"]
    task = world.preflight()
    assert world.execute(task.run_id)
    assert state(task) == "failed" and not PublicationPlan.objects.exists()
    # Safety review L3: the reason tells this apart from the switch off.
    assert audited("census_publication_read_unconfirmed") == [
        (None, task.run_id, "failed")
    ]
    assert audited("census_publication_switched_off") == []


def test_an_unreadable_member_is_blocked(world):
    """Any other read failure makes that Member cannot-check, so blocked."""
    world.gateway.read_errors = ["read_unavailable"]
    plan = world.planned(uuid4())
    entity = PublicationEntity.objects.get(plan=plan)
    assert entity.blocked and entity.planned_sha256 is None
    assert confirmation_counts(plan.pk)["write_entities"] == 0


# How a Member leaves the change's Family in the promoted data (#1738): the
# snapshot's record for the Member, rewritten in place (triggers off), so
# the shared status reads moved, inactive or deceased.
LEAVINGS = {
    "moved": ("family_key='999999'", "{family_key}", '"999999"'),
    "inactive": (None, "{active}", "false"),
    "deceased": (None, "{deceased}", "true"),
}


def leave(row, how):
    """Make the promoted data stop listing ``row``'s Member in its Family."""
    column, path, value = LEAVINGS[how]
    owner(
        "UPDATE stewardship_source_member SET "
        + (column + ", " if column else "")
        + "canonical=jsonb_set(canonical::jsonb, %s, %s::jsonb)::text "
        "WHERE id IN (SELECT payload_id FROM stewardship_snapshot_member m "
        "JOIN stewardship_source_current c ON c.snapshot_id=m.snapshot_id "
        "WHERE m.source_key=%s)",
        [path, value, row.entity_key],
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_member_source_status_v1(c.snapshot_id, %s, "
            "f.family_duid) FROM stewardship_source_current c, "
            "stewardship_family_campaign f WHERE f.id=%s",
            [row.entity_key, row.submission.family_id],
        )
        assert cursor.fetchone()[0] == how


@pytest.mark.parametrize("how", sorted(LEAVINGS))
def test_a_member_no_longer_in_the_family_is_neither_read_nor_written(world, how):
    """The promoted data no longer lists the Member, active and living, in
    the change's Family (#1738): preflight leaves the change out (open and
    unplanned) and does not read the Member for it."""
    leave(world.first, how)
    plan = world.planned(uuid4())
    assert world.gateway.reads == []
    assert not PublicationEntity.objects.filter(plan=plan).exists()
    assert confirmation_counts(plan.pk)["write_entities"] == 0
    assert proposal(world.first) == "pending"


@pytest.mark.parametrize("how", sorted(LEAVINGS))
def test_a_member_who_left_after_the_plan_is_not_written(world, how):
    """A refresh after the confirmation keeps the change open (rule 4), so
    the worker checks the Member again before reading them: the entity is
    invalidated with no read and no write, and the change stays pending."""
    plan = world.planned(uuid4())
    _, run = world.confirm(plan)
    leave(world.first, how)
    reads = len(world.gateway.reads)
    assert world.execute(run.run_id) and state(run) == "succeeded"
    assert len(world.gateway.reads) == reads and world.gateway.writes == []
    entity = PublicationEntity.objects.get(plan=plan)
    assert list(entity.outcomes.values_list("result", flat=True)) == ["invalidated"]
    assert proposal(world.first) == "pending"


def test_a_change_since_the_read_fails_the_preflight(world):
    """A decision recorded between the read and the plan leaves a stale
    version, which the plan guard refuses: the run fails, no plan."""
    world.gateway.on_read = lambda: as_owner(
        "UPDATE stewardship_proposed_change SET version=version+1 WHERE id=%s",
        [world.first.pk],
    )
    task = world.preflight()
    assert world.execute(task.run_id)
    assert state(task) == "failed" and not PublicationPlan.objects.exists()
    assert audited("census_publication_refused") == [(None, task.run_id, "failed")]


def test_one_run_per_plan(world):
    """A second unfinished run of the same plan is refused at creation."""
    plan = world.planned(uuid4())
    world.confirm(plan)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(PermissionError, match="Another run"),
        work_transaction(),
    ):
        enqueue(
            task_type=tasks.PUBLICATION_TASK_TYPE,
            domain_request_id=plan.pk,
            actor_id=world.admin,
            correlation_id=uuid4(),
            idempotency_key=uuid4(),
            admit=tasks.admit_publication,
        )


def test_a_crashed_run_is_recovered_on_the_same_plan(world):
    """Review note 2: a run that dies after its PUT is abandoned, recovered
    as a retry of the same plan, and the retry sees the landed PUT and
    publishes it without writing again."""
    plan = world.planned(uuid4())
    _, run = world.confirm(plan)
    world.gateway.crash_after_write = True
    with pytest.raises(Crash):
        world.execute(run.run_id)
    world.gateway.crash_after_write = False
    assert proposal(world.first) == "queued"
    due(run)
    assert world.recover(run.run_id)
    assert state(run) == "retry_wait"
    due(run)
    assert world.execute(run.run_id) and state(run) == "succeeded"
    assert len(world.gateway.writes) == 1
    assert proposal(world.first) == "published"


def test_recovery_dispositions(world):
    """Complete when nothing is unfinished, retry while runs remain, fail
    after MAX_RUNS; a preflight completes once its plan exists."""
    plan = world.planned(uuid4())
    _, run = world.confirm(plan)
    abandoned = replace(
        _status(TaskRun.objects.get(pk=run.run_id)), state="abandoned", attempt=1
    )
    assert tasks.recover_publication(abandoned).action == "recovery_retry"
    last = replace(abandoned, attempt=tasks.MAX_RUNS)
    assert tasks.recover_publication(last).action == "recovery_fail"
    assert world.execute(run.run_id)
    assert tasks.recover_publication(last).action == "recovery_complete"
    preflight = TaskRun.objects.get(task_type=tasks.PREFLIGHT_TASK_TYPE)
    status = replace(_status(preflight), state="abandoned")
    assert tasks.recover_preflight(status).action == "recovery_complete"
    other = replace(status, domain_request_id=uuid4(), attempt=tasks.MAX_RUNS)
    assert tasks.recover_preflight(other).action == "recovery_fail"
    with pytest.raises(PermissionError):
        tasks.recover_publication(replace(abandoned, state="running"))


def test_a_failed_run_is_rerun_explicitly(world):
    """Review note 2: a run refused by Testing mode leaves the plan
    unfinished; back in Production, the explicit rerun (same command, same
    retry) publishes it. Once finished, a rerun is refused."""
    plan = world.planned(uuid4())
    _, run = world.confirm(plan)
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    assert world.execute(run.run_id) and state(run) == "failed"
    assert audited("census_publication_refused") == [(None, run.run_id, "failed")]
    owner("UPDATE stewardship_system_configuration SET mode='production'")
    command = uuid4()
    retry = world.rerun(plan, command=command)
    assert world.rerun(plan, command=command).run_id == retry.run_id
    # Safety review L3: the rerun is audited once, a replay not again.
    assert audited("census_publication_rerun_requested") == [
        (world.admin, retry.run_id, "retry")
    ]
    assert retry.root_id == run.root_id and retry.run_id != run.run_id
    assert world.execute(retry.run_id) and state(retry) == "succeeded"
    assert proposal(world.first) == "published"
    with pytest.raises(PermissionError):
        world.rerun(plan)


def test_the_real_gateway_gets_the_timeout_recorder(monkeypatch, tmp_path):
    """Review note 4: the worker's gateway records request kills through
    the source timeout recorder and reads the credential at run time."""
    from types import SimpleNamespace

    from parishkit.parishsoft import DEFAULT_API_BASE_URL
    from parishkit.stewardship.responses import publication_gateway
    from parishkit.stewardship.source import credentials

    seen = {}

    def opened(cls, execution, credential, *, profile, base_url, on_timeout):
        """Capture what the task passed."""
        seen.update(
            credential=credential,
            profile=profile,
            base_url=base_url,
            on_timeout=on_timeout,
        )
        return "gateway"

    monkeypatch.setattr(
        publication_gateway.ParishSoftContactGateway, "open", classmethod(opened)
    )
    monkeypatch.setattr(
        credentials.SourceCredential, "read", classmethod(lambda cls, path: path)
    )
    execution = SimpleNamespace(claim=SimpleNamespace(run_id=uuid4()))
    path = tmp_path / "parishsoft"
    assert tasks.parishsoft_gateway(path)(execution) == "gateway"
    assert seen["credential"] == path
    # The test profile's default, the real API: no recorded choice.
    assert seen["base_url"] == DEFAULT_API_BASE_URL
    assert seen["on_timeout"].__qualname__.startswith("source_timeout_recorder")


def test_only_web_creates_and_only_the_worker_runs_the_types(world):
    """Migration 0101's maps: the worker cannot create either type, and web
    cannot claim one."""
    for task_type in (tasks.PREFLIGHT_TASK_TYPE, tasks.PUBLICATION_TASK_TYPE):
        with (
            task_login(ServiceRole.WORKER, exact=True, reconnect=True),
            pytest.raises(DatabaseError, match="cannot create"),
            transaction.atomic(),
        ):
            enqueue(
                task_type=task_type,
                domain_request_id=uuid4(),
                actor_id=None,
                correlation_id=uuid4(),
                admit=lambda *args: True,
            )
    task = world.preflight()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises((DatabaseError, PermissionError)),
    ):
        execute_hint(
            task.run_id,
            queue=WorkQueue.SOURCE,
            worker_id=uuid4(),
            handlers=world.handlers(),
        )


def queue_it(row):
    """Put ``row`` in ``queued``, as a publish run would (triggers off)."""
    owner(
        "UPDATE stewardship_proposed_change SET execution='queued', "
        "version=version+1 WHERE id=%s",
        [row.pk],
    )
    return ProposedChange.objects.get(pk=row.pk)


@pytest.mark.parametrize("decision", ["approved", "ignored", "unreviewed"])
def test_no_review_touches_a_queued_change(world, decision):
    """Review note 5: every review of a queued change is refused by the
    service and, bypassing it, by the review guard."""
    first = queue_it(world.first)
    if decision != first.decision:
        with pytest.raises(ValueError):
            review(world.harness, world.admin, first, decision)
    with pytest.raises((IntegrityError, DatabaseError), match="current authority"):
        forge_review(first, world.admin, decision, update=False)
    assert ProposalReview.objects.count() == 0
    assert proposal(first) == "queued"


@pytest.mark.parametrize("action", ["entered", "ignored"])
def test_no_resolution_touches_a_queued_change(world, action):
    """Review note 5: a queued change is never resolved by hand either, by
    the service or, bypassing it, by the resolution guard."""
    home = queue_it(world.home)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(ValueError),
    ):
        resolve_census_change(
            world.store,
            world.staff,
            home.pk,
            expected_version=home.version,
            request_key=uuid4(),
            action=action,
            note="",
        )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises((IntegrityError, DatabaseError)),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note) "
            "VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),%s,'')",
            [world.staff, home.pk, home.version, action],
        )
    assert ProposalResolution.objects.count() == 0


def _replaced(text, name):
    """One ``CREATE OR REPLACE FUNCTION`` statement of a frozen file, whole."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    return text[start : text.index("END $$;", start) + len("END $$;")]


@pytest.mark.parametrize(
    "name", ["stewardship_task_type_login_v1", "stewardship_task_type_creators_v1"]
)
def test_do_block_refuses_the_maps_it_replaces(name):
    """The file's closing check passes as installed and raises once either
    map is put back to 0077's body."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    old = (SCHEMA / "migrations/0077_address_retry.sql").read_text(encoding="utf-8")
    with (
        pytest.raises(DatabaseError, match="0101"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(_replaced(old, name))
        cursor.execute(check)
