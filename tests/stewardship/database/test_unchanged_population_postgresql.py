"""``population_current`` answers True exactly when reconciliation writes nothing.

The unchanged-quick-update check (#630) skips promotion only when Family
reconciliation at the current generation would write no Family row, code or
link. These tests hold the two decisions together on real stored rows, so they
cannot drift apart.
"""

from contextlib import contextmanager

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyAccessToken,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.family_identity import (
    FamilyStatus,
    population_current,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import campaign_clock, command
from .credential_builders import family_campaign, populate

pytestmark = pytest.mark.django_db(transaction=True)

ELIGIBLE = FamilyStatus(1, True, True, True, True)


def current(campaign, statuses):
    """``population_current`` against the population's own recorded generation."""
    population = CampaignCredentialState.objects.get(campaign=campaign)
    return population_current(
        campaign,
        source_snapshot_id=population.source_snapshot_id,
        source_generation=population.source_generation,
        statuses=statuses,
    )


def writes(campaign, ring, statuses):
    """Whether reconciliation at the same generation writes any Family state.

    Rows, codes and links are compared before and after; a refusal (an
    eligibility change cannot reuse a generation) also counts as a write,
    since promotion would then need a new generation.
    """
    population = CampaignCredentialState.objects.get(campaign=campaign)

    def state():
        """Every Family row's version and code, and every link."""
        return (
            sorted(
                FamilyCampaign.objects.values_list("id", "version", "code_ciphertext")
            ),
            FamilyAccessToken.objects.count(),
        )

    before = state()
    try:
        changed = populate(
            campaign, ring, statuses, generation=population.source_generation
        )
    except StorageInvariantError:
        return True
    return bool(changed) or state() != before


@contextmanager
def without_guards():
    """Make a row state the guards refuse, to exercise a defensive branch."""
    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET session_replication_role = origin")


@pytest.mark.parametrize(
    "statuses",
    [
        [ELIGIBLE],
        [FamilyStatus(1, True, True, True, False, "eligible", "provider_suppressed")],
        [],
        [
            ELIGIBLE,
            FamilyStatus(2, True, False, False, False, "ineligible", "ineligible"),
        ],
        [FamilyStatus(1, True, False, False, False, "ineligible", "ineligible")],
    ],
    ids=["same", "bounce", "absent", "new_family", "lost_eligibility"],
)
def test_population_current_matches_reconciliation(tmp_path, statuses):
    """True for the stored statuses only, and then reconciliation writes nothing."""
    _, campaign, _, ring = family_campaign(tmp_path)
    expected = current(campaign, statuses)
    assert expected == (statuses == [ELIGIBLE])
    assert writes(campaign, ring, statuses) is not expected


@pytest.mark.parametrize("problem", ["dirty", "other_snapshot", "other_generation"])
def test_population_evidence_must_be_clean_and_current(tmp_path, problem):
    """Dirty evidence, or evidence for another snapshot or generation, rebuilds."""
    _, campaign, _, ring = family_campaign(tmp_path)
    population = CampaignCredentialState.objects.get(campaign=campaign)
    snapshot, generation = population.source_snapshot_id, population.source_generation
    if problem == "dirty":
        CampaignCredentialState.objects.update(
            population_dirty=True, version=F("version") + 1
        )
    elif problem == "other_snapshot":
        snapshot = snapshot.__class__(int=snapshot.int ^ 1)
    else:
        generation += 1
    assert not population_current(
        campaign,
        source_snapshot_id=snapshot,
        source_generation=generation,
        statuses=[ELIGIBLE],
    )


def test_eligible_family_without_its_code_cannot_exist(tmp_path):
    """The code branch in ``population_current`` is defensive only.

    A check constraint refuses an eligible Family row without its code, so
    reconciliation never meets one outside the transaction that allocates it.
    """
    family_campaign(tmp_path)
    with (
        pytest.raises(IntegrityError, match="family_eligible_has_code_cohort"),
        without_guards(),
        transaction.atomic(),
    ):
        FamilyCampaign.objects.update(code_ciphertext=None)


def test_eligible_family_without_its_active_link_rebuilds(tmp_path):
    """Under an active link generation, a missing link is a change."""
    _, campaign, actor, ring = family_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        campaign.refresh_from_db()
        assert campaign.active_token_generation_id is not None
        assert current(campaign, [ELIGIBLE])
        with without_guards(), transaction.atomic():
            FamilyAccessToken.objects.all().delete()
        assert not current(campaign, [ELIGIBLE])
        assert writes(campaign, ring, [ELIGIBLE])
        assert current(campaign, [ELIGIBLE])


def population_locked():
    """Whether another session finds the population row locked right now."""
    from django.db import OperationalError, connections

    other = connections.create_connection("default")
    try:
        with other.cursor() as cursor:
            cursor.execute("BEGIN")
            try:
                cursor.execute(
                    "SELECT 1 FROM stewardship_campaign_credentials FOR UPDATE NOWAIT"
                )
            except OperationalError:
                return True
            finally:
                cursor.execute("ROLLBACK")
        return False
    finally:
        other.close()


def test_stale_link_generation_still_fails_without_promotion(tmp_path):
    """The unchanged path makes promotion's link-generation checks as a read.

    It holds none of the locks that order the population owner's lock, so it
    must take none: another session can still lock the population row.
    """
    from parishkit.stewardship.accounts.cryptography import CryptographicError
    from parishkit.stewardship.campaigns.link_tokens import require_current_generation

    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        campaign.refresh_from_db()
        with transaction.atomic():
            require_current_generation(campaign)
            assert not population_locked()
        CampaignCredentialState.objects.update(
            population_dirty=True, version=F("version") + 1
        )
        with (
            pytest.raises(CryptographicError, match="not current"),
            transaction.atomic(),
        ):
            require_current_generation(campaign)
        # The probe does see a real row lock (the population owner's).
        with transaction.atomic():
            CampaignCredentialState.objects.select_for_update().get()
            assert population_locked()
