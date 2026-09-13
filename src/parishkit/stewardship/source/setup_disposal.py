"""Bounded disposal of unused source staging after setup expires or completes."""

from django.db import connection
from django.db.models import CharField, Exists, F, OuterRef, Q
from django.db.models.functions import Cast

from parishkit.stewardship.accounts.setup_install_models import (
    SetupCompletion,
    SetupPreparationReceipt,
)
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.storage import StorageInvariantError

from .leases import verify_source
from .rejection import reject_snapshot
from .snapshot_models import SourceSnapshot
from .version_models import ENTITY_MODELS

TASK_TYPE = "setup_source_cleanup"


def terminal_attempt(attempt_id):
    """Completed setup requires its immutable provenance, not a caller's label."""
    attempt = SetupAttempt.objects.get(
        pk=attempt_id, state__in=("expired", "completed")
    )
    if (
        attempt.state == "completed"
        and not SetupCompletion.objects.filter(
            preparation__readiness__intent__attempt=attempt
        ).exists()
    ):
        raise PermissionError("Completed setup cleanup requires its retained marker.")
    return attempt


def owned_source_tasks(attempt_id):
    """Resolve only original catalog and exact prepared-finalization retry roots."""
    attempt = terminal_attempt(attempt_id)
    prepared = SetupPreparationReceipt.objects.filter(
        readiness__intent__attempt=attempt
    ).values("id")
    return TaskRun.objects.filter(
        Q(
            root_id=attempt.source_task_id,
            task_type="setup_source_load",
            domain_request_id=attempt.pk,
        )
        | Q(
            task_type="setup_finalize",
            domain_request_id__in=prepared,
            initiated_by_id=attempt.owner_id,
            root__task_type="setup_finalize",
            root__domain_request_id=F("domain_request_id"),
            root__initiated_by_id=attempt.owner_id,
            root__idempotency_key=Cast(F("domain_request_id"), CharField()),
        )
    )


def owned_snapshots(attempt_id):
    """Discard neither a promoted source nor any unrelated Task's staging."""
    return SourceSnapshot.objects.filter(
        task_id__in=owned_source_tasks(attempt_id).values("id"),
        state__in=("staging", "ready", "rejected"),
        generation=None,
    )


def dispose_batch(execution, claim):
    """Delete at most 500 memberships and their unshared payloads atomically.

    Payload deletion precedes membership deletion so each SQL guard can prove
    original ownership without a caller-supplied allowlist or broad orphan scan.
    Deferrable foreign keys still require every remaining reference to resolve
    at commit. Shared payloads survive until their last disposable reference.
    """
    require_work_order()
    task = lock_task_claim(execution.claim)
    verify_source(claim)
    if task.task_type != TASK_TYPE or claim.phase != "full":
        raise PermissionError("Source disposal requires its dedicated setup owner.")
    snapshots = owned_snapshots(task.domain_request_id).order_by("source_fence", "id")
    # Rejected manifests with no members are permanent safe receipts, not work.
    for candidate in snapshots:
        if candidate.state != "rejected" or any(
            membership.objects.filter(snapshot=candidate).exists()
            for _, membership in ENTITY_MODELS.values()
        ):
            snapshot = candidate
            break
    else:
        return None

    def admit(action, candidate):
        """The exact expired root and drained source fence cannot change mid-batch."""
        return (
            action == "reject"
            and owned_snapshots(task.domain_request_id).filter(pk=candidate.pk).exists()
        )

    snapshot = reject_snapshot(snapshot.pk, claim, admit=admit)
    for payload, membership in ENTITY_MODELS.values():
        rows = list(
            membership.objects.filter(snapshot=snapshot)
            .order_by("id")
            .values_list("id", "payload_id")[:500]
        )
        if not rows:
            continue
        identifiers, payload_ids = map(list, zip(*rows, strict=True))
        other = membership.objects.filter(payload_id=OuterRef("pk")).exclude(
            pk__in=identifiers
        )
        removable = list(
            payload.objects.filter(pk__in=payload_ids)
            .filter(~Exists(other))
            .values_list("pk", flat=True)
        )
        with connection.cursor() as cursor:
            if removable:
                table = connection.ops.quote_name(payload._meta.db_table)
                cursor.execute(f"DELETE FROM {table} WHERE id=ANY(%s)", [removable])
            table = connection.ops.quote_name(membership._meta.db_table)
            cursor.execute(f"DELETE FROM {table} WHERE id=ANY(%s)", [identifiers])
            if cursor.rowcount != len(identifiers):
                raise StorageInvariantError("Setup source cleanup batch changed.")
        verify_source(claim)
        return len(identifiers)
    return 0
