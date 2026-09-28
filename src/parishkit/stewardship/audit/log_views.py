"""Administrator-only combined operational and audit log screen and export."""

import csv
import io
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.contracts import MESSAGES, ErrorCode
from parishkit.stewardship.web.exports import csv_cell, download_headers

from .log_rows import (
    PAGE_SIZE,
    LogQuery,
    audit_row,
    merge,
    operational_row,
    page_context,
)
from .models import AuditEvent, OperationalLog
from .schemas import Action, ActorKind, Outcome
from .services import record_action

UNAVAILABLE = (ConfigError, LimiterUnavailable, ObjectDoesNotExist)
# One download holds at most this many of the newest matching entries, so an
# export is never an unbounded read assembled in a web request. The page says
# so and suggests a narrower date range for older entries.
EXPORT_LIMIT = 10_000
EXPORT_FORMATS = {"csv": "text/csv", "jsonl": "application/x-ndjson"}
EXPORT_COLUMNS = (
    "time",
    "source",
    "level",
    "type",
    "actor_email",
    "actor_id",
    "correlation_id",
    "campaign_id",
    "subject_id",
    "details",
)


def _error(code, status, *, query_string=False):
    """A fixed, accessible message with no submitted filter and no DB chrome.

    An unavailable database must not be queried again by the Admin navigation
    context processor while the error itself is rendered. Filter guidance is
    shown only when a filter value was the problem: a denied reader or an
    outage submitted nothing that could be corrected, and a query string is
    refused for where it was sent, not for what it said.
    """
    response = HttpResponse(
        render_to_string(
            "stewardship/logs-error.html",
            {
                "message": MESSAGES[code],
                "invalid": code is ErrorCode.INVALID and not query_string,
                "query_string": query_string,
            },
        ),
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    if status == 503:
        response["Retry-After"] = "5"
    return response


def _principal(request, store, *, final=False):
    """Only an Administrator reads the logs; the final recheck renews nothing."""
    actor = authenticated_admin(
        request, store=store, activity=not final, read_only=final
    )
    if not allows(actor, Capability.SYSTEM_LOGS):
        raise PermissionError("System logs require an Administrator.")
    return actor


def _bounded(rows, query, *, size):
    """Apply the filters both sources share, then the keyset cursor.

    Dates are whole UTC days, matching how the entries are stored.
    """
    if query.actor:
        rows = rows.filter(actor_id=query.actor)
    if query.correlation:
        rows = rows.filter(correlation_id=query.correlation)
    start, end = query.days
    if start:
        rows = rows.filter(created_at__gte=datetime.combine(start, time.min, UTC))
    if end:
        end += timedelta(days=1)
        rows = rows.filter(created_at__lt=datetime.combine(end, time.min, UTC))
    if query.cursor:
        instant, identifier = query.cursor
        rows = rows.filter(
            Q(created_at__lt=instant) | Q(created_at=instant, id__lt=identifier)
        )
    return rows.order_by("-created_at", "-id")[: size + 1]


def _load(query, *, size=None):
    """One bounded page from each source; a campaign filter is audit-only.

    ``size`` defaults to the screen's page size, read at call time.
    """
    size = PAGE_SIZE if size is None else size
    operational, audit = [], []
    if query.source != "audit" and not query.campaign and query.levels:
        rows = OperationalLog.objects.filter(level__in=query.levels)
        if query.event:
            rows = rows.filter(event=query.event)
        operational = [
            operational_row(record)
            for record in _bounded(rows, query, size=size).values(
                "id",
                "created_at",
                "level",
                "event",
                "actor_id",
                "correlation_id",
                "context",
            )
        ]
    if query.source != "operational":
        rows = AuditEvent.objects.all()
        if query.event:
            rows = rows.filter(event_type=query.event)
        if query.campaign:
            rows = rows.filter(campaign_reference=query.campaign)
        audit = [
            audit_row(record)
            for record in _bounded(rows, query, size=size).values(
                "id",
                "created_at",
                "event_type",
                "actor_id",
                "correlation_id",
                "campaign_reference",
                "subject_id",
                "auditcontext__context",
            )
        ]
    # The same size bounds each source's read, so the merge is the true next page.
    page, following = merge(operational, audit, size=size)
    # Shown on screen only, for the actors on this page; never audited or logged.
    identities = {row["actor_id"] for row in page if row["actor_id"]}
    actors = dict(
        PortalUser.objects.filter(pk__in=identities).values_list("id", "email")
    )
    # Most other actors are background worker processes (each task claim is
    # recorded under the claiming worker's identity), not people.
    workers = set(
        TaskRun.objects.filter(worker_id__in=identities - set(actors))
        .values_list("worker_id", flat=True)
        .distinct()
    )
    for row in page:
        row["actor"] = actors.get(row["actor_id"])
        row["actor_worker"] = row["actor_id"] in workers
        # Task entries name the task as their subject; link to its page.
        row["task_subject"] = bool(
            row["subject_id"] and row["event"].startswith("task_")
        )
    return page, following


@require_http_methods(["GET", "POST"])
def logs(request):
    """Capture a bounded page, render outside the transaction, then recheck.

    The log only grows, so an ordinary snapshot is coherent and the shared work
    lock is not taken. Every view is itself audited, so it appears in the list
    the next time; that is expected, not a loop. Downloads are ``export_logs``;
    free-text search and Ministry scope filtering belong to later increments.
    """
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            return _error(ErrorCode.INVALID, 400, query_string=True)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        query = LogQuery.parse(parameters)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            rows, following = _load(query)
        response = render(
            request, "stewardship/logs.html", page_context(query, rows, following)
        )
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Log reader changed.")
            if SystemConfiguration.objects.filter(
                restore_review_required=True
            ).exists():
                return _error(ErrorCode.UNAVAILABLE, 503)
            record_action(
                Action.SYSTEM_LOGS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                # A count only: never a filter, an identifier or an address.
                context={"outcome": Outcome.SUCCEEDED, "count": len(rows)},
            )
        response["Cache-Control"] = "no-store"
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


def _export_body(rows, fmt, zone):
    """Serialize the reviewed display rows; nothing beyond what the page shows."""
    records = [
        {
            "time": row["created_at"].astimezone(zone).isoformat(),
            "source": str(row["source"]),
            "level": row["level"] or "",
            "type": row["event"],
            "actor_email": row["actor"] or "",
            "actor_id": str(row["actor_id"] or ""),
            "correlation_id": str(row["correlation_id"] or ""),
            "campaign_id": str(row["campaign_id"] or ""),
            "subject_id": str(row["subject_id"] or ""),
            "details": dict(row["details"]),
        }
        for row in rows
    ]
    if fmt == "jsonl":
        return "".join(json.dumps(record) + "\n" for record in records)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for record in records:
        record["details"] = "; ".join(
            f"{key}={value}" for key, value in record["details"].items()
        )
        writer.writerow([csv_cell(record[column]) for column in EXPORT_COLUMNS])
    return buffer.getvalue()


@require_http_methods(["POST"])
def export_logs(request):
    """Download the filtered log, newest first, bounded to EXPORT_LIMIT entries.

    The same closed filters as the screen arrive in the CSRF POST body, plus a
    format (CSV or JSON Lines) and a timezone: UTC or one supported timezone
    name, which the page offers from the browser's own zone. The paging cursor
    is ignored, so an export always starts from the newest matching entry. Like
    the screen, the export is rechecked after it is built and then audited with
    a count only.
    """
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            return _error(ErrorCode.INVALID, 400, query_string=True)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        fmt = parameters.pop("format", ["csv"])
        zone_name = parameters.pop("timezone", ["UTC"])
        parameters.pop("before", None)
        parameters.pop("before_id", None)
        if (
            len(fmt) != 1
            or fmt[0] not in EXPORT_FORMATS
            or len(zone_name) != 1
            or (zone_name[0] != "UTC" and zone_name[0] not in timezone_names())
        ):
            raise ValueError("Invalid log export choice.")
        fmt, zone = fmt[0], ZoneInfo(zone_name[0])
        query = LogQuery.parse(parameters)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            rows, _following = _load(query, size=EXPORT_LIMIT)
        body = _export_body(rows, fmt, zone)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
        response = HttpResponse(
            body.encode("utf-8"),
            headers=download_headers(
                f"stewardship-logs-{stamp}.{fmt}", content_type=EXPORT_FORMATS[fmt]
            ),
        )
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Log reader changed.")
            record_action(
                Action.SYSTEM_LOGS_EXPORTED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                context={"outcome": Outcome.SUCCEEDED, "count": len(rows)},
            )
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)
