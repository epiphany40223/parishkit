"""High-impact login-policy expansions awaiting an Administrator's acknowledgement.

The activation trigger records each expansion the specification names, an
Administrator added to an exact address, a hosted-domain rule created or Staff
added to one, as a durable security event carrying the addresses of the
Administrators who existed before it. This module decides which of those
events an Administrator's dashboard still shows and records the
acknowledgement that clears one, audited; the operational email is a separate
delivery.
"""

from django.contrib.postgres.expressions import ArraySubquery
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.db.models.functions import JSONObject

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


def cleared(event, acknowledgements, *, viewer_email):
    """Whether the event has left the dashboard of the Administrator at this address.

    An acknowledgement by an Administrator who existed at activation, one of
    the event's recipients other than the granting actor, settles the event
    for everyone. The actor's own settles it only when no other Administrator
    existed at activation: the actor of a portal-driven activation is always
    among the recipients, so that is a recipient list of one. An event with
    nobody else to await, a deployment's root activation or one whose
    predecessor named no Administrator, is settled by any acknowledgement.
    Any other acknowledgement, the actor's, a newer Administrator's or the
    granted account's own, clears the event for that address alone, so a
    grant cannot be waved through by its beneficiary: a recovery event names
    the account it grants among its recipients, since that account must be
    told, but it was not an Administrator at activation and settles nothing.
    """
    recipients = set(event.recipients) - {event.target}
    for acknowledgement in acknowledgements:
        if acknowledgement["email"] == viewer_email or not recipients:
            return True
        if acknowledgement["email"] == event.target:
            continue
        if acknowledgement["own"]:
            if len(recipients) <= 1:
                return True
        elif acknowledgement["email"] in recipients:
            return True
    return False


def open_events(viewer):
    """The events still shown to this Administrator, newest first.

    One query: every expansion ever activated is one row, and only an
    acknowledged one needs judging, so the acknowledgements, the granting
    actor's address and the viewer's own address ride along as subqueries.
    The Admin page has a fixed query budget, and an expansion is recorded
    rarely, never per request.
    """
    address = PortalUser.objects.filter(pk=OuterRef("actor_id")).values("email")[:1]
    viewer_address = PortalUser.objects.filter(pk=viewer.identity).values("email")[:1]
    acknowledgements = PolicySecurityAcknowledgement.objects.filter(
        event=OuterRef("pk")
    ).values(item=JSONObject(email="email", own="own"))
    events = PolicySecurityEvent.objects.annotate(
        actor_address=Subquery(address),
        viewer_address=Subquery(viewer_address),
        acknowledged_by=ArraySubquery(acknowledgements),
    ).order_by("-created_at", "-id")
    rows = []
    for event in events:
        viewer_email = normalized_email(event.viewer_address)
        if cleared(event, event.acknowledged_by, viewer_email=viewer_email):
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
                "actor": event.actor_address,
            }
        )
    return rows


def acknowledge(event_id, actor, *, parish_id):
    """Record this Administrator's acknowledgement once, with its audit.

    Returns whether a new acknowledgement was recorded. A repeat from the same
    address, including one racing another request or made through a second
    Google identity, records nothing more. The acknowledgement is the granting
    actor's own when it comes from the actor's identity or the actor's current
    address, decided here so that a later change of address cannot turn it
    into another Administrator's.
    """
    event = PolicySecurityEvent.objects.filter(pk=event_id).first()
    if event is None:
        raise LookupError("Security event is unavailable.")
    email = normalized_email(
        PortalUser.objects.values_list("email", flat=True).get(pk=actor.identity)
    )
    own = actor.identity == event.actor_id
    if not own and event.actor_id is not None:
        address = (
            PortalUser.objects.filter(pk=event.actor_id)
            .values_list("email", flat=True)
            .first()
        )
        own = address is not None and normalized_email(address) == email
    with transaction.atomic():
        try:
            with transaction.atomic():
                PolicySecurityAcknowledgement.objects.create(
                    event=event, email=email, own=own, actor_id=actor.identity
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
