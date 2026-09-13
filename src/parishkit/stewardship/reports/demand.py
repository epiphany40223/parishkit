"""Transactional debounce/claim bookkeeping, separate from calculation scheduling.

Domain events call request_rebuild inside their own transaction so their visible
change and requested calculation commit together. Exact exports/digests call
begin_fact_set directly and never consume interactive pending revisions.
"""

from datetime import timedelta

from django.db import transaction

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.ownership import database_now, lock_task_claim
from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from .facts import (
    FactUnavailable,
    _admit,
    begin_fact_set,
    fact_inputs,
    require_fact_transaction,
)
from .inputs import FactInputs
from .models import CampaignDailyFactSet, CampaignFactRebuildDemand


def requested_inputs(demand):
    """The latest pending tuple is independent of the currently claimed build."""
    return FactInputs(
        demand.campaign_id,
        demand.population_scope,
        demand.requested_source_id,
        demand.requested_submission_watermark,
        demand.requested_timezone_configuration_id,
        demand.requested_through_date,
    )


def request_rebuild(inputs, *, admit):
    """Atomically hint current inputs; repeated identical hints do not delay work."""
    require_fact_transaction()
    if not isinstance(inputs, FactInputs):
        raise ValueError("A frozen fact input tuple is required.")
    _admit(admit, "request", inputs)
    campaign = Campaign.objects.select_for_update().get(pk=inputs.campaign_id)
    if campaign.active_configuration_id != inputs.timezone_configuration_id:
        raise FactUnavailable("Interactive demand requires current campaign inputs.")
    source = SourceSnapshot.objects.get(pk=inputs.source_id, state="promoted")
    row = (
        CampaignFactRebuildDemand.objects.select_for_update()
        .filter(
            campaign_id=inputs.campaign_id, population_scope=inputs.population_scope
        )
        .first()
    )
    if row is not None:
        if requested_inputs(row) == inputs:
            return row
        if (
            source.generation < row.requested_source_generation
            or inputs.submission_watermark < row.requested_submission_watermark
            or inputs.through_date < row.requested_through_date
        ):
            raise FactUnavailable("Interactive fact demand cannot regress.")
    now = database_now()
    if row is None:
        row = CampaignFactRebuildDemand(
            campaign_id=inputs.campaign_id,
            population_scope=inputs.population_scope,
            pending_first_at=now,
        )
    else:
        row.version += 1
        row.pending_revision += 1
        row.pending_first_at = row.pending_first_at or now
    row.requested_source = source
    row.requested_source_generation = source.generation
    row.requested_submission_watermark = inputs.submission_watermark
    row.requested_timezone_configuration_id = inputs.timezone_configuration_id
    row.requested_through_date = inputs.through_date
    row.pending_last_at = now
    row.pending_due_at = min(
        now + timedelta(seconds=5), row.pending_first_at + timedelta(seconds=30)
    )
    row.save()
    return row


def claim_rebuild(campaign_id, population_scope, claim, *, admit):
    """Freeze due inputs and consume only that window; one interactive build runs."""
    with transaction.atomic():
        lock_task_claim(claim)
        Campaign.objects.select_for_update().get(pk=campaign_id)
        row = CampaignFactRebuildDemand.objects.select_for_update().get(
            campaign_id=campaign_id,
            population_scope=population_scope,
        )
        inputs = requested_inputs(row)
        _admit(admit, "claim", inputs)
        if row.claimed_generation_id is not None or row.pending_due_at is None:
            return None
        if row.pending_due_at > database_now():
            return None
        record = begin_fact_set(inputs, claim, admit=admit)
        if record.state != "ready" and (
            record.state != "building"
            or record.task_id != claim.run_id
            or record.task_fence != claim.fence
            or record.worker_id != claim.worker_id
        ):
            return None
        row.claimed_generation = record
        row.claimed_task_id = claim.run_id
        row.claimed_task_fence = claim.fence
        row.claimed_worker_id = claim.worker_id
        row.claimed_revision = row.pending_revision
        row.pending_first_at = row.pending_last_at = row.pending_due_at = None
        row.version += 1
        row.save()
        lock_task_claim(claim)
        return row


def complete_rebuild(demand_id, claim, *, revision, admit):
    """Release only this claimed generation; never clear newer pending events."""
    if type(revision) is not int or revision < 1:
        raise ValueError("An exact positive demand revision is required.")
    with transaction.atomic():
        lock_task_claim(claim)
        campaign_id = CampaignFactRebuildDemand.objects.values_list(
            "campaign_id", flat=True
        ).get(pk=demand_id)
        Campaign.objects.select_for_update().get(pk=campaign_id)
        row = CampaignFactRebuildDemand.objects.select_for_update().get(pk=demand_id)
        if (
            row.claimed_revision != revision
            or row.claimed_task_id != claim.run_id
            or row.claimed_task_fence != claim.fence
            or row.claimed_worker_id != claim.worker_id
        ):
            raise FactUnavailable("Rebuild completion no longer owns this revision.")
        record = CampaignDailyFactSet.objects.select_for_update().get(
            pk=row.claimed_generation_id
        )
        _admit(admit, "complete", fact_inputs(record))
        if record.state != "ready":
            raise FactUnavailable("Rebuild completion requires ready facts.")
        row.claimed_generation = row.claimed_task = None
        row.claimed_task_fence = row.claimed_worker_id = None
        row.version += 1
        row.save()
        lock_task_claim(claim)
        return row
