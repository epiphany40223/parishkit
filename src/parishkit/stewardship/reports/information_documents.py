"""Detached complete information values shared by CSV, XLSX and paginated PDF."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

from parishkit.stewardship.source.snapshot_names import FAMILY_NAMES_DETAIL
from parishkit.stewardship.source.source_status import (
    STATUS_HEADING,
    family_status,
    source_status_label,
)

from .weekly_presentation import DISPOSITIONS

# Rows carry no internal references, in any format (Administrator,
# 2026-10-09 on PR #929 and decision B1 on #966): no row type or row
# version. An earlier-workflow row follows its item, repeats the item's
# Family, DUID, submission time and text, which identify the item to a
# reader, and is the only kind of row with a Changed at time; a superseded
# item names its replacement by that request's submission time. ParishSoft
# status follows the name, as the page's badge sits beside it, and the name
# is blank for a Family the data no longer has (#1216).
# A superseded item whose replacement the export's filters left out.
REPLACEMENT_NOT_EXPORTED = "Not in this export"
HEADINGS = (
    "Family",
    STATUS_HEADING,
    "Family DUID",
    "Submitted",
    "Disposition",
    "Replaced by request submitted",
    "Submitted text",
    "Follow-up needed",
    "Followed up at",
    "Completed by",
    "Staff notes",
    "Changed at",
    "Changed by",
    "Previously reported",
    "Correction resolved",
)


@dataclass(frozen=True, repr=False)
class InformationDocument:
    """All captured values, with no live queries, clocks or mutable nested rows."""

    metadata: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    headings: ClassVar[tuple[str, ...]] = HEADINGS
    title: ClassVar[str] = "Additional information and follow-up"
    sheet_name: ClassVar[str] = "Information"


def information_document(payload, parameters, *, parish_name, requested_at, timezone):
    """Localize instants and preserve every submitted/history character verbatim."""
    zone = ZoneInfo(timezone)

    def instant(value):
        """Never interpret an unzoned stored date/time as a local instant."""
        if not value:
            return ""
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Information report timestamps must be aware.")
        return parsed.astimezone(zone)

    source = payload["metadata"]
    if payload["total"] != len(payload["rows"]):
        raise ValueError("Information export requires every matching row.")
    metadata = (
        ("Report", "Additional information and follow-up"),
        ("Parish", parish_name),
        ("Campaign", source["name"]),
        ("Source as of", instant(source["source_as_of"])),
        (FAMILY_NAMES_DETAIL, source["family_names"]),
        ("Captured at", instant(source["observed_at"])),
        ("Requested at", instant(requested_at)),
        ("Display timezone", timezone),
        ("Campaign date-filter timezone", source["timezone"]),
        ("Matching items", f"{payload['total']:,}"),
        ("Workflow history", "Included" if parameters["history"] else "Not included"),
        (
            "Filters and sort",
            json.dumps(parameters["filters"], ensure_ascii=False, sort_keys=True),
        ),
        (
            "Privacy",
            "Sensitive parish information. Share only with authorized recipients.",
        ),
        ("Digest resolution", "Resolution does not necessarily mean email delivery."),
    )
    # A replacement is always a later submission of the same Family, so its
    # submission time finds its row; the capture holds only matching items.
    submitted = {item["id"]: item["submitted_at"] for item in payload["rows"]}

    def replaced_by(item):
        """The replacing request's submission time, or why it is not shown."""
        if not item["replacement_id"]:
            return ""
        if item["replacement_id"] not in submitted:
            return REPLACEMENT_NOT_EXPORTED
        return instant(submitted[item["replacement_id"]])

    rows = []
    for item in payload["rows"]:
        # Every row of an item repeats its identity, so a sorted or filtered
        # spreadsheet still says which item an earlier workflow belongs to.
        common = (
            item["family_name"] or "",
            # A capture made before migration 0094 has no active flag, and
            # reads as active.
            source_status_label(
                family_status(
                    item["family_name"], active=item.get("active") is not False
                )
            ),
            str(item["family_duid"]),
            instant(item["submitted_at"]),
            DISPOSITIONS[item["disposition"]],
            replaced_by(item),
            item["text"],
        )
        rows.append(
            (
                *common,
                "Yes" if item["follow_up_needed"] else "No",
                instant(item["followed_up_at"]),
                item["completed_by"] if item["followed_up_at"] else "",
                item["notes"],
                "",
                "",
                "Yes" if item["previously_reported"] else "No",
                "Yes" if item["correction_resolved"] else "No",
            )
        )
        if parameters["history"]:
            for revision in item["history"]:
                rows.append(
                    (
                        *common,
                        "Yes" if revision["follow_up_needed"] else "No",
                        instant(revision["followed_up_at"]),
                        revision["completed_by"] if revision["followed_up_at"] else "",
                        revision["notes"],
                        instant(revision["created_at"]),
                        revision["actor_label"],
                        "",
                        "",
                    )
                )
    return InformationDocument(metadata, tuple(rows), payload["total"], requested_at)
