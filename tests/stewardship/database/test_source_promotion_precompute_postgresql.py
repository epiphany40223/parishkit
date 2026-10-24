"""Promotion's read-only Family inputs are derived before the work order (#895).

A promotion used to decode the snapshot's Family corpus and check every
Family's eligibility inside its exclusive work-order transaction: about
60–90 ms of a 280–470 ms hold at 1,100 Families. ``prepare_promotion`` now
does that in a source step before promotion, and promotion uses the result
only for the same snapshot, applying the suppressions it reads under the
lock. These tests check that the prepared path writes exactly what the old
one did, that a bounce arriving in between still counts, that inputs for
another snapshot are ignored, and that the precompute never holds or waits
for the work-order lock.
"""

from dataclasses import replace

import pytest
from django.db import connection, transaction

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source import families
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.families import (
    FamilySuppressions,
    PreparedPromotion,
    family_statuses,
    prepare_promotion,
    reconcile_source_families,
)
from parishkit.stewardship.source.leases import (
    SourceFenceLost,
    acquire_source,
    release_source,
)
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    promote_snapshot,
    reconstruct_snapshot,
)

from ..test_source_corpus import source
from .source_builders import running_source_task
from .test_source_families_postgresql import (  # noqa: F401
    prepare,
    setup,
    source_singletons,
)
from .test_source_snapshots_postgresql import permit
from .test_source_step_lock_postgresql import (  # noqa: F401
    holder,
    holds_work_order,
    own_pid,
)

pytestmark = pytest.mark.django_db(transaction=True)

HEAD = "valid@example.org"
STATUS = (
    "family_duid",
    "active",
    "portal_eligible",
    "email_eligible",
    "email_deliverable",
    "status_reason",
    "deliverability_reason",
    "code_ciphertext",
    "first_eligible_at",
)


def decodes(monkeypatch):
    """Count the corpus decodes; each entry is whether it held the work order."""
    seen, real = [], families._decode

    def spy(snapshot):
        """Record the decode and whether this session holds 736220,1."""
        seen.append(holds_work_order(own_pid()))
        return real(snapshot)

    monkeypatch.setattr(families, "_decode", spy)
    return seen


def promote(snapshot, claim, campaign, ring, *, prepared=None, suppressed=None):
    """Promote with the real Family owner, optionally with prepared inputs."""

    def effects(value):
        """The Family owner, as the compiled effects call it."""
        reconcile_source_families(
            value.pk,
            claim,
            campaign_id=campaign.pk,
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressed_addresses=suppressed or frozenset(),
            admit=permit,
            prepared=prepared,
        )
        return True

    with work_transaction():
        result = promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
    release_source(claim)
    return result


def rows():
    """Each Family row's status, code and cohort, by Family."""
    return sorted(FamilyCampaign.objects.values_list(*STATUS))


def two_families():
    """The shared fixture plus a second, nonparishioner household with a head."""
    data = source()
    data.families[4] = dict(
        data.families[1], familyDUID=4, familyID=44, famGroupID=8, lastName="Other"
    )
    data.family_groups[8] = "Visitor"
    data.members[5] = dict(
        data.members[3], memberDUID=5, familyDUID=4, emailAddress="other@example.org"
    )
    return data


@pytest.mark.parametrize(
    "suppressed",
    [
        frozenset(),
        frozenset({HEAD}),
        FamilySuppressions(frozenset({(1, HEAD)})),
        FamilySuppressions(frozenset({(4, HEAD)})),
    ],
)
def test_prepared_statuses_equal_the_statuses_promotion_computed(tmp_path, suppressed):
    """The precomputed eligibility plus suppressions is the old status function."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare(two_families())
    prepared = prepare_promotion(snapshot.pk, claim)
    assert prepared.snapshot_id == snapshot.pk
    promote(snapshot, claim, campaign, ring)
    corpus = reconstruct_snapshot(
        snapshot.pk, kinds=("family", "member", "contact", "ministry", "roster")
    )
    assert prepared.corpus == corpus
    assert prepared.statuses(suppressed) == family_statuses(
        corpus, suppressed_addresses=suppressed
    )


def test_prepared_promotion_writes_what_the_old_path_wrote(tmp_path, monkeypatch):
    """Same corpus, promoted once each way: identical Family rows and population.

    The prepared promotion decodes nothing under the lock; the old one does.
    """
    campaign, ring = setup(tmp_path)
    seen = decodes(monkeypatch)
    first, claim = prepare(two_families())
    promote(first, claim, campaign, ring)
    assert seen == [True]
    before = rows(), CampaignCredentialState.objects.get().eligibility_digest
    second, claim = prepare(two_families())
    prepared = prepare_promotion(second.pk, claim)
    assert seen == [True, False]
    promoted = promote(second, claim, campaign, ring, prepared=prepared)
    assert seen == [True, False]
    population = CampaignCredentialState.objects.get()
    assert (rows(), population.eligibility_digest) == before
    assert population.source_snapshot_id == promoted.pk
    assert set(FamilyCampaign.objects.values_list("source_generation", flat=True)) == {
        promoted.generation
    }


def test_a_bounce_after_the_precompute_still_counts(tmp_path):
    """Suppressions read under the lock decide deliverability, not the precompute."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    prepared = prepare_promotion(snapshot.pk, claim)
    promote(
        snapshot, claim, campaign, ring, prepared=prepared, suppressed=frozenset({HEAD})
    )
    row = FamilyCampaign.objects.get(family_duid=1)
    assert row.email_eligible and not row.email_deliverable
    assert row.deliverability_reason == "provider_suppressed"


