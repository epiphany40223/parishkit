"""Transactional fact hints and bounded ordinary rebuild allocation.

Submission and source owners hint in their domain transaction. The scheduler
also reconciles current inputs, including local midnight and configuration
changes, so a restart cannot strand a day with no submissions or source poll.
"""

from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.models import Max

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.loop_settings import LoopSettings
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StorageInvariantError

from .demand import request_rebuild, requested_inputs
from .export_services import admit_campaign
from .fact_fields import rebuild_execution_key
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
    current_source = SourceCurrent.objects.values_list("snapshot_id", flat=True).get(
        singleton=True
    )
    if current_source is None:
        return ()
    if source_id is not None and source_id != current_source:
        raise PermissionError("Report hint does not own the current source.")
    return tuple(
        request_rebuild(
            inputs,
            admit=lambda action, inputs: (
                action == "request" and inputs.campaign_id == campaign_id
            ),
        )
        for inputs in _current_inputs(
            campaign_id, campaign.active_configuration, current_source
        )
    )


def _current_inputs(campaign_id, projection, current_source):
    """Both populations' current fact inputs: source, watermark, local day."""
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
        FactInputs(
            campaign_id, population, current_source, watermark, projection.pk, through
        )
        for population in ("historical", "current")
    )


def _waiting(demand, now):
    """Whether the locked pass would leave this demand alone (no root to allocate).

    It allocates a root only for a due, unclaimed demand window with no
    unfinished root; the same test is used under the lock below.
    """
    return (
        demand.pending_due_at is None
        or demand.pending_due_at > now
        or demand.claimed_generation_id is not None
        or TaskRun.objects.filter(
            task_type=TASK_TYPE,
            domain_request_id=demand.pk,
            state__in=NONTERMINAL_STATES,
        ).exists()
    )


def _settled(settings):
    """Whether the locked pass would neither hint new inputs nor allocate a root.

    That pass records new demand only when the current inputs differ from a
    population's recorded request (a new source, a live response, a new
    configuration or local day), and allocates a root only for a demand
    _waiting() would not leave alone and whose window has no root yet
    (_allocated()). This compares the same inputs, built from the loop's
    snapshot rows and a fresh clock, without the work-order lock (#715).
    Campaign admission is not checked: the locked pass refuses a held
    campaign itself, so this can only send it more work, never less.
    """
    campaign = settings.campaign
    if settings.campaign_id is None:
        return True
    if campaign is None:
        return False
    current_source = SourceCurrent.objects.values_list("snapshot_id", flat=True).get(
        singleton=True
    )
    if current_source is None:
        return True
    demands = {
        row.population_scope: row
        for row in CampaignFactRebuildDemand.objects.filter(campaign_id=campaign.pk)
    }
    for inputs in _current_inputs(
        campaign.pk, campaign.active_configuration, current_source
    ):
        demand = demands.get(inputs.population_scope)
        if demand is None or requested_inputs(demand) != inputs:
            return False
        if not _waiting(demand, database_now()) and not _allocated(demand):
            return False
    return True


def _allocated(demand):
    """Whether this demand window's root already exists, in any state.

    The locked pass enqueues with the window's execution key, which then
    returns that root and writes nothing: a failed pre-claim root keeps the
    key until an explicit retry (see produce_facts).
    """
    return TaskRun.objects.filter(
        task_type=TASK_TYPE,
        idempotency_key=str(rebuild_execution_key(demand.pk, demand.pending_revision)),
    ).exists()


def produce_facts(guard, *, settings=None):
    """Reconcile the current campaign and allocate at most one root per due scope.

    ``settings`` is the scheduler loop's LoopSettings; when _settled() finds
    nothing to hint or allocate, the work-order lock is skipped (#715).
    """
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Fact production requires actual scheduler ownership.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Fact production must own its transaction.")
    guard.check()
    with transaction.atomic():
        settled = _settled(LoopSettings() if settings is None else settings)
    if settled:
        return ()
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
            if _waiting(demand, database_now()):
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
                idempotency_key=rebuild_execution_key(
                    demand.pk, demand.pending_revision
                ),
                admit=admit,
            )
            # A failed pre-claim root still owns this execution key. Only an
            # explicit linked retry can resume it; do not report it as new work
            # or silently reset its exhausted retry budget on every tick.
            if task.state == "queued":
                result.append(task.root_id)
        guard.check()
        return tuple(result)
