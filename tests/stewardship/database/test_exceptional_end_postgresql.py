"""Canonical YAML end edits and reopen cannot partially change runtime facts."""

from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
from parishkit.stewardship.campaigns.catchup import bind_catchup, checkpoint_catchup
from parishkit.stewardship.campaigns.configuration_intents import (
    bind_configuration_intent,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CampaignTransition,
)
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .campaign_builders import admit_test_work, campaign_clock, command, draft_campaign
from .test_boundary_catchup_postgresql import claimed_task

pytestmark = pytest.mark.django_db(transaction=True)


def complete_empty_catchup(campaign, actor):
    """Finish actual direct-activation work; pre-start activation has no demand."""
    campaign.refresh_from_db()
    demand = ActivationCatchUpDemand.objects.filter(campaign=campaign).first()
    if demand is None:
        assert campaign.state == "scheduled"
        return
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


def end_request(store, campaign, actor, action, end_date="2026-11-10"):
    """Stage the date candidate, then bind reviewed runtime inputs separately."""
    campaign.refresh_from_db()
    runtime = SystemConfiguration.objects.get()
    request = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"end_date": end_date},
            }
        ],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    token = uuid4() if action == "reopen" else None
    bind_configuration_intent(
        campaign_id=campaign.pk,
        request_id=request.request_id,
        action=action,
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        actor_id=actor,
        correlation_id=uuid4(),
        token_generation_id=token,
        admit=admit_test_work,
    )
    return request, token


def test_exceptional_bind_reports_typed_stale_version(tmp_path):
    """Stale confirmation is a refreshable conflict, not an SQL invariant error."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        request, _ = end_request(store, campaign, actor, "edit_end")
        from parishkit.stewardship.campaigns.models import CampaignConfigurationIntent

        # A distinct command uses the same patch but intentionally stale versions.
        new = record_request(
            base_digest=store.active().digest,
            patch=[
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(campaign.pk),
                    "values": {"end_date": "2026-11-11"},
                }
            ],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
        with pytest.raises(StaleRecordError):
            bind_configuration_intent(
                campaign_id=campaign.pk,
                request_id=new.request_id,
                action="edit_end",
                expected_version=campaign.version + 1,
                expected_runtime_version=SystemConfiguration.objects.get().version,
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_test_work,
            )
        assert (
            CampaignConfigurationIntent.objects.get().request_id == request.request_id
        )


def test_live_end_edit_preserves_structural_lock_and_mode(tmp_path):
    """Only the expressly bound end edit can cross the live configuration lock."""
    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        request, _ = end_request(store, campaign, actor, "edit_end")
        result = install_request(
            store,
            request_id=request.request_id,
            correlation_id=uuid4(),
            admit_campaign=admit_test_work,
        )
    campaign.refresh_from_db()
    assert result.state == "applied" and campaign.state == "active"
    assert (
        campaign.structural_locked
        and campaign.active_configuration.starts_at == original.starts_at
    )
    assert campaign.active_configuration.end_date.isoformat() == "2026-11-10"
    assert SystemConfiguration.objects.get().mode == "production"
    from django.db import IntegrityError

    from parishkit.stewardship.campaigns.configuration_intents import (
        abort_configuration_intent,
    )
    from parishkit.stewardship.campaigns.models import CampaignConfigurationAbort

    with pytest.raises(IntegrityError, match="Only unapplied"):
        abort_configuration_intent(
            store,
            request_id=request.request_id,
            actor_id=actor,
            correlation_id=uuid4(),
            reason="Already applied",
            admit=admit_test_work,
        )
    assert not CampaignConfigurationAbort.objects.exists()
    with pytest.raises(
        StorageInvariantError, match="receipt requires current admission"
    ):
        install_request(store, request_id=request.request_id, correlation_id=uuid4())
    assert (
        install_request(
            store,
            request_id=request.request_id,
            correlation_id=uuid4(),
            admit_campaign=admit_test_work,
        ).state
        == "applied"
    )


def test_reopen_selects_yaml_state_and_prepared_generation_together(tmp_path):
    """Fresh owning admission is mandatory, including a failed confirmation's retry."""
    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    complete_empty_catchup(campaign, actor)
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    with campaign_clock(original.ends_at):
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    with campaign_clock(original.ends_at + timedelta(days=1)):
        request, token = end_request(store, campaign, actor, "reopen")
        with pytest.raises(StorageInvariantError, match="current admission"):
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            )
        campaign.refresh_from_db()
        assert (
            campaign.state == "closed"
            and campaign.active_configuration_id == original.pk
        )
        result = install_request(
            store,
            request_id=request.request_id,
            correlation_id=uuid4(),
            admit_campaign=admit_test_work,
        )
    campaign.refresh_from_db()
    assert result.state == "applied" and campaign.state == "active"
    assert campaign.active_token_generation_id == token
    transition = CampaignTransition.objects.get(action="reopen")
    assert transition.prior_projection_id == original.pk
    assert campaign.active_configuration.configuration_id == store.active().version_id
    assert campaign.readiness_revision == 2
    assert ActivationCatchUpDemand.objects.count() == 1


