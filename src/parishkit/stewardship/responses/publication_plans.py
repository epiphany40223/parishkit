"""Persist, confirm and record ParishSoft publication plans (#528, DAT-09.01).

The planner (``responses.publication``) computes each entity's plan from its
proposals and a fresh ParishSoft read; it holds proposal ids but not their
versions. This module keeps what a later worker needs to re-check, right
before each PUT, that nothing changed since the Administrator confirmed:

- ``record_plan`` (the worker, after preflight) stores the plans with each
  proposal's version and the read and planned payload hashes;
- ``confirm_plan`` (web) records an Administrator's signed-in
  confirmation of exactly the counts shown;
- ``matches_confirmed`` is the pure pre-PUT check of a fresh plan against
  the confirmed one;
- ``record_outcome`` (the worker, under the source mutation fence) records
  one write attempt on an entity.

Nothing here calls ParishSoft or changes a proposal. SQL independently
checks every row, its authority and its audit
(``schema/migrations/0087_publication_plans.sql``), so a forged or stale
write is refused even if this code were bypassed. Audit events hold an
outcome word and a count only, never a value or a name.
"""

import re
from uuid import UUID

from django.db import transaction

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.schemas import Outcome as AuditOutcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.leases import verify_source

from .publication import Outcome
from .publication_models import (
    FAILURE_KIND,
    PublicationConfirmation,
    PublicationEntity,
    PublicationField,
    PublicationOutcome,
    PublicationPlan,
)

# The audit word for each entity result.
_RESULT_AUDIT = {
    "published": AuditOutcome.SUCCEEDED,
    "invalidated": AuditOutcome.CANCELLED,
    "failed": AuditOutcome.FAILED,
}


def _uuids(*values):
    """Whether every value is a real UUID (never a string that parses as one)."""
    return all(isinstance(value, UUID) for value in values)


def _count(value):
    """Whether ``value`` is a plain non-negative integer count."""
    return type(value) is int and 0 <= value < 2**31


def record_plan(
    actor_id,
    *,
    campaign_id,
    source_snapshot_id,
    organization_id,
    request_key,
    plans,
    rows,
):
    """Store one preflight's plans, replay-safe, and return the plan row.

    ``actor_id`` is the Administrator who asked for the preflight;
    ``plans`` are ``publication.EntityPlan`` values and ``rows`` the
    proposals they were planned from, whose ``version`` each field row keeps.
    An entity with no plannable proposal has nothing to record and is left
    out. SQL requires the plan to be written in one transaction, each
    version to be the proposal's current one (so a decision or edit made
    since the read refuses the plan, and preflight runs again) and each
    outcome to agree with its proposal. The same ``request_key`` returns the
    plan already recorded for it.
    """
    if (
        not _uuids(actor_id, campaign_id, source_snapshot_id, request_key)
        or type(organization_id) is not int
        or not 0 < organization_id < 2**63
    ):
        raise ValueError("Invalid publication plan.")
    plans = [plan for plan in plans if plan.fields]
    versions = {row.pk: row.version for row in rows}
    if any(item.proposal_id not in versions for plan in plans for item in plan.fields):
        raise ValueError("A planned field has no proposal row.")
    with work_transaction():
        previous = PublicationPlan.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if (previous.campaign_id, previous.source_snapshot_id) != (
                campaign_id,
                source_snapshot_id,
            ):
                raise ValueError("This request key is already bound.")
            return previous
        record = PublicationPlan.objects.create(
            actor_id=actor_id,
            campaign_id=campaign_id,
            source_snapshot_id=source_snapshot_id,
            organization_id=organization_id,
            request_key=request_key,
            # The database stamps the system mode; this placeholder is
            # replaced by the guard.
            testing=True,
        )
        for plan in plans:
            entity = PublicationEntity.objects.create(
                plan=record,
                entity_kind=plan.entity_kind,
                entity_key=plan.entity_key,
                blocked=plan.blocked,
                read_sha256=plan.current_digest,
                planned_sha256=plan.planned_digest,
            )
            PublicationField.objects.bulk_create(
                PublicationField(
                    entity=entity,
                    proposal_id=item.proposal_id,
                    proposal_version=versions[item.proposal_id],
                    field=item.field,
                    outcome=item.outcome.value,
                )
                for item in plan.fields
            )
        record_action(
            Action.CENSUS_PUBLICATION_PLANNED,
            actor_kind=ActorKind.SYSTEM,
            subject_id=record.pk,
            campaign_id=campaign_id,
            current_parish=True,
            context={"outcome": AuditOutcome.SUCCEEDED, "count": len(plans)},
        )
    record.refresh_from_db()
    return record


def confirmation_counts(plan_id):
    """What the Administrator confirms: entities and fields to write, and blocked.

    The same counts the confirmation guard computes, so a page showing them
    and the confirmation it records agree.
    """
    entities = PublicationEntity.objects.filter(plan_id=plan_id)
    writing = entities.filter(planned_sha256__isnull=False)
    return {
        "write_entities": writing.count(),
        "write_fields": PublicationField.objects.filter(
            entity__in=writing, outcome=Outcome.WRITE.value
        ).count(),
        "blocked_entities": entities.filter(blocked=True).count(),
    }


