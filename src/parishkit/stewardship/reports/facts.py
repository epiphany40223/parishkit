"""Transactional fact-generation storage; report calculations are a later owner.

Only a running, freshly admitted internal task can stage or publish calculations.
An exact current-scope input pins the complete source corpus before construction.
Historical scope uses permanent manifests/provenance, not source membership maps.
"""

from contextlib import contextmanager
from uuid import uuid4

from django.db import connection, transaction

from parishkit.stewardship.campaigns.models import Campaign, CampaignConfiguration
from parishkit.stewardship.jobs.ownership import database_now, lock_task_claim
from parishkit.stewardship.source.pins import pin_snapshot
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.storage import StorageInvariantError

from .inputs import FactInputs, expected_dates, validate_day
from .models import CampaignDailyFact, CampaignDailyFactSet, CampaignFactPointer


class FactUnavailable(RuntimeError):
    """An exact generation is not ready/retained or its build ownership changed."""


def _admit(admit, action, inputs):
    """Owning policy must explicitly authorize every transaction under its locks."""
    if not callable(admit) or admit(action, inputs) is not True:
        raise PermissionError("Fact workflow admission was denied.")


def fact_inputs(record):
    """Reconstruct exact identity without exposing mutable ORM state to callbacks."""
    return FactInputs(
        record.campaign_id,
        record.population_scope,
        record.source_id,
        record.submission_watermark,
        record.timezone_configuration_id,
        record.through_date,
    )


def _owned(fact_set_id, claim, admit):
    """Keep task/retry-root locks before generation locks through the domain write."""
    lock_task_claim(claim)
    campaign_id = CampaignDailyFactSet.objects.values_list(
        "campaign_id", flat=True
    ).get(pk=fact_set_id)
    Campaign.objects.select_for_update().get(pk=campaign_id)
    record = CampaignDailyFactSet.objects.select_for_update().get(pk=fact_set_id)
    _admit(admit, "build", fact_inputs(record))
    if (
        record.state != "building"
        or record.task_id != claim.run_id
        or record.task_fence != claim.fence
        or record.worker_id != claim.worker_id
    ):
        raise FactUnavailable("Fact generation is not owned by this live builder.")
    return record


def begin_fact_set(inputs, claim, *, admit):
    """Deduplicate exact inputs and pin required source data before building rows."""
    if not isinstance(inputs, FactInputs):
        raise ValueError("A frozen fact input tuple is required.")
    with transaction.atomic():
        lock_task_claim(claim)
        _admit(admit, "create", inputs)
        # Serialize per campaign while allocating an exact input generation. No
        # network calls or calculations occur inside this short transaction.
        Campaign.objects.select_for_update().get(pk=inputs.campaign_id)
        source = SourceSnapshot.objects.select_for_update().get(pk=inputs.source_id)
        projection = CampaignConfiguration.objects.get(
            pk=inputs.timezone_configuration_id
        )
        if projection.record_id != inputs.campaign_id or source.state != "promoted":
            raise FactUnavailable("Fact input records do not describe this campaign.")
        key = dict(
            campaign_id=inputs.campaign_id,
            population_scope=inputs.population_scope,
            source_generation=source.generation,
            submission_watermark=inputs.submission_watermark,
            timezone_configuration_id=inputs.timezone_configuration_id,
            through_date=inputs.through_date,
        )
        existing = (
            CampaignDailyFactSet.objects.select_for_update().filter(**key).first()
        )
        if existing is not None:
            return existing
        dates = expected_dates(
            projection.start_date, projection.end_date, inputs.through_date
        )
        identifier = uuid4()
        if inputs.population_scope == "current":
            pin_snapshot(
                source.pk,
                parent_kind="facts",
                parent_id=identifier,
                admit=lambda action, snapshot: admit("create", inputs),
            )
        return CampaignDailyFactSet.objects.create(
            id=identifier,
            **key,
            source=source,
            first_date=dates[0] if dates else None,
            last_date=dates[-1] if dates else None,
            expected_count=len(dates),
            task_id=claim.run_id,
            task_fence=claim.fence,
            worker_id=claim.worker_id,
            actor_id=claim.worker_id,
        )


