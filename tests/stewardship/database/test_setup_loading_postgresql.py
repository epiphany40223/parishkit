"""Actual isolated worker stages setup without an installed provider credential."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models.query import QuerySet

from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_exchange_models import SetupSourceResult
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_source import start_source_load
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Execution, Handler, execute_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.source.credentials import SourceCredential
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.setup_admission import admit_setup_task
from parishkit.stewardship.source.setup_exchange import (
    publish_recipient,
    receive_credential,
    relay_pending,
)
from parishkit.stewardship.source.setup_execution import (
    complete_setup_load,
    setup_source_handler,
)
from parishkit.stewardship.source.setup_handoff import (
    EphemeralSetupRecipient,
    SetupCredentialScope,
)
from parishkit.stewardship.source.setup_loading import load_setup_source
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.storage import StorageInvariantError

from ..test_setup_forms import VALUES
from ..test_source_loading import provider_pages
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import staged
from .test_setup_exchange_postgresql import target_login
from .test_setup_progress_postgresql import bind_load
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


def prepared(service, *, with_request=False):
    """Set up original-login public input and a real target-isolated key exchange."""
    SourceCurrent.objects.get_or_create(singleton=True)
    request, attempt, receipt, private = staged(service)
    with web_login():
        save_section(
            request,
            service,
            attempt.attempt_id,
            step="parish",
            values=VALUES["parish"],
            expected_version=attempt.version,
        )
    task, source = bind_load(attempt.attempt_id)
    claim = TaskClaim(task.run_id, source.task_fence, source.worker_id)
    recipient = EphemeralSetupRecipient(
        SetupCredentialScope(
            attempt.attempt_id, receipt.identifier, claim, source.fence
        )
    )
    with task_login(ServiceRole.WORKER, exact=True):
        exchange_id = publish_recipient(recipient.public)
    with target_login():
        assert relay_pending(private)
    handler = Handler(
        WorkQueue.GENERAL, admit_setup_task, lambda _: None, scope=work_transaction
    )
    values = (
        Execution(claim, handler, TaskRun.objects.get(pk=task.run_id).correlation_id),
        source,
        recipient,
        exchange_id,
    )
    return (values, request) if with_request else values


def run(execution, source, recipient, exchange_id, *, credential=None, complete=False):
    """Keep all real database connections and renewal on the exact worker identity."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        credential = credential or receive_credential(recipient)
        with maintain_execution(execution):
            with execution.maintain_source(source):
                snapshot = load_setup_source(
                    execution, source, exchange_id=exchange_id, credential=credential
                )
            if complete:
                complete_setup_load(execution, source)
            return snapshot


def pages(**options):
    """Match the staged synthetic organization, without a financial query window."""
    values = provider_pages(**options)
    values[0][0]["organizationID"] = 1
    values[1][0]["registeredOrganizationID"] = 1
    return values


def test_worker_loads_ready_corpus_without_publishing_setup(setup_service, monkeypatch):
    """Real HTTP preflight and SQL result guards run under least-privilege grants."""
    prepared_values = prepared(setup_service)
    remaining, calls = fake_provider(monkeypatch, pages())
    snapshot = run(*prepared_values)
    assert not remaining and len(calls) == len(pages())
    assert SetupSourceResult.objects.get().snapshot_id == snapshot.pk
    assert snapshot.state == "ready" and snapshot.counts["family"] == 1
    assert snapshot.counts["fund"] == 1
    assert snapshot.counts["pledge"] == snapshot.counts["contribution"] == 0
    assert SourceCurrent.objects.get().snapshot_id is None
    assert SetupAttempt.objects.get().state == "loading"
    assert TaskRun.objects.get().state == "running"
    assert not setup_service.configured()
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as c:
        c.execute("UPDATE stewardship_setup_source_result SET actor_id=NULL")


def test_completion_restores_collecting_but_not_configured(setup_service, monkeypatch):
    """Only the exact validated result can atomically finish the original load."""
    values = prepared(setup_service)
    fake_provider(monkeypatch, pages())
    run(*values, complete=True)
    assert TaskRun.objects.get().state == "succeeded"
    assert SetupAttempt.objects.get().state == "collecting"
    assert values[0].control.finished.is_set()
    assert SourceCurrent.objects.get().snapshot_id is None
    assert not setup_service.configured()


