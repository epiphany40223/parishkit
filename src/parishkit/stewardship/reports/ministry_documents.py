"""One immutable Ministry presentation for CSV, XLSX and paginated PDF."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.web.presentation import out_of
from parishkit.stewardship.web.presentation import phone as format_phone

from .directories import address_lines
from .ministries import NOT_IN_CAMPAIGN, OUTCOMES, STATE_LABELS


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


def activity(item):
    """A summary row's activity, noting a Ministry no longer in the campaign.

    Captures made before the in_campaign flag existed carry no such key.
    """
    label = (
        "Unavailable"
        if item["active"] is None
        else "Active"
        if item["active"]
        else "Inactive"
    )
    if item.get("in_campaign") is False:
        label = f"{label}; {NOT_IN_CAMPAIGN.lower()}"
    return label


def new_member_labels(items):
    """The Member DUID label of each Member added on the form, by proposed id.

    A Member added on the form has no DUID yet; the page says "New Member"
    and keeps its proposed id in Technical details, which a file leaves out
    (#966). Two New Members with the same name would then give identical
    rows, so those get "New Member 1", "New Member 2", ... in form order:
    proposed id order, the order the Family form lists them once saved and
    the order their requests are recorded in. A New Member's rows (for
    example its earlier requests) share one label, and every format renders
    the one document, so the label is the same in the CSV, XLSX and PDF.
    """
    by_name = {}
    for item in items:
        if item["proposed_id"] and not item["member_duid"]:
            by_name.setdefault(item["member_name"], set()).add(item["proposed_id"])
    labels = {}
    for proposed_ids in by_name.values():
        ordered = sorted(proposed_ids)
        for number, proposed_id in enumerate(ordered, 1):
            labels[proposed_id] = (
                f"New Member {number}" if len(ordered) > 1 else "New Member"
            )
    return labels


def ministry_document(payload, parameters, *, parish_name, requested_at, timezone):
    """Use captured counts, age, publication flags and ordered complete results."""
    zone = ZoneInfo(timezone)

    def instant(value):
        """Localize immutable aware timestamps without a rendering-time clock."""
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Ministry report timestamps must be aware.")
        return parsed.astimezone(zone)

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
        ("Source as of", instant(source["source_as_of"])),
        ("Captured at", instant(source["observed_at"])),
        ("Requested at", instant(requested_at)),
        ("Display timezone", timezone),
        ("Age reference date", date.fromisoformat(source["report_date"])),
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
        # A list is one Ministry's, so its name and DUID head the file, as
        # they head the page's list, rather than repeat on every row (#932).
        metadata += (
            (
                "Ministry",
                "\n".join(item["name"] for item in payload["summaries"])
                or "Unavailable for these filters",
            ),
            (
                "Ministry DUID",
                "\n".join(str(item["duid"]) for item in payload["summaries"])
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
                    activity(item),
                    f"{item['joining']:,}",
                    f"{item['leaving']:,}",
                    f"{item['unresolved']:,}",
                    out_of(Percentage(item["completed"], item["requests"])),
                )
            )
    else:
        # The page's list order (#932): Member, Member DUID, status and
        # outcome, contact details or current role, and Submitted last.
        headings = ("Member", "Member DUID", "State", "Outcome")
        headings += (
            ("Gender", "Age", "Email", "Phones", "Known primary Family address")
            if action == "join"
            else ("Current Ministry role",)
        )
        headings += ("Submitted",)
        new_members = new_member_labels(selected)
        for item in selected:
            common = (
                item["member_name"],
                str(item["member_duid"])
                if item["member_duid"]
                else new_members.get(item["proposed_id"], ""),
                STATE_LABELS[item["state"]],
                OUTCOMES.get(item["outcome"], "Not yet recorded"),
            )
            submitted = (instant(item["submitted_at"]),)
            if action == "leave":
                rows.append(
                    common + (item["current_role"] or "Unavailable",) + submitted
                )
                continue
            emails = "\n".join(
                value["value"] for value in item["emails"] or [] if value.get("value")
            )
            phones = "\n".join(
                f"{key}: {format_phone(value)}"
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
                + submitted
            )
    return MinistryDocument(
        metadata,
        tuple(rows),
        payload["total"],
        requested_at,
        headings,
        title,
        sheet_name="Ministry summary" if action == "summary" else "Ministry requests",
    )
