"""Compiled boundary tasks preserve timezone resolution and concurrent ownership."""

from datetime import timedelta

import pytest

from parishkit.stewardship.campaigns.boundary_production import produce_boundaries
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    CampaignBoundaryOccurrence,
    CampaignTransition,
)
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..campaign_factory import campaign as campaign_record
from .campaign_builders import campaign_clock, command, draft_campaign
from .test_boundary_production_postgresql import scheduled  # noqa: F401
from .test_boundary_tasks_postgresql import run
from .test_campaign_races_postgresql import concurrent_calls

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("order", [(0, 1), (1, 0), (1, 1)])
def test_competing_compiled_workers_preserve_single_ordered_history(scheduled, order):  # noqa: F811
    """Real independent worker connections cannot commit an intermediate active gap."""
    with campaign_clock(scheduled.active_configuration.ends_at):
        with scheduler_session() as guard:
            work = produce_boundaries(guard)
        outcomes = concurrent_calls(
            *(lambda index=index: run(work[index]) for index in order)
        )
    assert "committed" in outcomes
    scheduled.refresh_from_db()
    assert scheduled.state == "closed"
    assert CampaignTransition.objects.filter(action="start").count() == 1
    assert CampaignTransition.objects.filter(action="close").count() == 1
    assert CampaignBoundaryOccurrence.objects.filter(state="succeeded").count() == 2
    assert CampaignBoundaryOccurrence.objects.values("task_id").distinct().count() == 1


@pytest.mark.parametrize(
    "start,end,hours",
    [("2026-03-07", "2026-03-08", 47), ("2026-10-31", "2026-11-01", 49)],
)
def test_compiled_dst_boundaries_keep_resolved_utc_instants(
    tmp_path, monkeypatch, start, end, hours
):
    """Spring/fall transitions use canonical parish dates, never a fixed 24h offset."""
    row = campaign_record()
    row["values"].update(start_date=start, end_date=end)
    from ..campaign_factory import schedule
    from . import campaign_builders

    monkeypatch.setattr(
        campaign_builders, "schedule", lambda owner: schedule(owner, date=start)
    )
    _, campaign, actor = draft_campaign(tmp_path, row)
    projection = campaign.active_configuration
    assert (projection.ends_at - projection.starts_at).total_seconds() == hours * 3600
    with campaign_clock(projection.starts_at - timedelta(seconds=1)):
        command(campaign, actor, Action.ACTIVATE)
        with scheduler_session() as guard:
            assert produce_boundaries(guard) == ()
    with campaign_clock(projection.ends_at):
        with scheduler_session() as guard:
            opening, closing = produce_boundaries(guard)
        assert (opening.due_at, closing.due_at) == (
            projection.starts_at,
            projection.ends_at,
        )
        assert run(closing)
    campaign.refresh_from_db()
    assert campaign.state == "closed"
