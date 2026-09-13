"""Compiled refreshes compose real Family and Chair owners, not success stubs."""

import pytest

from parishkit.stewardship.accounts.chair_models import ChairReconciliation
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.effects import refresh_reconciler
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from .campaign_builders import add_draft
from .credential_builders import keys
from .test_source_attempts_postgresql import configured
from .test_source_execution_postgresql import handler, run
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider, pages
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("with_campaign", [False, True])
def test_real_refresh_commits_all_available_phase_two_effects(
    tmp_path, monkeypatch, with_campaign
):
    """Full observation, current truth, effects and Task acknowledgement agree."""
    credential, store, version, actor = configured(tmp_path)
    if with_campaign:
        add_draft(store, version, actor)
    ring = keys()
    suppression_calls = []

    def suppressions(scope):
        """Synthetic suppression owner explicitly reads within current admission."""
        assert scope.campaign is not None
        suppression_calls.append(scope.campaign.pk)
        return frozenset()

    effects = refresh_reconciler(
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
        suppressions=suppressions,
    )
    compiled = handler(tmp_path, credential, reconcile=effects)
    request = command()
    remaining, calls = fake_provider(monkeypatch, pages())
    assert run(request, compiled)
    assert not remaining and calls
    snapshot = SourceSnapshot.objects.get()
    receipt = ChairReconciliation.objects.get()
    assert snapshot.state == "promoted" and receipt.snapshot_id == snapshot.pk
    assert SourceCurrent.objects.get().snapshot_id == snapshot.pk
    assert bool(suppression_calls) == with_campaign
    assert FamilyCampaign.objects.exists() == with_campaign
    if with_campaign:
        assert FamilyCampaign.objects.get().source_generation == snapshot.generation
    assert AuditEvent.objects.filter(
        pk=receipt.pk, event_type="chair_reconciled"
    ).exists()
    assert TaskRun.objects.get(pk=request.task_root_id).state == "succeeded"
    assert not run(request, compiled)
    assert ChairReconciliation.objects.count() == 1


@pytest.mark.parametrize("failure", ["provider_failed", "wrong_type"])
def test_failed_family_dependency_rolls_back_earlier_chair_effects(
    tmp_path, monkeypatch, failure
):
    """Suppression failure never leaves source or authorization partly promoted."""
    credential, store, version, actor = configured(tmp_path)
    add_draft(store, version, actor)
    ring = keys()

    def suppressions(scope):
        """Inject a later owner failure after the genuine Chair receipt is created."""
        assert ChairReconciliation.objects.exists()
        if failure == "provider_failed":
            raise RuntimeError("Synthetic suppression failure")
        return None

    compiled = handler(
        tmp_path,
        credential,
        reconcile=refresh_reconciler(
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressions=suppressions,
        ),
    )
    request = command()
    fake_provider(monkeypatch, pages())
    with pytest.raises(RuntimeError if failure == "provider_failed" else TypeError):
        run(request, compiled)
    assert SourceCurrent.objects.get().snapshot_id is None
    assert SourceSnapshot.objects.get().state == "ready"
    assert not ChairReconciliation.objects.exists()
    assert not FamilyCampaign.objects.exists()
    assert not AuditEvent.objects.filter(event_type="chair_reconciled").exists()


def test_factory_cannot_omit_the_suppression_owner():
    """Startup cannot silently ignore independently owned delivery eligibility."""
    with pytest.raises(TypeError, match="suppression owner"):
        refresh_reconciler(general=None, mac=None, public=None, suppressions=None)
