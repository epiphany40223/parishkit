"""Live source promotion extends current links without rotating existing Families."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.link_tokens import (
    extend_active_generation,
    rotate_token,
)

from .campaign_builders import campaign_clock, command
from .credential_builders import family_campaign, populate

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def active(tmp_path):
    """Select a real prepared generation through the normal activation ledger."""
    store, campaign, actor, ring = family_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        campaign.refresh_from_db()
        yield campaign, ring


def test_arrival_and_reactivation_keep_previous_links_and_frozen_manifest(active):
    """Active membership expands without changing original readiness evidence."""
    campaign, ring = active
    original = FamilyAccessToken.objects.get()
    generation = original.generation
    manifest = (generation.coverage_digest, generation.coverage_count)
    populate(
        campaign,
        ring,
        [FamilyStatus(index, True, True, True, True) for index in (1, 2)],
        generation=2,
    )
    assert FamilyAccessToken.objects.count() == 2
    original.refresh_from_db()
    initial_ciphertext, initial_digest = original.ciphertext, original.digest
    populate(campaign, ring, [], generation=3)
    assert FamilyAccessToken.objects.count() == 2
    populate(campaign, ring, generation=4)
    original.refresh_from_db()
    assert (original.ciphertext, original.digest) == (
        initial_ciphertext,
        initial_digest,
    )
    assert original.destroyed_at is None
    generation.refresh_from_db()
    assert (generation.coverage_digest, generation.coverage_count) == manifest
    with transaction.atomic():
        assert extend_active_generation(campaign, public=ring.public) == 0


def test_new_family_token_is_atomic_with_source_promotion(active):
    """A failed outer source commit exposes neither the added Family nor its token."""
    campaign, ring = active
    with pytest.raises(ValueError), transaction.atomic():
        populate(
            campaign,
            ring,
            [FamilyStatus(index, True, True, True, True) for index in (1, 2)],
            generation=2,
        )
        raise ValueError("Synthetic aborted source promotion.")
    assert FamilyCampaign.objects.count() == FamilyAccessToken.objects.count() == 1
    assert CampaignCredentialState.objects.get().source_generation == 1


def test_restored_epoch_cannot_accept_arrival_or_reactivate_old_links(active):
    """An epoch fence rejects issuance even before restored ciphertext is scrubbed."""
    campaign, ring = active
    DeploymentCredentialState.objects.update(
        family_link_epoch=uuid4(), version=F("version") + 1
    )
    with pytest.raises(CryptographicError, match="not current"):
        populate(
            campaign,
            ring,
            [FamilyStatus(index, True, True, True, True) for index in (1, 2)],
            generation=2,
        )
    assert FamilyCampaign.objects.count() == 1
    assert CampaignCredentialState.objects.get().source_generation == 1
    original = FamilyAccessToken.objects.get()
    with pytest.raises(IntegrityError, match="current eligible"), transaction.atomic():
        FamilyAccessToken.objects.create(
            family=original.family,
            campaign=campaign,
            generation=original.generation,
            ciphertext=original.ciphertext,
            digest="f" * 64,
        )


def test_explicit_link_rotation_changes_only_token_not_manual_code(active):
    """Revocation records the prior link, not a ban on the replacement link."""
    campaign, ring = active
    row = FamilyAccessToken.objects.get()
    prior = row.ciphertext, row.digest
    code = row.family.code_ciphertext
    rotate_token(token_id=row.pk, public=ring.public, admit=lambda *args: True)
    row.refresh_from_db()
    assert row.ciphertext != prior[0] and row.digest != prior[1]
    assert row.rotated_at is not None
    assert row.family.code_ciphertext == code
