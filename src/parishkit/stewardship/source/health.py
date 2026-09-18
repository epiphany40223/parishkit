"""Metadata-only source health, serialized with refresh failure and promotion.

The operational collector owns each minute sample and commits it with its Task
completion. No provider call, source payload or process-local success flag is
used here. Intentional admission holds cannot open or resolve an incident.
"""

from datetime import timedelta

from django.db.models import Q

from parishkit.stewardship.accounts.bootstrap_schema import BOOTSTRAP_SCHEMA
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.runtime_models import (
    ConfigurationActivation,
    SystemConfiguration,
)
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.admission import require_source_refresh
from parishkit.stewardship.jobs.operational_content import IncidentKind, IncidentLevel
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalLogReceipt,
)
from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.jobs.operational_storage import (
    record_observation,
    record_recovery,
)
from parishkit.stewardship.observability import Event

from .errors import SourceOrganizationChanged, SourceScopeChanged
from .models import SourceCurrent, SourceSnapshot
from .requests import _organization, _window

FAILURE_EVENTS = (
    Event.SOURCE_INVALID,
    Event.SOURCE_TENANT_MISMATCH,
    Event.SOURCE_DESTRUCTIVE_CHANGE,
    Event.SOURCE_CREDENTIAL_FAILED,
    Event.SOURCE_PROVIDER_FAILED,
)
FAILURE_KINDS = (
    IncidentKind.SOURCE_REFRESH_FAILED,
    IncidentKind.SOURCE_TENANT_MISMATCH,
    IncidentKind.SOURCE_DESTRUCTIVE_CHANGE,
)


def admitted_source_scope():
    """Use existing source gates, not delivery pause or go-live mail gates."""
    require_work_order()
    campaign_id = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()
    try:
        scope = require_source_refresh(campaign_id=campaign_id)
    except SourceScopeChanged:
        return None
    if (
        AppliedConfigurationVersion.objects.values_list(
            "validation_schema", flat=True
        ).get(pk=scope.runtime.active_configuration_id)
        == BOOTSTRAP_SCHEMA
    ):
        return None
    return scope


def initial_source_at(organization):
    """Start grace once per configured tenant, never after each unrelated edit."""
    return (
        ConfigurationActivation.objects.filter(
            configuration__integrations__kind="parishsoft",
            configuration__integrations__settings__organization_id=str(organization),
        )
        .exclude(configuration__validation_schema=BOOTSTRAP_SCHEMA)
        .values_list("created_at", flat=True)
        .earliest("sequence")
    )


def observe_source_health():
    """Observe one current sample under the collector's live fence and work lock.

    Failures and successful source promotion share this lock. All critical input
    receipts must exist before recovery, so late collection cannot reopen an
    already-resolved condition. A successful read must also start after every
    recorded source failure, including a newer transient failure not yet critical.
    Recovery deliberately avoids incident.last_seen: collection time is not the
    original failure time. No other source-health producer may bypass this order.
    """
    scope = admitted_source_scope()
    if scope is None:
        return
    try:
        organization = _organization(scope)
    except PermissionError as error:
        # Configuration defects are not holds. Feed the existing exact-receipt
        # collector rather than a second incident-writing path: its next page
        # consumes this durable, value-free failure. Its timestamp also prevents
        # old success from clearing the alert immediately after a config repair.
        operational(
            Event.SOURCE_TENANT_MISMATCH
            if isinstance(error, SourceOrganizationChanged)
            else Event.SOURCE_INVALID,
            level="CRITICAL",
            schema=ContextKind.ACTION,
            context={
                "version": scope.runtime.configuration_sequence,
                "outcome": Outcome.DENIED,
            },
        )
        return
    snapshot = (
        SourceCurrent.objects.select_related("snapshot").get(singleton=True).snapshot
    )
    policy = configured_policy()
    observed_at = (
        snapshot.started_at if snapshot is not None else initial_source_at(organization)
    )
    stale = scope.instant - observed_at >= timedelta(
        seconds=policy.source_stale_seconds
    )
    if stale:
        record_observation(
            IncidentKind.SOURCE_STALE, IncidentLevel.CRITICAL, policy=policy
        )
        return
    if (
        snapshot is None
        or snapshot.state != "promoted"
        or snapshot.organization_id != organization
        or snapshot.cursor.get("schema") != "source-refresh-v1"
        or snapshot.cursor.get("window_digest") != _window(scope).digest
    ):
        return
    if not OperationalIncident.objects.filter(
        kind__in=(IncidentKind.SOURCE_STALE, *FAILURE_KINDS), resolved_at__isnull=True
    ).exists():
        return
    # Filter by level first to use operational_level_time; never fetch raw error
    # context. EXISTS stops at the first counterexample, not a Python log scan.
    failures = OperationalLog.objects.filter(
        Q(event__in=FAILURE_EVENTS) | Q(event=Event.SOURCE_HELD, level="CRITICAL"),
        level__in=("WARNING", "ERROR", "CRITICAL"),
    )
    if failures.filter(created_at__gte=snapshot.started_at).exists():
        return
    if (
        failures.filter(level="CRITICAL")
        .exclude(pk__in=OperationalLogReceipt.objects.values("log_id"))
        .exists()
    ):
        return
    # A looser configured threshold cannot make a pre-outage read into new
    # successful evidence. Staleness is sampled directly, unlike delayed logs.
    record_recovery(IncidentKind.SOURCE_STALE, healthy_since=snapshot.promoted_at)
    for kind in FAILURE_KINDS:
        if kind is IncidentKind.SOURCE_DESTRUCTIVE_CHANGE and not _full_recovery_proof(
            snapshot, failures
        ):
            continue
        record_recovery(kind)


def _full_recovery_proof(snapshot, failures):
    """Delta success cannot certify recovery of rejected full-corpus loss.

    A subsequent delta may retain proof of a newer successful full refresh. Use
    that immutable full anchor so delayed collection does not miss recovery just
    because a delta has since become current.
    """
    full = SourceSnapshot.objects.filter(
        pk=snapshot.cursor["full_snapshot_id"],
        kind="full",
        state="promoted",
        organization_id=snapshot.organization_id,
        cursor__window_digest=snapshot.cursor["window_digest"],
    ).first()
    return (
        full is not None
        and not failures.filter(
            event=Event.SOURCE_DESTRUCTIVE_CHANGE, created_at__gte=full.started_at
        ).exists()
    )
