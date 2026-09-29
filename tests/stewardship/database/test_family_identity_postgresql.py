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


def test_eligibility_history_uses_the_current_source_promotion_attribution(tmp_path):
    """A second promotion must not reuse the initial allocation correlation."""
    from parishkit.stewardship.observability import correlation

    _, campaign, _, ring = family_campaign(tmp_path)
    actor, correlation_id = uuid4(), uuid4()
    with correlation(correlation_id):
        populate(campaign, ring, [], generation=2, actor_id=actor)
    change = FamilyEligibilityChange.objects.get(source_generation=2)
    assert change.actor_id == actor
    assert change.correlation_id == correlation_id
    with correlation(correlation_id):
        populate(
            campaign,
            ring,
            [FamilyStatus(2, True, True, True, True)],
            generation=3,
            actor_id=actor,
        )
    change = FamilyEligibilityChange.objects.get(family__family_duid=2)
    assert change.actor_id == actor and change.correlation_id == correlation_id


@pytest.mark.parametrize("field", ["status_reason", "deliverability_reason"])
def test_source_status_cannot_use_internal_history_suppression_sentinel(field):
    """The allocator's pre-eligibility placeholder is never valid source input."""
    with pytest.raises(ValueError, match="reasons"):
        FamilyStatus(1, True, True, True, True, **{field: "population_pending"})


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
            campaign_id=campaign.pk,
            general=ring.general,
            mac=replacement,
            admit=lambda: True,
        )
        demoted = CodeMacKeyring(
            [Key("m1", "collision-only", b"m" * 32), replacement.active]
        )
        collision_only(replacement, demoted, admit=lambda: True)
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


def test_reference_population_rewrites_families_in_set_based_batches(tmp_path):
    """#147: every promotion rewrites all ~2,700 Families under the work lock.

    Django's bulk_update built a CASE WHEN per row per field, which cost
    seconds of Python at parish scale while the global work-order lock was
    held. The set-based writer issues one unnest() UPDATE per 1,000 rows and
    still fires the per-row version, cohort and history triggers.
    """
    count = 2700
    _, campaign, _, ring = family_campaign(tmp_path, count=count)
    versions = dict(FamilyCampaign.objects.values_list("family_duid", "version"))
    history = FamilyEligibilityChange.objects.count()
    # Every tenth Family loses email delivery; the rest only change generation.
    statuses = [
        FamilyStatus(n, True, True, True, n % 10 != 0)
        if n % 10
        else FamilyStatus(n, True, True, True, False, "eligible", "provider_suppressed")
        for n in range(1, count + 1)
    ]
    with CaptureQueriesContext(connection) as captured:
        assert populate(campaign, ring, statuses, generation=2) == count
    updates = [
        query["sql"]
        for query in captured
        if query["sql"].startswith(
            (
                "UPDATE stewardship_family_campaign ",
                'UPDATE "stewardship_family_campaign"',
            )
        )
    ]
    assert len(updates) == 3
    assert not any("CASE WHEN" in sql for sql in updates)
    rows = FamilyCampaign.objects.values_list(
        "family_duid",
        "version",
        "source_generation",
        "email_deliverable",
        "deliverability_reason",
    )
    for duid, version, generation, deliverable, reason in rows:
        assert version == versions[duid] + 1 and generation == 2
        assert deliverable == (duid % 10 != 0)
        assert reason == ("deliverable" if duid % 10 else "provider_suppressed")
    assert FamilyEligibilityChange.objects.count() == history + count // 10


def test_set_based_family_writes_keep_field_and_row_guards(tmp_path):
    """Values still pass field preparation; a missing row fails the write."""
    from datetime import datetime

    from django.core.exceptions import ValidationError

    from parishkit.stewardship.campaigns.family_identity import _write_families

    family_campaign(tmp_path)
    row = FamilyCampaign.objects.get()
    row.version += 1
    row.eligibility_changed_at = datetime(2030, 1, 1)
    with pytest.raises(ValidationError), transaction.atomic():
        _write_families([row], ["eligibility_changed_at", "version"])
    row.pk = uuid4()
    with pytest.raises(StorageInvariantError), transaction.atomic():
        _write_families([row], ["version"])
    assert FamilyCampaign.objects.get().version == row.version - 1
