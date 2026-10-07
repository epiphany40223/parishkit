"""Request-free reads behind the System logs screen and its download.

The screen (``log_views.logs``), its download (``log_views.export_logs``) and
the command line's ``logs list`` and ``logs export`` (ADM-11 PR 8a) read
through these functions, so the page and the command cannot drift in what
they read. Admission, form parsing, rendering and auditing stay with each
caller.
"""

import csv
import io
import json
from zoneinfo import ZoneInfo

from django.urls import reverse

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.tables import bounded_count

from .log_rows import audit_row, log_table, merge, operational_row, task_subject
from .models import AuditEvent, OperationalLog

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
OPERATIONAL_FIELDS = (
    "id",
    "created_at",
    "level",
    "event",
    "actor_id",
    "correlation_id",
    "schema",
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


def filtered(rows, query, through):
    """Apply the filters both sources share, bounded by the snapshot instant.

    Dates are whole days in the viewer's browser zone (``LogQuery.bounds``).
    """
    if query.actor:
        rows = rows.filter(actor_id=query.actor)
    if query.correlation:
        rows = rows.filter(correlation_id=query.correlation)
    lower, upper = query.bounds
    if lower:
        rows = rows.filter(created_at__gte=lower)
    if upper:
        rows = rows.filter(created_at__lt=upper)
    if through is not None:
        rows = rows.filter(created_at__lte=through)
    return rows


def sources(query, through):
    """The two filtered source querysets; a campaign filter is audit-only.

    Either is None when the filters exclude that source entirely.
    """
    operational = audit = None
    if not query.campaign and query.levels:
        operational = OperationalLog.objects.filter(level__in=query.levels)
        if query.event:
            operational = operational.filter(event=query.event)
        operational = filtered(operational, query, through)
    if query.audits:
        audit = AuditEvent.objects.all()
        if query.event:
            audit = audit.filter(event_type=query.event)
        if query.campaign:
            audit = audit.filter(campaign_reference=query.campaign)
        audit = filtered(audit, query, through)
    return operational, audit


def ordered(rows, *, oldest):
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
    return list(ordered(rows, oldest=oldest)[:depth])


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


def load_page(query, *, through):
    """Read one page of the snapshot and describe it for the shared navigator.

    Returns (TablePage, depth_limited). The total is a bounded count per
    source (at most 10,000 each), and paging reaches at most EXPORT_LIMIT
    entries into the chosen order: a page past either the end or that depth
    shows the last reachable page, and ``depth_limited`` then tells the page
    to suggest a narrower date range or the other order.
    """
    operational, audit = sources(query, through)
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


def export_rows(query):
    """The newest EXPORT_LIMIT matching entries, newest first, unpaged."""
    operational, audit = sources(query, None)
    return _window(operational, audit, offset=0, size=EXPORT_LIMIT, oldest=False)


def export_choice(fmt, zone_name):
    """The download's format and display zone, as ``(format, ZoneInfo)``.

    The format is CSV or JSON Lines; the zone is UTC or one supported
    timezone name. Anything else is a ``ValueError`` (the page's 400).
    """
    if fmt not in EXPORT_FORMATS or (
        zone_name != "UTC" and zone_name not in timezone_names()
    ):
        raise ValueError("Invalid log export choice.")
    return fmt, ZoneInfo(zone_name)


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-logs-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(rows, fmt, zone):
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
