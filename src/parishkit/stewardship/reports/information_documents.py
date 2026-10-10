"""Detached complete information values shared by CSV, XLSX and paginated PDF."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

from .weekly_presentation import DISPOSITIONS

# Rows carry no internal references (Administrator, 2026-10-09, PR #929): an
# "Earlier workflow" row follows its item and repeats the item's Family, DUID,
# submission time and text, which identify the item to a reader; a superseded
# item names its replacement by that request's submission time.
ITEM = "Item"
EARLIER_WORKFLOW = "Earlier workflow"
# A superseded item whose replacement the export's filters left out.
REPLACEMENT_NOT_EXPORTED = "Not in this export"
HEADINGS = (
    "Row type",
    "Version",
    "Family",
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
        ("Campaign reference", source["id"]),
        ("Source reference", source["source_id"]),
        ("Source generation", f"{source['source_generation']:,}"),
        ("Source as of", instant(source["source_as_of"])),
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
            item["family_name"],
            str(item["family_duid"]),
            instant(item["submitted_at"]),
            DISPOSITIONS[item["disposition"]],
            replaced_by(item),
            item["text"],
        )
        rows.append(
            (
                ITEM,
                f"{item['version']:,}",
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
                        EARLIER_WORKFLOW,
                        f"{revision['version']:,}",
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
