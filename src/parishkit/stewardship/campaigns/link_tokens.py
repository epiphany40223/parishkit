"""Resumable sealed-token preparation with epoch, input and coverage fencing.

BG-02 supplies task/admission ownership and source snapshot validation. No
service here sends mail, changes a campaign date or directly selects a Campaign
generation pointer. Activation consumes the prepared manifest inside the owning
guarded lifecycle command.
"""

from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    new_token,
    token_digest,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .credential_keys import inventory_digest, key_set_lock
from .credential_models import (
    CampaignCredentialState,
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyAccessTokenGeneration,
    FamilyCampaign,
)
from .models import Campaign
from .runtime import _now


def token_context(identifier):
    """A sealed token cannot be transplanted to another retained token record."""
    if not isinstance(identifier, UUID):
        raise TypeError("Token identity must be a UUID.")
    return b"family-link-token-v1:" + identifier.bytes


def coverage(campaign_id):
    """Population records this exact manifest atomically; final admission is O(1)."""
    return CampaignCredentialState.objects.select_for_update().get(
        campaign_id=campaign_id
    )


def _current_inputs(generation, campaign, deployment, public):
    """Invalidated or stale prepared generations never regain access after restore."""
    if generation.credential_epoch != deployment.family_link_epoch:
        raise StaleRecordError(
            "Token preparation belongs to an earlier credential epoch."
        )
    if (
        generation.configuration_id != campaign.active_configuration_id
        or generation.key_inventory_digest != inventory_digest(public)
    ):
        raise StaleRecordError(
            "Token preparation inputs changed; prepare a new revision."
        )
    population = coverage(campaign.pk)
    if (
        population.population_dirty
        or population.source_generation != generation.source_generation
        or population.source_snapshot_id != generation.source_snapshot_id
    ):
        raise StaleRecordError("Token preparation source coverage changed.")
    return population


def begin_generation(
    *,
    campaign_id,
    operation_id,
    source_snapshot_id,
    source_generation,
    public,
    admit,
    actor_id,
    task_id=None,
    configuration_request_id=None,
):
    """Create/retry one inactive preparation identity; it cannot authenticate anyone."""
    if any(
        not isinstance(value, UUID)
        for value in (campaign_id, operation_id, source_snapshot_id)
    ) or not callable(admit):
        raise TypeError(
            "Token preparation requires canonical identities and owning admission."
        )
    if type(source_generation) is not int or source_generation < 1:
        raise ValueError("Token preparation requires a positive source generation.")
    with transaction.atomic(), key_set_lock(public):
        deployment = DeploymentCredentialState.objects.select_for_update().get()
        campaign = Campaign.objects.get(pk=campaign_id)
        CampaignCredentialState.objects.get_or_create(campaign=campaign)
        CampaignCredentialState.objects.select_for_update().get(campaign=campaign)
        admit(campaign, deployment, None)
        if configuration_request_id is not None:
            request = ConfigurationChangeRequest.objects.get(
                pk=configuration_request_id,
                base_id=campaign.active_configuration.configuration_id,
                authority="admin",
            )
            if (
                campaign.state != "closed"
                or len(request.patch) != 1
                or request.patch[0].get("operation") != "update"
                or request.patch[0].get("section") != "campaigns"
                or request.patch[0].get("id") != str(campaign.pk)
                or set(request.patch[0].get("values", {})) != {"end_date"}
            ):
                raise StorageInvariantError(
                    "Reopen preparation requires the exact proposed end-date request."
                )
        inputs = dict(
            campaign=campaign,
            credential_epoch=deployment.family_link_epoch,
            restore_id=deployment.restore_id,
            source_snapshot_id=source_snapshot_id,
            source_generation=source_generation,
            configuration=campaign.active_configuration,
            configuration_request_id=configuration_request_id,
            key_id=public.active.id,
            key_inventory_digest=inventory_digest(public),
            actor_id=actor_id,
            task_id=task_id,
        )
        existing = FamilyAccessTokenGeneration.objects.filter(
            operation_id=operation_id
        ).first()
        if existing:
            if any(getattr(existing, key) != value for key, value in inputs.items()):
                raise StorageInvariantError(
                    "Token operation identity already has different inputs."
                )
            return existing
        generation = FamilyAccessTokenGeneration.objects.create(
            operation_id=operation_id, **inputs
        )
        _current_inputs(generation, campaign, deployment, public)
        AuditEvent.objects.create(
            event_type="family_tokens_preparing",
            actor_id=actor_id,
            subject_id=generation.pk,
        )
        return generation


