"""``export census``: the Census changes download (ADM-11 PR 8h, #463).

The page's download (``admin:census_changes_export``,
``reports.census_change_views.export``) is the filtered Census changes list
rendered in memory as CSV, XLSX or PDF. The command writes the same file
through the page's own pieces, so the two cannot drift: its admission
(``census_change_views._principal`` with ``export``: the census capability
and ``REPORT_EXPORT``, never a Ministry leader; recording activity, as the
download's post does), the shared report read (``admin_report_reads._report``:
the current campaign, ``admit_report_read`` and the campaign read guard), its
filters (``CensusQuery``, without the search, as ``report census``), its read
(``census_changes``), its writer (``census_change_views.export_body``) and its
``census_changes_exported`` event, whose ``count`` is the rows in the file.

``jsonl`` is the command's own format, offered only on the command line:
the page's columns with the same text under fixed keys, plus each row's
``proposal_id`` and ``version`` (what ``census resolve``, ``census
review``, ``census edit`` and ``census resolve-conflict`` take), the
``actions`` this reader may record on it now, the ``reviews`` (decisions)
it may give it now, and whether it may edit its value (``editable``) or
resolve its conflict (``resolvable``) now. The page's three files leave
those internal references out (UX conventions, "Downloaded files", #956,
where this format is a recorded exception): they are not personal data,
the page's row-action dialogs already carry them, and a script resolving
a change needs them.

As ``logs export``, the bytes go to ``context["stream"]``, which the command
line writes alone to standard output, and the document describing them to
standard error.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

from .admin_reads import ReadModel, _recheck

# The page's formats, and the command's JSON Lines.
CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "jsonl": "application/x-ndjson",
}
# The fixed keys of a JSON Lines record, one per column of the page's files
# (``census_changes.HEADINGS``), in the same order; a test keeps them aligned.
JSONL_KEYS = (
    "family",
    "parishsoft_status",
    "family_duid",
    "who",
    "member_duid",
    "what",
    "parishsoft_now",
    "family_answer",
    "edited",
    "route",
    "status",
    "submitted",
    "recorded_by",
    "recorded_at",
    "note",
)


@dataclass(frozen=True)
class CensusExport(ReadModel):
    """The file ``export census`` wrote to standard output, described.

    ``size`` and ``sha256`` are of exactly the bytes written; ``count`` is
    how many rows it holds (the rows the filters keep).
    """

    campaign_id: UUID
    file_name: str
    content_type: str
    format: str
    timezone: str
    size: int
    sha256: str
    count: int


def actions(row, actor, *, mutable=True):
    """The outcomes ``actor`` may record on ``row`` now, as the page offers them.

    Entered in ParishSoft and Ignore on a By-hand row still open (the census
    capability Staff hold too); Reopen on an ignored row, for an
    Administrator only. None while other campaign work holds the campaign
    (``mutable`` false), as the page then offers no row actions.
    """
    from .accounts.policy import Capability, allows

    found = []
    if not mutable:
        return found
    if row["can_resolve"] and allows(actor, Capability.MANUAL_CENSUS):
        found += ["entered", "ignored"]
    if row["can_reopen"] and allows(actor, Capability.PUBLISH_CENSUS):
        found.append("reopened")
    return found


def reviews(row, actor, *, mutable=True):
    """The decisions ``actor`` may give ``row`` now, as the page's Review offers.

    The row's Review choices (``census_changes.REVIEW_CHOICES``: an
    Automatic row still open, never queued or final, and never the decision
    it has), as the decisions ``census review`` takes, for an Administrator
    only; none while other campaign work holds the campaign (``mutable``
    false), as the page then offers no Review.
    """
    from .accounts.policy import Capability, allows
    from .admin_census import decision_word
    from .reports.census_changes import REVIEW_CHOICES

    if not mutable or not allows(actor, Capability.PUBLISH_CENSUS):
        return []
    decisions = dict(REVIEW_CHOICES)
    # In the command line's words (decision 7): ``accepted``, not ``approved``.
    return [decision_word(decisions[action]) for action in row["review_actions"]]


def offered(row, actor, key, *, mutable=True):
    """Whether ``actor`` may use the row's ``key`` control now (#1434).

    ``edit`` is Edit value and Clear edit (``census edit``) and
    ``resolve_conflict`` Use the Family's value anyway (``census
    resolve-conflict``), as ``census_changes.shape`` offers them on the row.
    An Administrator's only, and neither while other campaign work holds
    the campaign, as the page then offers no Review.
    """
    from .accounts.policy import Capability, allows

    return bool(mutable and allows(actor, Capability.PUBLISH_CENSUS) and row.get(key))


def census_jsonl(result, zone, actor, *, mutable=True):
    """The filtered rows as JSON Lines: the page's cells, ids and actions.

    Each cell is the CSV's text (``census_changes.export_rows``), so the
    formats never disagree about a value; ``proposal_id``, ``version``,
    ``actions``, ``reviews``, ``editable`` and ``resolvable`` come from the
    row the cells were made from.
    ``mutable`` is the page's check that no other campaign work holds the
    campaign.
    """
    from .reports.census_changes import export_rows

    lines = []
    for row, cells in zip(result["rows"], export_rows(result, zone), strict=True):
        record = dict(zip(JSONL_KEYS, cells, strict=True))
        record |= {
            "proposal_id": str(row["id"]),
            "version": row["version"],
            "actions": actions(row, actor, mutable=mutable),
            "reviews": reviews(row, actor, mutable=mutable),
            "editable": offered(row, actor, "edit", mutable=mutable),
            "resolvable": offered(row, actor, "resolve_conflict", mutable=mutable),
        }
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return "".join(lines).encode("utf-8")


def choice(fmt, zone_name):
    """The file's format and display zone, as ``(format, ZoneInfo)``.

    The page's choices (UTC or one supported timezone name) and the
    command's ``jsonl``; anything else is a ``ValueError`` (``invalid``).
    """
    from zoneinfo import ZoneInfo

    from .schema_primitives import timezone_names

    if fmt not in CONTENT_TYPES or (
        zone_name != "UTC" and zone_name not in timezone_names()
    ):
        raise ValueError("Invalid census change export choice.")
    return fmt, ZoneInfo(zone_name)


def _mutable(campaign_id):
    """Whether a row's outcome may be recorded now: the page's own check.

    Other campaign work (a restore review, cleanup, purge or go-live step)
    holding the campaign makes the page offer no row actions.
    """
    from .reports.export_services import admit_campaign

    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return False
    return True


def export_census(caller, service, filters, *, fmt, zone_name, context):
    """``export census``: the Census changes download, as bytes.

    ``filters`` are ``report census``'s (``status``, ``history``, ``route``,
    ``kind``, ``start``, ``end`` and ``zone``; None when not given), ``fmt``
    and ``zone_name`` the file's format and the zone of its times. Admits
    passively with the page's download admission; then, once the reader is
    admitted, parses the choices (so a refusal is reported after the session
    recheck and an ended session is exit 5) and records activity as the
    download's post does. The read, the campaign check and the guard are
    the shared report read's: no current campaign is ``not_available``, a
    Ministry leader ``denied``. For ``jsonl`` the page's mutable check
    (``admit_campaign(..., mutating=True)``) runs once, so ``actions`` are
    empty while other work holds the campaign. Records the page's
    ``census_changes_exported`` (succeeded or failed) with the rows in the
    file.
    """
    from .admin_report_reads import _report
    from .reports.census_change_views import _audit, _principal, export_body
    from .reports.census_changes import CensusQuery, census_changes

    given = {key: value for key, value in filters.items() if value is not None}
    principal = partial(_principal, export=True)
    holder = {}

    def admit(actor, campaign_id):
        """The download post's activity and the page's checks of its choices."""
        try:
            holder["choice"] = choice(fmt, zone_name)
            holder["query"] = CensusQuery.parse(given)
        except ValueError:
            _recheck(caller, service.store, actor)
            raise
        principal(caller, service.store)

    def read(guard, actor, campaign_id):
        """The page's read, rendered as the chosen file."""
        chosen, zone = holder["choice"]
        result = census_changes(campaign_id, holder["query"], actor)
        if chosen == "jsonl":
            body = census_jsonl(result, zone, actor, mutable=_mutable(campaign_id))
        else:
            body = export_body(campaign_id, result, chosen, zone)
        holder["body"] = body
        return census_export_model(
            campaign_id,
            body,
            fmt=chosen,
            zone_name=zone_name,
            count=len(result["rows"]),
            now=datetime.now(UTC),
        ), len(result["rows"])

    def audit(actor, campaign_id, outcome, count):
        """The page's download event, with the rows the file holds."""
        from .audit.schemas import Action

        _audit(Action.CENSUS_CHANGES_EXPORTED, actor, campaign_id, outcome, count or 0)

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


def census_export_model(campaign_id, body, *, fmt, zone_name, count, now):
    """The document describing the bytes ``export census`` writes."""
    from .reports.census_change_views import export_file_name

    return CensusExport(
        campaign_id=campaign_id,
        file_name=export_file_name(fmt, now),
        content_type=CONTENT_TYPES[fmt],
        format=fmt,
        timezone=zone_name,
        size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        count=count,
    )
