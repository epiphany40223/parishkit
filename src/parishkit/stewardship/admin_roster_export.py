"""``export roster``: the Roster changes to enter download (ADM-11 PR 8g, #463).

The page's download (``admin:ministry_roster_export``,
``reports.ministry_roster_views.export``) is the Roster changes to enter
list rendered in memory as CSV, XLSX or PDF. The command writes the same
file through the page's own pieces, so the two cannot drift: its admission
(``ministry_roster_views._principal``: Administrators and Staff, never a
Ministry leader; first passive, then recording activity as the download's
post does), the shared report read (``admin_report_reads._report``: the
current campaign, ``admit_report_read`` and the campaign read guard), its
choices (the page's Show menu and Ministry filter, and
``ministry_roster_views.export_choice``), its read (``roster_rows``), its
writer (``ministry_roster_views.export_body``) and its
``roster_changes_exported`` event, whose ``count`` is the rows in the file.

As ``export limitations``, the bytes go to ``context["stream"]``, which the
command line writes alone to standard output, and the document describing
them to standard error.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from .admin_reads import ReadModel, _recheck


@dataclass(frozen=True)
class RosterExport(ReadModel):
    """The file ``export roster`` wrote to standard output, described.

    ``size`` and ``sha256`` are of exactly the bytes written; ``count`` is
    how many changes it lists, as the page records.
    """

    campaign_id: UUID
    file_name: str
    content_type: str
    format: str
    timezone: str
    size: int
    sha256: str
    count: int


def export_roster(caller, service, *, show, ministry, fmt, zone_name, context):
    """``export roster``: the Roster changes to enter download, as bytes.

    ``show`` is the page's Show choice (``todo`` or ``all``), ``ministry``
    its Ministry filter (a DUID, or None), ``fmt`` and ``zone_name`` the
    file's format and the zone of its times. The shared report read admits
    passively with the page's own admission first, so a reader without the
    page is refused before anything is read or built; then the choices are
    checked (after the session recheck, so an ended session is exit 5) and
    activity is recorded as the download's post does. No current campaign
    is ``not_available``, a Ministry leader ``denied``. Records the page's
    ``roster_changes_exported`` (succeeded or failed) with the rows in the
    file.
    """
    from .admin_report_reads import _report
    from .audit.schemas import Action
    from .reports.ministry_followup import valid_ministry
    from .reports.ministry_roster_views import (
        FORMATS,
        _audit,
        _principal,
        export_body,
        export_choice,
        export_file_name,
        roster_rows,
    )

    filter_text = "" if ministry is None else str(ministry)
    holder = {}

    def admit(actor, campaign_id):
        """The page's checks of its choices, then the download post's activity."""
        try:
            if filter_text and not valid_ministry(filter_text):
                raise ValueError("A Ministry DUID is 1 through 2147483647.")
            holder["choice"] = export_choice(fmt, zone_name)
        except ValueError:
            _recheck(caller, service.store, actor)
            raise
        _principal(caller, service.store)

    def read(guard, actor, campaign_id):
        """The page's rows, rendered as the chosen file."""
        choice = holder["choice"]
        rows, _ = roster_rows(campaign_id, actor, show, filter_text)
        body = export_body(campaign_id, rows, choice["format"], choice["zone"])
        holder["body"] = body
        model = RosterExport(
            campaign_id=campaign_id,
            file_name=export_file_name(choice["format"], datetime.now(UTC)),
            content_type=FORMATS[choice["format"]],
            format=choice["format"],
            timezone=zone_name,
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            count=len(rows),
        )
        return model, len(rows)

    def audit(actor, campaign_id, outcome, values):
        """The page's download event: the rows in the file (0 if it failed)."""
        _audit(Action.ROSTER_CHANGES_EXPORTED, actor, campaign_id, outcome, values or 0)

    model = _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=audit,
        admit=admit,
        shaping=True,
    )
    context["stream"] = holder["body"]
    return model
