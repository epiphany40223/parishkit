"""Explicit ready-generation pins and bounded, reference-safe derived cleanup."""

from uuid import UUID

from django.db import connection, transaction

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.source.pins import release_snapshot_pin
from parishkit.stewardship.source.snapshot_models import (
    SourceSnapshot,
    SourceSnapshotPin,
)

from .facts import FactUnavailable, _admit, fact_inputs
from .models import CampaignDailyFactSet, CampaignFactPin, FactCompactionRecord


def pin_facts(fact_set_id, *, parent_kind, parent_id, admit):
    """Lock selection and durable protection together; never substitute inputs."""
    if not isinstance(parent_id, UUID):
        raise ValueError("Fact protection requires a parent UUID.")
    with transaction.atomic():
        record = (
            CampaignDailyFactSet.objects.select_for_update()
            .filter(pk=fact_set_id)
            .first()
        )
        if record is None or record.state != "ready":
            raise FactUnavailable("The exact fact generation is not ready or retained.")
        _admit(admit, "pin", fact_inputs(record))
        pin, _ = CampaignFactPin.objects.get_or_create(
            fact_set=record,
            parent_kind=parent_kind,
            parent_id=parent_id,
        )
        return pin


def release_fact_pin(pin_id, *, admit):
    """Only the owning retained parent may release this specific durable pin."""
    with transaction.atomic():
        pin = CampaignFactPin.objects.filter(pk=pin_id).first()
        if pin is None:
            _admit(admit, "unpin", None)
            return False
        record = CampaignDailyFactSet.objects.select_for_update().get(
            pk=pin.fact_set_id
        )
        _admit(admit, "unpin", fact_inputs(record))
        return CampaignFactPin.objects.filter(pk=pin_id).delete()[0] == 1


def compact_facts(campaign_id, claim, *, admit, limit=50):
    """Remove a bounded set of whole superseded generations and exact input pins.

    Owning housekeeping supplies concrete campaign purge/restore admission.
    Locks use campaign -> source -> generation order, matching build allocation.
    Shared readers and late pins win over cleanup; no reader lease is inferred.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Fact cleanup requires a bounded positive batch size.")
    with transaction.atomic():
        lock_task_claim(claim)
        Campaign.objects.select_for_update().get(pk=campaign_id)
        # Only UUIDs are selected here. Each generation is reselected and checked
        # under its own row lock before any deletion can become visible.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,source_id FROM stewardship_daily_fact_set "
                "WHERE campaign_id=%s AND stewardship_fact_disposable(id) "
                "ORDER BY created_at,id LIMIT %s",
                (campaign_id, limit),
            )
            candidates = cursor.fetchall()
        removed = []
        for identifier, source_id in candidates:
            # Avoid waiting on an in-use source input or a live lazy renderer.
            if (
                not SourceSnapshot.objects.select_for_update(skip_locked=True)
                .filter(pk=source_id)
                .exists()
            ):
                continue
            record = (
                CampaignDailyFactSet.objects.select_for_update(skip_locked=True)
                .filter(pk=identifier)
                .first()
            )
            if record is None:
                continue
            _admit(admit, "compact", fact_inputs(record))
            with connection.cursor() as cursor:
                cursor.execute("SELECT stewardship_fact_disposable(%s)", (identifier,))
                if not cursor.fetchone()[0]:
                    continue
                evidence = FactCompactionRecord.objects.create(
                    campaign_id=record.campaign_id,
                    fact_set_id=record.pk,
                    population_scope=record.population_scope,
                    source_generation=record.source_generation,
                    submission_watermark=record.submission_watermark,
                    timezone_configuration_id=record.timezone_configuration_id,
                    through_date=record.through_date,
                    row_count=record.expected_count,
                    task_id=claim.run_id,
                    task_fence=claim.fence,
                    worker_id=claim.worker_id,
                    actor_id=claim.worker_id,
                )
                cursor.execute(
                    "DELETE FROM stewardship_daily_fact WHERE fact_set_id=%s",
                    (identifier,),
                )
                cursor.execute(
                    "DELETE FROM stewardship_daily_fact_set WHERE id=%s", (identifier,)
                )
            inputs = fact_inputs(record)
            for pin in SourceSnapshotPin.objects.filter(
                snapshot_id=source_id, parent_kind="facts", parent_id=identifier
            ):
                release_snapshot_pin(
                    pin.pk,
                    admit=lambda *args, inputs=inputs: admit("compact", inputs),
                )
            record_action(
                Action.FACTS_COMPACTED,
                actor_kind=ActorKind.SYSTEM,
                actor_id=claim.worker_id,
                subject_id=evidence.pk,
                context={"count": record.expected_count, "outcome": Outcome.SUCCEEDED},
            )
            removed.append(identifier)
        lock_task_claim(claim)
        return removed
