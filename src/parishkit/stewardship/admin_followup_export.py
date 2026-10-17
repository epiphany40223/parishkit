"""``followup export``: the follow-up queue's Export these requests (ADM-11 PR 11c).

The page's download (``admin:ministry_followup_export``,
``reports.ministry_followup_exports.export``, #1157) is the queue's whole
filtered set, with each Member's contact details, rendered in memory as CSV,
XLSX or PDF. It is never a stored export, so nothing is kept and there is
no history, Regenerate or download window (#1398). The command writes the
same file through the page's own pieces, so the two cannot drift: its
admission (``ministry_followup_views._principal``: Staff, Administrators and
Ministry leaders, the rows scoped to a leader's Ministries exactly as the
queue's; first passive, then recording activity as the download's post
does), the shared report read (``admin_report_reads._report``: the current
campaign, ``admit_report_read`` and the campaign read guard), its choice
check (``ministry_followup_exports._choices``), its read and writer
(``export_body``) and its ``ministry_followup_exported`` event, with the
count and the Ministry DUIDs in the file.

The options are ``followup list``'s filters without the search, which would
put text naming Members in shell history, and without the page and size,
which only window the screen. As ``export roster``, the bytes go to
``context["stream"]``, which the command line writes alone to standard
output, and the document describing them to standard error. Nothing of the
file is logged.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from .admin_reads import ReadModel, _recheck


@dataclass(frozen=True)
class FollowupExport(ReadModel):
    """The file ``followup export`` wrote to standard output, described.

    ``size`` and ``sha256`` are of exactly the bytes written; ``count`` is
    how many requests it lists, as the page records.
    """

    campaign_id: UUID
    file_name: str
    content_type: str
    format: str
    timezone: str
    size: int
    sha256: str
    count: int


class _PdfRefused(Exception):
    """A PDF above the page's limit, carried past the shared read's failures.

    The shared report read reports a ``ValueError`` from inside the guard as
    a read failure (``unavailable``); the page answers this one as a refused
    choice, so it travels as its own exception and becomes ``invalid``.
    """


def export_followup(caller, service, *, filters, fmt, zone_name, context):
    """``followup export``: the queue's filtered requests, as the page's file.

    ``filters`` holds the queue's choices by name (``ministry``, ``action``,
    ``state``, ``outcome``, ``sort``; None when not given), ``fmt`` and
    ``zone_name`` the file's format and the zone of its times. The shared
    report read admits passively with the page's own admission first, so a
    reader without the queue is refused before anything is read or built;
    then the page checks the choices (a refusal is ``invalid``, reported
    after the session recheck, so an ended session is exit 5) and activity
    is recorded as the download's post does. No current campaign is
    ``not_available``. A PDF of more requests than the page allows is
    ``invalid`` at ``format`` with the page's words. Records the page's
    ``ministry_followup_exported`` (succeeded or failed) with the requests
    and Ministries it reached.
    """
    from django.utils.datastructures import MultiValueDict

    from .admin_followup import InvalidFollowup, problem
    from .admin_report_reads import _report
    from .reports import ministry_followup_exports as page
    from .reports.ministry_followup_exports import (
        FORMATS,
        PDF_TOO_LARGE_FIX,
        PdfTooLarge,
        _audit,
        _choices,
        _principal,
        export_body,
        export_file_name,
    )

    given = {key: str(value) for key, value in filters.items() if value is not None}
    posted = MultiValueDict(
        {
            key: [value]
            for key, value in (given | {"format": fmt, "timezone": zone_name}).items()
        }
    )
    holder = {}
    progress = {}

    def admit(actor, campaign_id):
        """The page's check of its choices, then the download post's activity."""
        holder["actor"] = actor
        try:
            holder["choice"] = _choices(posted)
        except ValueError:
            _recheck(caller, service.store, actor)
            raise
        _principal(caller, service.store)

    def read(guard, actor, campaign_id):
        """The page's rows and contacts, rendered as the chosen file."""
        holder["actor"] = actor
        query, chosen, zone = holder["choice"]
        try:
            body = export_body(campaign_id, query, actor, chosen, zone, progress)
        except PdfTooLarge:
            raise _PdfRefused from None
        holder["body"] = body
        model = FollowupExport(
            campaign_id=campaign_id,
            file_name=export_file_name(chosen, datetime.now(UTC)),
            content_type=FORMATS[chosen],
            format=chosen,
            timezone=zone_name,
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            count=progress["count"],
        )
        return model, None

    def audit(actor, campaign_id, outcome, values):
        """The page's download event, with what the read reached.

        As the page's ``finish``, a failed file still records the requests
        and Ministries its selection had read (none if it read nothing).
        """
        _audit(
            actor,
            campaign_id,
            outcome,
            progress.get("count", 0),
            progress.get("ministries", ()),
        )

    try:
        model = _report(
            caller,
            service,
            principal=_principal,
            read=read,
            audit=audit,
            admit=admit,
            shaping=True,
        )
    except _PdfRefused:
        # Rechecked first, as every refusal is, so an ended session is exit 5.
        _recheck(caller, service.store, holder["actor"])
        # The page's refusal and fix (``pdf_refusal``), its limit read from
        # the page's module so the two always name the same number.
        message = (
            f"The PDF is limited to {page.EXPORT_PDF_LIMIT:,} requests. "
            f"{PDF_TOO_LARGE_FIX}"
        )
        raise InvalidFollowup([problem("format", message)]) from None
    context["stream"] = holder["body"]
    return model
