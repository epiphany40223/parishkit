"""Safe export cells and filenames; authorization belongs to the report owner."""

import re
import unicodedata
from urllib.parse import quote

from . import dates

# The format every export form preselects, and every page download and
# pk-admin command takes when none is given (#1190): the Administrator's one
# rule is XLSX. CSV stays offered wherever it was. pk-admin spells it out
# (admin_cli.DEFAULT_EXPORT_FORMAT, as its parser is built before Django is
# set up); a test keeps the two equal.
DEFAULT_EXPORT_FORMAT = "xlsx"

# Every synchronous page download: a view that writes a CSV, XLSX or PDF
# straight from its page instead of through the export worker. The name is
# the download's case in the one-column-set guard (#966,
# tests/stewardship/test_export_columns.py), which fails until every
# registered name is checked there or listed as a documented exception.
PAGE_DOWNLOADS = {}


def page_download(name):
    """Register a page-download view under ``name`` and mark it.

    The ``page_download`` attribute lets the guard find the view among the
    URL patterns, so a route to an unregistered download view is caught too.
    """

    def register(view):
        """Record ``view`` once under ``name``."""
        if PAGE_DOWNLOADS.setdefault(name, view) is not view:
            raise ValueError(f"Page download {name!r} is already registered.")
        view.page_download = name
        return view

    return register


def csv_cell(value):
    """Neutralize spreadsheet formulas, including control/whitespace prefixes.

    Dates and display-zone timestamps become ISO 8601 text (see dates.csv_text).
    """
    value = dates.csv_text(value)
    text = "" if value is None else str(value)
    start = 0
    while start < len(text) and (
        text[start].isspace() or unicodedata.category(text[start])[0] in {"C", "Z"}
    ):
        start += 1
    if text[start:].startswith(("=", "+", "-", "@")) or text.startswith(
        ("\t", "\r", "\n")
    ):
        return "'" + text
    return text


def download_headers(filename, *, content_type):
    """Never reflect path separators/control characters into response headers."""
    if (
        type(filename) is not str
        or not filename
        or len(filename) > 180
        or re.search(r"[\x00-\x1f\x7f/\\]", filename)
        or any(unicodedata.category(char).startswith("C") for char in filename)
        or filename in {".", ".."}
    ):
        raise ValueError("Invalid download filename.")
    if content_type not in {
        "text/csv",
        "application/pdf",
        "image/png",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/x-ndjson",
        "text/plain",
    }:
        raise ValueError("Unsupported download content type.")
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return {
        "Content-Type": content_type,
        "Content-Disposition": (
            f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        ),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
