"""Source fetch admission and staging run outside the global work order (#147).

A refresh's fetch admission (one per ParishSoft request) and each staging
batch used to join the work-order lock (736220,1), so every Family form,
mail step and installer queued behind about 170 holds per full refresh.
They are now source steps (attempts.source_step): fenced, admitted
transactions that never take that lock. Snapshot completion and promotion
still take it and re-verify the attempt there. These tests use a real
competing session that holds the lock while the steps run, and the real
attempt, transport and staging pipeline with only the provider exchange
replaced.
"""

import threading
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from time import monotonic
from uuid import uuid4

import psycopg
import pytest
from django.db import connection, connections

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import TaskOwnershipLost
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.runtime_background import bind_authority
from parishkit.stewardship.source import refreshing, transport
from parishkit.stewardship.source.attempts import (
    begin_refresh_attempt,
    source_step,
    verify_refresh_attempt,
)
from parishkit.stewardship.source.errors import (
    SourceCredentialChanged,
    SourceScopeChanged,
)
from parishkit.stewardship.source.leases import SourceFenceLost, acquire_source
from parishkit.stewardship.source.models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.source.outcomes import admit_refresh_metadata
from parishkit.stewardship.source.refreshing import _inputs
from parishkit.stewardship.source.snapshots import snapshot_manifest
from parishkit.stewardship.storage import StorageInvariantError

from .activation_builders import activation_window, recorded_holds
from .campaign_builders import add_draft
from .test_source_attempts_postgresql import configured, setup
from .test_source_refreshing_postgresql import (  # noqa: F401
    fake_provider,
    pages,
    run,
    seed_full,
    source_singletons,
    with_extra_funds,
)
from .test_source_requests_postgresql import claim as claim_request
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)

HELD = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
    "AND classid=736220 AND objid=1 AND objsubid=2 AND granted AND pid=%s)"
)


def holds_work_order(pid):
    """Whether backend ``pid`` holds the global work-order lock now."""
    with connection.cursor() as cursor:
        cursor.execute(HELD, [pid])
        return cursor.fetchone()[0]


def own_pid():
    """This connection's backend, as pg_locks names it."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


class Holder:
    """A separate session that holds the work-order lock exclusively.

    ``take`` waits until it holds the lock; ``give`` releases it. A safety
    timer releases it after 30 s, so a step that wrongly waited for the lock
    fails its assertions instead of hanging the suite.
    """

    def __init__(self):
        self.session = separate_session()
        self.pid = self.session.info.backend_pid
        self.mutex = threading.Lock()
        self.held = False
        self.timer = None

    def take(self):
        """Hold 736220,1 at session level until ``give``."""
        with self.mutex:
            if not self.held:
                self.session.execute("SELECT pg_advisory_lock(736220, 1)")
                self.held = True
                self.timer = threading.Timer(30, self.give)
                self.timer.start()

    def give(self):
        """Release the lock if it is held."""
        with self.mutex:
            if self.held:
                self.session.execute("SELECT pg_advisory_unlock(736220, 1)")
                self.held = False
                self.timer.cancel()

    def close(self):
        """Release and disconnect."""
        self.give()
        self.session.close()


@pytest.fixture
def holder():
    """One competing session, always released at the end."""
    value = Holder()
    yield value
    value.close()


def hold_outside_transitions(monkeypatch, holder):
    """Hold the lock from the fetch phase through staging, except transitions.

    Task transitions (progress) still take the work order, so the holder
    steps aside for each one and takes the lock back afterwards. It gives it
    up for good at the validating report, before the snapshot is finished.
    """
    real = Execution.progress

    def progress(execution, current, total, *, phase=None):
        """Let this transition through, then hold again unless validating."""
        holder.give()
        result = real(execution, current, total, phase=phase)
        if phase != "validating":
            holder.take()
        return result

    monkeypatch.setattr(Execution, "progress", progress)


def observe(monkeypatch, holder):
    """Record, from inside each fetch admission and staging batch, who holds
    the lock: (step, the competing session holds it, this step holds it)."""
    seen = []
    real_reserve, real_stage = (
        transport.reserve_source_request,
        refreshing.stage_entities,
    )

    def look(step):
        """Sample pg_locks from inside the step's own transaction."""
        assert connection.in_atomic_block
        seen.append((step, holds_work_order(holder.pid), holds_work_order(own_pid())))

    def reserve(claim, **options):
        """The fetch admission's lease reservation, observed."""
        look("fetch")
        return real_reserve(claim, **options)

    def stage(snapshot_id, claim, **options):
        """One staging batch, observed after it wrote its rows."""
        result = real_stage(snapshot_id, claim, **options)
        look("stage")
        return result

    monkeypatch.setattr(transport, "reserve_source_request", reserve)
    monkeypatch.setattr(refreshing, "stage_entities", stage)
    return seen


