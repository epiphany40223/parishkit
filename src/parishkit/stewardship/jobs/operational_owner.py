"""The operational alert owner: current Administrators, rechecked at send."""

from uuid import UUID

from parishkit.stewardship.accounts.configuration_models import (
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.domain import SystemMode

from .alert_owner import AlertOwner
from .family_mail_dispatch import FamilyDeliveryHeld
from .models import TaskRun
from .operational_models import (
    OperationalCohort,
    OperationalNotice,
    OperationalRecipient,
)
from .operational_routing import current_mail, notice_alert

TASK_TYPE = "operational_prepare"
NAMESPACE = UUID("fed3b97f-3692-4672-a7d1-f559a0fa71b1")


def _configured():
    """Before initial setup supplies routing, retain notices without failed jobs."""
    version = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    return (
        version is not None
        and Parish.objects.filter(configuration_id=version).exists()
        and AddressRule.objects.filter(
            configuration_id=version, roles__contains=["administrator"]
        ).exists()
        and AppliedIntegration.objects.filter(
            configuration_id=version, kind="email"
        ).exists()
    )


def _pending(limit):
    """Notices not yet owned by a preparation Task, oldest first."""
    owned = TaskRun.objects.filter(task_type=TASK_TYPE).values("domain_request_id")
    return tuple(
        OperationalNotice.objects.exclude(pk__in=owned)
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )


# The routing reader, the envelope compiler and the private submitter are
# looked up on their engine modules at call time: those module names are the
# seams the operational suites replace to stand in for a provider or a race.


def _mail_render(**kwargs):
    """Compile through the fanout module's name."""
    from . import operational_fanout

    return operational_fanout.mail_render(**kwargs)


def _submit(*args, **kwargs):
    """Submit through the MAIL module's name."""
    from . import operational_mail_tasks

    return operational_mail_tasks.submit_operational_mail(*args, **kwargs)


def _capture(row, claim, store):
    """Freeze the current Administrators under the current routing."""
    from . import operational_fanout

    route = operational_fanout.current_routing(store)
    if route.sender is None or route.reply_to is None or not route.admins:
        raise FamilyDeliveryHeld("Operational routing is not configured yet.")
    return OperationalCohort.objects.create(
        notice_id=row.domain_request_id,
        configuration_id=route.configuration_id,
        parish=Parish.objects.get(configuration_id=route.configuration_id),
        mode=route.mode.value,
        addresses=list(route.admins),
        recipient_count=len(route.admins),
        slack_channel=route.slack_channel,
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )


def _recipient_current(message):
    """Only the private mail process needs the exact address reauthorization."""
    address = (
        OperationalRecipient.objects.only("address").get(outbox_id=message.pk).address
    )
    version = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).get()
    return AddressRule.objects.filter(
        configuration_id=version, email=address, roles__contains=["administrator"]
    ).exists()


OPERATIONAL = AlertOwner(
    purpose="operational",
    label="Operational",
    task_type=TASK_TYPE,
    namespace=NAMESPACE,
    source_field="notice_id",
    cohort_model=OperationalCohort,
    recipient_model=OperationalRecipient,
    cohort_table="stewardship_ops_cohort",
    recipient_table="stewardship_ops_recipient",
    configured=_configured,
    pending_sources=_pending,
    source_exists=lambda notice_id: OperationalNotice.objects.filter(
        pk=notice_id
    ).exists(),
    capture=_capture,
    alert=lambda cohort, mode: notice_alert(cohort.notice_id, SystemMode(mode)),
    mail_render=_mail_render,
    current_mail=lambda store, cohort, address, semantic_key: current_mail(
        store, notice_id=cohort.notice_id, address=address, semantic_key=semantic_key
    ),
    recipient_current=_recipient_current,
    submit=_submit,
)
