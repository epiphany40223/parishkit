"""Authenticated intake for setup's source-load Task, and discarding a load."""

from uuid import uuid5

from django.db import connection
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.storage import _status as task_status
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.source.setup_admission import TASK_TYPE
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import check_version

from .sessions import authenticated_admin, database_now
from .setup_drafts import _owned, staged_load
from .setup_models import SetupAttempt, SetupDraftSection
from .setup_policy import SetupState
from .setup_secret_models import SetupSealedCredential
from .setup_staging import _expiry

# The transaction-local setting that names why a load is discarded; the
# attempt's audit trigger reads it into setup_source_voided and clears it.
VOID_SETTING = "stewardship.setup_void_reason"
# How a bound load can have ended for it to be discarded.
FINISHED = frozenset({"succeeded", "failed"})


def needs_reload(attempt):
    """Whether a collecting attempt's bound load must be loaded again (#1353).

    A load that failed, or one that succeeded but whose staged data is no
    longer valid by review's own predicate (``staged_load``). SQL repeats
    the check when the discard is audited with reason ``reload``.
    """
    return (
        attempt.state == SetupState.COLLECTING
        and attempt.source_task_id is not None
        and attempt.source_task.state in FINISHED
        and staged_load(attempt) is None
    )


def void_source_load(attempt, *, reason, actor_id):
    """Discard the attempt's finished load so another load can bind (#1353).

    The caller holds the work order and has locked and admitted ``attempt``
    (still collecting, bound to a load that succeeded or failed). SQL admits
    this only for the owning live Admin and records ``reason`` (key_changed,
    organization_changed or reload) as a setup_source_voided audit event.
    The discarded load's staging stays inert: finalization and campaign
    staging bind only to the current load, and the setup source cleanup
    disposes of it when setup ends. The renewal time goes too, so the next
    load starts a fresh renewal clock. Returns the attempt, refreshed.
    """
    require_work_order()
    if attempt.state != SetupState.COLLECTING or attempt.source_task_id is None:
        raise StorageInvariantError("Only a bound collecting setup can discard a load.")
    if attempt.source_task.state not in FINISHED:
        raise PermissionError("Available after the parish data load finishes.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [VOID_SETTING, reason])
    changed = SetupAttempt.objects.filter(
        pk=attempt.pk, version=attempt.version, state=SetupState.COLLECTING
    ).update(
        source_task_id=None,
        renewed_at=None,
        actor_id=actor_id,
        correlation_id=current_correlation(),
        version=F("version") + 1,
    )
    if changed != 1:
        raise StorageInvariantError("Setup changed before its load was discarded.")
    attempt.refresh_from_db()
    return attempt


def start_source_load(request, service, attempt_id, *, expected_version):
    """Queue once after public profile and sealed source settings are available.

    No provider call, credential read or source lease acquisition occurs in the
    browser transaction. The task and attempt binding commit together. A load
    already bound is returned as it is unless it needs loading again: one
    that failed, or one that succeeded but whose staged data is no longer
    valid (``staged_load``; for example its credential version changed).
    Load again discards it (audit reason ``reload``) and queues a new load in
    the same transaction (#1353). A still-valid load is kept; changing the
    ParishSoft connection is what discards it (see
    setup_credentials.stage_credential).
    """
    with work_transaction():
        actor, attempt = _owned(request, service, attempt_id)
        if _expiry(attempt, database_now()) is not None:
            raise PermissionError("Setup has expired.")
        if attempt.source_task_id is not None and not needs_reload(attempt):
            return task_status(attempt.source_task)
        check_version(attempt, expected_version)
        if attempt.state != SetupState.COLLECTING or not (
            SetupDraftSection.objects.filter(
                attempt=attempt, step="parish", scrubbed_at=None
            ).exists()
            and SetupSealedCredential.objects.filter(
                attempt=attempt, target="parishsoft", scrubbed_at=None
            ).exists()
        ):
            raise ValueError("Save the parish profile and ParishSoft credential first.")
        if attempt.source_task_id is not None:
            attempt = void_source_load(
                attempt, reason="reload", actor_id=actor.identity
            )
        # The version the new load is queued at; a discard above advanced it.
        version = attempt.version

        def admit(action, status):
            """Only the authenticated original attempt can allocate its root."""
            _, current = _owned(request, service, attempt_id)
            return (
                action == "enqueue"
                and status.task_type == TASK_TYPE
                and status.domain_request_id == current.pk
                and current.version == version
                and current.source_task_id is None
                and current.state == "collecting"
                and _expiry(current, database_now()) is None
            )

        result = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=attempt.pk,
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            admit=admit,
            # (task_type, idempotency_key) is unique, so each load of the
            # attempt needs its own key, a UUID: one derived from the attempt
            # and the version it binds at, which only ever advances (#1353).
            idempotency_key=uuid5(attempt.pk, str(version)),
        )
        SetupAttempt.objects.filter(pk=attempt.pk, version=version).update(
            source_task_id=result.run_id,
            state="loading",
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        authenticated_admin(request, store=service.store, activity=True)
        return result
