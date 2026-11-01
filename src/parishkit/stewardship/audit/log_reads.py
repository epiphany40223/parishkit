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
from contextlib import contextmanager
from time import monotonic
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts.actor_names import (
    actor_names,
    actor_text,
    match_windows,
    through,
)
from parishkit.stewardship.campaigns.read_guards import DEFAULT_LIMITS
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.observability import Event
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.tables import bounded_count

from .log_rows import (
    LogQuery,
    audit_row,
    log_table,
    merge,
    operational_row,
    task_subject,
)
from .log_search import narrowed
from .models import AuditContext, AuditEvent, OperationalLog
from .timeouts import record_timeout

# One download holds at most this many of the newest matching entries, so an
# export is never an unbounded read assembled in a web request. The page says
# so and suggests a narrower date range for older entries. The screen pages
# at most this deep into either order, for the same reason: each page reads
# the index-ordered keys of every entry before it.
EXPORT_LIMIT = 10_000
# CSV, XLSX and JSON Lines; no PDF, the recorded exception to the
# three-format rule (UX conventions spec, "Downloaded files"): each row
# carries structured details up to the export limit, too wide to print as a
# readable page.
EXPORT_FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "jsonl": "application/x-ndjson",
}
# The XLSX names its sheet, page header and workbook with the page's title,
# and its "Report information" sheet carries this privacy line (#926): the
# entries name portal users and hold troubleshooting detail.
EXPORT_TITLE = "System logs"
EXPORT_PRIVACY = "Sensitive system record. Share only with authorized recipients."
# Each read (the screen's page and count, or an export) may run at most this
# long in PostgreSQL: the interactive page-view limit the campaign read
# guards use. A search for a short phrase (under three letters or digits)
# has no index (#765), so on a very large log it could otherwise hold a web
# worker; it is stopped, recorded and answered with the page's unavailable
# message instead.
READ_SECONDS = DEFAULT_LIMITS.interactive_seconds
# The download's columns: JSON Lines keys (a program-readable format, so
# snake_case), each with its plain CSV and XLSX heading (#966): the page's
# column headings and identifier filter labels where it has them, and
# Source for an entry's kind (operational or audit), which the page shows
# only as the level icon. The
# cross-link identifiers stay, the recorded exception to leaving internal
# identifiers out of downloads (Administrator decision, 2026-10-11).
EXPORT_HEADINGS = {
    "time": "Time",
    "source": "Source",
    "level": "Level",
    "type": "Type",
    "actor_email": "Actor",
    "actor_id": "Actor identifier",
    "correlation_id": "Correlation identifier",
    "campaign_id": "Campaign identifier",
    "subject_id": "Subject identifier",
    "details": "Recorded detail",
}
EXPORT_COLUMNS = tuple(EXPORT_HEADINGS)
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


@contextmanager
def bounded_read():
    """One read transaction under ``READ_SECONDS``; a stop is recorded.

    ``SET LOCAL`` ends with the transaction, so the later audit write keeps
    the ordinary limits. A statement the limit stopped is logged (#287) with
    what stopped it, the limit and the time taken, then re-raised for the
    view's unavailable answer. The timeout entry names no task: a web
    request was stopped, not a task.
    """
    started = monotonic()
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SET LOCAL statement_timeout = '{int(READ_SECONDS * 1000)}ms'"
                )
            yield
    except DatabaseError as error:
        # Imported here, as service_status does: the broker pulls in the
        # whole job runtime, which this view needs only when a read stops.
        from parishkit.stewardship.jobs.broker import sql_timeout_kind

        if sql_timeout_kind(error) == "statement_timeout":
            record_timeout(
                Event.TASK_TIMED_OUT,
                what="statement_timeout",
                level="WARNING",
                limit_seconds=READ_SECONDS,
                elapsed_seconds=monotonic() - started,
                bind_task=False,
            )
        raise


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
    """The two filtered source querysets; campaign and subject are audit-only.

    Either is None when the filters exclude that source entirely. Text and
    Ministry filters (#536) apply to both, through ``log_search.narrowed``.
    An Activity group (#953) is a closed list of types and narrows both
    sources to it; a group of audit types only (Sign-in activity,
    Configuration changes) leaves operational entries out entirely, and a
    group of operational types only (Refused input, #2114) leaves audit
    records out. A typed type then narrows further within the group.
    """
    operational = audit = None
    group = query.activity_types
    if (
        not (query.campaign or query.subject or query.activity_audit_only)
        and query.levels
    ):
        operational = OperationalLog.objects.filter(level__in=query.levels)
        if group:
            operational = operational.filter(event__in=group)
        if query.event:
            operational = operational.filter(event=query.event)
        operational = narrowed(operational, query, type_column="event")
        operational = filtered(operational, query, through)
    if query.audits and not query.activity_operational_only:
        audit = AuditEvent.objects.all()
        if group:
            audit = audit.filter(event_type__in=group)
        if query.event:
            audit = audit.filter(event_type=query.event)
        if query.campaign:
            audit = audit.filter(campaign_reference=query.campaign)
        if query.subject:
            audit = audit.filter(subject_id=query.subject)
        audit = narrowed(
            audit, query, type_column="event_type", contexts=AuditContext.objects
        )
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
    # Each portal account is its user's Name (else address), linked on the
    # page to the user's Timeline (#2259); downloads keep the address.
    identities = {row["actor_id"] for row in page if row["actor_id"]}
    actors = actor_names(identities)
    # A Google account matched through another verified address (#2343) is
    # named by the match its entry's instant falls in, one read for the page.
    windows = match_windows(identities)
    # Most other actors are background worker processes (each task claim is
    # recorded under the claiming worker's identity), not people.
    workers = set(
        TaskRun.objects.filter(
            worker_id__in={key for key in identities if str(key) not in actors}
        )
        .values_list("worker_id", flat=True)
        .distinct()
    )
    for row in page:
        row["actor"] = through(
            actors.get(str(row["actor_id"])),
            windows.get(str(row["actor_id"])),
            row["created_at"],
        )
        row["actor_worker"] = row["actor_id"] in workers
        row["task_subject"] = task_subject(row)
    return page


