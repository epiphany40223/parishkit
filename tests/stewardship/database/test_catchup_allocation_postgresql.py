"""Direct activation cannot commit a demand without its original canonical task."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, ProgrammingError, connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.catchup_allocation import allocate_activation
from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessTokenGeneration,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CampaignTransition,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import campaign_clock, command, draft_campaign, prepared_tokens
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


def test_direct_activation_allocates_bound_task_without_family_enumeration(tmp_path):
    """The source and cutoff are fixed by activation, not eventual worker startup."""
    _, campaign, actor = draft_campaign(tmp_path)
    instant = campaign.active_configuration.starts_at + timedelta(days=2)
    with campaign_clock(instant):
        transition = command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()
    task = TaskRun.objects.get(task_type="activation_catchup")
    generation = FamilyAccessTokenGeneration.objects.get(
        pk=transition.token_generation_id
    )
    assert demand.cutoff == instant and demand.activation_id == transition.pk
    assert demand.task_root_id == task.pk == task.root_id
    assert demand.source_snapshot_id == generation.source_snapshot_id
    assert task.domain_request_id == demand.pk and task.idempotency_key == str(
        demand.pk
    )
    assert task.state == "queued" and task.initiated_by_id == actor
    assert demand.groups_completed == demand.items_completed == 0
    assert demand.completed_at is None and demand.cursor == ""
    with campaign_clock(instant + timedelta(days=1)), work_transaction():
        assert allocate_activation(transition.pk).run_id == task.pk
    assert TaskRun.objects.filter(task_type="activation_catchup").count() == 1
    demand.refresh_from_db()
    assert demand.cutoff == instant


def test_allocation_failure_rolls_back_lifecycle_and_demand(tmp_path, monkeypatch):
    """Allocation failure cannot leave an active campaign or orphaned demand."""
    _, campaign, actor = draft_campaign(tmp_path)

    def fail(**kwargs):
        """Inject failure at the real task-allocation boundary before commit."""
        raise RuntimeError("synthetic task allocation failure")

    monkeypatch.setattr(
        "parishkit.stewardship.campaigns.catchup_allocation.enqueue", fail
    )
    with (
        campaign_clock(campaign.active_configuration.starts_at),
        pytest.raises(RuntimeError),
    ):
        command(campaign, actor, Action.ACTIVATE)
    campaign.refresh_from_db()
    assert (
        campaign.state == "draft"
        and SystemConfiguration.objects.get().mode == "testing"
    )
    assert not ActivationCatchUpDemand.objects.exists()
    assert not CampaignTransition.objects.filter(action="activate").exists()
    assert not TaskRun.objects.filter(task_type="activation_catchup").exists()


def test_prestart_activation_does_not_allocate_catchup(tmp_path):
    """A future campaign has no missed cutoff work to enumerate yet."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(seconds=1)):
        command(campaign, actor, Action.ACTIVATE)
    assert not ActivationCatchUpDemand.objects.exists()
    assert not TaskRun.objects.filter(task_type="activation_catchup").exists()


def test_database_rejects_activation_when_allocation_is_omitted(tmp_path, monkeypatch):
    """The outer commit enforces the pairing even if the Python owner regresses."""
    _, campaign, actor = draft_campaign(tmp_path)
    monkeypatch.setattr(
        "parishkit.stewardship.campaigns.catchup_allocation.allocate_activation",
        lambda transition_id: None,
    )
    with (
        campaign_clock(campaign.active_configuration.starts_at),
        pytest.raises(IntegrityError, match="atomic catch-up task binding"),
    ):
        command(campaign, actor, Action.ACTIVATE)
    campaign.refresh_from_db()
    assert campaign.state == "draft" and not ActivationCatchUpDemand.objects.exists()


def test_prestart_transition_is_not_direct_activation_permission(tmp_path):
    """Allocation reads immutable evidence, not caller-provided lifecycle fields."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(seconds=1)):
        transition = command(campaign, actor, Action.ACTIVATE)
    with (
        work_transaction(),
        pytest.raises(StorageInvariantError, match="direct activation"),
    ):
        allocate_activation(transition.pk)


def test_real_web_role_commits_activation_and_canonical_task(tmp_path):
    """The activation writer needs no scheduler role or private worker grant."""
    _, campaign, actor = draft_campaign(tmp_path)
    generation = prepared_tokens(campaign, actor)
    with (
        campaign_clock(campaign.active_configuration.starts_at),
        task_login(ServiceRole.WEB),
    ):
        transition = command(
            campaign, actor, Action.ACTIVATE, token_generation_id=generation
        )
        demand = ActivationCatchUpDemand.objects.get(activation_id=transition.pk)
        assert (
            TaskRun.objects.get(pk=demand.task_root_id).domain_request_id == demand.pk
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE stewardship_family_token_generation "
        "SET state='cancelled',version=version+1",
        "UPDATE stewardship_campaign_credentials "
        "SET go_live_gate=false,version=version+1",
    ],
)
def test_web_cannot_directly_mutate_activation_credential_effects(tmp_path, statement):
    """Row-lock authority does not grant token cancellation or gate release."""
    _, campaign, actor = draft_campaign(tmp_path)
    prepared_tokens(campaign, actor)
    with (
        task_login(ServiceRole.WEB, exact=True),
        connection.cursor() as cursor,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        cursor.execute(statement)
