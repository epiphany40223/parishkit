"""Synthetic report storage inputs; no report calculation or live provider."""

from datetime import date, timedelta

from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.reports.facts import begin_fact_set, stage_fact_days
from parishkit.stewardship.reports.inputs import FactInputs
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .campaign_builders import draft_campaign
from .source_builders import running_source_task
from .test_source_snapshots_postgresql import permit, prepared, publish


def fact_fixture(tmp_path):
    """Use the real installer/source publication and a separate fenced fact task."""
    _, campaign, _ = draft_campaign(tmp_path)
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    snapshot, claim = prepared()
    snapshot = publish(snapshot, claim)
    release_source(claim)
    task = running_source_task()
    owner = TaskClaim(task["task_id"], task["task_fence"], task["worker_id"])
    inputs = FactInputs(
        campaign.pk,
        "current",
        snapshot.pk,
        1,
        campaign.active_configuration_id,
        date(2026, 10, 2),
    )
    return inputs, owner, snapshot


def staged_facts(inputs, owner, snapshot):
    """Stage two coherent daily rows using their source manifest's exact instant."""
    fact_set = begin_fact_set(inputs, owner, admit=permit)
    days = [
        dict(
            local_date=fact_set.first_date + timedelta(days=offset),
            first_responses=1,
            cumulative_responses=offset + 1,
            cohort_denominator=10,
            source_generation=snapshot.generation,
            source_as_of=snapshot.promoted_at,
            population_available=True,
            pledge_available=False,
            pledge_total=None,
        )
        for offset in range(fact_set.expected_count)
    ]
    stage_fact_days(fact_set.pk, owner, days=days, admit=permit)
    return fact_set, days
