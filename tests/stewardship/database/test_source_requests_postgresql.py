"""Durable request coalescing and current source-window admission on PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, claim_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.source.canonical import canonical_payload
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshCommand,
    SourceRefreshRequest,
)
from parishkit.stewardship.source.requests import (
    TASK_TYPE,
    admit_refresh_request,
    request_refresh,
)
from parishkit.stewardship.storage import StorageInvariantError

from ..campaign_factory import campaign as campaign_record
from ..campaign_factory import financial
from .campaign_builders import (
    add_draft,
    change,
    draft_campaign,
    initialized,
    restored_runtime,
)
from .source_builders import source_corpus
from .test_source_snapshots_postgresql import prepared, publish

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Disposable flush removes migration seeds; restore only their idle state."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def command(**overrides):
    """Explicit synthetic producer admission, with no browser/provider authority."""
    return request_refresh(
        **dict(
            command_id=uuid4(),
            cause="manual",
            actor_id=uuid4(),
            correlation_id=uuid4(),
            authorize=lambda scope: True,
        )
        | overrides
    )


def claim(receipt):
    """Exercise the real dispatcher and exact parent-bound admission."""
    return claim_hint(
        receipt.task_root_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={
            TASK_TYPE: Handler(
                WorkQueue.GENERAL,
                admit_refresh_request,
                lambda execution: None,
                scope=work_transaction,
            )
        },
    )


def test_repeated_manual_commands_coalesce_and_retain_each_actor(tmp_path):
    """Distinct requesters have durable evidence, not duplicate full loads."""
    initialized(tmp_path)
    one, two = command(), command()
    assert one.request_id == two.request_id
    assert one.task_root_id == two.task_root_id
    assert one.command_id != two.command_id
    assert SourceRefreshCommand.objects.count() == 2
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 1
    request = SourceRefreshRequest.objects.get()
    assert request.organization_id == 12345 and request.campaign_id is None
    assert request.window_canonical == '{"campaign_id":null,"periods":[]}'
    assert (
        request.window_digest
        == canonical_payload({"campaign_id": None, "periods": []})[1]
    )


def test_manual_during_running_poll_queues_exactly_one_followup(tmp_path):
    """The task is active before its first source lease or staging write."""
    initialized(tmp_path)
    active = command(cause="nightly", actor_id=None)
    execution = claim(active)
    assert execution is not None
    followup, repeated = command(), command()
    assert followup.request_id == repeated.request_id != active.request_id
    assert SourceRefreshRequest.objects.count() == 2
    assert TaskRun.objects.get(pk=active.task_root_id).state == "running"


def test_delta_may_coalesce_into_full_but_not_the_reverse(tmp_path):
    """A full request cannot be satisfied by only Family change indications."""
    initialized(tmp_path)
    delta = command(cause="delta", actor_id=None)
    full = command()
    assert delta.request_id != full.request_id
    claim(delta)
    another = command(cause="delta", actor_id=None)
    assert another.request_id == full.request_id
    assert SourceRefreshCommand.objects.get(pk=another.command_id).kind == "delta"


def test_replay_preserves_binding_and_rechecks_current_authorization(tmp_path):
    """A known UUID is not permission, even when its exact command already exists."""
    initialized(tmp_path)
    identifier, actor = uuid4(), uuid4()
    original = command(command_id=identifier, actor_id=actor)
    assert command(command_id=identifier, actor_id=actor) == original
    with pytest.raises(PermissionError):
        command(command_id=identifier, actor_id=actor, authorize=lambda scope: False)
    with pytest.raises(StorageInvariantError):
        command(command_id=identifier, actor_id=uuid4())
    with pytest.raises(StorageInvariantError):
        command(command_id=identifier, actor_id=actor, cause="nightly")
    assert SourceRefreshCommand.objects.count() == 1


def test_concurrent_manual_commands_choose_one_waiting_request(tmp_path):
    """Separate database connections serialize allocation before taking Task locks."""
    initialized(tmp_path)

    def create():
        """Own and close one background thread's real database connection."""
        try:
            return command()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: create(), range(4)))
    assert len({receipt.request_id for receipt in receipts}) == 1
    assert SourceRefreshCommand.objects.count() == 4


def test_new_campaign_invalidates_no_campaign_task_before_claim(tmp_path):
    """Queued work cannot adopt a campaign that did not exist at request time."""
    store, root, actor = initialized(tmp_path)
    old = command()
    add_draft(store, root, actor)
    with pytest.raises(PermissionError):
        claim(old)
    assert TaskRun.objects.get(pk=old.task_root_id).state == "queued"
    replacement = command()
    assert replacement.request_id != old.request_id
    assert claim(replacement) is not None


