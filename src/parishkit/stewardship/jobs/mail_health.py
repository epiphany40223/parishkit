"""Provider evidence owns alerts; delivery cooldowns never establish recovery."""

from django.db import connection
from django.db.models import Q

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.family_delivery import ProviderHealth
from parishkit.stewardship.observability import Event

from .family_mail_results import event_result
from .operational_content import IncidentKind
from .operational_models import OperationalIncident, OperationalLogReceipt
from .operational_storage import record_recovery
from .outbox_models import OutboxEvent

OBSERVED = (ProviderHealth.HEALTHY, ProviderHealth.UNAVAILABLE, ProviderHealth.SYSTEMIC)


def health_filter(values):
    """Select canonical result prefixes, then independently validate fetched facts.

    result_evidence uses sorted compact JSON, making health its first member.
    Avoid casting arbitrary historical/manual evidence text to JSON in SQL.
    A matching prefix is only a query bound: event_result still verifies the
    protocol, complete typed outcome and its immutable evidence digests.
    """
    query = Q(pk__in=[])
    for value in values:
        query |= Q(evidence_note__startswith='{"health":"' + value.value + '",')
    return query


def provider_identity(configuration_id):
    """Use the same SQL-derived transport identity as the immutable result owner."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_mail_provider_identity_v1(%s)", [configuration_id]
        )
        return cursor.fetchone()[0]


def observed_results(configuration_id):
    """Read only completed, typed SMTP evidence for the same transport identity."""
    identity = provider_identity(configuration_id)
    if not identity:
        return OutboxEvent.objects.none()
    return (
        OutboxEvent.objects.filter(
            previous_state="submitting",
            reason__in=(
                "smtp_accepted",
                "smtp_transient",
                "smtp_unavailable",
                "smtp_permanent",
                "smtp_delivery_unknown",
                "smtp_systemic",
            ),
            provider_identity=identity,
            submitted_at__isnull=False,
        )
        .filter(health_filter(OBSERVED))
        .select_related("render", "message")
        .order_by("-created_at", "-pk")
    )


def needs_mail_observation():
    """Metadata-only scheduling remains available during source admission holds."""
    require_work_order()
    return OperationalIncident.objects.filter(
        kind=IncidentKind.MAIL_PROVIDER_UNAVAILABLE, resolved_at__isnull=True
    ).exists()


def observe_mail_health():
    """Recover only from actual current-provider proof after every failed read.

    Outcome settlement and this sampler share the work-order lock. Operational
    delivery failures can veto recovery, but cannot originate another alert.
    Delayed critical intake must finish before resolving the corresponding episode.
    """
    require_work_order()
    if not OperationalIncident.objects.filter(
        kind=IncidentKind.MAIL_PROVIDER_UNAVAILABLE, resolved_at__isnull=True
    ).exists():
        return
    configuration = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    results = observed_results(configuration)
    healthy = results.filter(health_filter((ProviderHealth.HEALTHY,))).first()
    if healthy is None or event_result(healthy).health is not ProviderHealth.HEALTHY:
        return
    failed = results.filter(
        health_filter((ProviderHealth.UNAVAILABLE, ProviderHealth.SYSTEMIC)),
        created_at__gte=healthy.submitted_at,
    ).first()
    if failed is not None:
        event_result(failed)
        return
    critical = OperationalLog.objects.filter(
        event=Event.MAIL_PROVIDER_FAILED, level="CRITICAL"
    )
    if (
        critical.filter(created_at__gte=healthy.submitted_at).exists()
        or critical.exclude(
            pk__in=OperationalLogReceipt.objects.values("log_id")
        ).exists()
    ):
        return
    record_recovery(IncidentKind.MAIL_PROVIDER_UNAVAILABLE)