def _mark_related(page):
    """Mark the page's rows whose "Show related entries" lists another entry.

    The row's related icon (#1065) appears only when it has something to
    link to. That filter sends the correlation alone, so its answer is the
    first-visit default (every level but DEBUG, and audit records). Only
    "another entry besides this one" matters, so one statement reads, per
    correlation on the page, at most two such entries from each log
    (``LIMIT 2`` through each log's correlation index): a correlation
    shared by thousands of entries costs no more than one shared by two.
    A row is marked when the entries found, less itself when it is one of
    them, leave at least one.
    """
    levels = list(LogQuery().levels)
    correlations = list(
        {row["correlation_id"] for row in page if row["correlation_id"]}
    )
    found = {}
    if correlations:
        operational = OperationalLog._meta.db_table
        audit = AuditEvent._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT wanted.id,
                    (SELECT count(*) FROM (SELECT 1 FROM "{operational}" entry
                        WHERE entry.correlation_id = wanted.id
                        AND entry.level = ANY(%s) LIMIT 2) listed)
                    + (SELECT count(*) FROM (SELECT 1 FROM "{audit}" entry
                        WHERE entry.correlation_id = wanted.id LIMIT 2) listed)
                FROM unnest(%s::uuid[]) AS wanted(id)""",
                [levels, correlations],
            )
            found = dict(cursor.fetchall())
    for row in page:
        listed = row["source_kind"] == "audit" or row["level"] in levels
        row["has_related"] = found.get(row["correlation_id"], 0) - listed > 0
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


def load_page(query, *, through, mark_related=False):
    """Read one page of the snapshot and describe it for the shared navigator.

    Returns (TablePage, depth_limited). The total is a bounded count per
    source (at most 10,000 each), and paging reaches at most EXPORT_LIMIT
    entries into the chosen order: a page past either the end or that depth
    shows the last reachable page, and ``depth_limited`` then tells the page
    to suggest a narrower date range or the other order. ``mark_related``
    (the screen only) marks each row's ``has_related`` for its related icon.
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
    if mark_related:
        # Only the screen draws the related icon; the command line's
        # ``logs list`` skips the extra read.
        rows = _mark_related(rows)
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

    The format is CSV, XLSX or JSON Lines; the zone is UTC or one supported
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


def report_information(count, zone, *, as_of):
    """The XLSX "Report information" rows for a System logs download (#926).

    The download time ``as_of`` in ``zone`` (a native date-time cell), the
    entry count, the export limit and the privacy line, labeled as the other
    Admin downloads label them. System logs is system-wide and has no PDF
    frame, so no Parish or Campaign row; like Limitations and Census
    changes it does not list the filters, whose search text could carry
    words the file does not otherwise hold.
    """
    return (
        ("Report", EXPORT_TITLE),
        ("Downloaded at", as_of.astimezone(zone)),
        ("Display time zone", zone.key),
        ("Entries in this file", f"{count:,}"),
        ("Entry limit", f"The newest {EXPORT_LIMIT:,} matching entries"),
        ("Privacy", EXPORT_PRIVACY),
    )


def export_body(rows, fmt, zone, *, as_of):
    """Serialize the reviewed display rows as bytes; nothing beyond the page.

    CSV and XLSX carry the same ``EXPORT_COLUMNS`` under their plain
    ``EXPORT_HEADINGS``, with the details flattened to one
    ``key=value; ...`` cell; JSON Lines keys each record by
    ``EXPORT_COLUMNS`` and keeps the details as an object. The cross-link
    identifiers stay in every format, the recorded exception to leaving
    internal identifiers out of downloads (UX conventions spec,
    "Downloaded files"). ``as_of`` is the download time: the XLSX's
    "Report information" sheet and created time state it (#926).
    """
    records = [
        {
            "time": row["created_at"].astimezone(zone).isoformat(),
            "source": str(row["source"]),
            "level": row["level"] or "",
            "type": row["event"],
            "actor_email": actor_text(row["actor"]),
            "actor_id": str(row["actor_id"] or ""),
            "correlation_id": str(row["correlation_id"] or ""),
            "campaign_id": str(row["campaign_id"] or ""),
            "subject_id": str(row["subject_id"] or ""),
            "details": dict(row["details"]),
        }
        for row in rows
    ]
    if fmt == "jsonl":
        return "".join(json.dumps(record) + "\n" for record in records).encode("utf-8")
    table = [
        [
            (
                "; ".join(f"{key}={value}" for key, value in record[column].items())
                if column == "details"
                else record[column]
            )
            for column in EXPORT_COLUMNS
        ]
        for record in records
    ]
    if fmt == "xlsx":
        # Imported here to keep the web process's startup light.
        from parishkit.stewardship.reports.xlsx_design import table_workbook

        return table_workbook(
            tuple(EXPORT_HEADINGS.values()),
            table,
            sheet_title=EXPORT_TITLE,
            information=report_information(len(records), zone, as_of=as_of),
            created=as_of,
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_HEADINGS.values())
    writer.writerows([csv_cell(value) for value in values] for values in table)
    return buffer.getvalue().encode("utf-8")
