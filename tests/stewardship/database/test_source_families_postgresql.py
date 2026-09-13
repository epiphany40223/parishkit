"""Real source promotion and campaign codes commit or roll back together."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest
from django.db import connections, transaction

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    FamilyCodeFingerprint,
    FamilyEligibilityChange,
)
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.families import reconcile_source_families
from parishkit.stewardship.source.leases import (
    SourceFenceLost,
    acquire_source,
    release_source,
)
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    stage_entities,
)
from parishkit.stewardship.storage import StorageInvariantError

from ..test_source_corpus import TODAY, source
from .campaign_builders import draft_campaign
from .credential_builders import keys
from .source_builders import running_source_task
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore only idle singleton seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def setup(tmp_path):
    """Real campaign configuration and isolated keys, without earlier population."""
    _, campaign, _ = draft_campaign(tmp_path)
    return campaign, keys()


def prepare(data=None):
    """Stage actual normalized output for the configured synthetic tenant."""
    data = replace(source() if data is None else data, organization_id=12345)
    for family in data.families.values():
        if family.get("registeredOrganizationID") == 5:
            family["registeredOrganizationID"] = 12345
    corpus = normalize_core(data, as_of=TODAY)
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=12345, admit=permit)
    for kind, values in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=values, admit=permit)
    finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor={},
        admit=permit,
    )
    return snapshot, claim


def reconcile(snapshot, claim, campaign, ring, **overrides):
    """Call the source owner with a current explicit suppression evaluation."""
    return reconcile_source_families(
        snapshot.pk,
        claim,
        campaign_id=campaign.pk,
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
        **dict(suppressed_addresses=frozenset(), admit=permit) | overrides,
    )


def promote(snapshot, claim, campaign, ring, **overrides):
    """The real pointer owner invokes Family effects before its transaction commits."""

    def effects(value):
        """Other owning effects would also execute before reporting completion."""
        reconcile(value, claim, campaign, ring, **overrides)
        return True

    with work_transaction():
        result = promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
    release_source(claim)
    return result


def test_promotion_populates_exact_generation_codes_and_cohort(tmp_path):
    """Source identity, Family population and first eligibility all share one commit."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    promoted = promote(snapshot, claim, campaign, ring)
    row = FamilyCampaign.objects.get(family_duid=1)
    population = CampaignCredentialState.objects.get(campaign=campaign)
    assert SourceCurrent.objects.get().snapshot_id == promoted.pk
    assert population.source_snapshot_id == promoted.pk
    assert population.source_generation == row.source_generation == promoted.generation
    assert row.first_eligible_source_generation == promoted.generation
    assert row.portal_eligible and row.email_deliverable and row.code_ciphertext
    assert (
        len(ring.general.decrypt(row.code_ciphertext, context=code_context(row.pk)))
        == 8
    )
    assert not FamilyCampaign.objects.get(family_duid=2).code_ciphertext
    assert FamilyCodeFingerprint.objects.count() == 1


def test_failed_later_effect_rolls_back_pointer_codes_and_eligibility_history(tmp_path):
    """A later domain failure cannot leave codes derived from uncommitted truth."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()

    def fail_after_families(value):
        """Simulate another required owner's failure after successful code creation."""
        reconcile(value, claim, campaign, ring)
        assert FamilyCodeFingerprint.objects.exists()
        raise RuntimeError("Synthetic later effect failure")

    with pytest.raises(RuntimeError, match="later effect"), work_transaction():
        promote_snapshot(
            snapshot.pk,
            claim,
            admit=permit,
            reconcile=fail_after_families,
        )
    snapshot.refresh_from_db()
    assert snapshot.state == "ready" and SourceCurrent.objects.get().snapshot_id is None
    assert not FamilyCampaign.objects.exists()
    assert not FamilyCodeFingerprint.objects.exists()
    assert not FamilyEligibilityChange.objects.exists()
    assert not CampaignCredentialState.objects.filter(
        source_snapshot_id=snapshot.pk
    ).exists()


