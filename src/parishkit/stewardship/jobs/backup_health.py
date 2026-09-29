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


def destination_configured_since():
    """When the active configuration naming a Drive folder was activated, or None.

    ``None`` means off-site copies are off now. A saved folder link that no
    longer parses still counts as configured: every copy then fails before
    it can record an outcome, which is exactly the silence to alert on.
    """
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts.runtime_models import ConfigurationActivation
    from parishkit.stewardship.backup_offsite import destination_from

    active = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    # Every active configuration has exactly one activation row, written in
    # the same transaction that made it active.
    row = (
        ConfigurationActivation.objects.filter(configuration_id=active)
        .values_list("created_at", "configuration__canonical_document")
        .first()
        if active is not None
        else None
    )
    if row is None:
        return None
    try:
        configured = destination_from(row[1]) is not None
    except ConfigError:
        configured = True
    return row[0] if configured else None


def offsite_failing(instant=None):
    """True while off-site copies are failing, or have silently stopped.

    The backup profile records one outcome per attempted set, and "disabled"
    when the destination is removed, so an old failure stops counting once a
    later copy succeeds or copies are turned off. A copy that is killed, or
    loses its database connection, before recording anything leaves no row
    for its set: so the newest backup counts as failing when it completed
    more than ``OFFSITE_GRACE`` ago and no outcome names its set. Keying on
    the set, not on timestamps, also catches a catch-up run that copied an
    older set and was then killed during the newest one.

    With no outcome yet, or "disabled" as the newest one, the destination
    was just configured for the first time or turned back on. Every copy
    since may have died silently, so the same rule applies to a backup that
    completed after the configuration naming the folder was activated; one
    taken before that had copies off and is not expected on Drive. The
    configuration document is read only once a backup is silent past the
    grace, so an idle collector's check stays a few small row reads.
    """
    newest = (
        BackupUpload.objects.order_by("-created_at")
        .values_list("state", flat=True)
        .first()
    )
    if newest == "failed":
        return True
    latest_run = (
        BackupRun.objects.order_by("-completed_at")
        .values_list("completed_at", "manifest_digest")
        .first()
    )
    if latest_run is None:
        return False
    instant = database_now() if instant is None else instant
    if (
        instant - latest_run[0] <= OFFSITE_GRACE
        or BackupUpload.objects.filter(manifest_digest=latest_run[1]).exists()
    ):
        return False
    if newest is None or newest == "disabled":
        since = destination_configured_since()
        return since is not None and latest_run[0] >= since
    return True


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