def test_fetch_and_staging_run_while_another_session_holds_the_work_order(
    tmp_path, monkeypatch, holder
):
    """Every fetch admission and staging batch completes under a competing
    exclusive hold, and none of them takes the lock itself."""
    credential, execution, lease, *_ = setup(tmp_path)
    remaining, calls = fake_provider(monkeypatch, pages())
    with_extra_funds(monkeypatch, 400)
    seen = observe(monkeypatch, holder)
    hold_outside_transitions(monkeypatch, holder)
    result = run(credential, execution, lease)
    assert not remaining and result.state == "ready"
    fetches = [entry for entry in seen if entry[0] == "fetch"]
    batches = [entry for entry in seen if entry[0] == "stage"]
    assert len(fetches) == len(calls) and len(batches) >= 4
    # The competing session held the lock throughout every step, so no step
    # waited for it; and no step's own transaction ever held it.
    assert all(competing and not own for _, competing, own in seen)
    staged = snapshot_manifest(result.pk)
    assert {kind: len(rows) for kind, rows in staged.items() if rows} == {
        kind: count for kind, count in result.counts.items() if count
    }


def test_a_staging_batch_never_holds_the_work_order(tmp_path, monkeypatch, holder):
    """With the lock free, a batch's own transaction still never takes it."""
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    seen = observe(monkeypatch, holder)
    assert run(credential, execution, lease).state == "ready"
    assert seen and not any(own for _, _, own in seen)


@pytest.mark.parametrize("cause,phase", [("delta", "delta"), ("manual", "full")])
def test_refresh_inputs_read_the_base_corpus_outside_the_work_order(
    tmp_path, monkeypatch, holder, cause, phase
):
    """The current snapshot's corpus is read after the inputs' effect.

    A quick update reads the whole current corpus as its base, and a refresh
    whose current snapshot predates recorded derived counts (as the seeded
    one does) counts them from its rows. Both reads used to run inside the
    work-order effect, about 200 ms at 1,100 Families. Here each read
    starts without the lock and runs while a competing session holds it
    exclusively, so it neither holds nor waits for it; the inputs are
    unchanged.
    """
    from parishkit.stewardship.source.loading import derived_counts
    from parishkit.stewardship.source.snapshots import reconstruct_snapshot

    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    execution = claim_request(
        command(cause=cause)
        if cause == "manual"
        else command(cause=cause, actor_id=None)
    )
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase=phase,
        )
    attempt = begin_refresh_attempt(execution, lease, credential)
    seen = []

    def read(snapshot_id, kinds=None):
        """The real read, while the competing session holds the lock."""
        # A read still inside the effect holds the lock, so the competing
        # session could never take it: fail here rather than deadlock.
        assert not holds_work_order(own_pid()), "base read inside the work order"
        outside = not connection.in_atomic_block
        holder.take()
        started = monotonic()
        try:
            corpus = reconstruct_snapshot(snapshot_id, kinds)
        finally:
            elapsed = monotonic() - started
            competing = holds_work_order(holder.pid)
            holder.give()
        seen.append((snapshot_id, kinds, outside, competing, elapsed))
        return corpus

    monkeypatch.setattr(refreshing, "reconstruct_snapshot", read)
    inputs = _inputs(attempt.pk, execution, lease)
    # One read each: the whole base for a quick update (which also yields its
    # counts), only the counted kinds for a full refresh.
    expected_kinds = None if phase == "delta" else ("family", "contact")
    ((snapshot_id, kinds, outside, competing, elapsed),) = seen
    assert snapshot_id == first.pk and kinds == expected_kinds
    assert outside and competing and elapsed < 10
    assert not holds_work_order(own_pid())
    whole = reconstruct_snapshot(first.pk)
    assert inputs.previous_derived_counts == derived_counts(whole)
    assert inputs.base == (whole if phase == "delta" else None)


