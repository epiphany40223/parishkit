"""Timezone-only cadence changes retain SQL admission and rollback protections."""

from importlib import import_module

import pytest
from django.db import IntegrityError, connection

from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from .campaign_builders import (
    advance,
    campaign_clock,
    change,
    claimed_task,
    draft_campaign,
    occurrence,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("bypass_preflight", [False, True])
def test_timezone_only_change_cannot_bypass_running_schedule_work(
    tmp_path, monkeypatch, bypass_preflight
):
    """Even unchanged mail YAML must not replace cadence while delivery is in flight."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    prior = definition.current_revision_id
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        run = claimed_task("schedule_occurrence", row.pk, actor)
        row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    if bypass_preflight:
        monkeypatch.setattr(
            "parishkit.stewardship.campaigns.admission.validate_installation",
            lambda *args, **kwargs: None,
        )
    with pytest.raises(
        IntegrityError if bypass_preflight else CampaignAdmissionUnavailable
    ):
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(campaign.pk),
                    "values": {"timezone": "America/Los_Angeles"},
                }
            ],
        )
    campaign.refresh_from_db()
    definition.refresh_from_db()
    row.refresh_from_db()
    assert campaign.active_configuration.timezone == "America/New_York"
    assert definition.current_revision_id == prior and row.state == "running"


def test_empty_window_guard_roundtrip_and_populated_downgrade(tmp_path):
    """Empty rollback is repeatable; retained schedule selections prevent downgrade."""
    migration = import_module(
        "parishkit.stewardship.campaigns.migrations.0035_schedule_window_selections"
    )
    with connection.schema_editor() as editor:
        migration.backward(None, editor)
        migration.forward(None, editor)
    store, campaign, actor = draft_campaign(tmp_path)
    with (
        pytest.raises(IntegrityError, match="downgrade"),
        connection.schema_editor() as editor,
    ):
        migration.backward(None, editor)
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(campaign.pk),
                    "values": {"timezone": "America/Los_Angeles"},
                }
            ],
        ).state
        == "applied"
    )
    revision = ScheduleDefinition.objects.get().current_revision
    assert revision.configuration_id == store.active().version_id
    assert revision.actor_id == actor
    assert revision.correlation_id == revision.configuration.correlation_id
