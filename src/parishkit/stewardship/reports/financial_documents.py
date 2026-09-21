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


def _shares(row):
    """One cell: each chosen method's wording, with any Other text after it."""
    return (
        "; ".join(
            f"{share['label']}: {share['text']}" if share["text"] else share["label"]
            for share in row["shares"]
        )
        or "None chosen"
    )


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
        *(
            (f"Frequency: {label}", f"{count:,}")
            for label, count in summary["frequencies"]
        ),
        *(
            (f"Share method: {label}", f"{count:,}")
            for label, count in summary["shares"]
        ),
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
    rows = tuple(
        (
            row["family_name"],
            str(row["family_duid"]),
            STATUS[row["active"]],
            row["annual"].display,
            str(row["frequency_label"]),
            row["installment"].display if row["installment"].available else "",
            _shares(row),
            row["source_pledge"].display,
            row["source_contributions"].display,
            instant(row["submitted_at"]),
            instant(row["first_submitted_at"]),
            f"{row['family_version']:,}",
            row["id"],
        )
        for row in result["rows"]
    )
    return FinancialDocument(metadata, rows, result["total"], requested_at)
