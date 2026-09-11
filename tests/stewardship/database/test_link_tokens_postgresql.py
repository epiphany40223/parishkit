"""Token preparation, exact ready manifests, cancellation, restore and close."""

from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.link_tokens import (
    begin_generation,
    cancel_generation,
    prepare_generation_batch,
    scrub_generation,
    token_context,
    verify_generation,
)
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .credential_builders import family_campaign, populate

pytestmark = pytest.mark.django_db(transaction=True)


def admit(campaign, deployment, generation):
    """Pure storage admission only; no task or provider authority is fabricated."""
    assert campaign.state in {"draft", "closed", "active", "scheduled"}
    return True


def begin(campaign, actor, ring, *, operation_id=None):
    """Read the actual published source-population input manifest."""
    population = CampaignCredentialState.objects.get(campaign=campaign)
    return begin_generation(
        campaign_id=campaign.pk,
        operation_id=operation_id or uuid4(),
        source_snapshot_id=population.source_snapshot_id,
        source_generation=population.source_generation,
        public=ring.public,
        actor_id=actor,
        admit=admit,
    )


def assert_credential_inputs_locked(campaign):
    """A distinct SQL connection cannot change the epoch, campaign or runtime."""
    database = connection.copy()
    try:
        for table, identifier in (
            ("stewardship_campaign", campaign.pk),
            ("stewardship_credential_deployment", None),
            ("stewardship_system_configuration", None),
        ):
            with pytest.raises(DatabaseError), database.cursor() as cursor:
                clause = " WHERE id=%s" if identifier is not None else ""
                cursor.execute(
                    f'SELECT id FROM "{table}"{clause} FOR UPDATE NOWAIT',
                    [identifier] if identifier is not None else [],
                )
            database.rollback()
    finally:
        database.close()


def test_preparation_verification_and_cancellation_pin_live_inputs(tmp_path):
    """Every admission callback observes inputs that restore cannot replace."""
    _, campaign, actor, ring = family_campaign(tmp_path)
    population = CampaignCredentialState.objects.get(campaign=campaign)

    def checked_admit(row, deployment, generation):
        assert_credential_inputs_locked(row)
        return True

    generation = begin_generation(
        campaign_id=campaign.pk,
        operation_id=uuid4(),
        source_snapshot_id=population.source_snapshot_id,
        source_generation=population.source_generation,
        public=ring.public,
        actor_id=actor,
        admit=checked_admit,
    )
    prepare_generation_batch(
        generation_id=generation.pk, public=ring.public, admit=checked_admit
    )
    with transaction.atomic():
        # Final activation owns the runtime/campaign locks before verification.
        from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
        from parishkit.stewardship.campaigns.models import Campaign

        SystemConfiguration.objects.select_for_update().get()
        locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
        verify_generation(generation.pk, campaign=locked, public=ring.public)
        assert_credential_inputs_locked(locked)
    cancel_generation(generation_id=generation.pk, admit=checked_admit)


def test_preparation_checkpoints_and_verification_have_no_final_family_scan(tmp_path):
    _, campaign, actor, ring = family_campaign(tmp_path, count=3)
    generation = begin(campaign, actor, ring)
    first = prepare_generation_batch(
        generation_id=generation.pk, public=ring.public, admit=admit, batch_size=2
    )
    assert first.state == "building"
    assert first.checkpoint == 2
    assert FamilyAccessToken.objects.count() == 2
    final = prepare_generation_batch(
        generation_id=generation.pk, public=ring.public, admit=admit, batch_size=2
    )
    assert final.state == "ready"
    assert final.coverage_count == 3
    assert FamilyAccessToken.objects.count() == 3
    repeated = prepare_generation_batch(
        generation_id=generation.pk, public=ring.public, admit=admit, batch_size=2
    )
    assert repeated.version == final.version
    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        assert (
            verify_generation(final.pk, campaign=campaign, public=ring.public).pk
            == final.pk
        )
    assert not any(
        '"stewardship_family_campaign"' in query["sql"] for query in captured
    )
    for token in FamilyAccessToken.objects.all():
        assert (
            len(ring.private.decrypt(token.ciphertext, context=token_context(token.pk)))
            == 43
        )
    campaign.refresh_from_db()
    assert campaign.active_token_generation_id is None


