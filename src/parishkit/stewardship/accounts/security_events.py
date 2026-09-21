"""High-impact login-policy expansions awaiting an Administrator's acknowledgement.

The activation trigger records each expansion the specification names, an
Administrator added to an exact address, a hosted-domain rule created or Staff
added to one, as a durable security event carrying the Administrators who
existed before it. This module decides which of those events an
Administrator's dashboard still shows and records the acknowledgement that
clears one, audited; the operational email is a separate delivery.
"""

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action

from .policy_models import (
    PolicySecurityAcknowledgement,
    PolicySecurityEvent,
    PortalUser,
)
from .policy_schema import normalized_email
from .user_rows import role_labels

KINDS = {
    "administrator_granted": "Administrator added to an exact address",
    "domain_created": "Hosted-domain rule created",
    "domain_staff_granted": "Staff added to a hosted-domain rule",
}


def cleared(event, acknowledgements, *, viewer_id):
    """Whether the event has left this viewer's dashboard.

    Any Administrator other than the granting actor clears it for everyone.
    The granting actor's own acknowledgement clears it for that actor alone,
    and for everyone only when no other Administrator existed at activation.
    """
    for acknowledgement in acknowledgements:
        if acknowledgement.actor_id != event.actor_id:
            return True
        if viewer_id == event.actor_id or not (
            set(event.recipients) - {acknowledgement.email}
        ):
            return True
    return False


def open_events(viewer):
    """The events still shown to this Administrator, newest first."""
    others = PolicySecurityAcknowledgement.objects.filter(event=OuterRef("pk")).exclude(
        actor_id=OuterRef("actor_id")
    )
    # Cleared for everyone once someone other than the actor acknowledged;
    # the actor's own acknowledgement is judged per viewer below.
    events = list(
        PolicySecurityEvent.objects.annotate(settled=Exists(others))
        .filter(settled=False)
        .prefetch_related("acknowledgements")
        .order_by("-created_at", "-id")
    )
    actors = dict(
        PortalUser.objects.filter(
            pk__in={event.actor_id for event in events if event.actor_id}
        ).values_list("pk", "email")
    )
    rows = []
    for event in events:
        acknowledgements = list(event.acknowledgements.all())
        if cleared(event, acknowledgements, viewer_id=viewer.identity):
            continue
        rows.append(
            {
                "id": event.pk,
                "kind": event.kind,
                "label": KINDS.get(event.kind, event.kind),
                "target": event.target,
                "before": role_labels(event.before_roles),
                "after": role_labels(event.after_roles),
                "created_at": event.created_at,
                "actor": actors.get(event.actor_id),
                "acknowledged": any(
                    item.actor_id == viewer.identity for item in acknowledgements
                ),
            }
        )
    return rows


def acknowledge(event_id, actor, *, parish_id):
    """Record this Administrator's acknowledgement once, with its audit.

    Returns whether a new acknowledgement was recorded. A repeat, including one
    racing another request from the same Administrator, records nothing more.
    """
    event = PolicySecurityEvent.objects.filter(pk=event_id).first()
    if event is None:
        raise LookupError("Security event is unavailable.")
    email = normalized_email(
        PortalUser.objects.values_list("email", flat=True).get(pk=actor.identity)
    )
    with transaction.atomic():
        try:
            with transaction.atomic():
                PolicySecurityAcknowledgement.objects.create(
                    event=event, email=email, actor_id=actor.identity
                )
        except IntegrityError:
            return False
        record_action(
            Action.SECURITY_EVENT_ACKNOWLEDGED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=event.pk,
            parish_id=parish_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
    return True
