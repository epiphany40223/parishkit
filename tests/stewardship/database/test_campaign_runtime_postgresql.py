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
from parishkit.stewardship.storage import StaleRecordError

from ..configuration_factory import configuration_version
from .campaign_builders import admit_test_work, campaign_clock, command, draft_campaign
from .test_policy_postgresql import change

pytestmark = pytest.mark.django_db(transaction=True)


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
    assert ActivationCatchUpDemand.objects.get().activation_id == result.pk
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
        token_generation_id=uuid4(),
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