def stage_fact_days(fact_set_id, claim, *, days, admit):
    """Stage a bounded complete-value batch; exact repeat chunks are harmless."""
    if type(days) is not list or len(days) > 500:
        raise ValueError("Fact staging requires a bounded row list.")
    for row in days:
        validate_day(row)
    if len({row["local_date"] for row in days}) != len(days):
        raise ValueError("Fact staging cannot repeat a local date in one batch.")
    with transaction.atomic():
        record = _owned(fact_set_id, claim, admit)
        existing = {
            row.local_date: row
            for row in CampaignDailyFact.objects.filter(
                fact_set=record, local_date__in=[item["local_date"] for item in days]
            )
        }
        pending = []
        for values in days:
            prior = existing.get(values["local_date"])
            if prior is not None:
                if any(
                    getattr(prior, field) != value for field, value in values.items()
                ):
                    raise FactUnavailable(
                        "Repeated fact input differs from its stored row."
                    )
            else:
                row = CampaignDailyFact(fact_set=record, **values)
                row.full_clean(
                    exclude={
                        "created_at",
                        "source_generation",
                        "source_as_of",
                        "pledge_total",
                    },
                    validate_unique=False,
                    validate_constraints=False,
                )
                pending.append(row)
        CampaignDailyFact.objects.bulk_create(pending, batch_size=250)
        lock_task_claim(claim)
        return len(pending)


def publish_fact_set(fact_set_id, claim, *, admit, interactive=False):
    """SQL checks completeness before ready; older exact builds do not regress UI."""
    if type(interactive) is not bool:
        raise ValueError("Interactive publication must be an explicit boolean.")
    with transaction.atomic():
        record = _owned(fact_set_id, claim, admit)
        record.state = "ready"
        record.ready_at = database_now()
        record.version += 1
        record.save()
        if interactive:
            _publish_pointer(record)
        lock_task_claim(claim)
        return record


def _publish_pointer(record):
    """All input dimensions must be current/non-regressing before changing the UI."""
    campaign = Campaign.objects.select_for_update().get(pk=record.campaign_id)
    if campaign.active_configuration_id != record.timezone_configuration_id:
        return False
    pointer = (
        CampaignFactPointer.objects.select_for_update()
        .filter(
            campaign_id=record.campaign_id, population_scope=record.population_scope
        )
        .first()
    )
    if pointer is None:
        CampaignFactPointer.objects.create(
            campaign_id=record.campaign_id,
            population_scope=record.population_scope,
            fact_set=record,
        )
        return True
    prior = pointer.fact_set
    if (
        record.source_generation < prior.source_generation
        or record.submission_watermark < prior.submission_watermark
        or record.through_date < prior.through_date
    ):
        return False
    pointer.fact_set = record
    pointer.version += 1
    pointer.save()
    return True


@contextmanager
def read_fact_set(fact_set_id, *, admit):
    """Retain shared generation protection through all lazy queries and rendering.

    HTTP/download consumers additionally own the bounded campaign read guard;
    this generation lock does not replace authorization or response deadlines.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM stewardship_daily_fact_set WHERE id=%s "
                "AND state='ready' FOR SHARE",
                (fact_set_id,),
            )
            if cursor.fetchone() is None:
                raise FactUnavailable(
                    "The exact fact generation is not ready or retained."
                )
        record = CampaignDailyFactSet.objects.get(pk=fact_set_id)
        _admit(admit, "read", fact_inputs(record))
        yield record


def require_fact_transaction():
    """Owning demand/retention workflows keep their domain locks through effects."""
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise StorageInvariantError(
            "Fact state requires an outer PostgreSQL transaction."
        )