def test_inputs_for_another_snapshot_are_ignored(tmp_path, monkeypatch):
    """Promotion decodes its own snapshot when handed another one's inputs."""
    campaign, ring = setup(tmp_path)
    other, claim = prepare()
    stale = prepare_promotion(other.pk, claim)
    release_source(claim)
    data = source()
    data.members[3]["memberStatus"] = "Inactive"
    snapshot, claim = prepare(data)
    seen = decodes(monkeypatch)
    promote(snapshot, claim, campaign, ring, prepared=stale)
    assert seen == [True]
    assert not FamilyCampaign.objects.get(family_duid=1).portal_eligible


def test_prepare_reads_only_this_claims_ready_snapshot(tmp_path):
    """Another owner's snapshot, or one not (or no longer) ready, prepares nothing."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()
    with pytest.raises(SourceFenceLost):
        prepare_promotion(snapshot.pk, replace(claim, fence=claim.fence + 1))
    promote(snapshot, claim, campaign, ring)
    # The live owner's own snapshot that is still staging is not ready:
    # its membership could still grow.
    claim = acquire_source(**running_source_task(), phase="full")
    staging = begin_snapshot(claim, organization_id=12345, admit=permit)
    assert prepare_promotion(staging.pk, claim) is None
    # The promoted snapshot belongs to another, finished owner.
    assert prepare_promotion(snapshot.pk, claim) is None


def test_an_inconsistent_corpus_falls_back_to_the_locked_path(tmp_path, monkeypatch):
    """The precompute gives up; promotion then raises its usual error."""
    campaign, ring = setup(tmp_path)
    snapshot, claim = prepare()

    def refuse(corpus, rule=None):
        """Stand in for a corpus whose eligibility contradicts itself."""
        raise InvalidSourcePayload("Source Family eligibility is inconsistent.")

    monkeypatch.setattr(families, "family_eligibility", refuse)
    assert prepare_promotion(snapshot.pk, claim) is None
    with pytest.raises(InvalidSourcePayload):
        promote(snapshot, claim, campaign, ring)
    assert not FamilyCampaign.objects.exists()


def test_the_precompute_runs_while_another_session_holds_the_work_order(
    tmp_path,
    holder,  # noqa: F811
):
    """It completes under a competing exclusive hold and never takes the lock."""
    setup(tmp_path)
    snapshot, claim = prepare()
    holder.take()
    with transaction.atomic():
        prepared = prepare_promotion(snapshot.pk, claim)
        assert holds_work_order(holder.pid) and not holds_work_order(own_pid())
    holder.give()
    assert isinstance(prepared, PreparedPromotion)
    assert prepared.snapshot_id == snapshot.pk
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM stewardship_family_campaign")
        assert cursor.fetchone()[0] == 0


def test_a_real_refresh_decodes_its_family_corpus_off_the_lock(tmp_path, monkeypatch):
    """The compiled worker prepares in a source step and promotes with it.

    One decode per promotion, outside the work order; none under it.
    """
    from .refresh_harness import Refreshes

    seen = decodes(monkeypatch)
    refreshes = Refreshes(tmp_path, monkeypatch)
    assert seen == [False]
    assert FamilyCampaign.objects.get().source_generation == (refreshes.full.generation)
