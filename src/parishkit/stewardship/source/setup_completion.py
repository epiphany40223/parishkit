"""The one atomic transition from reviewed initial setup to a configured parish.

Only the config installer selects files. This worker takes the same pinned
serialization lock without writing files, then commits all SQL effects under
the normal task/source lock order. No provider call belongs in this transaction.
"""

from django.db import transaction

from parishkit.stewardship.accounts.chair_reconciliation import (
    reconcile_configuration_chairs,
    reconcile_source_chairs,
)
from parishkit.stewardship.accounts.installation_lock import installation_lock
from parishkit.stewardship.accounts.runtime_models import (
    ConfigurationActivation,
    SystemConfiguration,
)
from parishkit.stewardship.accounts.setup_install_models import SetupCompletion
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.storage import StorageInvariantError

from .families import reconcile_source_families
from .leases import release_source, verify_source
from .setup_final_tasks import require_final_task
from .snapshots import promote_snapshot


def complete_setup(execution, claim, snapshot_id, *, store, general, mac, public):
    """Commit exact final source, draft, codes, marker and successful Task together.

    Leave the source-renewal scope before entry. The execution control lock drains
    concurrent heartbeats, while both SQL fences remain checked through the final
    source effect. Any exception rolls back every derived row, including the Task
    outcome, and does not tell the execution controller that work has finished.
    """
    with execution.control.lock:
        execution.check()
        if execution.control.source_claim is not None:
            raise StorageInvariantError("Final setup must first leave source renewal.")
        with installation_lock() as guard, work_transaction():
            task = lock_task_claim(execution.claim)
            scope = require_final_task(_status(task), store=store)
            if (claim.task_id, claim.task_fence, claim.worker_id) != (
                task.pk,
                task.fence,
                task.worker_id,
            ):
                raise StorageInvariantError("Final source belongs to another task.")
            verify_source(claim)
            activation = None

            def admit(action, snapshot):
                """The final corpus must be fresh and cover the reviewed window."""
                return (
                    action == "promote"
                    and snapshot is not None
                    and snapshot.kind == "full"
                    and snapshot.organization_id == scope.organization_id
                    and snapshot.cursor.get("window_digest") == scope.window.digest
                    and require_final_task(_status(task), store=store) == scope
                )

            def reconcile(snapshot):
                """Select the first draft before population, still wholly invisible."""
                nonlocal activation
                runtime = SystemConfiguration.objects.select_for_update().get()
                activation = ConfigurationActivation.objects.create(
                    configuration_id=scope.configuration_id,
                    predecessor_id=runtime.active_configuration_id,
                    request_id=scope.request_id,
                    sequence=runtime.configuration_sequence,
                    actor_id=scope.owner_id,
                    correlation_id=execution.correlation_id,
                )
                reconcile_source_families(
                    snapshot.pk,
                    claim,
                    campaign_id=scope.attempt_id,
                    general=general,
                    mac=mac,
                    public=public,
                    suppressed_addresses=frozenset(),
                    admit=lambda campaign: (
                        campaign.pk == scope.attempt_id and campaign.state == "draft"
                    ),
                )
                reconcile_source_chairs(
                    snapshot.pk, claim, campaign_id=scope.attempt_id
                )
                reconcile_configuration_chairs(activation)
                return True

            snapshot = promote_snapshot(
                snapshot_id, claim, admit=admit, reconcile=reconcile
            )
            guard.check()
            if store.manifest_reference() != (
                scope.configuration_id,
                scope.configuration_digest,
            ):
                raise StorageInvariantError(
                    "Final setup selected configuration changed."
                )
            completed = SetupCompletion.objects.create(
                preparation_id=scope.preparation_id,
                activation=activation,
                snapshot=snapshot,
                task=task,
                task_fence=claim.task_fence,
                source_fence=claim.fence,
                actor_id=scope.owner_id,
                correlation_id=execution.correlation_id,
            )
            attempt = SetupAttempt.objects.select_for_update().get(pk=scope.attempt_id)
            attempt.state = "completed"
            attempt.actor_id = scope.owner_id
            attempt.correlation_id = execution.correlation_id
            attempt.version += 1
            attempt.save(
                update_fields=[
                    "state",
                    "actor_id",
                    "correlation_id",
                    "version",
                ]
            )
            release_source(claim)
            change_run(
                run_id=task.pk,
                expected_version=task.version,
                action="complete",
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                fence=execution.claim.fence,
                admit=execution.handler.admit,
            )
            transaction.on_commit(execution.control.finished.set)
        return completed
