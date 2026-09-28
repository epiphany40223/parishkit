"""Shared paging for Admin list tables: one parser, one model, one navigator.

Every paged Admin table takes the same two query parameters, optionally
prefixed so several tables can page independently on one page: ``page`` (a
1-based page number) and ``size`` (rows per page, one of ``PAGE_SIZES`` or
``all``). The template include ``stewardship/table-navigator.html`` renders
the matching controls above and below a table, and ``ui-v1.js`` adds row
selection for tables that offer bulk actions (see the admin-portal spec).

Only list pages whose query strings carry no private values use this helper;
report views that keep their filters in POST state keep their own paging.

Two kinds of page share the model and the navigator. ``paginate`` slices an
in-memory list and knows the total. ``window_table`` wraps a database page
read through ``web.contracts.PageWindow``, which fetches one sentinel row to
learn whether a next page exists instead of counting every matching row; its
navigator therefore shows no total or page count, and offers only the sizes a
PageWindow accepts.
"""

from dataclasses import dataclass
from math import ceil
from urllib.parse import urlencode

PAGE_SIZES = (25, 50, 100, 250)
# PageWindow caps a database page at 100 rows, so windowed tables offer fewer.
WINDOW_SIZES = (25, 50, 100)
ALL = "all"


@dataclass(frozen=True)
class TablePage:
    """One page of rows plus what the navigator needs to link to other pages."""

    rows: list
    number: int
    pages: int | None
    count: int | None
    size: int | None
    prefix: str
    carried: tuple
    # Only windowed tables set this: they know whether a next page exists
    # without knowing how many pages there are.
    has_next: bool | None = None
    sizes: tuple = PAGE_SIZES
    allow_all: bool = True

    @property
    def page_name(self):
        """The query parameter holding this table's page number."""
        return f"{self.prefix}page"

    @property
    def size_name(self):
        """The query parameter holding this table's rows-per-page choice."""
        return f"{self.prefix}size"

    @property
    def size_value(self):
        """The current rows-per-page choice as it appears in a URL."""
        return ALL if self.size is None else str(self.size)

    @property
    def first_index(self):
        """1-based position of the first row shown, or 0 for an empty table."""
        if not self.rows:
            return 0
        return 1 if self.size is None else (self.number - 1) * self.size + 1

    @property
    def last_index(self):
        """1-based position of the last row shown."""
        return self.first_index + len(self.rows) - 1 if self.rows else 0

    @property
    def size_choices(self):
        """(value, label, selected) for every rows-per-page option.

        A size outside the standard choices (for example one typed into an
        older bookmarked URL that the page still accepts) is listed too, so
        the control always shows the size actually in use.
        """
        sizes = sorted({*self.sizes, *([self.size] if self.size else [])})
        choices = [(str(size), str(size), size == self.size) for size in sizes]
        if self.allow_all:
            choices.append((ALL, "All", self.size is None))
        return choices

    def query(self, number):
        """Query string for another page, keeping filters and the page size."""
        return urlencode(
            [
                *self.carried,
                (self.size_name, self.size_value),
                (self.page_name, str(number)),
            ]
        )

    @property
    def previous_query(self):
        """Query string for the previous page, or None on the first page."""
        return self.query(self.number - 1) if self.number > 1 else None

    @property
    def next_query(self):
        """Query string for the next page, or None on the last page."""
        more = self.has_next if self.pages is None else self.number < self.pages
        return self.query(self.number + 1) if more else None


def paginate(rows, parameters, *, prefix="", default=50, carry=()):
    """Slice ``rows`` by the page/size values in ``parameters``.

    ``parameters`` is an already-validated mapping of single values (see
    ``web.contracts.filters``). A malformed number or unknown size is refused
    with ValueError; a page past the end shows the last page instead, so a
    bookmarked or typed page number never lands on an empty error.
    ``carry`` lists (name, value) pairs, such as search filters, that every
    navigator link must keep.
    """
    rows = list(rows)
    size_text = parameters.get(f"{prefix}size", str(default))
    if size_text == ALL:
        size = None
    elif size_text.isascii() and size_text.isdecimal() and int(size_text) in PAGE_SIZES:
        size = int(size_text)
    else:
        raise ValueError("Unsupported table page size.")
    page_text = parameters.get(f"{prefix}page", "1")
    if not (page_text.isascii() and page_text.isdecimal()) or len(page_text) > 9:
        raise ValueError("Invalid table page number.")
    pages = 1 if size is None else max(1, ceil(len(rows) / size))
    number = min(max(1, int(page_text)), pages)
    shown = rows if size is None else rows[(number - 1) * size : number * size]
    return TablePage(
        rows=shown,
        number=number,
        pages=pages,
        count=len(rows),
        size=size,
        prefix=prefix,
        carried=tuple((name, value) for name, value in carry if value),
    )


def window_table(window, rows, has_next, *, prefix="", carry=()):
    """Describe one PageWindow page for the shared navigator.

    ``window`` is the PageWindow the view already used to read ``rows`` and
    ``has_next`` (see ``PageWindow.rows``); the view keeps its own parsing and
    bounds, so JSON endpoints sharing that parser are unchanged. The total is
    unknown by design, so ``count`` and ``pages`` are None.
    """
    return TablePage(
        rows=list(rows),
        number=window.page,
        pages=None,
        count=None,
        size=window.size,
        prefix=prefix,
        carried=tuple((name, value) for name, value in carry if value),
        has_next=bool(has_next),
        sizes=WINDOW_SIZES,
        allow_all=False,
    )


def table_parameters(prefix=""):
    """The query parameter names ``paginate`` reads for one table."""
    return {f"{prefix}page", f"{prefix}size"}
