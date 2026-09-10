"""Safe export cells and filenames; authorization belongs to the report owner."""

import re
import unicodedata
from urllib.parse import quote


def csv_cell(value):
    """Neutralize spreadsheet formulas, including control/whitespace prefixes."""
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
