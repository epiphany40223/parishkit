"""Schedule only the exact installed first-setup preparation, once per root."""

from django.db import connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.setup_install_models import SetupPreparationReceipt
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.observability import correlation, emit_failure
from parishkit.stewardship.storage import StorageInvariantError

from .setup_final_tasks import enqueue_finalization


def produce_finalization(store, guard):
    """Expiry must run first; an abandoned original intent never becomes new work.

    One frozen setup is allowed globally. The query remains explicitly bounded
    and historical preparations do not induce an unbounded scan each tick.
    """
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Final setup production requires actual scheduler ownership.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Final setup production owns its transaction.")
    guard.check()
    with work_transaction():
        receipt = (
            SetupPreparationReceipt.objects.filter(
                readiness__intent__attempt__state="frozen"
            )
            .order_by("created_at", "id")
            .first()
        )
        if receipt is None:
            return ()
        try:
            task = enqueue_finalization(
                store, receipt.pk, correlation_id=receipt.correlation_id
            )
        except (PermissionError, ConfigError) as error:
            # A receipt whose YAML selection is not currently coherent is not
            # runnable, but must not starve unrelated cleanup and hint scanning.
            with correlation(receipt.correlation_id):
                emit_failure(error)
            return ()
    guard.check()
    return (task.run_id,)
