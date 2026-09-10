"""Real transaction ordering and durable catch-up holds without broker/provider IO."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
from parishkit.stewardship.campaigns.catchup import (
    bind_catchup,
    checkpoint_catchup,
    record_catchup_failure,
)
from parishkit.stewardship.campaigns.lifecycle import Action, portal_admitted
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CampaignBoundaryOccurrence,
    CampaignTransition,
)
from parishkit.stewardship.campaigns.runtime import campaign_facts
from parishkit.stewardship.jobs.storage import change_run
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .campaign_builders import (
    admit_task_work,
    admit_test_work,
    campaign_clock,
    claimed_task,
    command,
    draft_campaign,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_overdue_boundaries_commit_start_then_close_and_retry_exactly(tmp_path):
    """A close hint cannot strand scheduled state or expose overdue portal access."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    arguments = dict(
        campaign_id=campaign.pk,
        task_id=run.run_id,
        fence=run.fence,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with campaign_clock(campaign.active_configuration.ends_at):
        result = apply_due_boundaries(**arguments)
        assert [(r.kind, r.state) for r in result] == [
            ("start", "succeeded"),
            ("close", "succeeded"),
        ]
        assert [r.pk for r in apply_due_boundaries(**arguments)] == [
            r.pk for r in result
        ]
    campaign.refresh_from_db()
    assert campaign.state == "closed" and campaign.active_token_generation_id is None
    assert list(
        CampaignTransition.objects.order_by("created_at").values_list(
            "action", flat=True
        )
    ) == ["activate", "start", "close"]
    assert not portal_admitted(
        campaign_facts(campaign, SystemConfiguration.objects.get()),
        campaign.active_configuration.ends_at,
    )


def test_wrong_boundary_fence_rolls_back_both_occurrences(tmp_path):
    """No partial allocation, task binding or history survives failed ownership."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    with (
        campaign_clock(campaign.active_configuration.ends_at),
        pytest.raises(IntegrityError),
    ):
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence + 1,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    campaign.refresh_from_db()
    assert campaign.state == "scheduled"
    assert not CampaignBoundaryOccurrence.objects.exists()
    assert CampaignTransition.objects.count() == 1


def test_obsolete_start_still_requires_fenced_worker(tmp_path):
    """Worker-produced skips cannot bypass TaskRun ownership through a null binding."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        run = claimed_task("campaign_boundary", campaign.pk, actor)
        arguments = dict(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        with pytest.raises(IntegrityError):
            apply_due_boundaries(**(arguments | {"fence": run.fence + 1}))
        assert not CampaignBoundaryOccurrence.objects.exists()
        (result,) = apply_due_boundaries(**arguments)
    assert result.state == "skipped" and result.task_fence == run.fence


def test_close_admission_sees_started_facts_and_rejection_rolls_back(tmp_path):
    """Each due boundary has fresh owning admission, within one atomic transaction."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    seen = []

    def admission(action, current, runtime, subject):
        """Refuse close after observing start's transaction-local effects."""
        seen.append((action, current.state))
        if action is Action.CLOSE:
            raise StorageInvariantError("synthetic close admission")

    with (
        campaign_clock(campaign.active_configuration.ends_at),
        pytest.raises(StorageInvariantError),
    ):
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admission,
        )
    assert seen == [(Action.START, "scheduled"), (Action.CLOSE, "active")]
    campaign.refresh_from_db()
    assert campaign.state == "scheduled"
    assert not CampaignBoundaryOccurrence.objects.exists()
    assert CampaignTransition.objects.count() == 1