def test_source_inactivation_and_reactivation_preserve_code_and_cohort(tmp_path):
    """Repeated provider refreshes never replace a campaign's original Family code."""
    campaign, ring = setup(tmp_path)
    first, claim = prepare()
    promote(first, claim, campaign, ring)
    row = FamilyCampaign.objects.get(family_duid=1)
    original = (
        row.code_ciphertext,
        row.first_eligible_at,
        row.first_eligible_source_generation,
    )
    data = source()
    data.members[3]["memberStatus"] = "Inactive"
    second, claim = prepare(data)
    promote(second, claim, campaign, ring)
    row.refresh_from_db()
    assert not row.portal_eligible and row.code_ciphertext == original[0]
    third, claim = prepare()
    promote(third, claim, campaign, ring)
    row.refresh_from_db()
    assert row.portal_eligible
    assert (
        row.code_ciphertext,
        row.first_eligible_at,
        row.first_eligible_source_generation,
    ) == original
    assert FamilyCodeFingerprint.objects.count() == 1


def test_no_email_and_suppressed_families_still_receive_codes(tmp_path):
    """Delivery state neither prevents nor rotates portal credentials."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    promote(
        snapshot,
        claim,
        campaign,
        ring,
        suppressed_addresses=frozenset({"valid@example.org"}),
    )
    row = FamilyCampaign.objects.get(family_duid=1)
    code = row.code_ciphertext
    assert code and row.email_eligible and not row.email_deliverable
    data = source()
    data.members[3]["emailAddress"] = ""
    snapshot, claim = prepare(data)
    promote(snapshot, claim, campaign, ring)
    row.refresh_from_db()
    assert (
        row.code_ciphertext == code and row.portal_eligible and not row.email_eligible
    )


def test_ready_snapshot_cannot_drive_family_effects(tmp_path):
    """Only the just-promoted pointer is an accepted population input."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    with (
        pytest.raises(StorageInvariantError, match="current refresh"),
        work_transaction(),
    ):
        reconcile(snapshot, claim, campaign, ring)
    assert not FamilyCampaign.objects.exists()


def test_effects_require_outer_source_transaction(tmp_path):
    """Calling the effect alone cannot split source promotion from its population."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    with pytest.raises(StorageInvariantError, match="outer promotion"):
        reconcile(snapshot, claim, campaign, ring)


def test_stale_worker_and_denied_admission_cannot_leave_partial_population(tmp_path):
    """Source fencing and current domain admission both protect Family allocation."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    with pytest.raises(SourceFenceLost), work_transaction():
        reconcile(snapshot, replace(claim, worker_id=uuid4()), campaign, ring)
    with pytest.raises(PermissionError, match="not admitted"):
        promote(snapshot, claim, campaign, ring, admit=lambda _: False)
    assert not FamilyCampaign.objects.exists()
    assert SourceCurrent.objects.get().snapshot_id is None


def test_cannot_reconcile_a_noncurrent_campaign(tmp_path):
    """A valid source claim is not authority to populate an arbitrary campaign."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    campaign.pk = uuid4()
    with pytest.raises(PermissionError), transaction.atomic(), work_transaction():
        reconcile(snapshot, claim, campaign, ring)
    assert not FamilyCampaign.objects.exists()


def test_rotation_writer_causes_retry_without_waiting_under_source_locks(tmp_path):
    """Promotion never queues a key reader behind a conflicting rotation writer."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    ready, done = Event(), Event()

    def rotation():
        """Hold the genuine exclusive inventory lock on an independent connection."""
        try:
            with transaction.atomic(), key_set_lock(exclusive=True):
                ready.set()
                assert done.wait(5)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rotation)
        try:
            assert ready.wait(5)
            with pytest.raises(CryptographicError, match="busy"):
                promote(snapshot, claim, campaign, ring)
        finally:
            done.set()
        future.result(timeout=5)
    assert SourceCurrent.objects.get().snapshot_id is None
    assert not FamilyCampaign.objects.exists()
