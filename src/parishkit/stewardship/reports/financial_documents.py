"""Detached complete financial values shared by CSV, XLSX and paginated PDF.

The document is built from the read model's shaped projection, so an export
cell says exactly what the page's cell says: exact money, unavailable source
totals as the word, never a zero, and share wording versioned with the
configuration each Family answered under. Money cells stay ``MoneyAmount``
values, so CSV and PDF write the page's text and XLSX writes a summable number
(see ``information_rendering.xlsx_cell``).
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

from parishkit.stewardship.source.snapshot_names import FAMILY_NAMES_DETAIL
from parishkit.stewardship.web import dates

from .money import MoneyAmount

# The two comparison columns use the page's own labels (#404), so a file and
# the page name the same figures alike; ``financial_document`` adds the
# comparison period's years to them, as the page's headings show
# ("ParishSoft pledged (2026)").
HEADINGS = (
    "Family",
    "Family DUID",
    "Family status",
    "Annual pledge",
    "Frequency",
    "Approximate installment",
    "Share methods",
    "ParishSoft pledged",
    "ParishSoft contributed",
    "Latest response",
    "First response",
    "Family version",
    "Response reference",
)
STATUS = {True: "Active", False: "Inactive", None: "Status unavailable"}
UNPROVEN = (
    "Unavailable: the latest giving data read from ParishSoft is not confirmed "
    "complete for this campaign's comparison period. Unavailable does not mean "
    "zero."
)
# Below the spreadsheet cell maximum of 32,767 characters, which openpyxl would
# otherwise truncate silently, measured on the text the spreadsheet writer
# really stores: it doubles every backslash and spells a character XML cannot
# carry as a six-character escape, and an Other text may be nothing but those.
# A configuration may offer a hundred share options and each Other text may
# run to 2,000 characters, so one Family's wording can exceed a cell; it then
# continues in further rows for the same Family.
CELL_LIMIT = 32_000
CONTINUED = "(continued) "


@dataclass(frozen=True, repr=False)
class FinancialDocument:
    """All captured values, with no live queries, clocks or mutable nested rows."""

    metadata: tuple[tuple[str, object], ...]
    rows: tuple[tuple[str | MoneyAmount | datetime, ...], ...]
    item_count: int
    requested_at: datetime
    headings: tuple[str, ...] = HEADINGS
    title: ClassVar[str] = "Financial stewardship detail"
    sheet_name: ClassVar[str] = "Financial detail"


def period_years(start, end):
    """The years an ISO date period covers: "2026", or "2026–2027".

    Words the ParishSoft comparison period in the table headings, on the page
    and in the downloads, as the Family form does; "" when the campaign has
    no such period.
    """
    if not start or not end:
        return ""
    first, last = start[:4], end[:4]
    return first if first == last else f"{first}–{last}"


def headings(comparison_start, comparison_end):
    """The column headings, the two ParishSoft ones with the period's years."""
    years = period_years(comparison_start, comparison_end)
    if not years:
        return HEADINGS
    return tuple(
        f"{heading} ({years})" if heading.startswith("ParishSoft ") else heading
        for heading in HEADINGS
    )


def _share_cells(row):
    """Each chosen method's wording, with any Other text, in cells that fit.

    Whole entries move to the next cell, so no wording is cut mid-word and a
    reader can concatenate the cells in order to recover every character.
    """
    # Imported here: the renderer brings its PDF toolkit with it, which the web
    # process that queues an export never needs.
    from .information_rendering import visible_text

    entries = [
        f"{share['label']}: {share['text']}" if share["text"] else share["label"]
        for share in row["shares"]
    ]
    if not entries:
        return ["None chosen"]
    cells, current = [], entries[0]
    stored = len(visible_text(current)) + len(CONTINUED)
    for entry in entries[1:]:
        length = len(visible_text(entry)) + 2
        if stored + length > CELL_LIMIT:
            cells.append(current)
            current, stored = entry, len(visible_text(entry)) + len(CONTINUED)
        else:
            current, stored = f"{current}; {entry}", stored + length
    cells.append(current)
    return cells