def confirm_plan(
    store,
    actor_id,
    plan_id,
    *,
    session_id,
    authenticated_at,
    request_key,
    write_entities,
    write_fields,
    blocked_entities,
):
    """Record an Administrator's final confirmation of one plan, replay-safe.

    The counts are those the Administrator was shown
    (``confirmation_counts``). SQL also requires the live session's sign-in,
    a plan from Production mode less than an hour old, every planned
    proposal still at its recorded version, and no change the plan writes
    being written by another unfinished confirmed plan. The same
    ``request_key`` with the same intent returns the first confirmation;
    with another it is refused.
    """
    counts = (write_entities, write_fields, blocked_entities)
    if (
        not _uuids(actor_id, plan_id, session_id, request_key)
        or not all(map(_count, counts))
        or write_entities < 1
    ):
        raise ValueError("Invalid publication confirmation.")
    with work_transaction():
        if not allows(current_principal(store, actor_id), Capability.PUBLISH_CENSUS):
            raise PermissionError("Publishing to ParishSoft is unavailable.")
        previous = PublicationConfirmation.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if (
                previous.plan_id,
                previous.write_entities,
                previous.write_fields,
                previous.blocked_entities,
            ) != (plan_id, *counts):
                raise ValueError("This request key is already bound.")
            return previous
        plan = PublicationPlan.objects.get(pk=plan_id)
        confirmation = PublicationConfirmation.objects.create(
            actor_id=actor_id,
            plan=plan,
            session_id=session_id,
            authenticated_at=authenticated_at,
            request_key=request_key,
            write_entities=write_entities,
            write_fields=write_fields,
            blocked_entities=blocked_entities,
        )
        record_action(
            Action.CENSUS_PUBLICATION_CONFIRMED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            subject_id=confirmation.pk,
            campaign_id=plan.campaign_id,
            current_parish=True,
            context={"outcome": AuditOutcome.CHANGED, "count": write_entities},
        )
        return confirmation


def matches_confirmed(entity, fields, fresh, rows):
    """Whether a fresh plan is exactly the confirmed one, so its PUT may go ahead.

    ``entity`` and ``fields`` are the confirmed ``PublicationEntity`` and its
    ``PublicationField`` rows; ``fresh`` is the planner's ``EntityPlan`` from
    the uncached read made right before the PUT, and ``rows`` the proposals
    it was planned from. It matches only when the entity is the same, it is
    still written and not blocked, every proposal has the same version (so
    no decision or Administrator edit changed) and the same outcome, and
    both payload hashes are unchanged (so ParishSoft holds what was read).
    Anything else invalidates the entity without a write.
    """
    versions = {row.pk: row.version for row in rows}
    recorded = {
        (item.proposal_id, item.proposal_version, item.outcome) for item in fields
    }
    planned = {
        (item.proposal_id, versions.get(item.proposal_id), item.outcome.value)
        for item in fresh.fields
    }
    return (
        (fresh.entity_kind, fresh.entity_key) == (entity.entity_kind, entity.entity_key)
        and not fresh.blocked
        and fresh.payload is not None
        and entity.planned_sha256 is not None
        and recorded == planned
        and fresh.current_digest == entity.read_sha256
        and fresh.planned_digest == entity.planned_sha256
    )


def record_outcome(entity_id, *, claim, result, failure_kind=""):
    """Record one write attempt on a confirmed entity under the source fence.

    ``claim`` is the worker's live ``SourceClaim`` in its ``publication``
    phase; it is verified here and SQL checks the lease's fence again.
    ``failure_kind`` is a sanitized word (never provider text) and only for
    ``failed``. The attempt number is the next one; SQL refuses any attempt
    after a published or invalidated outcome, so a success is never
    replayed, and a concurrent attempt at the same number fails its key.
    """

    if (
        not _uuids(entity_id)
        or result not in _RESULT_AUDIT
        or getattr(claim, "phase", None) != "publication"
        or not isinstance(failure_kind, str)
        or (result == "failed") != bool(re.fullmatch(FAILURE_KIND, failure_kind))
    ):
        raise ValueError("Invalid publication outcome.")
    with transaction.atomic():
        verify_source(claim)
        entity = PublicationEntity.objects.select_related("plan").get(pk=entity_id)
        latest = (
            PublicationOutcome.objects.filter(entity=entity)
            .order_by("-attempt")
            .values_list("attempt", flat=True)
            .first()
        )
        outcome = PublicationOutcome.objects.create(
            entity=entity,
            attempt=(latest or 0) + 1,
            result=result,
            failure_kind=failure_kind,
            fence=claim.fence,
        )
        record_action(
            Action.CENSUS_PUBLICATION_RECORDED,
            actor_kind=ActorKind.SYSTEM,
            subject_id=outcome.pk,
            campaign_id=entity.plan.campaign_id,
            current_parish=True,
            context={
                "outcome": _RESULT_AUDIT[result],
                "count": PublicationField.objects.filter(
                    entity=entity, outcome=Outcome.WRITE.value
                ).count(),
            },
        )
        return outcome