def test_preparation_retry_identity_cannot_change_inputs(tmp_path):
    _, campaign, actor, ring = family_campaign(tmp_path)
    operation = uuid4()
    one = begin(campaign, actor, ring, operation_id=operation)
    assert begin(campaign, actor, ring, operation_id=operation).pk == one.pk
    populate(campaign, ring, generation=2)
    with pytest.raises(StorageInvariantError, match="different"):
        begin(campaign, actor, ring, operation_id=operation)


def test_changed_source_and_restored_epoch_reject_old_preparation(tmp_path):
    _, campaign, actor, ring = family_campaign(tmp_path)
    generation = begin(campaign, actor, ring)
    populate(campaign, ring, generation=2)
    with pytest.raises(StaleRecordError, match="coverage"):
        prepare_generation_batch(
            generation_id=generation.pk, public=ring.public, admit=admit
        )
    fresh = begin(campaign, actor, ring)
    DeploymentCredentialState.objects.update(
        family_link_epoch=uuid4(), version=F("version") + 1
    )
    with pytest.raises(StaleRecordError, match="epoch"):
        prepare_generation_batch(
            generation_id=fresh.pk, public=ring.public, admit=admit
        )


def test_cancellation_scrubs_bounded_batches_and_cannot_resume(tmp_path):
    _, campaign, actor, ring = family_campaign(tmp_path, count=3)
    generation = begin(campaign, actor, ring)
    prepare_generation_batch(
        generation_id=generation.pk, public=ring.public, admit=admit
    )
    with pytest.raises(StorageInvariantError, match="cleanup"):
        scrub_generation(generation.pk)
    cancel_generation(generation_id=generation.pk, admit=admit)
    assert scrub_generation(generation.pk, batch_size=2) == 2
    assert scrub_generation(generation.pk, batch_size=2) == 1
    assert scrub_generation(generation.pk) == 0
    assert not FamilyAccessToken.objects.filter(ciphertext__isnull=False).exists()
    assert not FamilyAccessToken.objects.filter(digest__isnull=False).exists()
    with pytest.raises(StorageInvariantError, match="building"):
        prepare_generation_batch(
            generation_id=generation.pk, public=ring.public, admit=admit
        )


def test_raw_family_change_invalidates_manifest_and_blocks_final_activation(tmp_path):
    _, campaign, actor, ring = family_campaign(tmp_path)
    generation = begin(campaign, actor, ring)
    prepare_generation_batch(
        generation_id=generation.pk, public=ring.public, admit=admit
    )
    FamilyCampaign.objects.update(portal_eligible=False, version=F("version") + 1)
    assert CampaignCredentialState.objects.get().population_dirty
    with transaction.atomic(), pytest.raises(StaleRecordError, match="coverage"):
        verify_generation(generation.pk, campaign=campaign, public=ring.public)
    with pytest.raises(IntegrityError), transaction.atomic():
        CampaignCredentialState.objects.update(
            population_dirty=False, version=F("version") + 1
        )


def test_sql_rejects_token_resurrection_after_scrub(tmp_path):
    _, campaign, actor, ring = family_campaign(tmp_path)
    generation = begin(campaign, actor, ring)
    prepare_generation_batch(
        generation_id=generation.pk, public=ring.public, admit=admit
    )
    token = FamilyAccessToken.objects.get()
    cancel_generation(generation_id=generation.pk, admit=admit)
    scrub_generation(generation.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        FamilyAccessToken.objects.filter(pk=token.pk).update(
            ciphertext=token.ciphertext,
            digest=token.digest,
            destroyed_at=None,
            version=F("version") + 1,
        )
