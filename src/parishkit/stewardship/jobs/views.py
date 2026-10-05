"""Admin-only operational task metadata; requester-owned exports have a separate API."""

import json
from types import SimpleNamespace
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.http import JsonResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.authority import AuthorityChanging
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.activation_hold import WEB_HOLD_SECONDS, wait_out_activation
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.request_scope import ACTIVATION_HOLD
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    PageWindow,
    filters,
    validation_response,
)
from parishkit.stewardship.web.tables import (
    window_table,
)

from .models import TASK_STATES
from .ownership import database_now
from .queue_wait import explain
from .task_reads import EVENT_SORTING, TASK_SORTING
from .task_reads import counts as _counts
from .task_reads import detail as _detail
from .task_reads import listing as _listing
from .task_reads import parse_window as _window
from .task_wording import (
    REFRESH,
    phase_words,
    refresh_kinds,
    refresh_label,
    refresh_result,
    retry_reason,
)


def _error(code, status):
    """Only static error codes/messages cross this metadata boundary."""
    return validation_response([FieldError(code)], status=status)


def _held(step):
    """Run a read, waiting out a configuration change that is activating.

    The header counts and an open task page poll every few seconds, so they
    regularly land in the second between an applied change's YAML selection
    and its database activation (#429). Each read is retried briefly; one
    that still meets the change answers the pollers' ordinary 503, which
    they retry. That response is marked as the hold's own, so the request
    scope keeps it out of the ERROR log; the wait logged a WARNING. A stuck
    activation (no installer running) is an ordinary ConfigError and 503.
    """
    try:
        return wait_out_activation(step, limit=WEB_HOLD_SECONDS, durable=False)
    except AuthorityChanging:
        response = _error(ErrorCode.UNAVAILABLE, 503)
        setattr(response, ACTIVATION_HOLD, True)
        return response
    except ConfigError:
        return _error(ErrorCode.UNAVAILABLE, 503)


def _read(request, identifier=None, *, counts_only=False, audit=True):
    """Recheck current Admin authority; automatic polls never renew idle activity."""
    return _held(
        lambda: _read_once(request, identifier, counts_only=counts_only, audit=audit)
    )


def _read_once(request, identifier, *, counts_only, audit):
    """Read once; see _read. An activation in progress propagates to _held."""
    try:
        service = runtime()
        principal = authenticated_admin(request, store=service.store, activity=False)
        if not allows(principal, Capability.BACKGROUND_WORK):
            return _error(ErrorCode.DENIED, 403)
        try:
            if counts_only:
                filters(request.GET, allowed=set())
            else:
                window, state, task_type, sort = _window(
                    request.GET, listing=identifier is None
                )
        except ValueError:
            return _error(ErrorCode.INVALID, 400)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            instant = database_now()
            if counts_only:
                data, count = {"as_of": instant, "counts": _counts(instant)}, 0
                from .delivery_metadata import unknown_count

                data["delivery_unknown"] = unknown_count()
            else:
                data, count = (
                    _listing(window, state, task_type, sort, instant)
                    if identifier is None
                    else _detail(identifier, window, sort, instant)
                )
            current = authenticated_admin(request, store=service.store, read_only=True)
            if not allows(current, Capability.BACKGROUND_WORK):
                return _error(ErrorCode.DENIED, 403)
            if data is None:
                return _error(ErrorCode.UNAVAILABLE, 404)
            response = JsonResponse(data)
            response["Cache-Control"] = "no-store"
            if not counts_only and audit:
                record_action(
                    Action.BACKGROUND_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=current.identity,
                    subject_id=identifier,
                    context={"outcome": Outcome.SUCCEEDED, "count": count},
                )
            response.stewardship_read_identity = current.identity
            response.stewardship_read_count = count
            return response
    except AuthorityChanging:
        raise
    except (ConfigError, LimiterUnavailable, DatabaseError, ValueError, TypeError):
        return _error(ErrorCode.UNAVAILABLE, 503)


def _finish_html(request, result, response, identifier=None, *, audit=True):
    """Release rendered HTML only to its still-authorized original reader.

    A real page view is audited once. The passive status fragment that an
    open task page polls (audit=False) is rechecked the same way but never
    audited: a watched task would otherwise add a background_viewed row
    every few seconds for up to an hour.
    """
    return _held(lambda: _finish_once(request, result, response, identifier, audit))