def test_financial_request_window_is_verified_by_sql_and_runtime(tmp_path):
    """Only selected current/comparison periods are bound, in deterministic order."""
    _, campaign, _ = draft_campaign(
        tmp_path, campaign_record(modules=["financial"], financial=financial())
    )
    receipt = command()
    request = SourceRefreshRequest.objects.get(pk=receipt.request_id)
    assert request.campaign_id == campaign.pk
    assert '"funds":[1]' in request.window_canonical
    assert '"funds":[2]' in request.window_canonical
    execution = claim(receipt)
    with execution.effect():
        assert connection.in_atomic_block


def test_intake_and_attempt_guards_share_the_installed_window_derivation():
    """Future window changes have one SQL owner rather than two drifting copies."""
    with connection.cursor() as cursor:
        for name in (
            "stewardship_refresh_request_guard_v1",
            "stewardship_refresh_attempt_guard_v1",
        ):
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [name + "()"])
            definition = cursor.fetchone()[0]
            assert "stewardship_source_current_window_v1(" in definition
            assert "comparison_start" not in definition


def test_restore_blocks_replay_and_new_commands(tmp_path):
    """Manual does not bypass an installation's restore safety gate."""
    _, campaign, _ = draft_campaign(tmp_path)
    actor, key = uuid4(), uuid4()
    command(command_id=key, actor_id=actor)
    with restored_runtime(campaign.active_configuration.starts_at):
        with pytest.raises(PermissionError):
            command(command_id=key, actor_id=actor)
        with pytest.raises(PermissionError):
            command()
    assert SourceRefreshCommand.objects.count() == 1


@pytest.mark.parametrize("table", ["request", "command"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_refresh_history_cannot_be_rewritten_via_raw_sql(tmp_path, table, operation):
    """Runtime history protection is not just an ORM convention."""
    initialized(tmp_path)
    command()
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as sql,
    ):
        name = "stewardship_source_refresh_" + table
        if operation == "UPDATE":
            sql.execute(f"UPDATE {name} SET actor_id=NULL")
        else:
            sql.execute(f"DELETE FROM {name}")


def bare_request(**overrides):
    """Create a structurally bound root for SQL-invalid request insertion cases."""
    identifier = uuid4()
    task = enqueue(
        task_type=TASK_TYPE,
        domain_request_id=identifier,
        actor_id=None,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    canonical, digest = canonical_payload({"campaign_id": None, "periods": []})
    return SourceRefreshRequest.objects.create(
        **dict(
            id=identifier,
            task_root_id=task.root_id,
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
            organization_id=12345,
            kind="full",
            window_canonical=canonical,
            window_digest=digest,
        )
        | overrides
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"organization_id": 5},
        {"window_digest": "f" * 64},
        {"window_canonical": '{"periods":[],"campaign_id":null}'},
        {"window_canonical": '{"campaign_id":null,"periods":[1]}'},
        {"campaign_id": uuid4()},
        {"id": uuid4()},
    ],
)
def test_sql_rejects_forged_source_scope_or_task_binding(tmp_path, overrides):
    """A caller cannot bypass canonical/current-parent checks using direct INSERT."""
    initialized(tmp_path)
    if "window_canonical" in overrides:
        import hashlib

        overrides = overrides | {
            "window_digest": hashlib.sha256(
                overrides["window_canonical"].encode()
            ).hexdigest()
        }
    with pytest.raises(IntegrityError), work_transaction():
        bare_request(**overrides)
    assert not SourceRefreshRequest.objects.exists()
    assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()


def test_sql_requires_owning_work_order(tmp_path):
    """A raw insertion cannot acquire lifecycle locks late after the task root."""
    initialized(tmp_path)
    with pytest.raises(IntegrityError), transaction.atomic():
        bare_request()


def test_wrong_task_cannot_borrow_request_identifier(tmp_path):
    """Exact retry root, not just an existing request UUID, owns the workload."""
    initialized(tmp_path)
    receipt = command()
    with work_transaction():
        impostor = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=receipt.request_id,
            actor_id=None,
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
        with pytest.raises(PermissionError):
            admit_refresh_request("claim", impostor)


def test_failed_run_does_not_absorb_new_command(tmp_path):
    """A newly requested load is not silently linked to terminal failed work."""
    initialized(tmp_path)
    receipt = command()
    execution = claim(receipt)
    execution.transition("permanent_failure")
    assert command().request_id != receipt.request_id


