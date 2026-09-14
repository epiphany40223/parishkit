"""Final setup's compiled worker owns fresh observation and atomic activation."""

from functools import partial
from pathlib import Path

from django.db import connection, transaction

from parishkit.stewardship.accounts.installation_lock import ConfigurationBusy
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Handler
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.observability import Event, correlation
from parishkit.stewardship.storage import StorageInvariantError

from .failures import ReadFailure, classify_read_failure
from .leases import acquire_source, release_source, verify_source
from .outcomes import failure_action, retry_delay
from .rejection import reject_snapshot
from .setup_completion import complete_setup
from .setup_final_loading import load_final_setup_source
from .setup_final_tasks import (
    bound_preparation,
    finalization_admission,
    recovery_plan,
    require_final_task,
)
from .snapshot_models import SourceSnapshot


def finalization_handler(
    store, *, credential_path=None, general=None, mac=None, public=None, scheduler=False
):
    """Schedulers get only metadata; workers need actual installed dependencies."""
    if scheduler:
        execute = _unavailable
    else:
        if not isinstance(credential_path, Path) or any(
            value is None for value in (general, mac, public)
        ):
            raise TypeError("Final setup requires installed credentials and keyrings.")
        execute = partial(
            _execute,
            store=store,
            credential_path=credential_path,
            general=general,
            mac=mac,
            public=public,
        )
    return Handler(
        queue=WorkQueue.GENERAL,
        admit=finalization_admission(store),
        execute=execute,
        recover=partial(recovery_plan, store=store),
        scope=work_transaction,
    )


def _unavailable(execution):
    """Metadata startup cannot accidentally expose provider execution."""
    raise PermissionError("The scheduler cannot execute final setup.")


def _execute(execution, *, store, credential_path, general, mac, public):
    """Maintain both fences through observation, then publish all local effects."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Final setup requires a maintained execution.")
    claim = None
    try:
        with execution.effect():
            claim = acquire_source(
                task_id=execution.claim.run_id,
                task_fence=execution.claim.fence,
                worker_id=execution.claim.worker_id,
                phase="full",
            )
        with execution.maintain_source(claim):
            snapshot = load_final_setup_source(
                execution, claim, store=store, credential_path=credential_path
            )
        count = sum(snapshot.counts.values())
        execution.progress(count, count, phase=TaskPhase.PROMOTING)
        complete_setup(
            execution,
            claim,
            snapshot.pk,
            store=store,
            general=general,
            mac=mac,
            public=public,
        )
    except Exception as error:
        _failed(execution, error, claim, store=store)


def _failed(execution, error, claim, *, store):
    """Settle only known drained read failures; never invent a successful setup."""
    # Final promotion runs after provider drainage, but can race an ordinary
    # installer pass. Reject this observation and release its source fence now;
    # waiting for abandoned-task recovery needlessly retains both leases.
    decision = (
        ReadFailure(True, True, Event.SOURCE_HELD)
        if isinstance(error, ConfigurationBusy) and claim is not None
        else classify_read_failure(error, has_source_claim=claim is not None)
    )
    if decision is None:
        raise error
    with execution.control.lock, correlation(execution.correlation_id):
        execution.control.check(allow_drain=True)
        with work_transaction():
            status = _status(lock_task_claim(execution.claim))
            bound_preparation(status)
            try:
                require_final_task(status, store=store)
            except (PermissionError, LookupError):
                action = "safe_cancel"
            else:
                action = failure_action(
                    status.attempt, retry=decision.retry, contention=decision.contention
                )
            if claim is not None:
                verify_source(claim)
                for snapshot in SourceSnapshot.objects.filter(
                    task_id=status.run_id,
                    source_fence=claim.fence,
                    state__in=("staging", "ready"),
                ):
                    reject_snapshot(
                        snapshot.pk,
                        claim,
                        admit=lambda action, row, identifier=snapshot.pk: (
                            action == "reject" and row.pk == identifier
                        ),
                    )
                release_source(claim)
            result = change_run(
                run_id=status.run_id,
                expected_version=status.version,
                action=action,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                fence=execution.claim.fence,
                admit=execution.handler.admit,
                **(
                    {"retry_seconds": retry_delay(status.attempt)}
                    if action == "retryable_failure"
                    else {}
                ),
            )
            operational(
                decision.event,
                level="CRITICAL" if action == "permanent_failure" else "INFO",
                schema=ContextKind.TASK,
                context={
                    "task_id": result.run_id,
                    "version": result.version,
                    "outcome": Outcome.FAILED
                    if action == "permanent_failure"
                    else Outcome.RETRY
                    if action == "retryable_failure"
                    else Outcome.CANCELLED,
                },
            )
            transaction.on_commit(execution.control.finished.set)