@pytest.mark.parametrize("action", ["edit_end", "reopen"])
@pytest.mark.parametrize("candidate", ["unchanged", "past"])
def test_invalid_exceptional_end_fails_before_yaml_selection(
    tmp_path, action, candidate
):
    """No-op and nonextending/elapsed candidates receive clean failure receipts."""
    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    if action == "reopen":
        complete_empty_catchup(campaign, actor)
        run = claimed_task("campaign_boundary", campaign.pk, actor)
        with campaign_clock(original.ends_at):
            apply_due_boundaries(
                campaign_id=campaign.pk,
                task_id=run.run_id,
                fence=run.fence,
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_test_work,
            )
    instant = (
        original.ends_at
        if action == "reopen"
        else original.ends_at - timedelta(hours=1)
    )
    date = (
        original.end_date
        if candidate == "unchanged"
        else original.end_date - timedelta(days=1)
    )
    with campaign_clock(instant):
        request, _ = end_request(store, campaign, actor, action, date.isoformat())
        before = store.active()
        result = install_request(
            store,
            request_id=request.request_id,
            correlation_id=uuid4(),
            admit_campaign=admit_test_work,
        )
    assert result.state == "failed" and result.failure_code == "invalid_candidate"
    assert store.active() == before
    campaign.refresh_from_db()
    assert campaign.active_configuration_id == original.pk


@pytest.mark.parametrize(
    "interruption", ["prepared", "yaml_activated", "prepared_advanced"]
)
def test_unapplied_exceptional_abort_recovers_after_file_failure(
    tmp_path, monkeypatch, interruption
):
    """Expired readiness can be cancelled without rewinding any applied history."""
    from parishkit.stewardship.accounts.configuration_installation import (
        DatabaseMaterializer,
    )
    from parishkit.stewardship.campaigns.configuration_intents import (
        abort_configuration_intent,
    )
    from parishkit.stewardship.campaigns.models import CampaignConfigurationAbort

    store, campaign, actor = draft_campaign(tmp_path)
    original = store.active()
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        request, _ = end_request(store, campaign, actor, "edit_end")

        def interrupted(materializer, digest):
            """Crash after durable YAML selection, before database activation."""
            materializer.checkpoint("yaml_activated")
            raise RuntimeError("synthetic interruption")

        original_checkpoint = DatabaseMaterializer.checkpoint

        def interrupted_checkpoint(materializer, state, **kwargs):
            """Crash with durable projections but no prepared receipt."""
            if state == "prepared":
                raise RuntimeError("synthetic interruption")
            return original_checkpoint(materializer, state, **kwargs)

        with monkeypatch.context() as patch:
            if interruption.startswith("prepared"):
                patch.setattr(
                    DatabaseMaterializer, "checkpoint", interrupted_checkpoint
                )
            else:
                patch.setattr(DatabaseMaterializer, "activate", interrupted)
            with pytest.raises(RuntimeError, match="synthetic interruption"):
                install_request(
                    store,
                    request_id=request.request_id,
                    correlation_id=uuid4(),
                    admit_campaign=admit_test_work,
                )
        assert store.active().version_id == (
            original.version_id
            if interruption.startswith("prepared")
            else request.candidate_version_id
        )
        assert (
            SystemConfiguration.objects.get().active_configuration_id
            == original.version_id
        )

        def failed_select(version):
            """Crash after the durable abort, leaving its exact recovery repeatable."""
            raise OSError("synthetic file failure")

        resolver = uuid4()
        with monkeypatch.context() as patch:
            # Even the pre-selection crash has a durable abort before checkpoint IO.
            if interruption.startswith("prepared"):
                patch.setattr(
                    DatabaseMaterializer,
                    "restore_aborted_candidate",
                    lambda _: failed_select(None),
                )
            else:
                patch.setattr(store, "select", failed_select)
            with pytest.raises(OSError, match="synthetic file failure"):
                abort_configuration_intent(
                    store,
                    request_id=request.request_id,
                    actor_id=resolver,
                    correlation_id=uuid4(),
                    reason="Readiness expired",
                    admit=admit_test_work,
                )
        assert CampaignConfigurationAbort.objects.count() == 1
        assert CampaignConfigurationAbort.objects.get().actor_id == resolver
        if interruption == "prepared_advanced":
            from .test_policy_postgresql import change

            parish = original.document()["sections"]["parish"][0]
            assert (
                change(
                    store,
                    original,
                    actor,
                    [
                        {
                            "operation": "update",
                            "section": "parish",
                            "id": parish["id"],
                            "values": {"name": "Updated after abort journal"},
                        }
                    ],
                ).state
                == "applied"
            )
            original = store.active()
        recovered = install_request(
            store,
            request_id=request.request_id,
            correlation_id=uuid4(),
            admit_campaign=admit_test_work,
        )
    assert recovered.state == "failed" and recovered.failure_code == "invalid_candidate"
    assert store.active() == original
    campaign.refresh_from_db()
    assert (
        campaign.state == "active"
        and campaign.active_configuration.configuration_id == original.version_id
    )
