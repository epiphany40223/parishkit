"""Shared paging and sorting for Admin tables: one parser, one model, one navigator.

Every Admin table takes the same query parameters, optionally prefixed so
several tables can page independently on one page: ``page`` (a 1-based page
number), ``size`` (rows per page, one of ``PAGE_SIZES`` or ``all``) and
``sort`` (one of the table's whitelisted sort tokens, see ``Sorting``). The
template include ``stewardship/table-navigator.html`` renders the matching
controls above and below a table, the ``sort_heading`` template tag renders
each sortable column heading, and ``ui-v1.js`` adds row selection for tables
that offer bulk actions and re-sorts or re-pages a table in place, without a
full page load, when its template wraps it in a ``data-table-region`` element
with the table's ``anchor`` id (see the admin-portal spec).

A table whose filters are public travels in the query string (``method``
"get"): navigator and heading controls are plain links and a GET form. A
report whose filters are private keeps them in CSRF-protected POST bodies
(``method`` "post"): the same controls are small POST forms that carry the
filters as hidden fields, so no private value ever reaches a URL.

Three kinds of page share the model and the navigator. ``paginate`` slices
an in-memory list and knows the total. ``window_table`` wraps a database page
read through ``web.contracts.PageWindow``, which fetches one sentinel row to
learn whether a next page exists, plus a ``bounded_count`` that stops
counting at ``COUNT_LIMIT`` so a huge table never costs a full count per
view. ``report_table`` describes a page an installed SQL selection already
cut, with the total that selection returned.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from math import ceil
from urllib.parse import urlencode

from django.db.models import F

PAGE_SIZES = (25, 50, 100, 250)
# PageWindow caps a database page at 100 rows, so windowed tables offer fewer.
WINDOW_SIZES = (25, 50, 100)
ALL = "all"
# A windowed table counts at most this many matching rows. Beyond it the
# navigator says "more than" and pages on with its sentinel row instead, so
# a page view never counts a whole growing log.
COUNT_LIMIT = 10_000


@dataclass(frozen=True)
class Fixed:
    """An ordering term whose direction never follows the chosen one.

    A column's later terms can order the rows its first terms tie, such as
    each Family's emails newest first under a Family sort (#934), whichever
    way the Family itself sorts. ``term`` is a field name, optionally
    "-"-prefixed, used exactly as given.
    """

    term: str


def _direction(term, descending, *, nulls_last=False):
    """One ORM ordering term, flipped when the chosen direction is descending.

    ``term`` is a field name (optionally already "-"-prefixed) or an ORM
    expression such as ``Lower("name")``; both are server-owned, never text
    from the request. PostgreSQL puts NULLs first in a descending order, so
    a column term (``nulls_last``) asks for them last: a first click on
    "newest heartbeat first" must not open on every task without one. The
    unique, non-null tiebreak keeps its plain form, which an index can serve.
    A ``Fixed`` term keeps its own direction.
    """
    if isinstance(term, Fixed):
        return term.term
    if isinstance(term, str):
        name = term[1:] if term.startswith("-") else term
        down = descending != term.startswith("-")
        if not (down and nulls_last):
            return f"-{name}" if down else name
        return F(name).desc(nulls_last=True)
    if descending:
        return term.desc(nulls_last=True) if nulls_last else term.desc()
    return term.asc()


def _present_first(rows, key, descending):
    """Sort rows by ``key``, always listing rows whose value is missing last.

    Present and missing values are sorted apart, so a descending sort (times,
    counts) never opens on the empty rows. Python's sort is stable in both
    directions, so equal values keep their source order.
    """
    present = [row for row in rows if key(row) is not None]
    missing = [row for row in rows if key(row) is None]
    return sorted(present, key=key, reverse=descending) + missing


@dataclass(frozen=True)
class Sorting:
    """The sort orders one table offers, keyed by the token a URL or form carries.

    ``tokens`` maps every accepted token to ``(column, descending)``, listing
    each column's first-click token first. Only these tokens are accepted, so
    a request can never name a database column or expression. ``orders`` maps
    a column to server-owned ordering: a tuple of ORM terms for a queryset, or
    a key function for in-memory rows. A report whose installed SQL selection
    orders its own rows leaves ``orders`` empty and passes the token on.
    ``tiebreak`` holds unique ORM fields appended to every database order,
    in the chosen direction, so rows with equal values never swap places
    between two page reads.
    """

    tokens: Mapping
    default: str
    orders: Mapping = field(default_factory=dict)
    tiebreak: tuple = ("pk",)

    def __post_init__(self):
        if self.default not in self.tokens:
            raise ValueError("A table's default sort must be one of its tokens.")

    @classmethod
    def by_column(cls, orders, *, default, descending_first=(), tiebreak=("pk",)):
        """Offer both directions of every column: ``key`` and ``-key``.

        ``default`` is a token, such as ``"-created"`` for newest first. A
        column named in ``descending_first`` (times, counts) sorts descending
        on its first click, since people usually want the newest or largest
        first; every other column starts ascending.
        """
        tokens = {}
        for key in orders:
            pair = [(key, (key, False)), (f"-{key}", (key, True))]
            if key in descending_first:
                pair.reverse()
            tokens.update(pair)
        return cls(tokens, default, orders, tiebreak)

    def parse(self, parameters, prefix=""):
        """The requested token, or the default; anything else is refused."""
        token = parameters.get(f"{prefix}sort", self.default)
        if token not in self.tokens:
            raise ValueError("Unsupported table sort.")
        return token

    def order(self, query, token):
        """Order a queryset by the token's column, then the unique tiebreak."""
        column, descending = self.tokens[token]
        return query.order_by(
            *(
                _direction(term, descending, nulls_last=True)
                for term in self.orders[column]
            ),
            *(_direction(term, descending) for term in self.tiebreak),
        )

    def sort_rows(self, rows, token):
        """Order in-memory rows by the token's column, missing values last.

        Python's sort is stable in both directions, so rows with equal values
        keep the source order the caller read them in, itself deterministic.
        """
        column, descending = self.tokens[token]
        return _present_first(rows, self.orders[column], descending)

    def columns(self):
        """Every sortable column key, once each."""
        return {column for column, _ in self.tokens.values()}


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
    # without always knowing how many pages there are.
    has_next: bool | None = None
    sizes: tuple = PAGE_SIZES
    allow_all: bool = True
    sorting: Sorting | None = None
    sort: str | None = None
    # "post" for reports whose filters are private POST state; ``action`` is
    # then the URL every navigator and heading form posts back to.
    method: str = "get"
    action: str = ""
    # True when a bounded count stopped at COUNT_LIMIT: ``count`` is then a
    # lower bound and ``pages`` is unknown.
    capped: bool = False
    # False for a short table shown whole, with no navigator (see
    # ``whole_table``): its headings then carry only the sort, never a size.
    paged: bool = True

    @property
    def page_name(self):
        """The query parameter holding this table's page number."""
        return f"{self.prefix}page"

    @property
    def size_name(self):
        """The query parameter holding this table's rows-per-page choice."""
        return f"{self.prefix}size"

    @property
    def sort_name(self):
        """The query parameter holding this table's sort token."""
        return f"{self.prefix}sort"

    @property
    def anchor(self):
        """The id of the element wrapping this table's navigators and rows.

        Templates put it on a ``<div data-table-region>`` around the table
        (see ``table-navigator.html``); every heading and navigator control
        names it as a URL fragment, so a full page load lands on the table
        rather than at the top, and ``ui-v1.js`` finds the same element in a
        fetched page to swap it in place (#478). A prefix keeps the id unique
        when several tables share one page: ``members_`` → ``members-table``.
        """
        prefix = self.prefix.rstrip("_-")
        return f"{prefix}-table" if prefix else "table"

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

    @property
    def sort_fields(self):
        """The current sort as (name, value) pairs; empty for an unsorted table."""
        return [(self.sort_name, self.sort)] if self.sorting else []

    @property
    def carried_fields(self):
        """Filters and sort every navigator control keeps (not size or page)."""
        return [*self.carried, *self.sort_fields]

    @property
    def view_fields(self):
        """Size and sort, for a page's own filter form to keep as hidden fields."""
        return [(self.size_name, self.size_value), *self.sort_fields]

    def page_fields(self, number):
        """Every field a link or form to another page carries."""
        return [
            *self.carried_fields,
            (self.size_name, self.size_value),
            (self.page_name, str(number)),
        ]

    def query(self, number):
        """Query string for another page, keeping filters, sort and page size."""
        return urlencode(self.page_fields(number))

    @property
    def current_query(self):
        """Query string for this same page, for a Refresh link that keeps the
        reader's filters, sort, page size and page (#519)."""
        return self.query(self.number)

    @property
    def previous_number(self):
        """The previous page's number, or None on the first page."""
        return self.number - 1 if self.number > 1 else None

    @property
    def next_number(self):
        """The next page's number, or None on the last page."""
        known = self.pages is not None and not self.capped
        more = self.number < self.pages if known else self.has_next
        return self.number + 1 if more else None

    @property
    def previous_query(self):
        """Query string for the previous page, or None on the first page."""
        number = self.previous_number
        return self.query(number) if number else None

    @property
    def next_query(self):
        """Query string for the next page, or None on the last page."""
        number = self.next_number
        return self.query(number) if number else None

    @property
    def previous_fields(self):
        """Hidden fields of a POST table's Previous form."""
        number = self.previous_number
        return self.page_fields(number) if number else None

    @property
    def next_fields(self):
        """Hidden fields of a POST table's Next form."""
        number = self.next_number
        return self.page_fields(number) if number else None

    def _column(self, column):
        """Reject a heading naming a column the table does not sort by."""
        if self.sorting is None or column not in self.sorting.columns():
            raise ValueError(f"Column {column!r} is not sortable.")
        return column

    def aria_sort(self, column):
        """ "ascending" or "descending" for the sorted column, else None."""
        current, descending = self.sorting.tokens[self.sort]
        if self._column(column) != current:
            return None
        return "descending" if descending else "ascending"

    def sort_target(self, column):
        """The token a heading selects: the other direction of the sorted
        column when the table offers it, else the column's first-click token.
        """
        self._column(column)
        tokens = [
            (token, descending)
            for token, (key, descending) in self.sorting.tokens.items()
            if key == column
        ]
        current, descending = self.sorting.tokens[self.sort]
        if current == column:
            other = [token for token, down in tokens if down != descending]
            return other[0] if other else self.sort
        return tokens[0][0]

    def sort_descends(self, column):
        """Whether choosing this heading sorts descending (for its label)."""
        return self.sorting.tokens[self.sort_target(column)][1]

    def heading_fields(self, column):
        """Fields a heading carries: filters and size, the new sort, page 1."""
        return [
            *self.carried,
            *([(self.size_name, self.size_value)] if self.paged else []),
            (self.sort_name, self.sort_target(column)),
        ]

    def heading_query(self, column):
        """Query string a GET table's heading links to."""
        return urlencode(self.heading_fields(column))

    @property
    def page_label(self):
        """(number, pages) for "Page N of M"; pages is None when unknown."""
        return self.number, None if self.capped else self.pages


