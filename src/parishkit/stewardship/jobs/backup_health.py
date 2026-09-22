"""The scheduler notices when the required backup is overdue, and when it is not.

The operations specification makes a day without a successful backup CRITICAL.
The v1 backup runs outside the application, so the only evidence is the row
each completed run records; the scheduler's operational collection compares
the newest one with the clock. A deployment that has never backed up is held
to the rule only once it is in Production, so a Testing install does not page
its operators before the operator has set the nightly backup up; once one
backup exists, the rule applies in every mode.
"""

from datetime import timedelta

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import require_work_order

from .backup_models import BackupRun
from .operational_content import IncidentKind, IncidentLevel
from .operational_models import OperationalIncident
from .operational_sources import configured_policy
from .operational_storage import record_observation, record_recovery
from .ownership import database_now

# The specification's window: a successful backup is required every 24 hours.
REQUIRED_WITHIN = timedelta(hours=24)


def needs_backup_observation():
    """Idle collection wakes only while an overdue episode may need resolving."""
    return OperationalIncident.objects.filter(
        kind=IncidentKind.BACKUP_RPO_BREACH, resolved_at__isnull=True
    ).exists()


def production_mode():
    """Whether the deployment's runtime row says Production."""
    mode = SystemConfiguration.objects.values_list("mode", flat=True).first()
    return mode == "production"


def backup_overdue(instant):
    """True when the rule applies and no backup completed within the window."""
    latest = (
        BackupRun.objects.order_by("-completed_at")
        .values_list("completed_at", flat=True)
        .first()
    )
    if latest is None:
        return production_mode()
    return instant - latest > REQUIRED_WITHIN


def observe_backup_health():
    """Open or resolve the overdue-backup episode from the newest recorded run."""
    require_work_order()
    if backup_overdue(database_now()):
        record_observation(
            IncidentKind.BACKUP_RPO_BREACH,
            IncidentLevel.CRITICAL,
            policy=configured_policy(),
        )
    else:
        record_recovery(IncidentKind.BACKUP_RPO_BREACH)