def test_explicit_retry_retains_request_and_can_absorb_new_command(tmp_path):
    """Coalescing follows the whole immutable retry chain, not the first run state."""
    from parishkit.stewardship.jobs.storage import retry_failed

    initialized(tmp_path)
    receipt = command()
    claim(receipt).transition("permanent_failure")
    with work_transaction():
        retried = retry_failed(
            run_id=receipt.task_root_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=admit_refresh_request,
        )
    assert retried.domain_request_id == receipt.request_id
    assert command().request_id == receipt.request_id


def test_retained_source_lease_excludes_waiting_retry_root(tmp_path):
    """A drained task may leave a safety lease; commands wait on a distinct load."""
    initialized(tmp_path)
    receipt = command()
    execution = claim(receipt)
    with execution.effect():
        acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="full",
        )
    execution.transition("retryable_failure", retry_seconds=1)
    assert command().request_id != receipt.request_id


def test_different_retained_tenant_prevents_new_load(tmp_path):
    """Changed credentials/configuration must not replace another parish's truth."""
    initialized(tmp_path)
    snapshot, source_claim = prepared(source_corpus())
    publish(snapshot, source_claim)
    release_source(source_claim)
    with pytest.raises(PermissionError):
        command()
    assert SourceCurrent.objects.get().organization_id == 100


def test_parent_admission_requires_same_lock_order_as_creation(tmp_path):
    """Detached pre-checks do not authorize a later source mutation."""
    initialized(tmp_path)
    receipt = command()
    status = _status(TaskRun.objects.get(pk=receipt.task_root_id))
    with pytest.raises(StorageInvariantError), transaction.atomic():
        admit_refresh_request("effect", status)


def test_full_command_cannot_be_directly_attached_to_delta(tmp_path):
    """SQL enforces compatibility even if service coalescing is skipped."""
    initialized(tmp_path)
    receipt = command(cause="delta", actor_id=None)
    with pytest.raises(IntegrityError), work_transaction():
        SourceRefreshCommand.objects.create(
            request_id=receipt.request_id, kind="full", cause="manual", actor_id=uuid4()
        )


def test_creation_rollback_leaves_no_orphan_task_when_authority_is_lost(tmp_path):
    """The producer's second admission may deny before root/parent insertion."""
    initialized(tmp_path)
    decisions = iter((True, False))
    with pytest.raises(PermissionError):
        command(authorize=lambda scope: next(decisions))
    assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()
    assert not SourceRefreshRequest.objects.exists()


@pytest.mark.parametrize("changed_funds", [False, True])
def test_campaign_edit_rechecks_only_query_relevant_window(tmp_path, changed_funds):
    """A content/name edit is harmless; changing selected funds invalidates a load."""
    store, campaign, actor = draft_campaign(
        tmp_path, campaign_record(modules=["financial"], financial=financial())
    )
    receipt = command()
    execution = claim(receipt)
    version = store.active()
    row = version.document()["sections"]["campaigns"][0]
    values = row["values"] | {"name": "Changed name"}
    if changed_funds:
        values["financial"] = financial(fund_duids=[3])
    result = change(
        store,
        version,
        actor,
        [dict(operation="update", section="campaigns", id=row["id"], values=values)],
    )
    assert result.state == "applied"
    if changed_funds:
        with pytest.raises(PermissionError), execution.effect():
            pytest.fail("An old load admitted different selected funds")
    else:
        with execution.effect():
            assert connection.in_atomic_block


@pytest.mark.parametrize("organization", ["012345", "-1", "2147483648", "private"])
def test_noncanonical_configured_organization_is_not_coerced(tmp_path, organization):
    """An invalid source tenant is rejected without echoing configured input."""
    store, version, actor = initialized(tmp_path)
    row = version.document()["sections"]["integrations"][0]
    result = change(
        store,
        version,
        actor,
        [
            dict(
                operation="update",
                section="integrations",
                id=row["id"],
                values={"settings": {"organization_id": organization}},
            )
        ],
    )
    assert result.state == "applied"
    with pytest.raises(PermissionError) as error:
        command()
    assert str(error.value) == "Source refresh requires its configured organization."
    assert not SourceRefreshRequest.objects.exists()


def test_completed_command_replay_does_not_create_fresh_work(tmp_path):
    """A retry of the caller command refers to its actual historical outcome."""
    initialized(tmp_path)
    actor, identifier = uuid4(), uuid4()
    receipt = command(actor_id=actor, command_id=identifier)
    claim(receipt).transition("permanent_failure")
    assert command(actor_id=actor, command_id=identifier) == receipt
    assert SourceRefreshRequest.objects.count() == 1
