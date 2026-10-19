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

Since #1735 it takes the download's Stage (the worklist's, #1685) and the
command's own ``jsonl``, as ``export census``'s: the CSV's cells under fixed
keys plus each answer's ``kind``, ``item_id`` and ``version`` (what
``limitations decide`` takes) and the ``actions`` the reader may take on it
now.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

from .admin_reads import ReadModel, _recheck

# The command's JSON Lines, beside the page's formats.
JSONL = "application/x-ndjson"
# The fixed keys of a JSON Lines record, one per column of the CSV's two
# tables (``limitations.MEMBER_HEADINGS`` and ``FAMILY_HEADINGS``), in the
# same order; a test keeps them aligned.
MEMBER_KEYS = (
    "member",
    "member_duid",
    "family",
    "parishsoft_status",
    "family_duid",
    "cannot_participate",
    "latest_response",
    "stage",
)
FAMILY_KEYS = (
    "family",
    "parishsoft_status",
    "family_duid",
    "limitation",
    "latest_response",
    "stage",
)


@dataclass(frozen=True)
class LimitationsExport(ReadModel):
    """The file ``export limitations`` wrote to standard output, described.

    ``size`` and ``sha256`` are of exactly the bytes written; ``count`` is
    how many rows it holds (Member rows plus Family rows, as the page
    records).
    """

    campaign_id: UUID
    stage: str
    file_name: str
    content_type: str
    format: str
    timezone: str
    size: int
    sha256: str
    count: int


def limitations_jsonl(result, zone, *, mutable=True):
    """The answers as JSON Lines: the CSV's cells, ids, versions and actions.

    Each cell is the CSV's text (``export_tables``), so the formats never
    disagree about a value; ``kind``, ``item_id``, ``version`` and
    ``actions`` come from the row the cells were made from. ``mutable`` is
    the page's check that no other campaign work holds the campaign.
    """
    from .admin_worklists import row_actions
    from .pipeline import Stream
    from .reports.limitations import export_tables
    from .responses.limitation_review import answer_id

    members, families = export_tables(result, zone)
    lines = []
    # The Families table also lists each Family's Cannot contribute
    # financially answer, when the report carries them (``contributions``,
    # R39 finding 11): it is decided on Pledges, so its record names its
    # Pledges item and offers no Limitations action.
    for keys, rows, cells in (
        (MEMBER_KEYS, result["members"], members),
        (FAMILY_KEYS, result["families"] + result.get("contributions", []), families),
    ):
        for row, values in zip(rows, cells, strict=True):
            elsewhere = row["kind"] == "cannot_give"
            record = dict(zip(keys, values, strict=True)) | {
                "kind": row["kind"],
                "item_id": f"pledge-{row['pledge_item']}"
                if elsewhere
                else answer_id(row["family_duid"], row["kind"], row["member_key"]),
                "version": row["version"],
                "actions": []
                if elsewhere
                else row_actions(Stream.CANNOT_PARTICIPATE, row["placement"], mutable),
            }
            lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return "".join(lines).encode("utf-8")


def _choice(fmt, zone_name):
    """The page's format and zone check, and the command's ``jsonl``."""
    from zoneinfo import ZoneInfo

    from .reports.limitation_views import export_choice

    if fmt == "jsonl":
        export_choice("csv", zone_name)
        return {"format": fmt, "zone": ZoneInfo(zone_name)}
    return export_choice(fmt, zone_name)


def export_limitations(caller, service, *, show, fmt, zone_name, context, stage="all"):
    """``export limitations``: the Limitations download, as bytes.

    ``show`` is the page's Show choice (``any``, ``cannot_serve`` or
    ``cannot_attend``) and ``stage`` the download's Stage (every stage by
    default, as the page's), ``fmt`` and ``zone_name`` the file's format and
    the zone of its times. Admits passively with the page's download admission;
    then, once the reader is admitted, parses the choices (so a refusal is
    reported after the session recheck and an ended session is exit 5) and
    records activity as the download's post does. The read, the campaign
    check and the guard are the shared report read's: no current campaign
    is ``not_available``, a Ministry leader ``denied``. Records the page's
    ``talents_report_exported`` (succeeded or failed) with the rows in the
    file and the page's ``audit_choices``.
    """
    from .admin_report_reads import _report
    from .admin_worklists import mutable
    from .audit.schemas import Action
    from .campaigns.models import Campaign
    from .reports.limitation_views import (
        FILE_STAGES,
        FORMATS,
        _audit,
        _principal,
        audit_choices,
        export_body,
        export_file_name,
    )
    from .reports.limitations import (
        LimitationsQuery,
        at_stage,
        limitations_report,
        row_count,
    )
    from .source.snapshot_names import current_snapshot

    principal = partial(_principal, export=True)
    holder = {}

    def admit(actor, campaign_id):
        """The download post's activity and the page's checks of its choices."""
        try:
            if stage not in FILE_STAGES:
                raise ValueError("Invalid Limitations stage.")
            holder["choice"] = _choice(fmt, zone_name)
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
        whole = limitations_report(campaign_id, query, actor)
        snapshot = before if current_snapshot() == before else None
        # The page's event counts every answer its read returned, before the
        # Stage keeps the file's rows (limitation_views._respond), so the
        # command's event does too; ``count`` in the document is the file's.
        read_count = row_count(whole)
        result = at_stage(whole, stage)
        count = row_count(result)
        if choice["format"] == "jsonl":
            body = limitations_jsonl(
                result, choice["zone"], mutable=mutable(campaign_id)
            )
        else:
            body = export_body(campaign_id, result, choice["format"], choice["zone"])
        holder["body"] = body
        model = LimitationsExport(
            campaign_id=campaign_id,
            stage=stage,
            file_name=export_file_name(choice["format"], datetime.now(UTC)),
            content_type=FORMATS.get(choice["format"], JSONL),
            format=choice["format"],
            timezone=zone_name,
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            count=count,
        )
        return model, (read_count, audit_choices(query, snapshot))

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