def _size(parameters, prefix, default, sizes, allow_all):
    """Parse one table's rows-per-page choice; None means All."""
    size_text = parameters.get(f"{prefix}size", str(default))
    if size_text == ALL and allow_all:
        return None
    if size_text.isascii() and size_text.isdecimal() and int(size_text) in sizes:
        return int(size_text)
    raise ValueError("Unsupported table page size.")


def _page(parameters, prefix):
    """Parse one table's 1-based page number, bounded before int()."""
    page_text = parameters.get(f"{prefix}page", "1")
    if not (page_text.isascii() and page_text.isdecimal()) or len(page_text) > 9:
        raise ValueError("Invalid table page number.")
    return max(1, int(page_text))


def _carried(carry):
    """Keep only filters with a value; blank ones are defaults."""
    return tuple((name, value) for name, value in carry if value)


def paginate(rows, parameters, *, prefix="", default=50, carry=(), sorting=None):
    """Sort and slice ``rows`` by the page/size/sort values in ``parameters``.

    ``parameters`` is an already-validated mapping of single values (see
    ``web.contracts.filters``). A malformed number, unknown size or unknown
    sort token is refused with ValueError; a page past the end shows the last
    page instead, so a bookmarked or typed page number never lands on an
    empty error. ``carry`` lists (name, value) pairs, such as search filters,
    that every navigator link must keep. ``sorting`` (a ``Sorting`` whose
    ``orders`` are key functions) sorts the whole list before slicing.
    """
    rows = list(rows)
    size = _size(parameters, prefix, default, PAGE_SIZES, True)
    token = None
    if sorting is not None:
        token = sorting.parse(parameters, prefix)
        rows = sorting.sort_rows(rows, token)
    pages = 1 if size is None else max(1, ceil(len(rows) / size))
    number = min(_page(parameters, prefix), pages)
    shown = rows if size is None else rows[(number - 1) * size : number * size]
    return TablePage(
        rows=shown,
        number=number,
        pages=pages,
        count=len(rows),
        size=size,
        prefix=prefix,
        carried=_carried(carry),
        sorting=sorting,
        sort=token,
    )


