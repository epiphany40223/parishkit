"""Production recipient refusals remain Family-scoped across source promotions.

No mail is sent here. Refusal creation requires an already recorded, definitive
Production address-failure event. Source correction appends resolution in the
same work transaction as Family eligibility; neither Testing failures nor census
email preferences can create refusals.
"""

from uuid import UUID

from django.db.models import BooleanField
from django.db.models.expressions import RawSQL

from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.families import FamilySuppressions
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.storage import StorageInvariantError

from .outbox_models import OutboxEvent
from .recipient_models import RecipientRefusal, RecipientRefusalResolution

_SOURCE_BATCH_SIZE = 500


def record_refusal(*, event_id, address, actor_id, correlation_id):
    """Append an idempotent refusal against exact immutable provider evidence.

    The caller owns delivery fencing and its transaction. SQL independently
    verifies that this address was routed for a definitive Production refusal;
    this internal function has no web or generic worker write grant.
    Attribution must match the immutable event's actor, including on replay;
    a later reconciler cannot relabel the original refusal as its own action.
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
        or event.actor_id != actor_id
        or address not in event.render.routed_recipients
        or address not in event.render.intended_recipients
    ):
        raise PermissionError(
            "Recipient refusal requires definitive delivery evidence."
        )
    previous = RecipientRefusal.objects.filter(
        event_id=event_id, address=address
    ).first()
    if previous is not None:
        return previous
    family = FamilyCampaign.objects.get(pk=event.message.family_id)
    population = CampaignCredentialState.objects.get(campaign_id=family.campaign_id)
    organization_id = SourceSnapshot.objects.get(
        pk=population.source_snapshot_id
    ).organization_id
    return RecipientRefusal.objects.create(
        event_id=event_id,
        family_id=event.message.family_id,
        organization_id=organization_id,
        family_duid=family.family_duid,
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
    require_work_order()
    if scope.campaign is None:
        raise StorageInvariantError("Recipient suppression requires campaign scope.")
    current = SourceCurrent.objects.select_related("snapshot").get(singleton=True)
    if current.snapshot_id is None or current.snapshot.state != "promoted":
        raise StorageInvariantError("Recipient suppression requires promoted source.")
    # Stable organization/DUID scope deliberately survives annual Campaign rows.
    # The new campaign's population may not yet exist during its first promotion.
    refusals = (
        RecipientRefusal.objects.filter(organization_id=current.organization_id)
        .exclude(pk__in=RecipientRefusalResolution.objects.values("refusal_id"))
        .annotate(
            present=RawSQL(
                "stewardship_refusal_address_present_v1(%s,family_duid,address)",
                (current.snapshot_id,),
                output_field=BooleanField(),
            )
        )
    )
    entries, resolutions = set(), []
    for refusal in refusals.iterator(chunk_size=_SOURCE_BATCH_SIZE):
        if refusal.present:
            entries.add((refusal.family_duid, refusal.address))
        else:
            resolutions.append(
                RecipientRefusalResolution(
                    refusal_id=refusal.pk,
                    source_snapshot_id=current.snapshot_id,
                    source_generation=current.generation,
                    actor_id=current.snapshot.actor_id,
                    correlation_id=current.snapshot.correlation_id,
                )
            )
        if len(resolutions) == _SOURCE_BATCH_SIZE:
            RecipientRefusalResolution.objects.bulk_create(resolutions)
            resolutions.clear()
    if resolutions:
        RecipientRefusalResolution.objects.bulk_create(resolutions)
    return FamilySuppressions(frozenset(entries))
