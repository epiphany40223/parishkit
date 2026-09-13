"""Recover exact derived work without consuming any later interactive demand."""

import re

from django.db import connection, transaction

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.ownership import lock_task_claim

from .facts import FactUnavailable, _admit, _owned, fact_inputs
from .models import CampaignDailyFactSet, CampaignFactRebuildDemand


def fail_fact_set(fact_set_id, claim, *, code, admit):
    """Record only a closed-format diagnostic code; never store provider exceptions."""
    if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,47}", code) is None:
        raise ValueError("A safe fact failure code is required.")
    with transaction.atomic():
        record = _owned(fact_set_id, claim, admit)
        record.state, record.failure_code = "failed", code
        record.version += 1
        record.save()
        lock_task_claim(claim)
        return record


def _recover(record, claim, admit):
    """Resume deterministic chunks after the old builder is no longer live."""
    _admit(admit, "recover", fact_inputs(record))
    if record.state == "ready":
        return record
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_fact_live(%s,%s,%s)",
            (record.task_id, record.task_fence, record.worker_id),
        )
        live = cursor.fetchone()[0]
    same = (
        record.task_id == claim.run_id
        and record.task_fence == claim.fence
        and record.worker_id == claim.worker_id
    )
    if live and not same:
        raise FactUnavailable("A live fact builder still owns these exact inputs.")
    record.task_id, record.task_fence, record.worker_id = (
        claim.run_id,
        claim.fence,
        claim.worker_id,
    )
    record.state, record.failure_code = "building", ""
    record.version += 1
    record.save()
    return record


def recover_fact_set(fact_set_id, claim, *, admit):
    """Resume an exact non-interactive build; interactive claims use recover_rebuild."""
    with transaction.atomic():
        lock_task_claim(claim)
        campaign_id = CampaignDailyFactSet.objects.values_list(
            "campaign_id", flat=True
        ).get(pk=fact_set_id)
        Campaign.objects.select_for_update().get(pk=campaign_id)
        if CampaignFactRebuildDemand.objects.filter(
            claimed_generation_id=fact_set_id
        ).exists():
            raise FactUnavailable(
                "Interactive recovery must also transfer its demand claim."
            )
        record = CampaignDailyFactSet.objects.select_for_update().get(pk=fact_set_id)
        result = _recover(record, claim, admit)
        lock_task_claim(claim)
        return result


def recover_rebuild(demand_id, claim, *, revision, admit):
    """Transfer one abandoned claim and generation; preserve newer pending windows."""
    if type(revision) is not int or revision < 1:
        raise ValueError("An exact positive demand revision is required.")
    with transaction.atomic():
        lock_task_claim(claim)
        campaign_id = CampaignFactRebuildDemand.objects.values_list(
            "campaign_id", flat=True
        ).get(pk=demand_id)
        Campaign.objects.select_for_update().get(pk=campaign_id)
        demand = CampaignFactRebuildDemand.objects.select_for_update().get(pk=demand_id)
        if demand.claimed_revision != revision or demand.claimed_generation_id is None:
            raise FactUnavailable("Rebuild recovery no longer owns this revision.")
        same = (
            demand.claimed_task_id == claim.run_id
            and demand.claimed_task_fence == claim.fence
            and demand.claimed_worker_id == claim.worker_id
        )
        if not same:
            # A ready generation does not release its interactive demand. The
            # original builder can still be between publish and completion.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT stewardship_fact_live(%s,%s,%s)",
                    (
                        demand.claimed_task_id,
                        demand.claimed_task_fence,
                        demand.claimed_worker_id,
                    ),
                )
                if cursor.fetchone()[0]:
                    raise FactUnavailable("A live fact builder still owns this demand.")
        record = CampaignDailyFactSet.objects.select_for_update().get(
            pk=demand.claimed_generation_id
        )
        _recover(record, claim, admit)
        if same:
            lock_task_claim(claim)
            return demand
        demand.claimed_task_id, demand.claimed_task_fence = claim.run_id, claim.fence
        demand.claimed_worker_id = claim.worker_id
        demand.version += 1
        demand.save()
        lock_task_claim(claim)
        return demand