def whole_table(rows, *, sorting, sort, prefix="", carry=()):
    """Sort a short table shown whole: no navigator, no page or size.

    ``sort`` is the token the view already parsed from ``{prefix}sort``
    (``Sorting.parse``), so a page offering such a table accepts just that
    parameter (and its own filters); a token the table does not offer is
    refused with ValueError. ``carry`` lists the (name, value) pairs every
    heading keeps, such as the page's filters and another table's sort.
    """
    if sort not in sorting.tokens:
        raise ValueError("Unsupported table sort.")
    rows = sorting.sort_rows(list(rows), sort)
    return TablePage(
        rows=rows,
        number=1,
        pages=1,
        count=len(rows),
        size=None,
        prefix=prefix,
        carried=_carried(carry),
        sorting=sorting,
        sort=sort,
        paged=False,
    )


def bounded_count(query, limit=COUNT_LIMIT):
    """Count matching rows, stopping at ``limit``: returns (count, capped).

    The count reads at most ``limit + 1`` rows (``SELECT count(*) FROM
    (... LIMIT n)``), so its cost is bounded however large the table grows.
    Ordering is dropped: it cannot change a count.
    """
    count = query.order_by()[: limit + 1].count()
    return (limit, True) if count > limit else (count, False)


def read_window(window, query, total):
    """Read one PageWindow page of an ordered ``query``, clamped to the end.

    ``total`` is the query's ``bounded_count``. A page number past the last
    page of a known (uncapped) total reads the last page instead, so a
    typed, bookmarked or stale page number never lands on an empty page.
    Returns (window, rows, has_next); the window is the one actually read,
    for ``window_table``.
    """
    window = clamp_window(window, total)
    rows, has_next = window.rows(query)
    return window, rows, has_next


