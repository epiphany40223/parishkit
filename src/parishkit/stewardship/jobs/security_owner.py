"""The security alert owner: recorded recipients, never current grants."""

from uuid import UUID

from parishkit.stewardship.accounts.configuration_models import (
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.operational_delivery_process import submit_security_mail

from .alert_owner import AlertOwner
from .family_mail_dispatch import FamilyDeliveryHeld
from .models import TaskRun
from .operational_routing import current_routing
from .outbox_validation import mailbox
from .security_models import SecurityCohort, SecurityRecipient
from .security_routing import current_mail, event_alert, mail_render

TASK_TYPE = "security_prepare"
NAMESPACE = UUID("2f5c0a6e-6d55-4c0e-9d29-3f1d8c9b4a71")


def _configured():
    """A parish and an email channel are all a security alert needs to route."""
    version = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    return (
        version is not None
        and Parish.objects.filter(configuration_id=version).exists()
        and AppliedIntegration.objects.filter(
            configuration_id=version, kind="email"
        ).exists()
    )


def _recipients(event_id):
    """The event's recorded recipients, sorted, each a valid mailbox, or none."""
    recorded = PolicySecurityEvent.objects.values_list("recipients", flat=True).get(
        pk=event_id
    )
    addresses = sorted(set(recorded))
    for address in addresses:
        mailbox(address)
    return tuple(addresses)


def _pending(limit):
    """Events without a preparation Task yet; one with nobody to tell is skipped."""
    owned = TaskRun.objects.filter(task_type=TASK_TYPE).values("domain_request_id")
    return tuple(
        PolicySecurityEvent.objects.exclude(pk__in=owned)
        .exclude(recipients=[])
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )


def _source_exists(event_id):
    """Only a recorded event with recipients may be prepared."""
    return (
        PolicySecurityEvent.objects.filter(pk=event_id).exclude(recipients=[]).exists()
    )


def _capture(row, claim, store):
    """Freeze the event's recorded recipients under the current routing."""
    route = current_routing(store)
    if route.sender is None or route.reply_to is None:
        raise FamilyDeliveryHeld("Security alert mail channel is not configured.")
    addresses = _recipients(row.domain_request_id)
    if not addresses:
        raise FamilyDeliveryHeld("Security alert has no recorded recipient.")
    return SecurityCohort.objects.create(
        event_id=row.domain_request_id,
        configuration_id=route.configuration_id,
        parish=Parish.objects.get(configuration_id=route.configuration_id),
        mode=route.mode.value,
        addresses=list(addresses),
        recipient_count=len(addresses),
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )


def _recipient_current(message):
    """A recorded recipient is never revoked: it is current while its cohort names it.

    The check exists so the shared dispatch contract has one answer per owner.
    """
    recipient = (
        SecurityRecipient.objects.select_related("cohort")
        .only("address", "cohort__addresses")
        .get(outbox_id=message.pk)
    )
    return recipient.address in recipient.cohort.addresses


SECURITY = AlertOwner(
    purpose="security_event",
    label="Security alert",
    task_type=TASK_TYPE,
    namespace=NAMESPACE,
    source_field="event_id",
    cohort_model=SecurityCohort,
    recipient_model=SecurityRecipient,
    cohort_table="stewardship_security_cohort",
    recipient_table="stewardship_security_recipient",
    configured=_configured,
    pending_sources=_pending,
    source_exists=_source_exists,
    capture=_capture,
    alert=lambda cohort, mode: event_alert(cohort.event_id, SystemMode(mode)),
    mail_render=mail_render,
    current_mail=lambda store, cohort, address, semantic_key: current_mail(
        store, event_id=cohort.event_id, address=address, semantic_key=semantic_key
    ),
    recipient_current=_recipient_current,
    submit=submit_security_mail,
)