def _finish_once(request, result, response, identifier, audit):
    """Recheck and audit once; see _finish_html."""
    try:
        service = runtime()
        with transaction.atomic():
            current = authenticated_admin(request, store=service.store, read_only=True)
            if (
                not allows(current, Capability.BACKGROUND_WORK)
                or current.identity != result.stewardship_read_identity
            ):
                return _error(ErrorCode.DENIED, 403)
            if audit:
                record_action(
                    Action.BACKGROUND_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=current.identity,
                    subject_id=identifier,
                    context={
                        "outcome": Outcome.SUCCEEDED,
                        "count": result.stewardship_read_count,
                    },
                )
        response["Cache-Control"] = "no-store"
        return response
    except AuthorityChanging:
        raise
    except (ConfigError, LimiterUnavailable, DatabaseError, ValueError, TypeError):
        return _error(ErrorCode.UNAVAILABLE, 503)


@require_safe
def task_counts(request):
    """Passive header counts are not an operator report read or an idle renewal."""
    return _read(request, counts_only=True)


@require_safe
def task_list(request):
    """List bounded operational metadata only for a freshly authorized Admin."""
    return _read(request)


@require_safe
def task_detail(request, task_id):
    """Read bounded immutable attempt history, never task commands or provider data."""
    return _read(request, task_id)


# Plain-language names for every task type; the stable internal name stays
# under Technical details. A type missing here still shows its internal name.
TASK_NAMES = {
    "setup_source_load": _("Initial ParishSoft data load"),
    "setup_source_cleanup": _("Initial setup cleanup"),
    "setup_finalize": _("Finishing initial setup"),
    "setup_mail_test": _("Setup test email"),
    "source_refresh": _("ParishSoft data refresh"),
    "activation_catchup": _("Initial campaign mail after go-live"),
    "automation_maintenance": _("Cleaning up ended Admin sign-ins"),
    "branding_cleanup": _("Removing old logo files"),
    "campaign_boundary": _("Campaign start or end"),
    "campaign_mail_test": _("Campaign test email"),
    "daily_digest_prepare": _("Preparing the daily Admin report"),
    "daily_digest_finalize": _("Finishing the daily Admin report"),
    "weekly_digest_prepare": _("Preparing the weekly Admin report"),
    "weekly_digest_finalize": _("Finishing the weekly Admin report"),
    "family_mail_prepare": _("Preparing Family emails"),
    "family_mail_test": _("Test email for chosen Families"),
    "operational_collect": _("Collecting system alerts"),
    "operational_prepare": _("Preparing system alert email"),
    "operational_slack": _("Posting system alerts to Slack"),
    "outbox_delivery": _("Sending email"),
    "production_cleanup": _("Deleting Testing data before go-live"),
    "production_tokens": _("Preparing Family links for go-live"),
    "production_token_cleanup": _("Discarding unused Family links"),
    "report_export": _("Report export file"),
    "report_exact_export": _("Calculating a report export"),
    "report_export_cleanup": _("Removing an expired export file"),
    "report_facts": _("Updating report figures"),
    "report_fact_verification": _("Checking report figures"),
    "security_prepare": _("Preparing a security alert email"),
}


def _named(task):
    """Add a display name to one task's bounded metadata."""
    task["name"] = TASK_NAMES.get(task["type"], task["type"])
    return task


@require_safe
def background_page(request):
    """Render the same authorized bounded metadata as the passive polling API."""
    result = _read(request, audit=False)
    if result.status_code != 200:
        return result
    work = json.loads(result.content)
    # One query labels every refresh on this page as full or 15-minute.
    kinds = refresh_kinds(
        task["root_id"] for task in work["tasks"] if task["type"] == REFRESH
    )
    for task in work["tasks"]:
        _named(task)
        task["refresh_label"] = refresh_label(task, kinds)
        task["phase_text"] = phase_words(task["type"], task["progress"]["phase"])
        progress = task["progress"]
        progress["display"] = Percentage(progress["current"], progress["total"])
    # _read already validated every query value, so the filters can be
    # carried on each navigator link as they are.
    table = window_table(
        PageWindow(work["page"], work["size"]),
        work["tasks"],
        work["has_next"],
        carry=[
            (name, value)
            for name, value in request.GET.items()
            if name not in {"page", "size", "sort"}
        ],
        total=(work["matching"], work["matching_capped"]),
        sorting=TASK_SORTING,
        sort=work["sort"],
    )
    response = render(
        request,
        "stewardship/background.html",
        {
            "work": work,
            "table": table,
            "selected_state": request.GET.get("state", "nonterminal"),
            "states": ("nonterminal", "all", *TASK_STATES),
        },
    )
    return _finish_html(request, result, response)


