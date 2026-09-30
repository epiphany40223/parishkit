"""Rows-per-page and carried filters for the private POST report tables.

The Family directory, Financial stewardship detail and Additional
information reports keep their filters in CSRF POST bodies and page through
an installed SQL selection that also owns their sort order (see
``web.tables.report_table``). The page size is parsed here, apart from each
report's own query object, so it never enters the selection's closed filter
JSON, a persisted export selection or audit context.
"""

from dataclasses import replace


def pop_page_size(parameters, sizes, *, default=50):
    """Remove and return the rows-per-page choice from a mutable POST QueryDict.

    Only sizes the report's SQL selection accepts are offered and accepted.
    """
    values = parameters.pop("size", [str(default)])
    if (
        len(values) != 1
        or not (values[0].isascii() and values[0].isdecimal())
        or str(int(values[0])) != values[0]
        or int(values[0]) not in sizes
    ):
        raise ValueError("Unsupported report page size.")
    return int(values[0])


def carried_filters(query, *extra):
    """The private filters every navigator and heading form carries as hidden
    fields; page, size and sort come from the table itself."""
    return [
        *((key, value) for key, value in query.form_values().items() if key != "sort"),
        *extra,
    ]


def last_page(total, size):
    """The last page holding a matching row (1 for an empty result)."""
    return max(1, -(-total // size))


def clamp_query(query, total, size):
    """The query moved back to the last page when it asked for one past it.

    A typed page number or a stale Next click after the result shrank would
    otherwise show an empty page; like every other Admin table, a page past
    the end shows the last page instead; an empty result's last page is 1.
    Returns None when no move is needed.
    """
    last = last_page(total, size)
    return replace(query, page=last) if query.page > last else None
