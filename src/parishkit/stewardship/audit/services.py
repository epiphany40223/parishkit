"""Transaction-bound audited actions; identity/approval is always rechecked."""

from django.db import connection, transaction

from parishkit.stewardship.observability import Event
from parishkit.stewardship.storage import StorageInvariantError

from .models import AuditContext, AuditEvent, OperationalLog
from .schemas import Action, ActorKind, ContextKind, sanitize


def record_action(
    action,
    *,
    actor_kind,
    actor_id=None,
    subject_id=None,
    parish_id=None,
    campaign_id=None,
    context=None,
):
    """Store only approved evidence in the same transaction as its domain effect."""
    if not connection.in_atomic_block:
        raise StorageInvariantError("Audited effects require their owning transaction.")
    if not isinstance(action, Action) or not isinstance(actor_kind, ActorKind):
        raise ValueError("Audit requires canonical action and actor types.")
    safe = sanitize(ContextKind.ACTION, {} if context is None else context)
    event = AuditEvent.objects.create(
        event_type=action.value,
        actor_id=actor_id,
        subject_id=subject_id,
        parish_id=parish_id,
        ownership_scope="parish" if parish_id else "deployment",
        campaign_reference=campaign_id,
    )
    AuditContext.objects.create(
        event=event,
        actor_id=actor_id,
        actor_kind=actor_kind.value,
        schema=ContextKind.ACTION.value,
        context=safe,
    )
    return event


def audited_effect(action, *, authorize, operation, **evidence):
    """Run within the owner's locks; a stale principal or failed effect saves nothing.

    This wrapper does not authorize by action name and is not a replacement for
    domain serialization. The supplied operation must not perform external I/O.
    """
    if (
        not connection.in_atomic_block
        or not callable(authorize)
        or not callable(operation)
    ):
        raise StorageInvariantError("Audited effects need locked owning admission.")
    with transaction.atomic():
        if authorize() is not True:
            raise PermissionError("Access is unavailable.")
        result = operation()
        record_action(action, **evidence)
        return result


def operational(event, *, level="INFO", schema=ContextKind.EXCEPTION, context=None):
    """Retain searchable safe diagnostics without exception or provider payloads."""
    if not isinstance(event, Event) or level not in {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }:
        raise ValueError("Operational event and level must be approved values.")
    safe = sanitize(schema, {} if context is None else context)
    return OperationalLog.objects.create(
        event=event.value,
        level=level,
        schema=schema.value,
        context=safe,
    )
