"""Current operational recipients and fixed content, without provider authority.

Preparation captures a cohort; dispatch must call the current reader again.
These values are not permits and cannot bypass durable submission ownership.
Campaign gates and Testing redirection deliberately do not apply to this owner.
"""

from dataclasses import dataclass
from uuid import UUID

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.operational_delivery import (
    OperationalMail,
    OperationalSlack,
)
from parishkit.stewardship.runtime_background import mail_authority

from .operational_content import (
    AlertPhase,
    IncidentKind,
    IncidentLevel,
    OperationalAlert,
    render_alert,
)
from .operational_models import OperationalNotice
from .outbox_validation import RenderInput


@dataclass(frozen=True, repr=False)
class OperationalRouting:
    """Private exact addresses from one coherent configuration, not a send permit."""

    configuration_id: UUID
    mode: SystemMode
    admins: tuple[str, ...]
    sender: str | None
    reply_to: str | None
    slack_channel: str | None


def current_routing(store):
    """Read exact Admin grants under work ownership and file/SQL coherence.

    Independent optional channel settings allow Slack to remain usable when email
    is not configured. Neither a campaign, Production activation nor restore release
    is required. No old configuration or caller-supplied address is a fallback.
    """
    require_work_order()
    runtime = mail_authority(store)
    version = runtime.active_configuration_id
    admins = tuple(
        AddressRule.objects.filter(
            configuration_id=version, roles__contains=["administrator"]
        )
        .order_by("email")
        .values_list("email", flat=True)
    )
    channels = {
        item.kind: item.settings
        for item in AppliedIntegration.objects.filter(
            configuration_id=version, kind__in=("email", "slack")
        ).only("kind", "settings")
    }
    email = channels.get("email", {})
    return OperationalRouting(
        version,
        SystemMode(runtime.mode),
        admins,
        email.get("sender"),
        email.get("reply_to"),
        channels.get("slack", {}).get("channel_id"),
    )


def notice_alert(notice_id, mode):
    """Reload immutable facts by opaque identity; never accept a caller's prose."""
    require_work_order()
    if not isinstance(notice_id, UUID) or not isinstance(mode, SystemMode):
        raise TypeError("Operational content requires a typed notice and mode.")
    notice = OperationalNotice.objects.select_related("incident").get(pk=notice_id)
    return OperationalAlert(
        notice.incident_id,
        IncidentKind(notice.incident.kind),
        IncidentLevel(notice.level),
        AlertPhase(notice.phase),
        mode,
        notice.first_seen,
        notice.observed_at,
        notice.occurrences,
    )


def current_mail(store, *, notice_id, address, semantic_key):
    """Recheck this exact Admin and rebuild current-mode content and audit render."""
    route = current_routing(store)
    if address not in route.admins or route.sender is None or route.reply_to is None:
        raise PermissionError("Operational mail recipient or channel is unavailable.")
    alert = notice_alert(notice_id, route.mode)
    return mail_render(
        configuration_id=route.configuration_id,
        sender=route.sender,
        reply_to=route.reply_to,
        address=address,
        semantic_key=semantic_key,
        alert=alert,
    )


def mail_render(*, configuration_id, sender, reply_to, address, semantic_key, alert):
    """Compile a journal envelope; neither retained nor current input is authority."""
    content = render_alert(alert)
    mail = OperationalMail(semantic_key, sender, reply_to, (address,), alert)
    render = RenderInput(
        configuration_id=configuration_id,
        template_id=None,
        sender=sender,
        reply_to=reply_to,
        intended_recipients=(address,),
        routed_recipients=(address,),
        subject=content.subject,
        html=content.html,
        text=content.text,
    )
    return mail, render


def current_slack(store, *, notice_id, delivery_id):
    """Use only the current configured channel, without any email dependency."""
    route = current_routing(store)
    if route.slack_channel is None:
        raise PermissionError("Operational Slack channel is unavailable.")
    return OperationalSlack(
        delivery_id, route.slack_channel, notice_alert(notice_id, route.mode)
    )
