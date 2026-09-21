"""Security alert recipients and fixed content, without provider authority.

Preparation copies the event's recorded recipients into a cohort; dispatch
rebuilds the current content and checks the address is still one of them.
A recorded recipient is never revoked: an Administrator who existed before
the expansion is told even if their grant was removed since.
"""

from uuid import UUID

from parishkit.stewardship.accounts.policy_models import (
    PolicySecurityEvent,
    PortalUser,
)
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.security_delivery import SecurityMail

from .family_mail_dispatch import FamilyDeliveryHeld
from .operational_routing import current_routing
from .outbox_validation import RenderInput
from .security_content import SecurityAlert, render_security_alert


def event_alert(event_id, mode):
    """Reload immutable facts by opaque identity; never accept a caller's prose."""
    require_work_order()
    if not isinstance(event_id, UUID) or not isinstance(mode, SystemMode):
        raise TypeError("Security content requires a typed event and mode.")
    event = PolicySecurityEvent.objects.get(pk=event_id)
    actor = (
        PortalUser.objects.filter(pk=event.actor_id)
        .values_list("email", flat=True)
        .first()
    )
    return SecurityAlert(
        event.pk,
        event.kind,
        event.target,
        actor,
        mode,
        event.created_at,
        tuple(event.before_roles),
        tuple(event.after_roles),
    )


def current_mail(store, *, event_id, address, semantic_key):
    """Recheck the recorded recipient and rebuild current-mode content."""
    route = current_routing(store)
    if route.sender is None or route.reply_to is None:
        raise FamilyDeliveryHeld("Security alert mail channel is not configured.")
    recipients = PolicySecurityEvent.objects.values_list("recipients", flat=True).get(
        pk=event_id
    )
    if address not in recipients:
        raise PermissionError("Security alert recipient is not recorded.")
    alert = event_alert(event_id, route.mode)
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
    content = render_security_alert(alert)
    mail = SecurityMail(semantic_key, sender, reply_to, (address,), alert)
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
