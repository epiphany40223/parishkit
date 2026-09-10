"""Internal occurrence storage with exact retry identities and semantic coverage.

BG-01/BG-02 own eligibility, outbox effects and reconciliation evidence. Every
operation rechecks their callback under campaign/schedule locks; these functions
are deliberately not public task or provider APIs.
"""

import hashlib
import json
from uuid import UUID

from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .models import (
    OccurrenceTransition,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from .runtime import campaign_transaction


def occurrence_key(revision_id, mode, target, slot):
    """Hash an unambiguous revision-specific identity; no private values in errors."""
    if not isinstance(revision_id, UUID) or mode not in {"testing", "production"}:
        raise ValueError("Invalid occurrence revision or mode.")
    if any(
        type(value) is not str or not value or len(value) > 128
        for value in (target, slot)
    ):
        raise ValueError("Invalid semantic occurrence identity.")
    return hashlib.sha256(
        json.dumps(
            [str(revision_id), mode, target, slot],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def create_occurrence(
    *,
    definition_id,
    revision_id,
    mode,
    target,
    slot,
    due_at,
    actor_id,
    correlation_id,
    admit,
):
    """Allocate once after current admission; old revisions never gain new work."""
    if not callable(admit):
        raise TypeError("Occurrence admission callback is required.")
    key = occurrence_key(revision_id, mode, target, slot)
    definition = ScheduleDefinition.objects.get(pk=definition_id)
    with campaign_transaction(
        definition.campaign_id, correlation_id=correlation_id
    ) as (campaign, runtime):
        definition = ScheduleDefinition.objects.select_for_update().get(
            pk=definition_id
        )
        admit("create_occurrence", campaign, runtime, definition)
        existing = ScheduleOccurrence.objects.filter(occurrence_key=key).first()
        if existing:
            if existing.definition_id != definition_id or existing.due_at != due_at:
                raise StorageInvariantError("Occurrence key has different intent.")
            return existing
        return ScheduleOccurrence.objects.create(
            definition=definition,
            revision_id=revision_id,
            mode=mode,
            routing="production" if mode == "production" else "testing_override",
            target=target,
            slot=slot,
            due_at=due_at,
            occurrence_key=key,
            pause_version=campaign.pause_version
            if campaign.delivery_paused and mode == "production"
            else None,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )


def change_occurrence(
    *,
    occurrence_id,
    state,
    expected_version,
    actor_id,
    correlation_id,
    admit,
    task_id=None,
    fence=None,
    reason="",
    replacement_id=None,
    retry_command_id=None,
):
    """Advance one version, with explicit retry replay and TaskRun-owned leases.

    The owning callback proves safe retry/reconciliation/coalescing. It must
    also lock and reconcile related outbox state atomically before returning.
    The occurrence never invents a new retry identity or provider-success fact.
    """
    if not callable(admit) or type(expected_version) is not int or expected_version < 1:
        raise TypeError("Occurrence transition requires admission and a version.")
    if state not in {
        "pending",
        "running",
        "delivery_unknown",
        "succeeded",
        "skipped",
        "coalesced",
        "failed",
    }:
        raise ValueError("Invalid occurrence state.")
    original = ScheduleOccurrence.objects.select_related("definition").get(
        pk=occurrence_id
    )
    with campaign_transaction(
        original.definition.campaign_id, correlation_id=correlation_id
    ) as (campaign, runtime):
        ScheduleDefinition.objects.select_for_update().get(pk=original.definition_id)
        row = ScheduleOccurrence.objects.select_for_update().get(pk=occurrence_id)
        admit("change_occurrence", campaign, runtime, row)
        if retry_command_id is not None:
            if not isinstance(retry_command_id, UUID) or state != "pending":
                raise ValueError("Invalid explicit occurrence retry command.")
            prior = OccurrenceTransition.objects.filter(
                occurrence=row, retry_command_id=retry_command_id
            ).first()
            if prior:
                if (
                    prior.version != expected_version + 1
                    or prior.actor_id != actor_id
                    or prior.reason != reason
                ):
                    raise StorageInvariantError(
                        "Occurrence retry command has different intent."
                    )
                return row
            if row.state != "failed":
                raise StorageInvariantError(
                    "Explicit retry requires a failed occurrence."
                )
        if row.version != expected_version:
            raise StaleRecordError("Occurrence changed; reload before retrying.")
        if state == "running":
            if not isinstance(task_id, UUID) or type(fence) is not int or fence < 1:
                raise ValueError("Occurrence claim requires task ownership.")
            task = TaskRun.objects.select_for_update().get(pk=task_id)
            row.task_id, row.worker_id, row.fence = task.pk, actor_id, fence
            row.lease_expires_at, row.heartbeat_at = (
                task.lease_expires_at,
                task.heartbeat_at,
            )
            if row.state != "running":
                row.attempts += 1
        else:
            if row.state == "running" and (
                fence != row.fence or actor_id != row.worker_id
            ):
                raise StaleRecordError("Occurrence claim is no longer owned.")
            row.lease_expires_at = None
        row.state, row.reason, row.replacement_id = state, reason, replacement_id
        row.retry_command_id = retry_command_id
        row.version += 1
        row.actor_id, row.correlation_id = actor_id, correlation_id
        row.save()
        return row


def record_fulfillment(*, occurrence_id, disposition, actor_id, correlation_id, admit):
    """Cover the original semantic slot without confusing coalescing with success."""
    if not callable(admit) or disposition not in {"delivered", "coalesced"}:
        raise TypeError("Semantic coverage requires an owning outcome verifier.")
    original = ScheduleOccurrence.objects.select_related("definition").get(
        pk=occurrence_id
    )
    with campaign_transaction(
        original.definition.campaign_id, correlation_id=correlation_id
    ) as (campaign, runtime):
        row = ScheduleOccurrence.objects.select_for_update().get(pk=occurrence_id)
        admit("fulfillment", campaign, runtime, row)
        covered_by = row.replacement_id if disposition == "coalesced" else row.pk
        identity = dict(
            definition_id=row.definition_id,
            mode=row.mode,
            target=row.target,
            slot=row.slot,
        )
        existing = ScheduleFulfillment.objects.filter(**identity).first()
        if existing:
            if (
                existing.disposition != disposition
                or existing.occurrence_id != covered_by
            ):
                raise StorageInvariantError(
                    "Semantic slot is already covered differently."
                )
            return existing
        return ScheduleFulfillment.objects.create(
            **identity,
            disposition=disposition,
            occurrence_id=covered_by,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )


def recover_occurrence(
    *,
    occurrence_id,
    expected_version,
    action,
    actor_id,
    correlation_id,
    admit,
    replacement_id=None,
):
    """Reconcile lost ownership after TaskRun fencing, never from timeout alone.

    The verifier must inspect durable/provider evidence before choosing an
    outcome. Unknown delivery stays nonterminal; no automatic resend is inferred.
    """
    outcomes = {
        "recovery_retry": "pending",
        "recovery_unknown": "delivery_unknown",
        "recovery_complete": "succeeded",
        "recovery_fail": "failed",
        "recovery_skip": "skipped",
        "recovery_coalesce": "coalesced",
    }
    if not callable(admit) or action not in outcomes or not isinstance(actor_id, UUID):
        raise TypeError("Occurrence recovery requires attributed owning evidence.")
    original = ScheduleOccurrence.objects.select_related("definition").get(
        pk=occurrence_id
    )
    with campaign_transaction(
        original.definition.campaign_id, correlation_id=correlation_id
    ) as (campaign, runtime):
        row = ScheduleOccurrence.objects.select_for_update().get(pk=occurrence_id)
        admit(action, campaign, runtime, row)
        if row.version != expected_version:
            raise StaleRecordError("Occurrence recovery inputs changed.")
        if row.state not in {"running", "delivery_unknown"}:
            raise StorageInvariantError("Only unresolved execution can be reconciled.")
        if row.state == "delivery_unknown" and action not in {
            "recovery_retry",
            "recovery_complete",
            "recovery_fail",
        }:
            raise StorageInvariantError("Unknown delivery requires a resolved outcome.")
        task = TaskRun.objects.select_for_update().get(pk=row.task_id)
        if task.state not in {"abandoned", "cancelled", "succeeded", "failed"}:
            raise StorageInvariantError("Task ownership must be reconciled first.")
        row.state, row.reason = outcomes[action], action
        row.lease_expires_at, row.replacement_id = None, replacement_id
        row.actor_id, row.correlation_id = actor_id, correlation_id
        row.version += 1
        row.save()
        return row
