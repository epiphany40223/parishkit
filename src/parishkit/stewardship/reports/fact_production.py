"""Transactional fact hints and bounded ordinary rebuild allocation.

Submission and source owners hint in their domain transaction. The scheduler
also reconciles current inputs, including local midnight and configuration
changes, so a restart cannot strand a day with no submissions or source poll.
"""

from uuid import uuid5
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import Max

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StorageInvariantError

from .demand import request_rebuild
from .export_services import admit_campaign
from .fact_tasks import TASK_TYPE
from .inputs import FactInputs
from .models import CampaignFactRebuildDemand


def hint_current_facts(campaign_id, *, source_id=None):
    """Record both scopes from one transactionally coherent source/version tuple."""
    require_work_order()
    admit_campaign(campaign_id, mutating=True)
    campaign = (
        Campaign.objects.select_for_update(of=("self",))
        .select_related("active_configuration")
        .get(pk=campaign_id)
    )
    projection = campaign.active_configuration
    current_source = SourceCurrent.objects.values_list("snapshot_id", flat=True).get(
        singleton=True
    )
    if current_source is None:
        return ()
    if source_id is not None and source_id != current_source:
        raise PermissionError("Report hint does not own the current source.")
    watermark = (
        Submission.objects.filter(campaign_id=campaign_id, mode="live").aggregate(
            value=Max("campaign_sequence")
        )["value"]
        or 0
    )
    through = min(
        projection.end_date, _now().astimezone(ZoneInfo(projection.timezone)).date()
    )
    return tuple(
        request_rebuild(
            FactInputs(
                campaign_id,
                population,
                current_source,
                watermark,
                projection.pk,
                through,
            ),
            admit=lambda action, inputs: (
                action == "request" and inputs.campaign_id == campaign_id
            ),
        )
        for population in ("historical", "current")
    )


def produce_facts(guard):
    """Reconcile the current campaign and allocate at most one root per due scope."""
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Fact production requires actual scheduler ownership.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Fact production must own its transaction.")
    guard.check()
    with work_transaction():
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).first()
        if campaign_id is None:
            return ()
        try:
            hints = hint_current_facts(campaign_id)
        except PermissionError:
            return ()
        result = []
        for hint in hints:
            guard.check()
            demand = CampaignFactRebuildDemand.objects.select_for_update().get(
                pk=hint.pk
            )
            if (
                demand.pending_due_at is None
                or demand.pending_due_at > database_now()
                or demand.claimed_generation_id is not None
                or TaskRun.objects.filter(
                    task_type=TASK_TYPE,
                    domain_request_id=demand.pk,
                    state__in=NONTERMINAL_STATES,
                ).exists()
            ):
                continue

            def admit(action, status, demand_id=demand.pk):
                """The compiled producer can allocate only this exact demand window."""
                admit_campaign(campaign_id, mutating=True)
                return (
                    action == "enqueue"
                    and status.task_type == TASK_TYPE
                    and status.domain_request_id == demand_id
                )

            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=demand.pk,
                actor_id=None,
                correlation_id=demand.pk,
                idempotency_key=uuid5(
                    demand.pk, f"report-facts:{demand.pending_revision}"
                ),
                admit=admit,
            )
            result.append(task.root_id)
        guard.check()
        return tuple(result)