def test_catchup_checkpoint_is_exact_fenced_and_independent_of_task_completion(
    tmp_path,
):
    """Finishing an execution does not claim enumeration/coverage is complete."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()
    run = claimed_task("activation_catchup", demand.pk, actor)
    bind_catchup(
        demand_id=demand.pk,
        task_root_id=run.root_id,
        source_snapshot_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    arguments = dict(
        demand_id=demand.pk,
        group_key="first",
        cursor="family:1",
        items=5,
        phase="enumerating",
        complete=False,
        task_id=run.run_id,
        fence=run.fence,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    first = checkpoint_catchup(**arguments)
    assert checkpoint_catchup(**arguments).pk == first.pk
    with pytest.raises(StaleRecordError, match="current fenced input"):
        checkpoint_catchup(**(arguments | {"fence": run.fence + 1}))
    with pytest.raises(StorageInvariantError):
        checkpoint_catchup(**(arguments | {"items": 6}))
    with pytest.raises(StaleRecordError):
        checkpoint_catchup(
            **(arguments | {"group_key": "second", "fence": run.fence + 1})
        )
    demand.refresh_from_db()
    failure_args = dict(
        demand_id=demand.pk,
        request_id=uuid4(),
        expected_version=demand.version,
        task_id=run.run_id,
        fence=run.fence,
        code="enumeration_failed",
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with pytest.raises(ValueError):
        record_catchup_failure(**(failure_args | {"code": "untrusted provider detail"}))
    for value in (True, 0, "1"):
        with pytest.raises(ValueError):
            record_catchup_failure(**(failure_args | {"fence": value}))
    with pytest.raises(StaleRecordError):
        record_catchup_failure(**(failure_args | {"fence": run.fence + 1}))
    failure = record_catchup_failure(**failure_args)
    assert record_catchup_failure(**failure_args).pk == failure.pk
    with pytest.raises(StaleRecordError):
        record_catchup_failure(**(failure_args | {"actor_id": uuid4()}))
    demand.refresh_from_db()
    assert demand.failure_code == "enumeration_failed"
    assert demand.groups_completed == 1 and demand.items_completed == 5
    assert demand.completed_at is None
    checkpoint_catchup(**(arguments | {"group_key": "second", "items": 0}))
    demand.refresh_from_db()
    assert demand.failure_code == "" and demand.groups_completed == 2
    change_run(
        run_id=run.run_id,
        action="complete",
        expected_version=run.version,
        fence=run.fence,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_task_work,
    )
    demand.refresh_from_db()
    assert demand.completed_at is None and demand.items_completed == 5
    with pytest.raises(IntegrityError), transaction.atomic():
        ActivationCatchUpDemand.objects.filter(pk=demand.pk).update(
            completed_at=campaign.active_configuration.ends_at, version=F("version") + 1
        )


def test_catchup_final_checkpoint_releases_hold_and_cannot_be_rewritten(tmp_path):
    """Only a verified fenced checkpoint makes the durable demand complete."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()
    run = claimed_task("activation_catchup", demand.pk, actor)
    bind_catchup(
        demand_id=demand.pk,
        task_root_id=run.root_id,
        source_snapshot_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    checkpoint_catchup(
        demand_id=demand.pk,
        group_key="complete",
        cursor="end",
        items=0,
        phase="complete",
        complete=True,
        task_id=run.run_id,
        fence=run.fence,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    demand.refresh_from_db()
    assert demand.completed_at is not None
    assert not campaign_facts(
        campaign, SystemConfiguration.objects.get()
    ).catch_up_pending
    with pytest.raises(IntegrityError), transaction.atomic():
        ActivationCatchUpDemand.objects.filter(pk=demand.pk).update(
            phase="pending", version=F("version") + 1
        )


def test_catchup_binding_replay_cannot_rebind_source_or_execution(tmp_path):
    """The immutable activation cutoff and worker inputs survive exact retries."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()
    run = claimed_task("activation_catchup", demand.pk, actor)
    args = dict(
        demand_id=demand.pk,
        task_root_id=run.root_id,
        source_snapshot_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    first = bind_catchup(**args)
    replay = bind_catchup(**args)
    assert (replay.pk, replay.version, replay.cutoff) == (
        first.pk,
        first.version,
        first.cutoff,
    )
    for field in ("task_root_id", "source_snapshot_id"):
        with pytest.raises(StorageInvariantError, match="already bound"):
            bind_catchup(**(args | {field: uuid4()}))
    demand.refresh_from_db()
    assert demand.version == first.version


def test_prestart_activation_and_withdrawal_need_no_catchup(tmp_path):
    """No overdue work exists before start; withdrawal must not need fake coverage."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(seconds=1)):
        command(campaign, actor, Action.ACTIVATE)
        assert not ActivationCatchUpDemand.objects.exists()
        command(campaign, actor, Action.WITHDRAW, reason="Postponed")
    campaign.refresh_from_db()
    assert campaign.state == "draft"
    assert not ActivationCatchUpDemand.objects.exists()
