"""Admin-only operational task metadata; requester-owned exports have a separate API."""

import re

from django.db import DatabaseError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    PageWindow,
    expected_version,
    filters,
    validation_response,
)

from .models import NONTERMINAL_STATES, TASK_STATES, TaskRun
from .ownership import database_now


def _error(code, status):
    """Only static error codes/messages cross this metadata boundary."""
    if status != 404:
        return validation_response([FieldError(code)], status=status)
    response = JsonResponse({"errors": [FieldError(code).as_dict()]}, status=404)
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _window(parameters, *, listing):
    """Reject repeated/unknown/oversized inputs before selecting any task rows."""
    allowed = {"page", "size", "state", "task_type"} if listing else {"page", "size"}
    selected = filters(parameters, allowed=allowed)
    window = PageWindow(
        expected_version(selected.get("page", "1")),
        expected_version(selected.get("size", "50" if listing else "20")),
    )
    state = selected.get("state", "nonterminal")
    if state not in {*TASK_STATES, "all", "nonterminal"}:
        raise ValueError("Unknown task state filter.")
    task_type = selected.get("task_type")
    if (
        task_type is not None
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", task_type) is None
    ):
        raise ValueError("Invalid task type filter.")
    return window, state, task_type


def _progress(row):
    """Closed phases and counts, with no arguments, worker exceptions or results."""
    return {
        "phase": row.phase,
        "current": row.progress_current,
        "total": row.progress_total,
        "percent": round(100 * row.progress_current / row.progress_total, 1)
        if row.progress_total
        else None,
    }


def _task(row, instant):
    """Return an explicit public metadata whitelist, never serialize the ORM object."""
    return {
        "id": str(row.pk),
        "root_id": str(row.root_id),
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "retry_sequence": row.retry_sequence,
        "type": row.task_type,
        "state": row.state,
        "action": row.action,
        "version": row.version,
        "attempt": row.attempt,
        "progress": _progress(row),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "not_before": row.not_before,
        "heartbeat_at": row.heartbeat_at,
        "lease_expires_at": row.lease_expires_at,
        "active": row.state == "running" and row.lease_expires_at > instant,
    }


def _listing(window, state, task_type, instant):
    """Bound rows and count only indexed nonterminal work for the header indicator."""
    query = TaskRun.objects.all()
    if state != "all":
        query = query.filter(
            state__in=NONTERMINAL_STATES if state == "nonterminal" else [state]
        )
    if task_type:
        query = query.filter(task_type=task_type)
    rows, has_next = window.rows(query.order_by("-created_at", "-id"))
    counts = TaskRun.objects.filter(state__in=NONTERMINAL_STATES).aggregate(
        active=Count("id", filter=Q(state="running", lease_expires_at__gt=instant)),
        **{state: Count("id", filter=Q(state=state)) for state in NONTERMINAL_STATES},
    )
    return {
        "as_of": instant,
        "counts": counts,
        "page": window.page,
        "size": window.size,
        "has_next": has_next,
        "tasks": [_task(row, instant) for row in rows],
    }, len(rows)


def _detail(identifier, window, instant):
    """Freeze the event upper version to match the captured current task metadata."""
    row = TaskRun.objects.filter(pk=identifier).first()
    if row is None:
        return None, 0
    events, has_next = window.rows(
        row.events.filter(version__lte=row.version).order_by("-version")
    )
    return {
        "as_of": instant,
        "task": _task(row, instant),
        "page": window.page,
        "size": window.size,
        "has_next": has_next,
        "events": [
            {
                "version": event.version,
                "at": event.created_at,
                "action": event.action,
                "state": event.state,
                "attempt": event.attempt,
                "progress": _progress(event),
            }
            for event in events
        ],
    }, len(events)


def _read(request, identifier=None):
    """Recheck current Admin authority; automatic polls never renew idle activity."""
    try:
        service = runtime()
        principal = authenticated_admin(request, store=service.store, activity=False)
        if not allows(principal, Capability.BACKGROUND_WORK):
            return _error(ErrorCode.DENIED, 403)
        try:
            window, state, task_type = _window(request.GET, listing=identifier is None)
        except ValueError:
            return _error(ErrorCode.INVALID, 400)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            instant = database_now()
            data, count = (
                _listing(window, state, task_type, instant)
                if identifier is None
                else _detail(identifier, window, instant)
            )
            current = authenticated_admin(request, store=service.store, read_only=True)
            if not allows(current, Capability.BACKGROUND_WORK):
                return _error(ErrorCode.DENIED, 403)
            if data is None:
                return _error(ErrorCode.UNAVAILABLE, 404)
            response = JsonResponse(data)
            response["Cache-Control"] = "no-store"
            record_action(
                Action.BACKGROUND_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                subject_id=identifier,
                context={"outcome": Outcome.SUCCEEDED, "count": count},
            )
            return response
    except (ConfigError, LimiterUnavailable, DatabaseError, ValueError, TypeError):
        return _error(ErrorCode.UNAVAILABLE, 503)


@require_safe
def task_list(request):
    """List bounded operational metadata only for a freshly authorized Admin."""
    return _read(request)


@require_safe
def task_detail(request, task_id):
    """Read bounded immutable attempt history, never task commands or provider data."""
    return _read(request, task_id)
