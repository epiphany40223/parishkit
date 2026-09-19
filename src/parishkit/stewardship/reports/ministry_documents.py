"""One immutable Ministry presentation for CSV, XLSX and paginated PDF."""

import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.web.presentation import out_of

from .directories import address_lines
from .ministries import OUTCOMES, STATES


@dataclass(frozen=True, repr=False)
class MinistryDocument:
    """Detached strings only; rendering never queries current private source data."""

    metadata: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    headings: tuple[str, ...]
    title: str
    sheet_name: str = "Ministry requests"


def ministry_document(payload, parameters, *, parish_name, requested_at, timezone):
    """Use captured counts, age, publication flags and ordered complete results."""
    zone = ZoneInfo(timezone)

    def instant(value):
        """Localize immutable aware timestamps without a rendering-time clock."""
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Ministry report timestamps must be aware.")
        return parsed.astimezone(zone).isoformat(timespec="seconds")

    action = parameters["action"]
    selected = payload["summaries"] if action == "summary" else payload["rows"]
    if payload["total"] != len(selected):
        raise ValueError("Ministry exports require the complete result.")
    source = payload["metadata"]
    title = {
        "summary": "Ministry request summary",
        "join": "Prospective Ministry joiners",
        "leave": "Requested Ministry leavers",
    }[action]
    metadata = (
        ("Report", title),
        ("Parish", parish_name),
        ("Campaign", source["name"]),
        ("Campaign reference", source["id"]),
        ("Source reference", source["source_id"]),
        ("Source generation", f"{source['source_generation']:,}"),
        ("Source as of", instant(source["source_as_of"])),
        ("Captured at", instant(source["observed_at"])),
        ("Requested at", instant(requested_at)),
        ("Display timezone", timezone),
        ("Age reference date", source["report_date"]),
        ("Campaign date-filter timezone", source["timezone"]),
        ("Matching results", f"{payload['total']:,}"),
        (
            "Filters and sort",
            json.dumps(parameters, ensure_ascii=False, sort_keys=True),
        ),
        (
            "Privacy",
            "Sensitive parish information. Share only with authorized recipients.",
        ),
        (
            "Contacts",
            "Publication flags and contact values are from the recorded "
            "source snapshot.",
        ),
        (
            "Addresses",
            "Known primary Family address; separate mailing address "
            "unavailable in source.",
        ),
    )
    rows = []
    if action != "summary":
        metadata += (
            (
                "Ministry",
                "\n".join(item["name"] for item in payload["summaries"])
                or "Unavailable for these filters",
            ),
        )
    if action == "summary":
        headings = (
            "Ministry",
            "Ministry DUID",
            "Activity",
            "Joining",
            "Leaving",
            "Unresolved",
            "Follow-up progress",
        )
        for item in selected:
            rows.append(
                (
                    item["name"],
                    str(item["duid"]),
                    "Unavailable"
                    if item["active"] is None
                    else "Active"
                    if item["active"]
                    else "Inactive",
                    f"{item['joining']:,}",
                    f"{item['leaving']:,}",
                    f"{item['unresolved']:,}",
                    out_of(Percentage(item["completed"], item["requests"])),
                )
            )
    else:
        headings = (
            "Member",
            "Member DUID",
            "Proposed Member reference",
            "Ministry DUID",
            "Submitted",
            "State",
            "Outcome",
            "Assignee",
        )
        headings += (
            ("Gender", "Age", "Email", "Phones", "Known primary Family address")
            if action == "join"
            else ("Current Ministry role",)
        )
        for item in selected:
            common = (
                item["member_name"],
                str(item["member_duid"]) if item["member_duid"] else "",
                item["proposed_id"] or "",
                str(item["ministry_duid"]),
                instant(item["submitted_at"]),
                STATES[item["state"]],
                OUTCOMES.get(item["outcome"], "Not yet recorded"),
                "Not recorded",
            )
            if action == "leave":
                rows.append(common + (item["current_role"] or "Unavailable",))
                continue
            emails = "\n".join(
                value["value"] for value in item["emails"] or [] if value.get("value")
            )
            phones = "\n".join(
                f"{key}: {value}"
                for key, value in sorted((item["phones"] or {}).items())
                if value
            )
            rows.append(
                common
                + (
                    item["gender"] or "Unavailable",
                    f"{item['age']:,}" if item["age"] is not None else "Unavailable",
                    "Not published"
                    if item["email_visibility"] == "not_published"
                    else emails or "Unavailable",
                    "Not published"
                    if item["phone_visibility"] == "not_published"
                    else phones or "Unavailable",
                    "\n".join(address_lines(item["address"] or {})) or "Unavailable",
                )
            )
    return MinistryDocument(
        metadata, tuple(rows), payload["total"], requested_at, headings, title
    )
