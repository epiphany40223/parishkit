"""Atomic lifecycle/runtime history and coexistence with YAML activation."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    Campaign,
    CampaignTransition,
    RuntimeTransition,
)
from parishkit.stewardship.campaigns.runtime import transition_campaign
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from ..configuration_factory import configuration_version
from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    change,
    command,
    draft_campaign,
    prepared_tokens,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_initial_configuration_cannot_leave_dangling_campaign_selection():
    """Bootstrap must establish runtime authority before a separate draft selection."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.campaigns.admission import validate_installation

    from ..campaign_factory import campaign, schedule

    owner = campaign()
    for sections in ({"campaigns": [owner]}, {"schedules": [schedule(owner["id"])]}):
        with pytest.raises(ConfigError, match="Initial configuration"):
            validate_installation({"sections": sections})


def test_activation_and_subsequent_policy_edit_have_independent_versions(tmp_path):
    """Runtime actions cannot create gaps or collisions in YAML activation ordinals."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        result = command(campaign, actor, Action.ACTIVATE)
    campaign.refresh_from_db()
    runtime = SystemConfiguration.objects.get()
    assert campaign.state == "scheduled" and campaign.structural_locked
    assert not campaign.ever_active and runtime.mode == "production"
    assert runtime.version == runtime.configuration_sequence + 1
    assert not ActivationCatchUpDemand.objects.exists()
    assert RuntimeTransition.objects.get().campaign_transition_id == result.pk
    version = configuration_version(
        AppliedConfigurationVersion.objects.get(
            pk=runtime.active_configuration_id
        ).canonical_document
    )
    parish = version.document()["sections"]["parish"][0]
    edited = change(
        store,
        version,
        actor,
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"phone": "+12025550199"},
            }
        ],
    )
    assert edited.state == "applied"
    runtime.refresh_from_db()
    assert (
        runtime.mode == "production"
        and runtime.version == runtime.configuration_sequence + 1
    )


def test_transition_replay_is_exact_and_rechecks_admission(tmp_path):
    """A saved command is not authority; retry does not add history or catch-up rows."""
    _, campaign, actor = draft_campaign(tmp_path)
    runtime = SystemConfiguration.objects.get()
    arguments = dict(
        campaign_id=campaign.pk,
        action=Action.ACTIVATE,
        request_id=uuid4(),
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        token_generation_id=prepared_tokens(campaign, actor),
    )
    with campaign_clock(campaign.active_configuration.starts_at):
        first = transition_campaign(**arguments)
        second = transition_campaign(**arguments)
        assert first.pk == second.pk
        assert (
            CampaignTransition.objects.count()
            == ActivationCatchUpDemand.objects.count()
            == 1
        )
        with pytest.raises(StaleRecordError):
            transition_campaign(**(arguments | {"request_id": uuid4()}))
        for change in (
            {"action": Action.WITHDRAW},
            {"actor_id": uuid4()},
            {"token_generation_id": uuid4()},
            {"boundary_id": uuid4()},
            {"task_fence": 1},
            {"reason": "different"},
            {"expected_version": arguments["expected_version"] + 1},
            {"expected_runtime_version": arguments["expected_runtime_version"] + 1},
        ):
            with pytest.raises(StorageInvariantError, match="different intent"):
                transition_campaign(**(arguments | change))

        def revoked(*_):
            raise PermissionError("revoked")

        with pytest.raises(PermissionError):
            transition_campaign(**(arguments | {"admit": revoked}))


def test_runtime_and_campaign_mutations_without_command_are_rejected(tmp_path):
    """Raw writes cannot change state/mode or bypass lifecycle attribution."""
    draft_campaign(tmp_path)
    for table, update in [
        (
            "stewardship_campaign",
            "state='active', structural_locked=true, ever_active=true",
        ),
        ("stewardship_system_configuration", "mode='production'"),
    ]:
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(f"UPDATE {table} SET {update}, version=version+1")
    assert Campaign.objects.get().state == "draft"
    assert SystemConfiguration.objects.get().mode == "testing"


@pytest.mark.parametrize(
    "change",
    [
        {"token_generation_id": "00000000-0000-0000-0000-000000000001"},
        {"boundary_id": "00000000-0000-0000-0000-000000000001"},
        {"task_fence": True},
        {"task_fence": "1"},
        {"task_fence": 0},
        {"reason": 1},
        {"reason": "x" * 1025},
    ],
)
def test_optional_lifecycle_metadata_is_canonical_before_writing(tmp_path, change):
    """No implicit Django coercion can make the first command and replay disagree."""
    _, campaign, actor = draft_campaign(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        transition_campaign(
            **(
                dict(
                    campaign_id=campaign.pk,
                    action=Action.ACTIVATE,
                    request_id=uuid4(),
                    expected_version=campaign.version,
                    expected_runtime_version=SystemConfiguration.objects.get().version,
                    actor_id=actor,
                    correlation_id=uuid4(),
                    admit=admit_test_work,
                    token_generation_id=uuid4(),
                )
                | change
            )
        )
    assert not CampaignTransition.objects.exists()
