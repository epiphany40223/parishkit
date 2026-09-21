"""Detached complete financial values shared by CSV, XLSX and paginated PDF.

The document is built from the read model's shaped projection, so an export
cell says exactly what the page's cell says: exact money as text, unavailable
source totals as the word, never a zero, and share wording versioned with the
configuration each Family answered under.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

HEADINGS = (
    "Family",
    "Family DUID",
    "Family status",
    "Annual pledge",
    "Frequency",
    "Approximate installment",
    "Share methods",
    "Source pledged",
    "Source contributed",
    "Latest response",
    "First response",
    "Family version",
    "Response reference",
)
STATUS = {True: "Active", False: "Inactive", None: "Status unavailable"}
UNPROVEN = (
    "Unavailable: the latest giving read is not proven complete for the "
    "comparison period. Unavailable does not mean zero."
)
# Below the spreadsheet cell maximum of 32,767 characters, which openpyxl would
# otherwise truncate silently. A configuration may offer a hundred share options
# and each Other text may run to 2,000 characters, so one Family's wording can
# exceed a cell; it then continues in further rows for the same Family.
CELL_LIMIT = 32_000


@dataclass(frozen=True, repr=False)
class FinancialDocument:
    """All captured values, with no live queries, clocks or mutable nested rows."""

    metadata: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    headings: ClassVar[tuple[str, ...]] = HEADINGS
    title: ClassVar[str] = "Financial stewardship detail"
    sheet_name: ClassVar[str] = "Financial detail"


def _share_cells(row):
    """Each chosen method's wording, with any Other text, in cells that fit.

    Whole entries move to the next cell, so no wording is cut mid-word and a
    reader can concatenate the cells in order to recover every character.
    """
    entries = [
        f"{share['label']}: {share['text']}" if share["text"] else share["label"]
        for share in row["shares"]
    ]
    if not entries:
        return ["None chosen"]
    cells, current = [], entries[0]
    for entry in entries[1:]:
        if len(current) + len(entry) + 2 > CELL_LIMIT:
            cells.append(current)
            current = entry
        else:
            current = f"{current}; {entry}"
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
        return parsed.astimezone(zone).isoformat(timespec="seconds")

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
        # A Family-only refresh keeps an older giving read, so the money's own
        # observation time is stated apart from the source promotion time.
        (
            "Source giving read as of",
            instant(source["giving_observed_at"]) or "Unavailable",
        ),
        ("Captured at", instant(source["observed_at"])),
        ("Requested at", instant(requested_at)),
        ("Display timezone", timezone),
        ("Campaign date-filter timezone", source["timezone"]),
        (
            "Source comparison period",
            f"{source['comparison_start']} through {source['comparison_end']}",
        ),
        (
            "Source contributions through",
            through.isoformat() if isinstance(through, date) else UNPROVEN,
        ),
        ("Matching Families", f"{result['total']:,}"),
        ("Total annual pledges", summary["annual_total"].display),
        ("Pledges by frequency", _counts(summary["frequencies"])),
        ("Pledges by share method", _counts(summary["shares"])),
        ("No share method chosen", f"{summary['no_share']:,}"),
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
                row["annual"].display,
                str(row["frequency_label"]),
                row["installment"].display if row["installment"].available else "",
                first,
                row["source_pledge"].display,
                row["source_contributions"].display,
                instant(row["submitted_at"]),
                instant(row["first_submitted_at"]),
                f"{row['family_version']:,}",
                row["id"],
            )
        )
        # A continuation row names the Family and the response it continues
        # and carries nothing else, so no amount is ever counted twice.
        for cell in overflow:
            rows.append(
                (row["family_name"], str(row["family_duid"]), "Continued")
                + ("",) * 3
                + (f"(continued) {cell}",)
                + ("",) * 5
                + (row["id"],)
            )
    return FinancialDocument(metadata, tuple(rows), result["total"], requested_at)
