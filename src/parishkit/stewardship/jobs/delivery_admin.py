"""Current Admin authority and verified recipient clearance; never provider IO."""

from uuid import UUID, uuid4

from django.db import connection

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from .recipient_models import RecipientRefusalResolution


def authorize(store, user_id):
    """Re-read coherent current policy, including on an idempotent command replay."""
    try:
        principal = current_principal(store, user_id)
    except PortalUser.DoesNotExist:
        raise PermissionError("Delivery administration is unavailable.") from None
    if not allows(principal, Capability.BACKGROUND_WORK):
        raise PermissionError("Delivery administration is unavailable.")
    return principal


def evidence_note(value):
    """Bound private evidence without interpolating it in errors or diagnostics."""
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 2000
        or "\x00" in value
    ):
        raise ValueError("A bounded verification note is required.")
    return value


def clear_recipient_refusal(
    store,
    user_id,
    *,
    refusal_id,
    command_id,
    source_snapshot_id,
    source_generation,
    note,
    verified,
):
    """Append verified clearance and recalculate deliverability in one transaction.

    The unique refusal identity prevents parallel contradictory resolutions.
    A replay must match the original command exactly and still have current
    Admin authority. PostgreSQL independently checks policy/source/gates and
    creates the audit plus eligibility edge; web receives no Family UPDATE grant.
    """
    if any(
        not isinstance(value, UUID)
        for value in (user_id, refusal_id, command_id, source_snapshot_id)
    ):
        raise ValueError("Verification requires canonical identities.")
    if (
        verified is not True
        or type(source_generation) is not int
        or not 1 <= source_generation <= 2**63 - 1
    ):
        raise ValueError("Verification and a current source version are required.")
    note = evidence_note(note)
    with work_transaction():
        authorize(store, user_id)
        intent = dict(
            refusal_id=refusal_id,
            actor_id=user_id,
            source_snapshot_id=source_snapshot_id,
            source_generation=source_generation,
            reason="verified_admin",
            evidence_note=note,
        )
        previous = RecipientRefusalResolution.objects.filter(pk=command_id).first()
        if previous is not None:
            if any(
                getattr(previous, field) != value for field, value in intent.items()
            ):
                raise ValueError("Verification command is already bound.")
            return previous
        if RecipientRefusalResolution.objects.filter(refusal_id=refusal_id).exists():
            raise StaleRecordError("This refusal has already been resolved.")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM stewardship_source_current "
                "WHERE snapshot_id=%s AND generation=%s)",
                (source_snapshot_id, source_generation),
            )
            if cursor.fetchone() != (True,):
                raise StaleRecordError("Source data changed; review the refusal again.")
        return RecipientRefusalResolution.objects.create(
            id=command_id, correlation_id=uuid4(), **intent
        )