def prepare_generation_batch(*, generation_id, public, admit, batch_size=500):
    """Commit bounded ciphertext batches, checkpoint and final manifest atomically."""
    if (
        type(batch_size) is not int
        or not 1 <= batch_size <= 1000
        or not callable(admit)
    ):
        raise ValueError(
            "Token preparation requires bounded batches and owning admission."
        )
    with transaction.atomic(), key_set_lock(public):
        deployment = DeploymentCredentialState.objects.select_for_update().get()
        generation = (
            FamilyAccessTokenGeneration.objects.select_for_update()
            .select_related("campaign")
            .get(pk=generation_id)
        )
        campaign = generation.campaign
        CampaignCredentialState.objects.select_for_update().get(campaign=campaign)
        admit(campaign, deployment, generation)
        population = _current_inputs(generation, campaign, deployment, public)
        families = FamilyCampaign.objects.filter(
            campaign=campaign, portal_eligible=True
        )
        digest = population.eligibility_digest
        if generation.state == "ready":
            if (
                generation.coverage_digest != digest
                or generation.coverage_count != population.eligible_count
            ):
                raise StaleRecordError("Prepared token coverage changed.")
            return generation
        if generation.state != "building":
            raise StorageInvariantError("Token preparation is no longer building.")
        rows = list(
            families.filter(family_duid__gt=generation.checkpoint).order_by(
                "family_duid"
            )[:batch_size]
        )
        tokens = []
        for family in rows:
            identifier, value = uuid4(), new_token()
            tokens.append(
                FamilyAccessToken(
                    id=identifier,
                    family=family,
                    campaign=campaign,
                    generation=generation,
                    ciphertext=public.encrypt(
                        value.encode("ascii"), context=token_context(identifier)
                    ),
                    digest=token_digest(value, campaign.pk),
                )
            )
        FamilyAccessToken.objects.bulk_create(tokens)
        if rows:
            generation.checkpoint = rows[-1].family_duid
        generation.version += 1
        remaining = families.filter(family_duid__gt=generation.checkpoint).exists()
        if not remaining:
            actual = set(
                FamilyAccessToken.objects.filter(
                    generation=generation, destroyed_at__isnull=True
                ).values_list("family_id", flat=True)
            )
            if actual != set(families.values_list("id", flat=True)):
                raise StorageInvariantError("Prepared token manifest is incomplete.")
            generation.state = "ready"
            generation.coverage_digest, generation.coverage_count = (
                digest,
                population.eligible_count,
            )
            generation.completed_at = _now()
            AuditEvent.objects.create(
                event_type="family_tokens_ready",
                actor_id=generation.actor_id,
                subject_id=generation.pk,
            )
        generation.save()
        return generation


def verify_generation(generation_id, *, campaign, public):
    """Final owner calls under its campaign transaction before selecting the pointer.

    This only verifies the frozen manifest: it never allocates tokens or updates
    Family rows in the final confirmation transaction.
    """
    with key_set_lock(public):
        deployment = DeploymentCredentialState.objects.get()
        generation = FamilyAccessTokenGeneration.objects.select_for_update().get(
            pk=generation_id, campaign=campaign
        )
        population = _current_inputs(generation, campaign, deployment, public)
        if (
            generation.state != "ready"
            or generation.coverage_digest != population.eligibility_digest
            or generation.coverage_count != population.eligible_count
        ):
            raise StaleRecordError("Token generation is not ready for activation.")
        return generation


def cancel_generation(*, generation_id, admit):
    """Stop admission first; later bounded cleanup cannot resurrect cancelled work."""
    with transaction.atomic():
        row = (
            FamilyAccessTokenGeneration.objects.select_for_update()
            .select_related("campaign")
            .get(pk=generation_id)
        )
        admit(row.campaign, DeploymentCredentialState.objects.get(), row)
        if row.state in {"cancelled", "superseded"}:
            return row
        if row.campaign.active_token_generation_id == row.pk or row.state == "active":
            raise StorageInvariantError(
                "An active token generation cannot be cancelled."
            )
        row.state, row.version = "cancelled", row.version + 1
        row.save()
        AuditEvent.objects.create(
            event_type="family_tokens_cancelled",
            subject_id=row.pk,
            actor_id=row.actor_id,
        )
        return row


def scrub_generation(generation_id, *, batch_size=500):
    """Retry-safe cleanup only for terminal preparations, never selected live links."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Token cleanup requires a bounded batch size.")
    with transaction.atomic():
        row = (
            FamilyAccessTokenGeneration.objects.select_for_update()
            .select_related("campaign")
            .get(pk=generation_id)
        )
        if (
            row.state not in {"failed", "cancelled", "superseded"}
            or row.campaign.active_token_generation_id == row.pk
        ):
            raise StorageInvariantError("Token generation is not admitted for cleanup.")
        ids = list(
            FamilyAccessToken.objects.filter(generation=row, destroyed_at__isnull=True)
            .order_by("pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        FamilyAccessToken.objects.filter(pk__in=ids).update(
            ciphertext=None, digest=None, destroyed_at=_now(), version=F("version") + 1
        )
        return len(ids)


def rotate_token(*, token_id, public, admit):
    """Invalidate prior email links without changing a Family's stable manual code."""
    with transaction.atomic(), key_set_lock(public):
        deployment = DeploymentCredentialState.objects.select_for_update().get()
        row = (
            FamilyAccessToken.objects.select_for_update()
            .select_related("family", "generation", "campaign")
            .get(pk=token_id)
        )
        admit(row.campaign, deployment, row.generation)
        if (
            row.destroyed_at is not None
            or row.generation.credential_epoch != deployment.family_link_epoch
            or row.campaign.active_token_generation_id != row.generation_id
            or not row.family.portal_eligible
            or row.campaign.state not in {"scheduled", "active"}
        ):
            raise CryptographicError("The Family link is unavailable for rotation.")
        value = new_token()
        row.ciphertext = public.encrypt(value.encode(), context=token_context(row.pk))
        row.digest = token_digest(value, row.campaign_id)
        row.revoked_at, row.version = _now(), row.version + 1
        row.save()
        AuditEvent.objects.create(event_type="family_link_rotated", subject_id=row.pk)
        return row.pk