def test_failed_attempt_completion_rolls_back_task_and_source_release(
    setup_service, monkeypatch
):
    """A missed final attempt update cannot leave a false-success Task receipt."""
    values = prepared(setup_service)
    fake_provider(monkeypatch, pages())
    original = QuerySet.update

    def update(query, **fields):
        """Report a lost version after the real update to prove whole-unit rollback."""
        count = original(query, **fields)
        if query.model is SetupAttempt and fields.get("state") == "collecting":
            return 0
        return count

    monkeypatch.setattr(QuerySet, "update", update)
    with pytest.raises(StorageInvariantError):
        run(*values, complete=True)
    assert TaskRun.objects.get().state == "running"
    assert SetupAttempt.objects.get().state == "loading"
    assert SourceMutationLease.objects.get().owner_id == values[0].claim.run_id
    assert not values[0].control.finished.is_set()
    assert not setup_service.configured()


def test_wrong_private_key_stops_before_any_provider_read(setup_service, monkeypatch):
    """A correct exchange UUID does not authorize bytes with a different receipt."""
    values = prepared(setup_service)
    _, calls = fake_provider(monkeypatch, [])
    with pytest.raises(PermissionError):
        run(*values, credential=SourceCredential(b"incorrect-key"))
    assert not calls and not SourceSnapshot.objects.exists()
    assert not SetupSourceResult.objects.exists()


def test_setup_load_requires_maintained_lifetime(setup_service):
    """Possessing the source claim is insufficient without its renewal lifetime."""
    execution, source, recipient, exchange_id = prepared(setup_service)
    with task_login(ServiceRole.WORKER, exact=True):
        credential = receive_credential(recipient)
        with pytest.raises(StorageInvariantError, match="maintained"):
            load_setup_source(
                execution, source, exchange_id=exchange_id, credential=credential
            )
    assert not SourceSnapshot.objects.exists()


def queued(service):
    """Use real web intake, not a synthetic Task/attempt binding fixture."""
    SourceCurrent.objects.get_or_create(singleton=True)
    from parishkit.stewardship.source.models import SourceMutationLease

    SourceMutationLease.objects.get_or_create(singleton=True)
    request, attempt, _, private = staged(service)
    with web_login():
        attempt = save_section(
            request,
            service,
            attempt.attempt_id,
            step="parish",
            values=VALUES["parish"],
            expected_version=attempt.version,
        )
        task = start_source_load(
            request, service, attempt.attempt_id, expected_version=attempt.version
        )
        assert (
            start_source_load(
                request, service, attempt.attempt_id, expected_version=attempt.version
            )
            == task
        )
    return request, attempt, task, private


def installer_reply(monkeypatch, private):
    """Model a separate isolated installer between the worker's real queue polls.

    Only this disposable superuser-backed fixture can switch SQL identities.
    The production worker has no ability to create or assume the target role.
    """
    from parishkit.stewardship.source import setup_execution

    original = setup_execution.receive_credential

    def receive(recipient):
        """Run the real relay under target grants, then restore exact worker scope."""
        with connection.cursor() as c:
            c.execute("RESET SESSION AUTHORIZATION")
        try:
            with target_login():
                relay_pending(private)
        finally:
            with connection.cursor() as c:
                c.execute('SET SESSION AUTHORIZATION "pk_stewardship_worker"')
        return original(recipient)

    monkeypatch.setattr(setup_execution, "receive_credential", receive)


