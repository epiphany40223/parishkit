"""Scheduler-owned empty epoch initialization; credentials remain worker-owned."""

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.family_schedule_planning import _planning_scope
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StorageInvariantError

from .scheduler import SchedulerGuard


def ensure_preparation_epoch(guard, campaign_id, worker_id):
    """Create only missing Testing metadata under actual scheduler/work ownership."""
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Rehearsal initialization requires scheduler ownership.")
    guard.check()
    # This unlocked read can only defer work until the next sweep. Creation still
    # rechecks all evidence below under the work lock; it grants no authority.
    if (
        SystemConfiguration.objects.values_list("mode", flat=True).first() != "testing"
        or not CampaignCredentialState.objects.filter(
            campaign_id=campaign_id, rehearsal_epoch_id__isnull=True
        ).exists()
    ):
        return
    with work_transaction():
        if (
            SystemConfiguration.objects.values_list("mode", flat=True).first()
            != "testing"
        ):
            return
        scope, epoch = _planning_scope(campaign_id, allow_missing_epoch=True)
        if scope.runtime.mode != "testing" or epoch is not None:
            return
        population = CampaignCredentialState.objects.select_for_update().get(
            campaign_id=campaign_id
        )
        if population.population_dirty or population.source_snapshot_id is None:
            raise PermissionError(
                "Rehearsal initialization requires source population."
            )
        epoch = RehearsalEpoch.objects.create(
            campaign_id=campaign_id, actor_id=worker_id, correlation_id=campaign_id
        )
        updated = CampaignCredentialState.objects.filter(
            pk=population.pk,
            version=population.version,
            rehearsal_epoch_id__isnull=True,
        ).update(
            rehearsal_epoch=epoch,
            version=population.version + 1,
            actor_id=worker_id,
            correlation_id=campaign_id,
        )
        if updated != 1:
            raise StorageInvariantError("Rehearsal initialization lost its scope.")
        guard.check()
