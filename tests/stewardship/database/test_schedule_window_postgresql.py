"""Timezone-only cadence changes retain SQL admission and rollback protections."""

from datetime import UTC, datetime

import pytest
from django.db import IntegrityError

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
from .test_digest_schedule_planning_postgresql import add_digest

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


@pytest.mark.parametrize("bypass_preflight", [False, True])
def test_digest_date_change_cannot_bypass_running_work(
    tmp_path, monkeypatch, bypass_preflight
):
    """Digest date edits share Python preflight and SQL fail-closed rules."""
    store, campaign, actor = draft_campaign(tmp_path)
    identifier = add_digest(store, campaign)
    definition = ScheduleDefinition.objects.get(pk=identifier)
    prior = definition.current_revision_id
    due = datetime(2026, 10, 3, tzinfo=UTC)
    with campaign_clock(due):
        row = occurrence(
            definition, actor, target="admins", slot="2026-10-01", due_at=due
        )
        run = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
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
                    "values": {"end_date": "2026-10-30"},
                }
            ],
        )
    definition.refresh_from_db()
    row.refresh_from_db()
    assert definition.current_revision_id == prior and row.state == "running"
