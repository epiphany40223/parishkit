"""Report, export, digest and log commands of the Admin command line (ADM-11 PR 8).

PR 8 lands in parts; PR 8a adds the System logs commands. ``logs list`` reads
the System logs screen and ``logs export`` its download, through the
functions the page uses (``audit.log_reads``) and with the page's own closed
filters (``audit.log_rows.LogQuery``), so every filter refusal is the page's.

``logs list`` admits passively (any session) with the page's
``SYSTEM_LOGS`` capability, reads in the page's snapshot, rechecks the
session and records the page's ``system_logs_viewed`` with its count only.
Its entries carry identifiers and stored values: not the actor's email
address the screen shows beside an identifier, not a Family's or member's
DUID from an entry's detail, and not the screen's translated sentences.

``logs export`` needs a full-scope session, as every export does, and admits
as the page's form post does (recording activity). It builds the exact bytes
the page downloads, which the command line writes to standard output with
its document on standard error (``CommandSpec.streams``), and records the
page's ``system_logs_exported`` with its count after the recheck. The page
keeps no export record, so neither does the command.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from django.db import transaction

from .admin_reads import ReadModel, Unavailable, _admit, _audit, _held, _recheck


@dataclass(frozen=True)
class LogList(ReadModel):
    """One page of System logs, anchored to the snapshot instant ``through``.

    ``through`` is given back with ``--through`` to page through the same
    snapshot. ``matching`` is the reachable entry count (at most the paging
    depth), ``matching_capped`` whether more matched, and ``depth_limited``
    whether the page asked for lay past that depth, so the last reachable
    page is shown instead.
    """

    through: str
    page: int
    pages: int
    size: int
    sort: str
    matching: int
    matching_capped: bool
    depth_limited: bool
    has_next: bool
    entries: list


@dataclass(frozen=True)
class LogExport(ReadModel):
    """The file ``logs export`` wrote to standard output, described.

    ``size`` and ``sha256`` are of exactly the bytes written; ``count`` is
    how many entries it holds, at most ``limit`` (the newest ones).
    """

    file_name: str
    content_type: str
    format: str
    timezone: str
    size: int
    sha256: str
    count: int
    limit: int


# Reviewed detail fields that identify a Family or one of its members. The
# screen and its download show them to the Administrator; the terminal
# document leaves them out (the specification's personal data rule), as the
# delivery commands leave out a recipient's DUID. Ministry DUIDs name
# Ministries, not people, and stay.
FAMILY_DETAILS = frozenset({"family_duid", "member_duid"})


def log_entry(row):
    """One display row as the command prints it: identifiers and stored values.

    ``details`` holds the page's reviewed fields under their stored names,
    as the page's export does, without those naming a Family or member. The
    screen's actor email and translated sentences are left out.
    """
    return {
        "id": row["id"],
        "source": row["source_kind"],
        "created_at": row["created_at"],
        "level": row["level"],
        "type": row["event"],
        "actor_id": row["actor_id"],
        "actor_kind": row["actor_kind"],
        "correlation_id": row["correlation_id"],
        "campaign_id": row["campaign_id"],
        "subject_id": row["subject_id"],
        "details": {
            key: value for key, value in row["details"] if key not in FAMILY_DETAILS
        },
    }


def log_list_model(table, *, through, depth_limited):
    """The command's projection of ``log_reads.load_page``."""
    return LogList(
        # The page's own snapshot spelling (``LogQuery`` accepts only it).
        through=through.astimezone(UTC).isoformat(timespec="microseconds"),
        page=table.number,
        pages=table.pages,
        size=table.size,
        sort=table.sort,
        matching=table.count,
        matching_capped=bool(table.capped),
        depth_limited=bool(depth_limited),
        has_next=bool(table.has_next),
        entries=[log_entry(row) for row in table.rows],
    )


def log_query(filters):
    """The page's ``LogQuery`` from the command's filter values.

    ``filters`` maps the page's field names to option values (None when not
    given) plus ``show``, the kinds of entry ticked. Ticking any kind is an
    applied form, as on the page; ticking none keeps the page's first-visit
    default (every level but debug, and audit records).
    """
    from .admin_reads import query
    from .audit.log_rows import LogQuery

    values = dict(filters)
    show = values.pop("show", None) or ()
    if show:
        values["applied"] = "yes"
        values.update(dict.fromkeys(show, "yes"))
    return LogQuery.parse(query(**values))


