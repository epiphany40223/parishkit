"""Bounded renewal for the original Admin's correlated staged-source progress page."""

from datetime import timedelta
from uuid import UUID

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.source.models import SourceMutationLease

from .sessions import authenticated_admin, database_now
from .setup_drafts import _owned
from .setup_models import SetupAttempt
from .setup_policy import SetupState
from .setup_staging import _expire, _expiry, _window


def source_progress(request, service, task_id, *, renew=False):
    """Observe one exact task; only its current live leases permit throttled renewal.

    The Task's immutable creation instant anchors the hard watchdog. Failed,
    queued or abandoned work never extends idle time. A renewal POST commits an
    expired fence before returning; passive reads leave that mutation to the
    scheduler. Every worker effect must independently check the same deadlines.
    """
    if not isinstance(task_id, UUID) or type(renew) is not bool:
        raise ValueError("Exact setup task and renewal intent are required.")
    with work_transaction():
        actor, attempt = _owned(request, service)
        if attempt is None or attempt.source_task_id != task_id:
            raise LookupError("Setup source progress is unavailable.")
        task = attempt.source_task
        now = database_now()
        if (
            task.task_type != "setup_source_load"
            or task.domain_request_id != attempt.pk
            or task.initiated_by_id != actor.identity
            or task.root_id != task.pk
        ):
            raise PermissionError("Setup source identity is unavailable.")
        reason = _expiry(attempt, now)
        if (
            renew
            and reason is not None
            and attempt.state not in {SetupState.EXPIRED, SetupState.COMPLETED}
        ):
            _expire(attempt, actor_id=None, reason=reason)
        window = _window(attempt, request.portal_session)
        worker_live = (
            task.state == "running"
            and task.lease_expires_at > now
            and task.heartbeat_at > now - timedelta(seconds=90)
            and SourceMutationLease.objects.filter(
                owner_id=task.pk,
                worker_id=task.worker_id,
                task_fence=task.fence,
                expires_at__gt=now,
                heartbeat_at__gt=now - timedelta(seconds=90),
            ).exists()
        )
        renewed = False
        if renew and window.may_renew(
            now,
            session_id=request.portal_session.pk,
            task_id=task_id,
            worker_live=worker_live,
            session_live=True,
        ):
            SetupAttempt.objects.filter(pk=attempt.pk, version=attempt.version).update(
                renewed_at=now,
                actor_id=actor.identity,
                correlation_id=current_correlation(),
                version=F("version") + 1,
            )
            # SQL stamps renewed_at. Update session activity afterward so its
            # deadline cannot precede that accepted renewal, even by microseconds.
            if (
                authenticated_admin(request, store=service.store, activity=True) is None
                or request.portal_session.pk != attempt.session_id
            ):
                raise PermissionError("Setup session expired during renewal.")
            attempt.refresh_from_db()
            window = _window(attempt, request.portal_session)
            renewed = True
        return {
            "server_now": now.isoformat(),
            "task_id": str(task.pk),
            "task_state": task.state,
            "setup_state": attempt.state,
            "phase": task.phase,
            "current": task.progress_current,
            "total": task.progress_total,
            "worker_live": bool(worker_live),
            "renewed": renewed,
            "active": (
                attempt.state == SetupState.LOADING
                and reason is None
                and task.state in NONTERMINAL_STATES
            ),
            "idle_at": window.idle_at.isoformat(),
            "absolute_at": window.absolute_at.isoformat(),
            "watchdog_at": window.watchdog_at.isoformat(),
        }
