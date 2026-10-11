"""Read one campaign archive workbook in a short-lived helper process (#1018).

``archive.check`` starts this module with ``python -I -m`` and an empty
environment, writes the workbook's bytes to its stdin and reads one JSON
document from its stdout, killing it at the time limit. The workbook is
untrusted input, so it is parsed here, under a 1 GiB address-space limit,
never in a web or worker process: a hostile file can at worst exhaust this
process. No exception text leaves it: stderr is discarded, a refusal is a
document of fixed problem texts, and any other failure is an exit status.

Exit statuses: 0 with a document; 3 when the memory limit was reached
("too large to check"); 1 for anything else.
"""

import sys
from decimal import Decimal

# The address-space limit of the helper (Linux; other systems run it
# unlimited, which only development machines do).
MEMORY_BYTES = 1024 * 1024 * 1024
TOO_LARGE = 3


def limit_memory():
    """Apply MEMORY_BYTES as this process's address-space limit on Linux."""
    if not sys.platform.startswith("linux"):
        return
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))


def document(data):
    """The JSON-ready result for one workbook's bytes: its rows or problems."""
    from .workbook import (
        BAND_SET_VERSION,
        ArchiveRefused,
        flagged_notes,
        pledge_distribution,
        pledged_amounts,
        quartiles,
        read_archive,
    )

    try:
        archive = read_archive(data)
    except ArchiveRefused as refused:
        return {
            "valid": False,
            "problems": [problem.as_dict() for problem in refused.problems],
            "more_problems": refused.more,
        }
    pledges = pledged_amounts(archive)
    metadata = {
        key: value if isinstance(value, str | int | bool | None) else str(value)
        for key, value in archive.metadata.items()
    }
    return {
        "valid": True,
        "minor_version": archive.minor_version,
        "metadata": metadata,
        "notes": archive.notes,
        "other_counts": archive.other_counts,
        "flagged_notes": flagged_notes(archive),
        "family_id_systems": list(archive.family_id_systems),
        "families": len(archive.families),
        "submission_rows": archive.submission_rows,
        "unattributed_rows": archive.unattributed_rows,
        "ministry_rows": archive.ministry_rows,
        "pledging_families": len(pledges),
        "annual_pledged": str(sum(pledges, Decimal("0.00"))),
        "daily": [
            {
                key: str(value)
                if key in {"local_date", "cumulative_annual_pledged"}
                else value
                for key, value in row.items()
                if key not in {"_row", "source"}
            }
            for row in archive.daily
        ],
        # The only Family fields the renewal figure uses (spec, "What is
        # stored"); envelope-only Families cannot be matched across years.
        "renewal_families": [
            [f["family_id_system"], f["family_id"], f["first_campaign_day"]]
            for f in archive.families
            if f["family_id"] is not None
        ],
        "band_set_version": BAND_SET_VERSION,
        "bands": [
            {**band, "total": str(band["total"])}
            for band in pledge_distribution(pledges)
        ],
        "quartiles": [str(q) for q in quartiles(pledges) or ()] or None,
    }


def main():
    """Read the workbook from stdin, write its document to stdout."""
    try:
        limit_memory()
    except Exception:
        return 1
    import json

    from .package import MAX_FILE_BYTES

    data = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    try:
        result = document(data)
        output = json.dumps(result, separators=(",", ":")).encode()
    except MemoryError:
        return TOO_LARGE
    except Exception:
        return 1
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