def _counts(pairs):
    """One wrapped value per line, so a long label never becomes a metadata key."""
    return "\n".join(f"{label}: {count:,}" for label, count in pairs)


def financial_document(result, parameters, *, parish_name, requested_at, timezone):
    """Localize instants and carry the whole-result summary as report metadata."""
    zone = ZoneInfo(timezone)

    def instant(value):
        """Never interpret an unzoned stored date/time as a local instant."""
        if not value:
            return ""
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Financial report timestamps must be aware.")
        return parsed.astimezone(zone)

    if result["total"] != len(result["rows"]):
        raise ValueError("Financial export requires every matching Family.")
    source, summary = result["metadata"], result["summary"]
    through = source["giving_through"]
    metadata = (
        ("Report", FinancialDocument.title),
        ("Parish", parish_name),
        ("Campaign", source["name"]),
        ("Campaign reference", source["id"]),
        ("Source reference", source["source_id"]),
        ("Source generation", f"{source['source_generation']:,}"),
        ("Source as of", instant(source["source_as_of"])),
        (FAMILY_NAMES_DETAIL, source["family_names"]),
        # A Family-only refresh keeps an older giving read, so the money's own
        # observation time is stated apart from the source promotion time.
        (
            "ParishSoft giving read as of",
            instant(source["giving_observed_at"]) or "Unavailable",
        ),
        ("Captured at", instant(source["observed_at"])),
        ("Requested at", instant(requested_at)),
        ("Display timezone", timezone),
        ("Campaign date-filter timezone", source["timezone"]),
        (
            "ParishSoft comparison period",
            dates.Span(
                date.fromisoformat(source["comparison_start"]),
                date.fromisoformat(source["comparison_end"]),
            ),
        ),
        (
            "ParishSoft contributions through",
            through if isinstance(through, date) else UNPROVEN,
        ),
        ("Matching Families", f"{result['total']:,}"),
        ("Total annual pledges", summary["annual_total"]),
        ("Pledges by frequency", _counts(summary["frequencies"])),
        ("Pledges by share method", _counts(summary["shares"])),
        ("No share method chosen", f"{summary['no_share']:,}"),
        ("Cannot contribute financially", f"{summary.get('cannot_give', 0):,}"),
        (
            "Filters and sort",
            json.dumps(parameters["filters"], ensure_ascii=False, sort_keys=True),
        ),
        (
            "Privacy",
            "Sensitive parish financial information. Share only with authorized "
            "recipients.",
        ),
        (
            "Money",
            "Amounts are exact as pledged or recorded. Unavailable never means zero.",
        ),
    )
    rows = []
    for row in result["rows"]:
        first, *overflow = _share_cells(row)
        rows.append(
            (
                row["family_name"],
                str(row["family_duid"]),
                STATUS[row["active"]],
                row["annual"],
                row["frequency_label"],
                # No installment (no pledge or no frequency) stays blank.
                row["installment"] if row["installment"].available else "",
                first,
                row["source_pledge"],
                row["source_contributions"],
                instant(row["submitted_at"]),
                instant(row["first_submitted_at"]),
                f"{row['family_version']:,}",
                row["id"],
            )
        )
        # A continuation row names the Family and the response it continues
        # and carries nothing else, so no amount is ever counted twice and a
        # filter on any other column never sees a second value for the Family.
        for cell in overflow:
            rows.append(
                (row["family_name"], str(row["family_duid"]))
                + ("",) * 4
                + (CONTINUED + cell,)
                + ("",) * 5
                + (row["id"],)
            )
    return FinancialDocument(
        metadata,
        tuple(rows),
        result["total"],
        requested_at,
        headings(source["comparison_start"], source["comparison_end"]),
    )
