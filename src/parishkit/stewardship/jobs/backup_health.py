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

from .backup_models import BackupRun, BackupUpload
from .operational_content import IncidentKind, IncidentLevel
from .operational_models import OperationalIncident
from .operational_sources import configured_policy
from .operational_storage import record_observation, record_recovery
from .ownership import database_now

# The specification's window: a successful backup is required every 24 hours.
REQUIRED_WITHIN = timedelta(hours=24)
# A completed backup's off-site copy must record an outcome within this long.
# The copy stops starting work after four hours (backup_offsite.COPY_SECONDS),
# so a longer silence means it was killed or lost its database connection.
OFFSITE_GRACE = timedelta(hours=6)


def needs_backup_observation():
    """Wake idle collection when a backup is overdue or its episode is open.

    Nothing else produces this incident, so an idle deployment must notice
    the first overdue night itself; both reads are single rows under the
    collector's work order.
    """
    return (
        OperationalIncident.objects.filter(
            kind__in=[
                IncidentKind.BACKUP_RPO_BREACH,
                IncidentKind.BACKUP_OFFSITE_FAILED,
            ],
            resolved_at__isnull=True,
        ).exists()
        or backup_overdue(database_now())
        or offsite_failing()
    )


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


def offsite_failing(instant=None):
    """True while off-site copies are failing, or have silently stopped.

    The backup profile records one outcome per attempted set, and "disabled"
    when the destination is removed, so an old failure stops counting once a
    later copy succeeds or copies are turned off. A copy that is killed, or
    loses its database connection, before recording anything leaves an older
    set's "uploaded" row as the newest: so once copies have started, the
    newest backup counts as failing when it completed more than
    ``OFFSITE_GRACE`` ago and no outcome names its set. Keying on the set,
    not on timestamps, also catches a catch-up run that copied an older set
    and was then killed during the newest one.
    """
    newest = (
        BackupUpload.objects.order_by("-created_at")
        .values_list("state", "created_at")
        .first()
    )
    if newest is None or newest[0] == "disabled":
        return False
    if newest[0] == "failed":
        return True
    latest_run = (
        BackupRun.objects.order_by("-completed_at")
        .values_list("completed_at", "manifest_digest")
        .first()
    )
    if latest_run is None:
        return False
    instant = database_now() if instant is None else instant
    return (
        instant - latest_run[0] > OFFSITE_GRACE
        and not BackupUpload.objects.filter(manifest_digest=latest_run[1]).exists()
    )


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
    if offsite_failing():
        record_observation(
            IncidentKind.BACKUP_OFFSITE_FAILED,
            IncidentLevel.CRITICAL,
            policy=configured_policy(),
        )
    else:
        record_recovery(IncidentKind.BACKUP_OFFSITE_FAILED)