@pytest.mark.parametrize("invalid", [False, True])
def test_dispatched_setup_load_finishes_only_a_valid_corpus(
    setup_service, monkeypatch, invalid
):
    """The complete queued worker path handles good data and rejected provider data."""
    from uuid import uuid4

    _, _, task, private = queued(setup_service)
    installer_reply(monkeypatch, private)
    fake_provider(
        monkeypatch,
        pages(
            member_change={"birthdate": "invalid-private-value"} if invalid else None
        ),
    )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"setup_source_load": setup_source_handler()},
        )
    assert TaskRun.objects.get().state == ("failed" if invalid else "succeeded")
    assert SourceSnapshot.objects.get().state == ("rejected" if invalid else "ready")
    assert SetupSourceResult.objects.exists() is not invalid
    assert not setup_service.configured()
    assert SourceCurrent.objects.get().snapshot_id is None


def test_original_cancel_during_provider_page_blocks_all_staging(
    setup_service, monkeypatch
):
    """Cancellation between real HTTP pages cannot resurrect scrubbed setup data."""
    from uuid import uuid4

    from parishkit import parishsoft_transport

    request, attempt, task, private = queued(setup_service)
    installer_reply(monkeypatch, private)
    calls = []

    def exchange(payload, **kwargs):
        """Simulate the original browser committing cancel during network wait."""
        calls.append(payload)
        assert not connection.in_atomic_block
        with connection.cursor() as c:
            c.execute("RESET SESSION AUTHORIZATION")
        try:
            with web_login():
                cancel_setup(request, setup_service, attempt.attempt_id)
        finally:
            with connection.cursor() as c:
                c.execute('SET SESSION AUTHORIZATION "pk_stewardship_worker"')
        return b'200\n[{"organizationID":1}]'

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"setup_source_load": setup_source_handler()},
        )
    assert len(calls) == 1
    assert TaskRun.objects.get().state == "cancelled"
    assert SetupAttempt.objects.get().state == "expired"
    assert SourceSnapshot.objects.get().state == "rejected"
    assert not SetupSourceResult.objects.exists()


@pytest.mark.parametrize("stage", ["publish", "receive"])
def test_cancel_during_private_handoff_settles_without_provider_reads(
    setup_service, monkeypatch, stage
):
    """Cancellation before recipient publication or its next poll releases ownership."""
    from unittest.mock import Mock
    from uuid import uuid4

    from parishkit.stewardship.source import setup_execution

    request, attempt, task, _ = queued(setup_service)
    name = "EphemeralSetupRecipient" if stage == "publish" else "monotonic"
    original = getattr(setup_execution, name)
    cancelled = False

    def cancel(*args, **kwargs):
        """Use the actual browser owner between, never inside, worker effects."""
        nonlocal cancelled
        result = original(*args, **kwargs)
        if not cancelled:
            assert not connection.in_atomic_block
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")
            try:
                with web_login():
                    cancel_setup(request, setup_service, attempt.attempt_id)
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_worker")
            cancelled = True
        return result

    monkeypatch.setattr(setup_execution, name, cancel)
    loader = Mock()
    monkeypatch.setattr(setup_execution, "load_setup_source", loader)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"setup_source_load": setup_source_handler()},
        )
    assert cancelled
    assert TaskRun.objects.get().state == "cancelled"
    assert SourceMutationLease.objects.get().owner_id is None
    assert not SourceSnapshot.objects.exists()
    loader.assert_not_called()


def test_success_without_result_rolls_back_lease_and_control_flag(setup_service):
    """Incomplete staging cannot release ownership or return the wizard to editing."""
    execution, source, _, _ = prepared(setup_service)
    from parishkit.stewardship.source.models import SourceMutationLease

    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        maintain_execution(execution),
    ):
        with pytest.raises(PermissionError):
            complete_setup_load(execution, source)
        assert not execution.control.finished.is_set()
        assert SourceMutationLease.objects.get().owner_id == execution.claim.run_id
    assert TaskRun.objects.get().state == "running"
    assert SetupAttempt.objects.get().state == "loading"


def test_web_intake_requires_profile_before_queueing(setup_service):
    """Credential possession alone does not bind a load with missing local dates."""
    request, attempt, _, _ = staged(setup_service)
    with web_login(), pytest.raises(ValueError, match="profile"):
        start_source_load(
            request, setup_service, attempt.attempt_id, expected_version=attempt.version
        )
    assert not TaskRun.objects.exists()
    assert SetupAttempt.objects.get().source_task_id is None
