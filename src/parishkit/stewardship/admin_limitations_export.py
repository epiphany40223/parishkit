"""``export limitations``: the Limitations download (ADM-11 PR 8h, #463).

The page's download (``admin:limitations_export``,
``reports.limitation_views.export``) is the filtered Limitations report
rendered in memory as CSV, XLSX or PDF. The command writes the same file
through the page's own pieces, so the two cannot drift: its admission
(``limitation_views._principal`` with ``export``: ``CAMPAIGN_REPORT`` and
``REPORT_EXPORT``, never a Ministry leader; recording activity, as the
download's post does), the shared report read (``admin_report_reads._report``:
the current campaign, ``admit_report_read`` and the campaign read guard), its
choices (``limitation_views.export_choice`` and ``LimitationsQuery``, without
the search: no command takes text that names a Family), its read
(``limitations_report``), its writer (``limitation_views.export_body``) and
its ``talents_report_exported`` event (the name the report kept from
"Talents and limitations", #970), whose ``count`` is the rows in the file
and whose context is the page's ``audit_choices``.

As ``export census``, the bytes go to ``context["stream"]``, which the
command line writes alone to standard output, and the document describing
them to standard error.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

from .admin_reads import ReadModel, _recheck


@dataclass(frozen=True)
class LimitationsExport(ReadModel):
    """The file ``export limitations`` wrote to standard output, described.

    ``size`` and ``sha256`` are of exactly the bytes written; ``count`` is
    how many rows it holds (Member rows plus Family rows, as the page
    records).
    """

    campaign_id: UUID
    file_name: str
    content_type: str
    format: str
    timezone: str
    size: int
    sha256: str
    count: int


def export_limitations(caller, service, *, show, fmt, zone_name, context):
    """``export limitations``: the Limitations download, as bytes.

    ``show`` is the page's Show choice (``any``, ``cannot_serve`` or
    ``cannot_attend``), ``fmt`` and ``zone_name`` the file's format and the
    zone of its times. Admits passively with the page's download admission;
    then, once the reader is admitted, parses the choices (so a refusal is
    reported after the session recheck and an ended session is exit 5) and
    records activity as the download's post does. The read, the campaign
    check and the guard are the shared report read's: no current campaign
    is ``not_available``, a Ministry leader ``denied``. Records the page's
    ``talents_report_exported`` (succeeded or failed) with the rows in the
    file and the page's ``audit_choices``.
    """
    from .admin_report_reads import _report
    from .audit.schemas import Action
    from .campaigns.models import Campaign
    from .reports.limitation_views import (
        FORMATS,
        _audit,
        _principal,
        audit_choices,
        export_body,
        export_choice,
        export_file_name,
    )
    from .reports.limitations import (
        LimitationsQuery,
        limitations_report,
        row_count,
    )
    from .source.snapshot_names import current_snapshot

    principal = partial(_principal, export=True)
    holder = {}

    def admit(actor, campaign_id):
        """The download post's activity and the page's checks of its choices."""
        try:
            holder["choice"] = export_choice(fmt, zone_name)
            holder["query"] = LimitationsQuery.parse({"show": show})
        except ValueError:
            _recheck(caller, service.store, actor)
            raise
        principal(caller, service.store)

    def read(guard, actor, campaign_id):
        """The page's read, rendered as the chosen file.

        As on the page, a campaign that no longer exists is refused, and the
        audit names the ParishSoft snapshot only when no refresh promoted
        another between the statements of the read.
        """
        Campaign.objects.only("pk").get(pk=campaign_id)
        query, choice = holder["query"], holder["choice"]
        before = current_snapshot()
        result = limitations_report(campaign_id, query, actor)
        snapshot = before if current_snapshot() == before else None
        count = row_count(result)
        body = export_body(campaign_id, result, choice["format"], choice["zone"])
        holder["body"] = body
        model = LimitationsExport(
            campaign_id=campaign_id,
            file_name=export_file_name(choice["format"], datetime.now(UTC)),
            content_type=FORMATS[choice["format"]],
            format=choice["format"],
            timezone=zone_name,
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            count=count,
        )
        return model, (count, audit_choices(query, snapshot))

    def audit(actor, campaign_id, outcome, values):
        """The page's download event: the rows in the file and its choices.

        A failed read has no values, so it records 0 rows and the parsed
        choices without a snapshot, as the page's ``finish(False)`` does.
        """
        if values is None:
            query = holder.get("query") or LimitationsQuery()
            values = (0, audit_choices(query, None))
        count, choices = values
        _audit(
            Action.TALENTS_REPORT_EXPORTED, actor, campaign_id, outcome, count, choices
        )

    model = _report(
        caller,
        service,
        principal=principal,
        read=read,
        audit=audit,
        admit=admit,
        shaping=True,
    )
    context["stream"] = holder["body"]
    return model
