"""Live source promotion extends current links without rotating existing Families."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns import link_tokens
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

from .campaign_builders import campaign_clock, close_campaign, command, restored_runtime
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


@pytest.mark.parametrize("decision", [None, False, 1])
def test_explicit_rotation_requires_exact_admission(active, decision):
    """Truthy values are not authorization to replace a live credential."""
    _, ring = active
    row = FamilyAccessToken.objects.get()
    previous = row.ciphertext, row.digest, row.version
    with pytest.raises(PermissionError, match="not admitted"):
        rotate_token(token_id=row.pk, public=ring.public, admit=lambda *_: decision)
    row.refresh_from_db()
    assert (row.ciphertext, row.digest, row.version) == previous


@pytest.mark.parametrize(
    "change", ["epoch", "ineligible", "dirty", "destroyed", "closed", "restore"]
)
def test_explicit_rotation_rechecks_durable_fences(active, change):
    """Real stored lifecycle, eligibility, restore and epoch changes deny rotation."""
    campaign, ring = active
    row = FamilyAccessToken.objects.get()
    if change == "epoch":
        DeploymentCredentialState.objects.update(
            family_link_epoch=uuid4(), version=F("version") + 1
        )
    elif change == "ineligible":
        populate(campaign, ring, [], generation=2)
    elif change == "dirty":
        CampaignCredentialState.objects.update(
            population_dirty=True, version=F("version") + 1
        )
    elif change == "destroyed":
        FamilyAccessToken.objects.filter(pk=row.pk).update(
            ciphertext=None,
            digest=None,
            destroyed_at=database_now(),
            version=F("version") + 1,
        )
    elif change == "closed":
        close_campaign(campaign, uuid4())
    if change == "restore":
        with (
            restored_runtime(database_now()),
            pytest.raises(CryptographicError, match="unavailable"),
        ):
            rotate_token(token_id=row.pk, public=ring.public, admit=lambda *_: True)
    else:
        with pytest.raises(CryptographicError, match="unavailable"):
            rotate_token(token_id=row.pk, public=ring.public, admit=lambda *_: True)
    row.refresh_from_db()
    assert not row.rotated_at


@pytest.mark.parametrize(
    "field,value", [("mode", "testing"), ("current_campaign_id", None)]
)
def test_rotation_defends_against_unadmitted_runtime_projection(
    active, monkeypatch, field, value
):
    """The primitive checks its projection even when an owning callback says yes."""
    _, ring = active
    row = FamilyAccessToken.objects.get()
    runtime = SystemConfiguration.objects.get()
    setattr(runtime, field, value)
    monkeypatch.setattr(
        link_tokens.SystemConfiguration.objects,
        "select_for_update",
        lambda: Mock(get=lambda: runtime),
    )
    with pytest.raises(CryptographicError, match="unavailable"):
        rotate_token(token_id=row.pk, public=ring.public, admit=lambda *_: True)


@pytest.mark.parametrize("field", ["pointer", "generation"])
def test_rotation_rejects_stale_joined_generation_projection(
    active, monkeypatch, field
):
    """A stale pointer or generation cannot be used as a credential minting port."""
    _, ring = active
    row = FamilyAccessToken.objects.select_related(
        "family", "generation", "campaign"
    ).get()
    if field == "pointer":
        row.campaign.active_token_generation_id = None
    else:
        row.generation.state = "cancelled"
    query = Mock()
    query.select_related.return_value.get.return_value = row
    monkeypatch.setattr(FamilyAccessToken.objects, "select_for_update", lambda: query)
    with pytest.raises(CryptographicError, match="unavailable"):
        rotate_token(token_id=row.pk, public=ring.public, admit=lambda *_: True)
