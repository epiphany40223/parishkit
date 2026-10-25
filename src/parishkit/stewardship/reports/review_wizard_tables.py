"""Review these one at a time: the review tables' way into the wizard (#2123).

Review pipeline spec, "Wizard entry points". Each Review work table
(``components/review-worklist.html``) draws one **Review these one at a
time** button in its selection bar. It posts the table's current view to
the wizard's start (``review_wizard_views.start``), which starts a run with
it through the start page's own parsing, access check and read, so the
table and the run never disagree on what the filters mean.

This module only works out what the button posts and whether it is
available; it reads nothing. The button never asks about unfinished runs
(R52 Part W): a start replaces the reader's earlier open runs.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.pipeline import Stream

from .review_streams import BY_KEY

# Each table's pipeline stream (``Worklist.stream``), as the wizard names it.
TABLE_STREAMS = {
    Stream.CENSUS: "census",
    Stream.MINISTRY: "ministry",
    Stream.FINANCIAL: "pledges",
    Stream.ADDITIONAL_INFORMATION: "information",
    Stream.CANNOT_PARTICIPATE: "limitations",
}
# The button's reason while its table lists nothing.
EMPTY = _("Nothing is listed to review one at a time.")


@dataclass(frozen=True)
class TableEntry:
    """What the table's button draws.

    ``fields`` are the ``(name, value)`` pairs its form posts; ``reason``
    says why it is unavailable (an empty table), else None.
    """

    prefix: str
    fields: list
    reason: str | None = None


def table_sort(table):
    """The wizard's sort for a table's: Submitted ascending is Oldest
    submitted first, Submitted descending Newest submitted first, and any
    other column (or none) Last name (A–Z) (the spec's sort mapping)."""
    tokens = table.sorting.tokens if table.sorting else {}
    column, descending = tokens.get(table.sort, (None, False))
    if column != "submitted":
        return "name"
    return "newest" if descending else "oldest"


def table_fields(stream, worklist, table):
    """The table's view as the wizard's start takes it.

    The table's own view fields (``Worklist.fields``: its Stage and the
    stream's filters, the search included) narrowed to the ones the start
    reads for this stream in Advanced mode, plus the mapped sort. Empty
    values (a filter left unchosen) are left out.
    """
    names = {"stage", "search", *(name for name, _default in stream.closed)}
    return [
        ("stream", stream.key),
        ("mode", "advanced"),
        ("sort", table_sort(table)),
        ("table", "1"),
        *(
            (name, str(value))
            for name, value in worklist.fields.items()
            if name in names and value not in (None, "")
        ),
    ]


def table_entry(worklist, table):
    """The table's button, or None for a table that has none.

    Unavailable, with its reason, while the table lists nothing.
    """
    key = TABLE_STREAMS.get(worklist.stream)
    if key is None:
        return None
    stream = BY_KEY[key]
    fields = table_fields(stream, worklist, table)
    if not table.rows:
        return TableEntry(worklist.prefix, fields, reason=str(EMPTY))
    return TableEntry(worklist.prefix, fields)
