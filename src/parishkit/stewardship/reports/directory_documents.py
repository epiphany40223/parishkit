"""Detached directory export documents: a code list or a postal mail merge.

Each export is one header row plus one row per Family, so a spreadsheet or
word processor can use it directly. Report details (parish, campaign, capture
time, filters and the privacy note) are not columns: they go in the PDF header
and footer and in the XLSX "Report information" sheet.

- The Family-code directory lists, as the page does (#932, #933), Family
  first (the surname, then the heads of household: "Squyres, Tracy and
  Jeff"), then Family DUID, Envelope number, Family code, and any
  Submissions count, What to check and response dates. When it is filtered
  to Families that no campaign mail can reach (reach "neither"), it adds
  their phone numbers for follow-up calls.
- Every export ends with Family head emails: each distinct head address once,
  with the heads who have it ("Anna Example and Ben Example: a@x; Cara
  Example: (no email)"), read when the file is rendered (#604): from the
  captured source, or from the current ParishSoft data once that has been
  compacted, which the report details then say ("Head emails as of").
- The postal export is a mail merge for envelope labels and cover letters. It
  holds exactly the filtered Families, like the page: since #951 only active
  Families with no deliverable head email and a usable mailing address
  (reach ``mail``). A capture made before that may list a Family without a
  usable mailing address: it keeps its row with the Addressee and address
  columns blank, and the report details count those rows. Existing columns
  never change name or order (Family head emails was added at the end), so
  existing mail-merge templates keep working.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from parishkit.stewardship.source.family_names import (
    heads_salutation_name,
)
from parishkit.stewardship.web.presentation import phone as format_phone

from .directories import (
    CHECKS,
    INACTIVE,
    MISSING_NAME,
    REACH,
    REASONS,
    RESPONSES,
    add_responses,
    head_emails_text,
    response_columns,
    row_name,
)

# The Family-code file's identity columns, named as the page names them and
# leading the row: the most relevant column first, and dates lead only event
# or log tables (#932). Submissions, What to check and the response dates
# follow them when the page shows those.
IDENTITY_HEADINGS = ("Family", "Family DUID", "Envelope number")
CODE_HEADING = "Family code"
# The code list's columns with no response columns, data check or phones.
CODE_HEADINGS = (*IDENTITY_HEADINGS, CODE_HEADING)
CHECK_HEADING = "What to check"
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
    # Captures made before #933 hold the older yes and no.
    "response": (
        "Response",
        RESPONSES | {"yes": "Responded", "no": "Not yet responded"},
    ),
    "reason": ("Email availability", REASONS),
    "reach": ("Campaign mail can reach", REACH),
    "check": ("ParishSoft data to check", CHECKS),
}
COUNTED_DETAIL = "Responses counted at"


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


def export_headings(*, postal, reach, dates=(), counts=(), checks=False):
    """The export's columns: the mail merge, or codes (plus phones for "neither").

    The mail merge's columns never change. The code list follows the page:
    the identity columns and the Family code (always in the same place),
    then the response ``counts``, What to check when ``checks`` (a data
    check filter is applied) and the response ``dates``
    (``directories.response_columns``, when the response columns are on).
    Family head emails is always the last column, after any phones.

    The directory page lists them so the Admin knows what the file contains.
    """
    if postal:
        return POSTAL_HEADINGS
    phones = (PHONE_HEADING,) if reach == "neither" else ()
    return (
        IDENTITY_HEADINGS
        + (CODE_HEADING,)
        + tuple(column.heading for column in counts)
        + ((CHECK_HEADING,) if checks else ())
        + tuple(column.heading for column in dates)
        + phones
        + (EMAIL_HEADING,)
    )


def file_columns(parameters):
    """The response (dates, counts) and whether What to check is in the file.

    Read from the captured selection's parameters; captures made before #933
    have neither choice, so their files have neither.
    """
    filters = parameters["filters"]
    if parameters.get("responses"):
        dates, counts = response_columns(filters["response"])
    else:
        dates, counts = (), ()
    return dates, counts, filters.get("check", "any") != "any"


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
    if parameters.get("responses"):
        applied.append("Response columns included")
    return "; ".join(applied) or "None"


def _family_cell(item):
    """The code list's Family cell: the page's name, marked when inactive.

    A Family the Response filter lists after the current ParishSoft data
    stopped listing it as active says so, as the page does (#933).
    """
    name = row_name(item)
    return name if item.get("active", True) else f"{name} ({INACTIVE})"


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
                    item["family_name"] or MISSING_NAME,
                    mailing[0],
                    heads,
                    *mailing[1:],
                    item["code"] or "",
                    head_emails_text(item["heads"]),
                )
            )
    else:
        dates, counts, checks = file_columns(parameters)
        headings = export_headings(
            postal=False,
            reach=parameters["filters"].get("reach"),
            dates=dates,
            counts=counts,
            checks=checks,
        )
        # Only the "neither" list adds the phone column; see export_headings.
        phones = PHONE_HEADING in headings
        add_responses(payload["rows"])
        for item in payload["rows"]:
            row = (
                _family_cell(item),
                str(item["family_duid"]),
                # Envelope number 0 is a value, not a blank (#933).
                "" if item.get("envelope") is None else str(item["envelope"]).strip(),
                item["code"] or "",
                *(item[column.field] for column in counts),
                *(("; ".join(item["checks"]),) if checks else ()),
                *(
                    instant(item[column.field]) if item[column.field] else None
                    for column in dates
                ),
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
        *(
            ((COUNTED_DETAIL, instant(source["counted_at"])),)
            if source.get("counted_at")
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