def _configured():
    """Refuse while setup is unfinished or a restore is under review (exit 3)."""
    from .accounts.runtime_models import SystemConfiguration

    configuration = SystemConfiguration.objects.first()
    if configuration is None or configuration.restore_review_required:
        raise Unavailable("System logs cannot be read now.")


def read_logs(caller, service, filters):
    """``logs list``: one page of the System logs screen.

    Admits passively with ``SYSTEM_LOGS``, reads the page in one
    transaction anchored to ``through`` (the given snapshot, or the database
    time now), rechecks the session and the restore, and records the page's
    ``system_logs_viewed`` with the page's count. Invalid filters are the
    page's refusals (``invalid``).
    """
    from .accounts.policy import Capability
    from .audit.log_reads import load_page
    from .audit.schemas import Action
    from .jobs.ownership import database_now

    query = log_query(filters)

    def step():
        """Admit, read, recheck and audit, as the page."""
        actor = _admit(caller, service.store, Capability.SYSTEM_LOGS)
        with transaction.atomic():
            _configured()
            through = query.snapshot or database_now()
            table, depth_limited = load_page(query, through=through)
        with transaction.atomic():
            current = _recheck(caller, service.store, actor, Capability.SYSTEM_LOGS)
            _configured()
            _audit(Action.SYSTEM_LOGS_VIEWED, current, count=len(table.rows))
        return log_list_model(table, through=through, depth_limited=depth_limited)

    return _held(step)


def _export_admit(caller, service):
    """The page's admission of a download, recording activity as its post does.

    A session that ended since the command was admitted is exit 5
    (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import Capability, allows
    from .accounts.sessions import authenticated_admin

    actor = authenticated_admin(caller, store=service.store, activity=True)
    if actor is None:
        raise SessionUnusable("session_ended")
    if not allows(actor, Capability.SYSTEM_LOGS):
        raise PermissionError("System logs require an Administrator.")
    return actor


def export_logs(caller, service, filters, *, fmt, zone_name, context):
    """``logs export``: the System logs page's download, as bytes.

    The page's filters (its snapshot and paging are not offered, as the page
    ignores them), ``fmt`` (``csv`` or ``jsonl``) and ``zone_name`` (UTC or
    a supported timezone name) for the times in the file. Admits as the
    page's form post does, reads the newest matching entries (at most the
    page's limit), rechecks the session and records the page's
    ``system_logs_exported`` with its count. The bytes go to
    ``context["stream"]``, which the command line writes to standard output.
    """
    from .accounts.policy import Capability
    from .audit.log_reads import export_body, export_choice, export_rows
    from .audit.schemas import Action

    fmt, zone = export_choice(fmt, zone_name)
    query = log_query(filters)

    def step():
        """Admit, read, build the file, recheck and audit, as the page."""
        actor = _export_admit(caller, service)
        with transaction.atomic():
            _configured()
            rows = export_rows(query)
        body = export_body(rows, fmt, zone).encode("utf-8")
        with transaction.atomic():
            current = _recheck(caller, service.store, actor, Capability.SYSTEM_LOGS)
            _audit(Action.SYSTEM_LOGS_EXPORTED, current, count=len(rows))
        return body, len(rows)

    body, count = _held(step)
    context["stream"] = body
    return log_export_model(
        body, fmt=fmt, zone_name=zone_name, count=count, now=datetime.now(UTC)
    )


def log_export_model(body, *, fmt, zone_name, count, now):
    """The document describing the bytes ``logs export`` writes."""
    from .audit.log_reads import EXPORT_FORMATS, EXPORT_LIMIT, export_file_name

    return LogExport(
        file_name=export_file_name(fmt, now),
        content_type=EXPORT_FORMATS[fmt],
        format=fmt,
        timezone=zone_name,
        size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        count=count,
        limit=EXPORT_LIMIT,
    )
