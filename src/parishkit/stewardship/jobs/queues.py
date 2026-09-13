"""Pure reviewed broker vocabulary shared by runtime and offline provisioning."""

from enum import StrEnum
from types import MappingProxyType

from parishkit.stewardship.deployment import ServiceRole

HINT_TASK = "stewardship.execution_hint"
BROKER_PREFIX = "stewardship:broker:v1:"


class WorkQueue(StrEnum):
    """Separate consumers never receive another service's secret-bearing work."""

    GENERAL = "general"
    MAIL = "mail-dispatch"
    BACKUP = "backup-worker"
    RESTORE_GENERAL = "restore-general"
    RESTORE_MAIL = "restore-mail"
    RESTORE_BACKUP = "restore-backup"


ROLE_QUEUES = MappingProxyType(
    {
        ServiceRole.WORKER: frozenset({WorkQueue.GENERAL, WorkQueue.RESTORE_GENERAL}),
        ServiceRole.MAIL_DISPATCH: frozenset({WorkQueue.MAIL, WorkQueue.RESTORE_MAIL}),
        ServiceRole.BACKUP_WORKER: frozenset(
            {WorkQueue.BACKUP, WorkQueue.RESTORE_BACKUP}
        ),
        ServiceRole.SCHEDULER: frozenset(),
    }
)