def separate_session():
    """A new autocommit session as the test database owner."""
    settings = connection.settings_dict
    return psycopg.connect(
        host=settings["HOST"] or "127.0.0.1",
        port=settings["PORT"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        dbname=settings["NAME"],
        autocommit=True,
    )


NEXT_KEY = b"SYNTHETIC-NEXT-PRIVATE-KEY"


@contextmanager
def installer_identity():
    """Act as the ParishSoft credential installer's SQL login.

    The state guard lets only that login advance a ParishSoft key change.
    Tests never provision it, so a disposable superuser role stands in, as
    in test_family_mail_worker_postgresql.
    """
    name = "pk_stewardship_credential_parishsoft"
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{name}" SUPERUSER')
        cursor.execute(f'SET SESSION AUTHORIZATION "{name}"')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(f'DROP ROLE "{name}"')


def installed_key_change(store):
    """Record a ParishSoft key change from the configured key to NEXT_KEY.

    It is walked through the installer's states to ``applied`` and
    acknowledged by every consumer of the key, as a completed replacement
    is just before the configuration switches to it. The key was checked
    against the active configuration's ParishSoft settings.
    """
    from datetime import timedelta

    from django.utils import timezone

    from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
    from parishkit.stewardship.accounts.credential_installation import (
        acknowledge_loaded_credential,
    )
    from parishkit.stewardship.accounts.cryptography import Key
    from parishkit.stewardship.accounts.integration_selection import (
        authentication_scope,
        integration_records,
    )
    from parishkit.stewardship.accounts.key_files import file_fingerprint
    from parishkit.stewardship.accounts.secret_models import (
        SealedCredentialStaging,
        SecretReplacementRequest,
    )
    from parishkit.stewardship.accounts.secret_requests import stage_secret_request
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

    from .test_background_grants_postgresql import task_login

    consumers = [
        role for role, names in ALLOWED_SECRETS.items() if "parishsoft" in names
    ]
    request_id = uuid4()
    private = PrivateHandoff("parishsoft", Key("h", "active", b"w" * 32))
    stage_secret_request(
        request_id=request_id,
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=uuid4(),
        reauthenticated_at=timezone.now() - timedelta(seconds=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint=file_fingerprint(b"SYNTHETIC-PRIVATE-KEY"),
        correlation_id=uuid4(),
        sealed_candidate=private.public().seal(request_id, NEXT_KEY),
        candidate_fingerprint=file_fingerprint(NEXT_KEY),
        required_consumers=tuple(role.value for role in consumers),
        provider_settings=authentication_scope(
            "parishsoft", integration_records(store.active().document())
        ),
    )
    row = SecretReplacementRequest.objects.get(pk=request_id)

    def advance(state, **values):
        """One installer transition, as the SQL state guard requires."""
        row.state = state
        row.version += 1
        row.actor_id = None
        for name, value in values.items():
            setattr(row, name, value)
        with installer_identity():
            row.save()

    advance("testing")
    advance("installing", resulting_fingerprint=file_fingerprint(NEXT_KEY))
    advance("awaiting_ack")
    for role in consumers:
        with task_login(ServiceRole(role), exact=True):
            acknowledge_loaded_credential(
                request_id=request_id, consumer=role.value, loaded_value=NEXT_KEY
            )
    row.refresh_from_db()
    advance("cleanup_pending", cleanup_reason="applied")
    with installer_identity():
        SealedCredentialStaging.objects.filter(request_id=request_id).update(
            ciphertext=None
        )
    row.refresh_from_db()
    advance("applied")
    return file_fingerprint(NEXT_KEY)


def credential_switch(store, version, actor):
    """Switch the active configuration to a new, installed ParishSoft key.

    A real configuration activation (YAML selection, then the database
    pointer swap): the credential-format request a completed key
    replacement records, installed through install_request, which verifies
    the key change's receipt and acknowledgements.
    """
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.accounts.request_patch import CREDENTIAL_REQUEST_SCHEMA

    fingerprint = installed_key_change(store)
    base = store.active()
    record = next(
        item
        for item in base.document()["sections"]["integrations"]
        if item["values"]["kind"] == "parishsoft"
    )
    request = record_request(
        base_digest=base.digest,
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
        request_schema=CREDENTIAL_REQUEST_SCHEMA,
        patch=[
            {
                "operation": "update",
                "section": "integrations",
                "id": record["id"],
                "values": {"credential_fingerprint": fingerprint},
            }
        ],
    )
    receipt = install_request(
        store, request_id=request.request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied", receipt.failure_code


@pytest.mark.parametrize(
    "switch,error",
    [
        (credential_switch, SourceCredentialChanged),
        (lambda *args: add_draft(*args), SourceScopeChanged),
    ],
    ids=["credential", "campaign"],
)
def test_a_change_committed_during_staging_stops_the_next_batch(
    tmp_path, monkeypatch, switch, error
):
    """The unlocked admission sees a committed scope change at the next batch.

    The change commits between two batches (outside any step). The next
    batch refuses, no row of it is staged, and nothing becomes current.
    """
    credential, execution, lease, store, version, actor = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    with_extra_funds(monkeypatch, 400)
    real, batches = refreshing.stage_entities, []

    def stage(snapshot_id, claim, **options):
        """Stage normally, recording each committed batch."""
        result = real(snapshot_id, claim, **options)
        batches.append(len(options["entities"]))
        return result

    real_progress = Execution.progress

    def progress(execution, current, total, *, phase=None):
        """After the first staging report, commit the change."""
        result = real_progress(execution, current, total, phase=phase)
        if phase == "staging" and current == 0:
            switch(store, version, actor)
        return result

    monkeypatch.setattr(refreshing, "stage_entities", stage)
    monkeypatch.setattr(Execution, "progress", progress)
    with pytest.raises(error):
        run(credential, execution, lease)
    snapshot = SourceSnapshot.objects.get()
    assert not batches and snapshot.state == "staging" and not snapshot.counts
    assert not any(snapshot_manifest(snapshot.pk).values())
    assert SourceCurrent.objects.get().snapshot_id is None


def test_a_lost_fence_refuses_a_source_step(tmp_path):
    """A stale source lease fence or task fence cannot stage or fetch."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    assert verify_refresh_attempt(attempt.pk, execution, lease, step=True)
    with pytest.raises(SourceFenceLost):
        verify_refresh_attempt(
            attempt.pk, execution, replace(lease, fence=lease.fence + 1), step=True
        )
    stale = replace(
        execution, claim=replace(execution.claim, fence=lease.task_fence + 1)
    )
    with pytest.raises(TaskOwnershipLost):
        verify_refresh_attempt(
            attempt.pk,
            stale,
            replace(lease, task_fence=lease.task_fence + 1),
            step=True,
        )
    with (
        pytest.raises(TaskOwnershipLost),
        source_step(
            replace(execution, claim=replace(execution.claim, worker_id=uuid4()))
        ),
    ):
        pass


def bound(execution, store):
    """The execution with the compiled handler's configuration check bound,
    as the worker binds it (runtime_background.bind_authority)."""
    handler = bind_authority({"source_refresh": execution.handler}, store)
    return replace(execution, handler=handler["source_refresh"])


def test_a_source_step_waits_out_an_activation(tmp_path, monkeypatch):
    """A source step matches the selected configuration with the database
    (#429): it waits while an activation is in progress, then proceeds, and
    refuses when a selection is ahead with nothing running to finish it."""
    _, execution, _, store, *_ = setup(tmp_path)
    execution = bound(execution, store)
    holds = recorded_holds(monkeypatch)
    with activation_window(store, closes_after=0.5), source_step(execution):
        pass
    assert holds
    with (
        activation_window(store, installing=False),
        pytest.raises(ConfigError),
        source_step(execution),
    ):
        pass


def test_staging_waits_out_an_activation_and_completes(tmp_path, monkeypatch):
    """An activation that starts during staging holds the next batch until
    it commits; the refresh then stages every row."""
    credential, execution, lease, store, *_ = setup(tmp_path)
    execution = bound(execution, store)
    fake_provider(monkeypatch, pages())
    with_extra_funds(monkeypatch, 400)
    holds = recorded_holds(monkeypatch)
    windows = ExitStack()
    real_progress = Execution.progress

    def progress(execution, current, total, *, phase=None):
        """Open a half-second activation window after the first staging report."""
        result = real_progress(execution, current, total, phase=phase)
        if phase == "staging" and current == 0:
            windows.enter_context(activation_window(store, closes_after=0.5))
        return result

    monkeypatch.setattr(Execution, "progress", progress)
    with windows:
        result = run(credential, execution, lease)
    assert holds and result.state == "ready"
    assert sum(len(rows) for rows in snapshot_manifest(result.pk).values()) == sum(
        result.counts.values()
    )


@pytest.mark.parametrize(
    "switch,error",
    [
        (credential_switch, SourceCredentialChanged),
        (lambda *args: add_draft(*args), SourceScopeChanged),
    ],
    ids=["credential", "campaign"],
)
def test_a_change_committed_inside_a_step_is_refused_before_publication(
    tmp_path, monkeypatch, switch, error
):
    """A change that commits while a batch is inside its step, after that
    step's admission passed, lets the batch land; the attempt is still
    refused before anything becomes current.

    During the first batch, the change runs to its commit on another
    connection while the step's transaction is still open, so it commits
    before the step does. Completion or promotion then refuses, under the
    exclusive lock, and the source pointer never moves.
    """
    credential, execution, lease, store, version, actor = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    with_extra_funds(monkeypatch, 400)
    real, batches, changes = refreshing.stage_entities, [], []

    def change_elsewhere():
        """Commit the change on this thread's own connection."""
        try:
            switch(store, version, actor)
            changes.append("committed")
        except Exception as caught:  # surfaced by the assertion below
            changes.append(caught)
        finally:
            connections.close_all()

    def stage(snapshot_id, claim, **options):
        """Stage; in the first batch, commit the change before the step does."""
        result = real(snapshot_id, claim, **options)
        if not batches:
            # Inside the step: its admission passed and its rows are written,
            # but its transaction has not committed.
            assert connection.in_atomic_block
            thread = threading.Thread(target=change_elsewhere)
            thread.start()
            thread.join(60)
            assert not thread.is_alive() and changes == ["committed"]
            assert connection.in_atomic_block
        batches.append(len(options["entities"]))
        return result

    monkeypatch.setattr(refreshing, "stage_entities", stage)
    with pytest.raises(error):
        run(credential, execution, lease)
    assert changes == ["committed"]
    # The batch whose admission passed before the change landed.
    assert len(batches) >= 1
    snapshot = SourceSnapshot.objects.get()
    assert any(snapshot_manifest(snapshot.pk).values())
    assert snapshot.state in {"staging", "rejected"}
    assert SourceCurrent.objects.get().snapshot_id is None


def admit_queued(action, root_id):
    """Admit a queued refresh root as the scheduler or a claim does: under
    the work order, with the task row locked. Lock waits fail fast, so a
    wait shows up as an error rather than a slow pass."""
    with work_transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '2s'")
        row = TaskRun.objects.select_for_update().get(pk=root_id)
        started = monotonic()
        result = admit_refresh_metadata(action, _status(row))
        return result, monotonic() - started


@pytest.mark.parametrize("action", ["hint", "claim"])
def test_hint_and_claim_answer_busy_for_a_locked_lease(tmp_path, action):
    """A lease row another session holds locked (as a source step does) is
    busy: hint and claim admission answer False at once, without waiting."""
    configured(tmp_path)
    receipt = command()
    assert admit_queued(action, receipt.task_root_id)[0] is True
    with separate_session() as session, session.transaction():
        session.execute("SELECT 1 FROM stewardship_source_lease FOR UPDATE")
        result, seconds = admit_queued(action, receipt.task_root_id)
    assert result is False and seconds < 1
    assert admit_queued(action, receipt.task_root_id)[0] is True


def test_hint_admission_without_source_ownership_fails_closed(tmp_path):
    """A missing lease row is not mistaken for a busy one."""
    configured(tmp_path)
    receipt = command()
    with separate_session() as session:
        session.execute("SET session_replication_role = replica")
        session.execute("DELETE FROM stewardship_source_lease")
    with pytest.raises(StorageInvariantError, match="not initialized"):
        admit_queued("hint", receipt.task_root_id)
