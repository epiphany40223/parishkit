"""Production recipient refusals remain Family-scoped across source promotions.

No mail is sent here. Refusal creation requires an already recorded, definitive
Production address-failure event. Source correction appends resolution in the
same work transaction as Family eligibility; neither Testing failures nor census
email preferences can create refusals.
"""

from uuid import UUID

from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.families import FamilySuppressions
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StorageInvariantError

from .outbox_models import OutboxEvent
from .recipient_models import RecipientRefusal, RecipientRefusalResolution


def record_refusal(*, event_id, address, actor_id, correlation_id):
    """Append an idempotent refusal against exact immutable provider evidence.

    The caller owns delivery fencing and its transaction. SQL independently
    verifies that this address was routed for a definitive Production refusal;
    this internal function has no web or generic worker write grant.
    """
    require_work_order()
    if any(
        not isinstance(value, UUID) for value in (event_id, actor_id, correlation_id)
    ):
        raise TypeError("Recipient refusal requires attributed delivery identities.")
    if normalized_email(address) != address:
        raise ValueError("Refusal address must be canonical.")
    event = OutboxEvent.objects.select_related("message", "render").get(pk=event_id)
    if (
        event.state != "permanent_failure"
        or event.reason != "recipient_refused"
        or event.message.mode != "production"
        or event.message.routing != "production"
        or event.message.family_id is None
        or address not in event.render.routed_recipients
    ):
        raise PermissionError(
            "Recipient refusal requires definitive delivery evidence."
        )
    previous = RecipientRefusal.objects.filter(
        event_id=event_id, address=address
    ).first()
    if previous is not None:
        return previous
    return RecipientRefusal.objects.create(
        event_id=event_id,
        family_id=event.message.family_id,
        address=address,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )


def source_suppressions(scope):
    """Read current refusals and resolve corrected source values under promotion.

    A Family or Member becoming inactive does not itself correct an email value.
    The SQL predicate examines current member contact values, not only currently
    eligible heads, so reactivation cannot silently reset a known refusal.
    Retained manifests and refusal/resolution records provide durable provenance.
    """
    from django.db import connection

    require_work_order()
    if scope.campaign is None:
        raise StorageInvariantError("Recipient suppression requires campaign scope.")
    current = SourceCurrent.objects.select_related("snapshot").get(singleton=True)
    if current.snapshot_id is None or current.snapshot.state != "promoted":
        raise StorageInvariantError("Recipient suppression requires promoted source.")
    families = dict(
        FamilyCampaign.objects.filter(campaign_id=scope.campaign.pk).values_list(
            "id", "family_duid"
        )
    )
    refusals = RecipientRefusal.objects.filter(family_id__in=families).exclude(
        pk__in=RecipientRefusalResolution.objects.values("refusal_id")
    )
    entries = set()
    for refusal in refusals.iterator(chunk_size=500):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_refusal_address_present_v1(%s,%s,%s)",
                (current.snapshot_id, refusal.family_id, refusal.address),
            )
            present = cursor.fetchone()[0]
        if present:
            entries.add((families[refusal.family_id], refusal.address))
        else:
            RecipientRefusalResolution.objects.create(
                refusal_id=refusal.pk,
                source_snapshot_id=current.snapshot_id,
                source_generation=current.generation,
                actor_id=current.snapshot.actor_id,
                correlation_id=current.snapshot.correlation_id,
            )
    return FamilySuppressions(frozenset(entries))
