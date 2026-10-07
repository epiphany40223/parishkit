"""Closed report navigation state; identifiers select data, never authority."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse

from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.contracts import PageWindow, filters
from parishkit.stewardship.web.tables import ALL, PAGE_SIZES, Sorting

from .participation import SCOPE_LABELS


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
# column on the server over the whole in-memory document. The date tokens
# keep their established names (date_asc and date_desc), which older report
# links and the options form still carry; an unavailable value sorts after
# every known one ascending.
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
    "date_asc",
    {
        "date": lambda row: row[0].local_date,
        "first": lambda row: _first(row[0]),
        "participation": lambda row: _ratio(row[0]),
        "pledge": lambda row: _pledge(row[0]),
    },
)


@dataclass(frozen=True)
class ReportQuery:
    """Only non-identifying, validated presentation state may enter report URLs."""

    scope: str = "historical"
    timezone: str = "UTC"
    page: int = 1
    sort: str = "date_asc"
    timezone_explicit: bool = True
    # Rows per page of the daily table: one of PAGE_SIZES or "all".
    size: str = "50"

    @classmethod
    def parse(cls, parameters):
        """Reject duplicate/unknown keys, unbounded pages and arbitrary sort text.

        ``inactive`` is still accepted, and ignored, so bookmarks made while the
        removed inactive subtotal option existed keep loading (#728).
        """
        values = filters(
            parameters,
            allowed={"scope", "timezone", "inactive", "page", "sort", "size"},
        )
        scope = values.get("scope", "historical")
        zone = values.get("timezone", "UTC")
        page = values.get("page", "1")
        sort = values.get("sort", "date_asc")
        size = values.get("size", "50")
        if (
            scope not in SCOPE_LABELS
            or zone not in timezone_names()
            or not page.isascii()
            or not page.isdecimal()
            or str(int(page)) != page
            or sort not in DAILY_SORTING.tokens
            or size not in {*map(str, PAGE_SIZES), ALL}
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
        )

    def carried(self):
        """Filter values every daily-table navigator link must keep.

        The sort token is not listed: the shared table carries it itself.
        """
        values = [("scope", self.scope)]
        if self.timezone_explicit:
            values.append(("timezone", self.timezone))
        return values

    def url(self, campaign_id, *, page=None):
        """Preserve validated state and the explicit campaign on every link."""
        values = {
            "scope": self.scope,
            "page": self.page if page is None else page,
            "sort": self.sort,
        }
        # Keep existing report URLs unchanged at the default size.
        if self.size != "50":
            values["size"] = self.size
        if self.timezone_explicit:
            values["timezone"] = self.timezone
        return (
            reverse("admin:participation", args=[campaign_id]) + "?" + urlencode(values)
        )


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
