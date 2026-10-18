"""Closed report navigation state; identifiers select data, never authority."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse

from parishkit.stewardship.web.contracts import PageWindow, filters
from parishkit.stewardship.web.dates import unasked_zone
from parishkit.stewardship.web.tables import ALL, PAGE_SIZES, Sorting

from .participation import SCOPE_LABELS
from .response_metrics import MODES


def _ratio(day):
    """Cumulative participation as a fraction, or None when unavailable."""
    if not day.population_available or not day.cohort_denominator:
        return None
    return day.cumulative_responses / day.cohort_denominator


def _first(day):
    """First submissions that day, or None when the population is unavailable."""
    return day.first_responses if day.population_available else None


def _pledge(day):
    """Cumulative annual pledges, or None when unavailable."""
    return day.pledge_total if day.pledge_available else None


# The daily table's rows are (day, formatted cells) pairs, and it sorts every
# column on the server over the whole in-memory document. It opens newest
# first (#1534). The date tokens keep their established names (date_asc and
# date_desc), which older report links still carry, so a saved link with
# date_asc still opens oldest first; an unavailable value sorts after every
# known one ascending.
DAILY_SORTING = Sorting(
    {
        "date_asc": ("date", False),
        "date_desc": ("date", True),
        "-first": ("first", True),
        "first": ("first", False),
        "-participation": ("participation", True),
        "participation": ("participation", False),
        "-pledge": ("pledge", True),
        "pledge": ("pledge", False),
    },
    "date_desc",
    {
        "date": lambda row: row[0].local_date,
        "first": lambda row: _first(row[0]),
        "participation": lambda row: _ratio(row[0]),
        "pledge": lambda row: _pledge(row[0]),
    },
)


# The daily table's rows-per-page choices (#1537): it follows its chart, so
# it offers 5 as a smaller first size and opens with it (ui-conventions,
# "Admin tables"); the shared sizes follow.
DAILY_SIZES = (5, *PAGE_SIZES)
DAILY_SIZE = "5"


@dataclass(frozen=True)
class ReportQuery:
    """Only non-identifying, validated presentation state may enter report URLs.

    The Response dashboard's state (#1537): ``mode`` (Production, or an
    Administrator's Testing rehearsal view), the daily chart's population
    ``scope``, the browser's zone, and the daily table's page, sort and size.
    """

    scope: str = "historical"
    timezone: str = "UTC"
    page: int = 1
    sort: str = DAILY_SORTING.default
    timezone_explicit: bool = True
    # Rows per page of the daily table: one of DAILY_SIZES or "all".
    size: str = DAILY_SIZE
    mode: str = "production"

    @classmethod
    def parse(cls, parameters):
        """Reject duplicate/unknown keys, unbounded pages and arbitrary sort text.

        ``inactive`` is still accepted, and ignored, so bookmarks made while the
        removed inactive subtotal option existed keep loading (#728); so is
        the former Response dashboard's ``grain`` (#1537), whose activity
        chart this page does not draw. Neither is a redirect: the address
        stays the one the reader opened.
        """
        values = filters(
            parameters,
            allowed={
                "scope",
                "timezone",
                "inactive",
                "grain",
                "page",
                "sort",
                "size",
                "mode",
            },
        )
        scope = values.get("scope", "historical")
        # The page asks for no zone (#1127): report-v1.js supplies the
        # browser's, and an unknown one falls back to UTC, never a refusal.
        zone = unasked_zone(values.get("timezone"), "UTC")
        page = values.get("page", "1")
        sort = values.get("sort", DAILY_SORTING.default)
        size = values.get("size", DAILY_SIZE)
        mode = values.get("mode", "production")
        if (
            scope not in SCOPE_LABELS
            or mode not in MODES
            or not page.isascii()
            or not page.isdecimal()
            or str(int(page)) != page
            or sort not in DAILY_SORTING.tokens
            or size not in {*map(str, DAILY_SIZES), ALL}
        ):
            raise ValueError("Invalid report filters.")
        PageWindow(page=int(page))
        return cls(
            scope,
            zone,
            int(page),
            sort,
            "timezone" in values,
            size,
            mode,
        )

    def carried(self):
        """Filter values every daily-table navigator link must keep.

        The sort token is not listed: the shared table carries it itself.
        """
        values = [("scope", self.scope)]
        if self.mode != "production":
            values.append(("mode", self.mode))
        if self.timezone_explicit:
            values.append(("timezone", self.timezone))
        return values

    def url(self, *, page=None, scope=None, mode=None):
        """Preserve validated state on every link to the Response dashboard.

        ``scope`` gives another Chart population's link (#1535), ``mode``
        the other Production / Testing rehearsal view's (#1537), and
        ``page`` another daily-table page; otherwise the current ones are
        kept. The default mode, sort and size are left out.
        """
        mode = self.mode if mode is None else mode
        values = {
            "scope": self.scope if scope is None else scope,
            "page": self.page if page is None else page,
        }
        if mode != "production":
            values["mode"] = mode
        if self.sort != DAILY_SORTING.default:
            values["sort"] = self.sort
        if self.size != DAILY_SIZE:
            values["size"] = self.size
        if self.timezone_explicit:
            values["timezone"] = self.timezone
        return reverse("admin:response_dashboard") + "?" + urlencode(values)


class RenderedReport:
    """Eagerly prepared bytes retain all selection contexts until response close.

    Unlike an unstarted generator, close also releases its ExitStack when WSGI
    disconnects before requesting the first byte. The outer response owns the
    campaign guard and closes it only after this generation protection ends.
    """

    def __init__(self, content, stack):
        self.content, self.stack = iter((content,)), stack

    def __iter__(self):
        return self

    def __next__(self):
        """Emit one bounded document; the outer guard checks both sides."""
        return next(self.content)

    def close(self):
        """ExitStack closure is safe before, during or after iteration."""
        self.stack.close()
