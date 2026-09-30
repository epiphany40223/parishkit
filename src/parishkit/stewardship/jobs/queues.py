"""Pure reviewed broker vocabulary shared by runtime and offline provisioning."""

from enum import StrEnum
from types import MappingProxyType

from parishkit.stewardship.deployment import ServiceRole

HINT_TASK = "stewardship.execution_hint"
BROKER_PREFIX = "stewardship:broker:v1:"


class WorkQueue(StrEnum):
    """Separate consumers never receive another service's secret-bearing work."""

    GENERAL = "general"
    # Long, provider-bound ParishSoft work (refresh and setup source loads) has
    # its own consumer process in the worker container, so it never holds up
    # exports and operational collection on the general queue (#336).
    SOURCE = "general-source"
    MAIL = "mail-dispatch"
    BACKUP = "backup-worker"
    RESTORE_GENERAL = "restore-general"
    RESTORE_MAIL = "restore-mail"
    RESTORE_BACKUP = "restore-backup"


ROLE_QUEUES = MappingProxyType(
    {
        ServiceRole.WORKER: frozenset(
            {WorkQueue.GENERAL, WorkQueue.SOURCE, WorkQueue.RESTORE_GENERAL}
        ),
        ServiceRole.MAIL_DISPATCH: frozenset({WorkQueue.MAIL, WorkQueue.RESTORE_MAIL}),
        ServiceRole.BACKUP_WORKER: frozenset(
            {WorkQueue.BACKUP, WorkQueue.RESTORE_BACKUP}
        ),
        ServiceRole.SCHEDULER: frozenset(),
    }
)

# The worker container's second consumer process takes only these queues; the
# first takes the rest of the worker's queues (see runtime_process).
SOURCE_QUEUES = frozenset({WorkQueue.SOURCE})


def exchange(queue):
    """The broker exchange a queue is bound to.

    The source queue shares the general queue's exchange, and its name extends
    the general name, so a Valkey ACL provisioned before the queue existed
    (``~PREFIX general*`` and ``_kombu.binding.general``) already grants it.
    Retarget keeps that once-generated ACL, so a new exchange would need a
    reinstall; this keeps the source queue deployable by ``retarget-image``.
    """
    return WorkQueue.GENERAL if queue is WorkQueue.SOURCE else queue