def _task_read(request, task_id):
    """Read one task unaudited and build the context its status region shows.

    Returns (result, context); context is None when result is an error
    response to return as is.
    """
    result = _read(request, task_id, audit=False)
    if result.status_code != 200:
        return result, None
    work = json.loads(result.content)
    task = _named(work["task"])
    for item in [task, *work["events"]]:
        progress = item["progress"]
        progress["display"] = Percentage(progress["current"], progress["total"])
        # The history names each step in words, as the status line does.
        item["phase_text"] = phase_words(task["type"], progress["phase"])
    return result, {
        "work": work,
        "task": task,
        "is_refresh": task["type"] == REFRESH,
        "phase_text": phase_words(task["type"], task["progress"]["phase"]),
        "retry_text": retry_reason(task),
        # Records checked and changed (#242), once a refresh has finished.
        "refresh_result": refresh_result(task["id"])
        if task["type"] == REFRESH and task["state"] == "succeeded"
        else None,
        # Why a still-queued task has not started (#340), from the metadata
        # already read above rather than a second read of the same row.
        "wait": explain(
            SimpleNamespace(
                pk=task["id"],
                task_type=task["type"],
                state=task["state"],
                created_at=parse_datetime(task["created_at"]),
                not_before=parse_datetime(task["not_before"]),
            )
        ),
    }


@require_safe
def task_status(request, task_id):
    """Passive status fragment that an open task page polls; never audited.

    live-status-v1.js follows a queued or running task every 2-10 s for up
    to an hour. Re-reading the whole page recorded background_viewed on
    every poll (#308), burying real System logs entries, so the page points
    the poller here instead, as presence count polls do. Only the task page
    itself records the view.
    """
    # The region shows no history, so history paging does not apply here.
    request.GET = QueryDict()
    result, context = _task_read(request, task_id)
    if context is None:
        return result
    response = render(request, "stewardship/background-task-status.html", context)
    return _finish_html(request, result, response, task_id, audit=False)


@require_safe
def task_page(request, task_id):
    """Render bounded chronological task history without exposing worker payloads."""
    result, context = _task_read(request, task_id)
    if context is None:
        return result
    work, task = context["work"], context["task"]
    # _read already validated every query value; the history has no filters.
    history = window_table(
        PageWindow(work["page"], work["size"]),
        work["events"],
        work["has_next"],
        total=(work["matching"], work["matching_capped"]),
        sorting=EVENT_SORTING,
        sort=work["sort"],
    )
    kinds = refresh_kinds([task["root_id"]]) if task["type"] == REFRESH else {}
    response = render(
        request,
        "stewardship/background-task.html",
        context
        | {
            "refresh_label": refresh_label(task, kinds),
            "status_url": reverse("admin:background_task_status", args=[task_id]),
            "export_cleanup_retry_key": str(uuid4())
            if work["task"]["type"] == "report_export_cleanup"
            and work["task"]["state"] == "failed"
            and work["latest_run_id"] == work["task"]["id"]
            else None,
            "family_preparation_retry_key": str(uuid4())
            if work["task"]["type"] == "family_mail_prepare"
            and work["task"]["state"] == "failed"
            and work["latest_run_id"] == work["task"]["id"]
            else None,
            "digest_retry_key": str(uuid4())
            if work["task"]["type"]
            in {
                "daily_digest_prepare",
                "daily_digest_finalize",
                "weekly_digest_prepare",
                "weekly_digest_finalize",
            }
            and work["task"]["state"] == "failed"
            and work["latest_run_id"] == work["task"]["id"]
            else None,
            "digest_retry_route": "admin:retry_weekly_digest"
            if work["task"]["type"].startswith("weekly_digest_")
            else "admin:retry_daily_digest",
            "history": history,
        },
    )
    return _finish_html(request, result, response, task_id)
