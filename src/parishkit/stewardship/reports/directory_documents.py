"""Detached, complete mail-merge columns with explicit address availability."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

from .directories import REASONS, address_lines

HEADINGS = (
    "Record",
    "Family",
    "Family DUID",
    "Manual code",
    "Eligible email",
    "Deliverable email",
    "Email availability reason",
    "Campaign response",
    "Envelope number",
    "Active Family heads",
    "Family and Member phones",
    "Primary address line 1",
    "Primary address line 2",
    "Primary address line 3",
    "Primary city",
    "Primary state",
    "Primary postal code",
    "Primary ZIP extension",
    "Primary address availability",
    "Separate home address",
    "Separate mailing address",
)
ADDRESS_FIELDS = (
    "primaryAddress1",
    "primaryAddress2",
    "primaryAddress3",
    "primaryCity",
    "primaryState",
    "primaryPostalCode",
    "primaryZipPlus",
)


@dataclass(frozen=True, repr=False)
class DirectoryDocument:
    """The renderer has no database handles, clocks, key material or live selectors."""

    metadata: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    title: str
    headings: ClassVar[tuple[str, ...]] = HEADINGS
    sheet_name: ClassVar[str] = "Families"


def directory_document(
    payload, parameters, *, parish_name, captured_at, requested_at, timezone
):
    """Preserve the capture; add codes before detaching within the worker's guard."""
    if payload["total"] != len(payload["rows"]):
        raise ValueError("Directory export requires the complete matching result.")
    zone = ZoneInfo(timezone)

    def instant(value):
        """Require explicit stored offsets before browser-local presentation."""
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if value.utcoffset() is None:
            raise ValueError("Directory timestamps must be aware.")
        return value.astimezone(zone).isoformat(timespec="seconds")

    title = (
        "Families without deliverable email"
        if parameters["postal"]
        else "Family-code directory"
    )
    source = payload["metadata"]
    metadata = (
        ("Report", title),
        ("Parish", parish_name),
        ("Campaign", source["name"]),
        ("Campaign reference", source["id"]),
        ("Source reference", source["source_id"]),
        ("Source generation", f"{source['source_generation']:,}"),
        ("Source as of", instant(source["source_as_of"])),
        ("Captured at", instant(captured_at)),
        ("Requested at", instant(requested_at)),
        ("Display timezone", timezone),
        ("Matching Families", f"{payload['total']:,}"),
        (
            "Filters and sort",
            json.dumps(parameters["filters"], ensure_ascii=False, sort_keys=True),
        ),
        ("Exact-code filter", "Applied" if parameters["exact"] else "Not applied"),
        (
            "Address note",
            "The source does not distinguish separate home and mailing addresses.",
        ),
        (
            "Mail merge",
            "Select rows where Record is Family; "
            "Report metadata is not a mailing recipient.",
        ),
        (
            "Privacy",
            "Sensitive parish information and campaign manual codes. "
            "Authorized recipients only.",
        ),
    )
    rows = []
    for item in payload["rows"]:
        address = item["address"]
        rows.append(
            (
                "Family",
                item["family_name"],
                str(item["family_duid"]),
                item["code"] or "Unavailable",
                "Yes" if item["email_eligible"] else "No",
                "Yes" if item["email_deliverable"] else "No",
                REASONS[item["reason"]],
                "Responded" if item["responded"] else "Not yet responded",
                str(item["envelope"] or ""),
                "; ".join(head["name"] for head in item["heads"]),
                "\n".join(
                    f"{phone['owner']} — {phone['kind']}: {phone['value']}"
                    for phone in item["phones"]
                ),
                *(str(address.get(field) or "") for field in ADDRESS_FIELDS),
                "Known primary address"
                if address_lines(address)
                else "No address supplied"
                if address
                else "Unavailable",
                "Unavailable",
                "Unavailable",
            )
        )
    return DirectoryDocument(
        metadata, tuple(rows), payload["total"], requested_at, title
    )