def clamp_window(window, total):
    """The requested PageWindow, or the last page when it is past the end of
    a known (uncapped) ``bounded_count`` total."""
    count, capped = total
    last = max(1, ceil(count / window.size))
    return replace(window, page=last) if not capped and window.page > last else window


def window_table(
    window,
    rows,
    has_next,
    *,
    prefix="",
    carry=(),
    total=None,
    sorting=None,
    sort=None,
):
    """Describe one PageWindow page for the shared navigator.

    ``window`` is the PageWindow the view already used to read ``rows`` and
    ``has_next`` (see ``PageWindow.rows``); the view keeps its own parsing and
    bounds, so JSON endpoints sharing that parser are unchanged. ``total`` is
    the view's ``bounded_count`` result, (count, capped), or None when the
    view does not count; the navigator then shows no total or page count.
    """
    count, capped = total if total is not None else (None, False)
    pages = None if count is None else max(1, ceil(count / window.size))
    return TablePage(
        rows=list(rows),
        number=window.page,
        pages=pages,
        count=count,
        size=window.size,
        prefix=prefix,
        carried=_carried(carry),
        has_next=bool(has_next),
        sizes=WINDOW_SIZES,
        allow_all=False,
        sorting=sorting,
        sort=sort,
        capped=capped,
    )


def report_table(
    rows,
    *,
    number,
    size,
    total,
    carry,
    sorting,
    sort,
    action,
    sizes=PAGE_SIZES,
    allow_all=False,
):
    """Describe one page an installed SQL report selection already cut.

    The selection returned ``rows`` for page ``number`` of ``size`` rows,
    ordered by ``sort``, and the ``total`` of every matching row. The page's
    filters are private, so ``carry`` becomes hidden POST fields sent to
    ``action``; ``sizes`` lists the page sizes that selection accepts.
    """
    return TablePage(
        rows=list(rows),
        number=number,
        pages=max(1, ceil(total / size)),
        count=total,
        size=size,
        prefix="",
        carried=_carried(carry),
        sizes=sizes,
        allow_all=allow_all,
        sorting=sorting,
        sort=sort,
        method="post",
        action=action,
    )


def table_parameters(prefix=""):
    """The query parameter names the table helpers read for one table."""
    return {f"{prefix}page", f"{prefix}size", f"{prefix}sort"}
