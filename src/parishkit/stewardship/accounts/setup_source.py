"""Authenticated intake for the one original setup source-load Task."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.storage import _status as task_status
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.source.setup_admission import TASK_TYPE
from parishkit.stewardship.web.contracts import check_version

from .sessions import authenticated_admin, database_now
from .setup_drafts import _owned
from .setup_models import SetupAttempt, SetupDraftSection
from .setup_secret_models import SetupSealedCredential
from .setup_staging import _expiry


def start_source_load(request, service, attempt_id, *, expected_version):
    """Queue once after public profile and sealed source settings are available.

    No provider call, credential read or source lease acquisition occurs in the
    browser transaction. The task and original attempt binding commit together.
    """
    with work_transaction():
        actor, attempt = _owned(request, service, attempt_id)
        if _expiry(attempt, database_now()) is not None:
            raise PermissionError("Setup has expired.")
        if attempt.source_task_id is not None:
            return task_status(attempt.source_task)
        check_version(attempt, expected_version)
        if attempt.state != "collecting" or not (
            SetupDraftSection.objects.filter(
                attempt=attempt, step="parish", scrubbed_at=None
            ).exists()
            and SetupSealedCredential.objects.filter(
                attempt=attempt, target="parishsoft", scrubbed_at=None
            ).exists()
        ):
            raise ValueError("Save the parish profile and ParishSoft credential first.")

        def admit(action, status):
            """Only the authenticated original attempt can allocate its root."""
            _, current = _owned(request, service, attempt_id)
            return (
                action == "enqueue"
                and status.task_type == TASK_TYPE
                and status.domain_request_id == current.pk
                and current.version == expected_version
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
            idempotency_key=attempt.pk,
        )
        SetupAttempt.objects.filter(pk=attempt.pk, version=attempt.version).update(
            source_task_id=result.run_id,
            state="loading",
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        authenticated_admin(request, store=service.store, activity=True)
        return result
