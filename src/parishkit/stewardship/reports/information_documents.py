"""Detached complete information values shared by CSV, XLSX and paginated PDF."""

import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .weekly_presentation import DISPOSITIONS

HEADINGS = (
    "Record",
    "Item reference",
    "Version",
    "Family",
    "Family DUID",
    "Submitted",
    "Disposition",
    "Replacement reference",
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
        return parsed.astimezone(zone).isoformat(timespec="seconds")

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
    rows = []
    for item in payload["rows"]:
        common = (
            item["family_name"],
            str(item["family_duid"]),
            instant(item["submitted_at"]),
            DISPOSITIONS[item["disposition"]],
            item["replacement_id"] or "",
        )
        rows.append(
            (
                "Item",
                item["id"],
                f"{item['version']:,}",
                *common,
                item["text"],
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
                        "Workflow revision",
                        item["id"],
                        f"{revision['version']:,}",
                        *common,
                        "",
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
