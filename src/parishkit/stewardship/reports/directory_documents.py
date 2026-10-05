"""Detached directory export documents: a code list or a postal mail merge.

Each export is one header row plus one row per Family, so a spreadsheet or
word processor can use it directly. Report details (parish, campaign, capture
time, filters and the privacy note) are not columns: they go in the PDF header
and footer and in the XLSX "Report information" sheet.

- The Family-code directory lists Family (the surname, then the heads of
  household: "Squyres, Tracy and Jeff"), ParishSoft DUID and Family code. When
  it is filtered to Families that no campaign mail can reach (reach
  "neither"), it adds their phone numbers for follow-up calls.
- Every export ends with Family head emails: each distinct head address once,
  with the heads who have it ("Anna Example and Ben Example: a@x; Cara
  Example: (no email)"), read when the file is rendered (#604): from the
  captured source, or from the current ParishSoft data once that has been
  compacted, which the report details then say ("Head emails as of").
- The postal export is a mail merge for envelope labels and cover letters. It
  holds exactly the filtered Families, like the page. A Family without a
  usable mailing address keeps its row with the Addressee and address
  columns blank, and the report details count those rows so staff can follow
  up. Existing columns never change name or order (Family head emails was
  added at the end), so existing mail-merge templates keep working.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from parishkit.stewardship.source.family_names import (
    family_heads_name,
    heads_salutation_name,
)
from parishkit.stewardship.web.presentation import phone as format_phone

from .directories import REACH, REASONS, head_emails_text

CODE_HEADINGS = ("Family", "ParishSoft DUID", "Family code")
PHONE_HEADING = "Phone numbers"
EMAIL_HEADING = "Family head emails"
# The postal columns and their order are what parishes' mail-merge templates
# name, so they never change (a Family without an address blanks, not drops).
# New columns are only ever appended: Family head emails came last (#604).
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
    EMAIL_HEADING,
)
UNADDRESSED_DETAIL = "Rows with no usable mailing address (address columns blank)"
PRIVACY = "Sensitive: Family codes. Authorized recipients only."
# In Testing mode the Family sign-in accepts only rehearsal credentials from a
# chosen-Family test send, so a file of live codes says so in its details.
HEAD_EMAILS_DETAIL = "Head emails as of"
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
    ``unaddressed`` counts a postal file's rows whose Addressee and address
    columns are blank for want of a usable mailing address. ``item_count`` is
    the captured matching count, which the publication must record (the SQL
    publication guard binds it to the snapshot); every file has that many rows.
    """

    metadata: tuple[tuple[str, str], ...]
    headings: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    title: str
    postal: bool
    unaddressed: int = 0
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
    Williams"), or "" without heads; see ``heads_salutation_name``."""
    return heads_salutation_name(heads)


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


def export_headings(*, postal, reach):
    """The export's columns: the mail merge, or codes (plus phones for "neither").

    Family head emails is always the last column, after any phones.

    The directory page lists them so the Admin knows what the file contains.
    """
    if postal:
        return POSTAL_HEADINGS
    phones = (PHONE_HEADING,) if reach == "neither" else ()
    return CODE_HEADINGS + phones + (EMAIL_HEADING,)


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
    head_emails_as_of=None,
):
    """Preserve the capture; add codes before detaching within the worker's guard.

    ``testing`` adds the note that live codes work only after go-live; it is a
    report detail (PDF footer, XLSX information sheet), never a CSV row.
    ``head_emails_as_of`` is the promotion time of the current ParishSoft data
    the head emails were read from when the capture's own source had been
    compacted; it becomes the "Head emails as of" report detail.
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
    rows, unaddressed = [], 0
    if postal:
        headings = POSTAL_HEADINGS
        for item in payload["rows"]:
            address = item["address"]
            heads = head_names(item["heads"])
            if item.get("mailable", mailable(address)):
                lines = [
                    _clean(address.get(f"primaryAddress{index}")) for index in (1, 2, 3)
                ]
                lines = [line for line in lines if line]
                lines += [""] * (3 - len(lines))
                # Addressee, Address line 1-3, City, State, ZIP.
                mailing = (
                    heads or item["family_name"],
                    *lines,
                    _clean(address.get("primaryCity")),
                    _clean(address.get("primaryState")),
                    _zip(address),
                )
            else:
                # The row stays so the file matches the page, but a partial
                # address would print an undeliverable envelope, so the
                # Addressee and every address column are blank.
                unaddressed += 1
                mailing = ("",) * 7
            rows.append(
                (
                    str(item["family_duid"]),
                    item["family_name"],
                    mailing[0],
                    heads,
                    *mailing[1:],
                    item["code"] or "",
                    head_emails_text(item["heads"]),
                )
            )
    else:
        headings = export_headings(
            postal=False, reach=parameters["filters"].get("reach")
        )
        # Only the "neither" list adds the phone column; see export_headings.
        phones = PHONE_HEADING in headings
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
            rows.append(row + (head_emails_text(item["heads"]),))
    source = payload["metadata"]
    metadata = (
        ("Report", title),
        ("Parish", parish_name),
        ("Campaign", source["name"]),
        ("Captured at", instant(captured_at)),
        *(
            ((HEAD_EMAILS_DETAIL, instant(head_emails_as_of)),)
            if head_emails_as_of
            else ()
        ),
        ("Families in this file", f"{len(rows):,}"),
        *(((UNADDRESSED_DETAIL, f"{unaddressed:,}"),) if postal else ()),
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
        unaddressed=unaddressed,
    )
