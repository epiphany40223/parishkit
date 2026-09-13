"""Exact setup-source completion; ready staging is not product activation."""

from time import monotonic

from django.db import connection, connections
from django.db.models import F

from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Handler
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StorageInvariantError

from .failures import classify_read_failure
from .leases import acquire_source, release_source, verify_source
from .outcomes import failure_action, retry_delay
from .rejection import reject_snapshot
from .setup_admission import (
    admit_setup_task,
    bound_attempt,
    recovery_plan,
    require_live_setup,
)
from .setup_exchange import publish_recipient, receive_credential
from .setup_handoff import EphemeralSetupRecipient, SetupCredentialScope
from .setup_loading import load_setup_source
from .snapshot_models import SourceSnapshot


def setup_source_handler():
    """Register a compiled setup-only owner, with no persistent provider key path."""
    return Handler(
        queue=WorkQueue.GENERAL,
        admit=admit_setup_task,
        execute=_execute,
        recover=recovery_plan,
        scope=work_transaction,
    )


def _receive(execution, claim):
    """Wait at most two minutes for the isolated target while both leases renew."""
    from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential

    with execution.effect():
        attempt = require_live_setup(
            bound_attempt(_status(lock_task_claim(execution.claim)))
        )
        candidate_id = SetupSealedCredential.objects.values_list("id", flat=True).get(
            attempt=attempt, target="parishsoft", scrubbed_at=None
        )
    recipient = EphemeralSetupRecipient(
        SetupCredentialScope(attempt.pk, candidate_id, execution.claim, claim.fence)
    )
    with execution.effect():
        require_live_setup(bound_attempt(_status(lock_task_claim(execution.claim))))
        exchange_id = publish_recipient(recipient.public)
    deadline = monotonic() + 120
    while monotonic() < deadline:
        execution.check()
        with execution.effect():
            require_live_setup(bound_attempt(_status(lock_task_claim(execution.claim))))
            credential = receive_credential(recipient)
        if credential is not None:
            return exchange_id, credential
        connections.close_all()
        # The Task's independent lifetime continues to renew while waiting. No
        # transaction, private-key file or inherited installer privilege is held.
        execution.control.finished.wait(1)
    raise TimeoutError("The setup credential target did not respond in time.")


def _failed(execution, error, claim):
    """Known drained reads may fail/retry; invariant and lost-fence errors propagate."""
    decision = classify_read_failure(error, has_source_claim=claim is not None)
    if decision is None:
        raise error
    with execution.control.lock, correlation(execution.correlation_id):
        execution.control.check(allow_drain=True)
        with work_transaction():
            status = _status(lock_task_claim(execution.claim))
            attempt = bound_attempt(status)
            try:
                require_live_setup(attempt)
            except PermissionError:
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

                    def admit(candidate_action, candidate, identifier=snapshot.pk):
                        """Reject only this exact historical read."""
                        bound_attempt(status)
                        return (
                            candidate_action == "reject" and candidate.pk == identifier
                        )

                    reject_snapshot(snapshot.pk, claim, admit=admit)
                release_source(claim)
            result = change_run(
                run_id=status.run_id,
                expected_version=status.version,
                action=action,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                fence=execution.claim.fence,
                admit=admit_setup_task,
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
        execution.control.finished.set()


def _execute(execution):
    """Keep the full provider load under maintained Task and source ownership."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Setup source execution requires a maintained lifetime."
        )
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
            exchange_id, credential = _receive(execution, claim)
            load_setup_source(
                execution, claim, exchange_id=exchange_id, credential=credential
            )
        complete_setup_load(execution, claim)
    except Exception as error:
        _failed(execution, error, claim)


def complete_setup_load(execution, claim):
    """Commit Task success and collecting state together after source renewal ends.

    The dispatcher completion event is set only after the whole transaction
    commits. A failure preserves both the running Task and original setup state
    for fenced recovery; a successful commit never advances current source truth.
    """
    with execution.control.lock:
        execution.control.check(allow_drain=True)
        if execution.control.source_claim is not None:
            raise StorageInvariantError("End source renewal before setup completion.")
        with work_transaction():
            status = _status(lock_task_claim(execution.claim))
            attempt = require_live_setup(bound_attempt(status))
            verify_source(claim)
            release_source(claim)
            change_run(
                run_id=status.run_id,
                expected_version=status.version,
                action="complete",
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                fence=execution.claim.fence,
                admit=admit_setup_task,
            )
            changed = SetupAttempt.objects.filter(
                pk=attempt.pk, version=attempt.version
            ).update(
                state="collecting",
                actor_id=attempt.owner_id,
                correlation_id=execution.correlation_id,
                version=F("version") + 1,
            )
            if changed != 1:
                raise StorageInvariantError("Setup changed before source completion.")
        execution.control.finished.set()
