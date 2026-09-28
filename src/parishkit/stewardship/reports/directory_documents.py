"""Detached directory export documents: a code list or a postal mail merge.

Each export is one header row plus one row per Family, so a spreadsheet or
word processor can use it directly. Report details (parish, campaign, capture
time, filters and the privacy note) are not columns: they go in the PDF header
and footer and in the XLSX "Report information" sheet.

- The Family-code directory lists Family (the surname, then the heads of
  household: "Squyres, Tracy and Jeff"), ParishSoft DUID and Family code. When
  it is filtered to Families that no campaign mail can reach (reach
  "neither"), it adds their phone numbers for follow-up calls.
- The postal export is a mail merge for envelope labels and cover letters. It
  includes only Families with a usable mailing address; the rest are counted
  so staff can follow up.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from parishkit.stewardship.source.family_names import (
    family_heads_name,
)
from parishkit.stewardship.web.presentation import phone as format_phone

from .directories import REACH, REASONS

CODE_HEADINGS = ("Family", "ParishSoft DUID", "Family code")
PHONE_HEADING = "Phone numbers"
POSTAL_HEADINGS = (
    "ParishSoft DUID",
    "Family",
    "Addressee",
    "Family heads",
    "Address line 1",
    "Address line 2",
    "Address line 3",
    "City",
    "State",
    "ZIP",
    "Family code",
)
PRIVACY = "Sensitive: Family codes. Authorized recipients only."
# In Testing mode the Family sign-in accepts only rehearsal credentials from a
# chosen-Family test send, so a file of live codes says so in its details.
TESTING_NOTE = (
    "These are the live codes; they work only after go-live. To try the Family "
    "form now, send yourself a test invitation (Campaign settings: Try the "
    "Family form as a chosen Family)."
)
FILTER_LABELS = {
    "phone": ("Phone available", {"yes": "Yes", "no": "No"}),
    "response": ("Campaign response", {"yes": "Responded", "no": "Not yet responded"}),
    "reason": ("Email availability", REASONS),
    "reach": ("Campaign mail can reach", REACH),
}


@dataclass(frozen=True, repr=False)
class DirectoryDocument:
    """The renderer has no database handles, clocks, key material or live selectors.

    ``postal`` selects the PDF layout (address blocks rather than a table);
    ``excluded`` counts Families left out of a postal file for want of a
    usable mailing address. ``item_count`` is the captured matching count,
    which the publication must record (the SQL publication guard binds it to
    the snapshot), so for a postal file it includes the excluded Families.
    """

    metadata: tuple[tuple[str, str], ...]
    headings: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    title: str
    postal: bool
    excluded: int = 0
    sheet_name: str = "Families"


def _clean(value):
    """A source value as trimmed text; missing values become empty."""
    return str(value or "").strip()


def mailable(address):
    """A usable mailing address: the same rule as directory_reports.sql.

    A street line and a city, plus a state or a postal code. Captures made
    before the SQL computed ``mailable`` are judged here the same way.
    """
    return bool(
        _clean(address.get("primaryAddress1"))
        and _clean(address.get("primaryCity"))
        and (
            _clean(address.get("primaryState"))
            or _clean(address.get("primaryPostalCode"))
        )
    )


def head_names(heads):
    """The Family heads' names as one natural phrase ("Aaron and Isabelle
    Williams"), or "" without heads; see ``family_heads_name``."""
    return family_heads_name("", heads, surname_first=False)


def _zip(address):
    """The postal code with its +4 extension when there is one."""
    return "-".join(
        filter(
            None,
            (
                _clean(address.get("primaryPostalCode")),
                _clean(address.get("primaryZipPlus")),
            ),
        )
    )


def _filters(parameters):
    """The applied filters in words, for the report details."""
    applied = []
    values = parameters["filters"]
    if values.get("search"):
        applied.append(f"Search: {values['search']}")
    for key, (label, choices) in FILTER_LABELS.items():
        value = values.get(key, "any")
        if value != "any":
            applied.append(f"{label}: {choices[value]}")
    if parameters["exact"]:
        applied.append("Exact Family code")
    return "; ".join(applied) or "None"


def directory_document(
    payload,
    parameters,
    *,
    parish_name,
    captured_at,
    requested_at,
    timezone,
    testing=False,
):
    """Preserve the capture; add codes before detaching within the worker's guard.

    ``testing`` adds the note that live codes work only after go-live; it is a
    report detail (PDF footer, XLSX information sheet), never a CSV row.
    """
    if payload["total"] != len(payload["rows"]):
        raise ValueError("Directory export requires the complete matching result.")
    zone = ZoneInfo(timezone)

    def instant(value):
        """Require explicit stored offsets before browser-local presentation."""
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if value.utcoffset() is None:
            raise ValueError("Directory timestamps must be aware.")
        return value.astimezone(zone)

    postal = parameters["postal"]
    title = "Postal mail merge" if postal else "Family-code directory"
    rows, excluded = [], 0
    if postal:
        headings = POSTAL_HEADINGS
        for item in payload["rows"]:
            address = item["address"]
            if not item.get("mailable", mailable(address)):
                excluded += 1
                continue
            heads = head_names(item["heads"])
            lines = [
                _clean(address.get(f"primaryAddress{index}")) for index in (1, 2, 3)
            ]
            lines = [line for line in lines if line]
            lines += [""] * (3 - len(lines))
            rows.append(
                (
                    str(item["family_duid"]),
                    item["family_name"],
                    heads or item["family_name"],
                    heads,
                    *lines,
                    _clean(address.get("primaryCity")),
                    _clean(address.get("primaryState")),
                    _zip(address),
                    item["code"] or "",
                )
            )
    else:
        phones = parameters["filters"].get("reach") == "neither"
        headings = CODE_HEADINGS + ((PHONE_HEADING,) if phones else ())
        for item in payload["rows"]:
            row = (
                family_heads_name(item["family_name"], item["heads"]),
                str(item["family_duid"]),
                item["code"] or "",
            )
            if phones:
                row += (
                    "; ".join(
                        f"{row['owner']} ({row['kind']}): {format_phone(row['value'])}"
                        for row in item["phones"]
                    ),
                )
            rows.append(row)
    source = payload["metadata"]
    metadata = (
        ("Report", title),
        ("Parish", parish_name),
        ("Campaign", source["name"]),
        ("Captured at", instant(captured_at)),
        ("Families in this file", f"{len(rows):,}"),
        *(
            (("Not in this file: no usable mailing address", f"{excluded:,}"),)
            if postal
            else ()
        ),
        ("Filters applied", _filters(parameters)),
        ("Privacy", PRIVACY),
        *((("Testing mode", TESTING_NOTE),) if testing else ()),
    )
    return DirectoryDocument(
        metadata=metadata,
        headings=headings,
        rows=tuple(rows),
        item_count=payload["total"],
        requested_at=requested_at,
        title=title,
        postal=postal,
        excluded=excluded,
    )
