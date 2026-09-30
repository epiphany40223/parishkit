"""Administrator-only combined operational and audit log screen and export."""

import csv
import io
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.contracts import MESSAGES, ErrorCode
from parishkit.stewardship.web.exports import csv_cell, download_headers
from parishkit.stewardship.web.tables import bounded_count

from .log_rows import (
    LogQuery,
    audit_row,
    log_table,
    merge,
    operational_row,
    page_context,
    task_subject,
)
from .models import AuditEvent, OperationalLog
from .schemas import Action, ActorKind, Outcome
from .services import record_action

UNAVAILABLE = (ConfigError, LimiterUnavailable, ObjectDoesNotExist)
# One download holds at most this many of the newest matching entries, so an
# export is never an unbounded read assembled in a web request. The page says
# so and suggests a narrower date range for older entries. The screen pages
# at most this deep into either order, for the same reason: each page reads
# the index-ordered keys of every entry before it.
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


OPERATIONAL_FIELDS = (
    "id",
    "created_at",
    "level",
    "event",
    "actor_id",
    "correlation_id",
    "context",
)
AUDIT_FIELDS = (
    "id",
    "created_at",
    "event_type",
    "actor_id",
    "correlation_id",
    "campaign_reference",
    "subject_id",
    "auditcontext__context",
    "auditcontext__actor_kind",
)


def _filtered(rows, query, through):
    """Apply the filters both sources share, bounded by the snapshot instant.

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
    if through is not None:
        rows = rows.filter(created_at__lte=through)
    return rows


def _sources(query, through):
    """The two filtered source querysets; a campaign filter is audit-only.

    Either is None when the filters exclude that source entirely.
    """
    operational = audit = None
    if query.source != "audit" and not query.campaign and query.levels:
        operational = OperationalLog.objects.filter(level__in=query.levels)
        if query.event:
            operational = operational.filter(event=query.event)
        operational = _filtered(operational, query, through)
    if query.source != "operational":
        audit = AuditEvent.objects.all()
        if query.event:
            audit = audit.filter(event_type=query.event)
        if query.campaign:
            audit = audit.filter(campaign_reference=query.campaign)
        audit = _filtered(audit, query, through)
    return operational, audit


def _ordered(rows, *, oldest):
    """(created_at, id) keys in the chosen order, exactly the index's order.

    Both sources have a (created_at, id) index, so PostgreSQL reads the keys
    from it, forwards or backwards, instead of sorting the table.
    """
    order = ("created_at", "id") if oldest else ("-created_at", "-id")
    return rows.order_by(*order).values("created_at", "id")


def _keys(rows, depth, *, oldest):
    """The first ``depth`` keys of one source, or none when it is excluded."""
    if rows is None or depth <= 0:
        return []
    return list(_ordered(rows, oldest=oldest)[:depth])


def _annotate(page):
    """Add display-only actor names and task links to one page's rows."""
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
        row["task_subject"] = task_subject(row)
    return page


def _window(operational, audit, *, offset, size, oldest):
    """One merged page: keys of both sources merged, then full rows for it.

    Each source supplies at most ``offset + size`` keys in the chosen order,
    so the merge's slice is exactly that page of the union; only the page's
    own entries are then read in full.
    """
    depth = offset + size
    keys = merge(
        [
            key | {"source": "operational"}
            for key in _keys(operational, depth, oldest=oldest)
        ],
        [key | {"source": "audit"} for key in _keys(audit, depth, oldest=oldest)],
        oldest=oldest,
    )[offset:depth]
    wanted = {
        source: [key["id"] for key in keys if key["source"] == source]
        for source in ("operational", "audit")
    }
    loaded = {}
    if wanted["operational"]:
        for record in OperationalLog.objects.filter(
            pk__in=wanted["operational"]
        ).values(*OPERATIONAL_FIELDS):
            loaded["operational", record["id"]] = operational_row(record)
    if wanted["audit"]:
        for record in AuditEvent.objects.filter(pk__in=wanted["audit"]).values(
            *AUDIT_FIELDS
        ):
            loaded["audit", record["id"]] = audit_row(record)
    return _annotate([loaded[key["source"], key["id"]] for key in keys])


def _load(query, *, through):
    """Read one page of the snapshot and describe it for the shared navigator.

    Returns (TablePage, depth_limited). The total is a bounded count per
    source (at most 10,000 each), and paging reaches at most EXPORT_LIMIT
    entries into the chosen order: a page past either the end or that depth
    shows the last reachable page, and ``depth_limited`` then tells the page
    to suggest a narrower date range or the other order.
    """
    operational, audit = _sources(query, through)
    counts = [bounded_count(rows) for rows in (operational, audit) if rows is not None]
    total = sum(count for count, _ in counts)
    capped = any(flag for _, flag in counts) or total > EXPORT_LIMIT
    reachable = min(total, EXPORT_LIMIT)
    size = query.page_size
    pages = max(1, -(-reachable // size))
    number = min(query.page_number, pages)
    depth_limited = query.page_number > pages and capped
    offset = (number - 1) * size
    # The last reachable page stops at the paging depth, not at the page size.
    rows = _window(
        operational,
        audit,
        offset=offset,
        size=min(size, reachable - offset),
        oldest=query.oldest,
    )
    table = log_table(
        query,
        rows,
        through=through,
        action=reverse("admin:logs"),
        number=number,
        total=reachable,
        capped=capped,
    )
    return table, depth_limited


def _export(query):
    """The newest EXPORT_LIMIT matching entries, newest first, unpaged."""
    operational, audit = _sources(query, None)
    return _window(operational, audit, offset=0, size=EXPORT_LIMIT, oldest=False)


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
            through = query.snapshot or database_now()
            table, depth_limited = _load(query, through=through)
        response = render(
            request,
            "stewardship/logs.html",
            page_context(query, table, depth_limited=depth_limited),
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
                context={"outcome": Outcome.SUCCEEDED, "count": len(table.rows)},
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
    name, which the page offers from the browser's own zone. The page's
    snapshot, page, size and sort are ignored, so an export always starts from
    the newest matching entry. Like
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
        for name in ("through", "page", "size", "sort"):
            parameters.pop(name, None)
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
            rows = _export(query)
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
