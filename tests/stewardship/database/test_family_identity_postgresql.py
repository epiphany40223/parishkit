"""Atomic population, permanent manual codes, immutable cohort and MAC collisions."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.cryptography import CodeMacKeyring, Key
from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilyCodeFingerprint,
    FamilyEligibilityChange,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus, code_context
from parishkit.stewardship.storage import StorageInvariantError

from .credential_builders import TestKeys, family_campaign, populate

pytestmark = pytest.mark.django_db(transaction=True)


def test_population_and_reactivation_preserve_original_code_and_cohort(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path)
    row = FamilyCampaign.objects.get()
    code = ring.general.decrypt(row.code_ciphertext, context=code_context(row.pk))
    provenance = (row.first_eligible_at, row.first_eligible_source_generation)
    assert len(code) == 8
    populate(campaign, ring, [], generation=2)
    row.refresh_from_db()
    assert not row.portal_eligible
    assert (row.first_eligible_at, row.first_eligible_source_generation) == provenance
    populate(campaign, ring, generation=3)
    row.refresh_from_db()
    assert row.portal_eligible
    assert (
        ring.general.decrypt(row.code_ciphertext, context=code_context(row.pk)) == code
    )
    assert (row.first_eligible_at, row.first_eligible_source_generation) == provenance
    assert FamilyCodeFingerprint.objects.count() == 1
    assert list(
        FamilyEligibilityChange.objects.order_by("family_version").values_list(
            "portal_eligible", flat=True
        )
    ) == [True, False, True]


def test_no_email_family_still_gets_stable_manual_code(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path, count=0)
    populate(
        campaign,
        ring,
        [FamilyStatus(22, True, True, False, False, "eligible", "no_eligible_email")],
        generation=2,
    )
    assert FamilyCampaign.objects.get().code_ciphertext


def test_corpus_population_rolls_back_as_one_source_commit(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path, count=0)
    with pytest.raises(ValueError), transaction.atomic():
        populate(campaign, ring, generation=2)
        raise ValueError("Synthetic source-promotion failure")
    assert not FamilyCampaign.objects.exists()
    assert not FamilyCodeFingerprint.objects.exists()
    assert not FamilyEligibilityChange.objects.exists()


@pytest.mark.parametrize("demote", [False, True])
def test_fingerprint_prevents_duplicate_after_mac_rotation(
    tmp_path, monkeypatch, demote
):
    from parishkit.stewardship.campaigns import family_identity

    _, campaign, _, ring = family_campaign(tmp_path)
    row = FamilyCampaign.objects.get()
    code = ring.general.decrypt(
        row.code_ciphertext, context=code_context(row.pk)
    ).decode()
    replacement = CodeMacKeyring(
        [Key("m1", "lookup-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    add_rotation_key(ring.mac, replacement)
    if demote:
        from parishkit.stewardship.campaigns.mac_rotation import (
            backfill_mac_batch,
            collision_only,
        )

        backfill_mac_batch(
            campaign_id=campaign.pk, general=ring.general, mac=replacement
        )
        demoted = CodeMacKeyring(
            [Key("m1", "collision-only", b"m" * 32), replacement.active]
        )
        collision_only(replacement, demoted)
        replacement = demoted
    ring = TestKeys(ring.general, replacement, ring.private)
    candidates = iter([code, "ABCDEFGH"])
    monkeypatch.setattr(family_identity, "new_code", lambda: next(candidates))
    populate(
        campaign,
        ring,
        [
            FamilyStatus(1, True, True, True, True),
            FamilyStatus(2, True, True, True, True),
        ],
        generation=2,
    )
    new = FamilyCampaign.objects.get(family_duid=2)
    assert (
        ring.general.decrypt(new.code_ciphertext, context=code_context(new.pk))
        == b"ABCDEFGH"
    )
    assert FamilyCodeFingerprint.objects.filter(family=new).count() == (
        1 if demote else 2
    )
    assert FamilyCodeFingerprint.objects.filter(family=row).count() == (
        2 if demote else 1
    )


def test_allocation_subtransactions_scale_with_batches_not_families(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path, count=0)
    with CaptureQueriesContext(connection) as captured:
        populate(
            campaign,
            ring,
            [FamilyStatus(n + 1, True, True, True, True) for n in range(501)],
            generation=2,
        )
    savepoints = [query for query in captured if query["sql"].startswith("SAVEPOINT")]
    assert len(savepoints) <= 5
    assert FamilyCampaign.objects.count() == 501
    assert FamilyCodeFingerprint.objects.count() == 501


def test_cohort_and_duid_cannot_be_rewritten_by_raw_orm(tmp_path):
    family_campaign(tmp_path)
    for values in (
        {"family_duid": 99},
        {"first_eligible_source_generation": 2},
        {"code_ciphertext": None},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            FamilyCampaign.objects.update(**values, version=F("version") + 1)


def test_stale_source_generation_cannot_rewind_eligibility(tmp_path):
    _, campaign, _, ring = family_campaign(tmp_path)
    populate(campaign, ring, generation=3)
    with pytest.raises(StorageInvariantError, match="stale"):
        populate(campaign, ring, [], generation=2)
    assert FamilyCampaign.objects.get().portal_eligible


def test_cross_campaign_fingerprint_scope_fails_in_sql(tmp_path):
    family_campaign(tmp_path)
    row = FamilyCampaign.objects.get()
    with (
        pytest.raises(IntegrityError, match="Fingerprint campaign must match Family"),
        transaction.atomic(),
    ):
        FamilyCodeFingerprint.objects.create(
            family=row, campaign_id=uuid4(), key_id="m2", digest="a" * 64
        )


def test_empty_population_cannot_regress_source_generation(tmp_path):
    """Even an empty population retains its campaign generation high-water mark."""
    _, campaign, _, ring = family_campaign(tmp_path, count=0)
    populate(campaign, ring, [], generation=3)
    with pytest.raises(StorageInvariantError, match="stale"):
        populate(campaign, ring, [], generation=2)


def test_identical_population_retry_does_not_rewrite_family_versions(tmp_path):
    """An unchanged same-generation retry preserves optimistic Family versions."""
    _, campaign, _, ring = family_campaign(tmp_path)
    before = FamilyCampaign.objects.get().version
    assert populate(campaign, ring) == 0
    assert FamilyCampaign.objects.get().version == before
