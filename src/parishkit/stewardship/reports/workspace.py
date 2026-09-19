"""Closed report navigation state; identifiers select data, never authority."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse

from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.contracts import PageWindow, filters

from .participation import SCOPE_LABELS


@dataclass(frozen=True)
class ReportQuery:
    """Only non-identifying, validated presentation state may enter report URLs."""

    scope: str = "historical"
    timezone: str = "UTC"
    inactive: bool = False
    page: int = 1
    sort: str = "date_asc"
    timezone_explicit: bool = True

    @classmethod
    def parse(cls, parameters):
        """Reject duplicate/unknown keys, unbounded pages and arbitrary sort text."""
        values = filters(
            parameters, allowed={"scope", "timezone", "inactive", "page", "sort"}
        )
        scope = values.get("scope", "historical")
        zone = values.get("timezone", "UTC")
        inactive = values.get("inactive", "no")
        page = values.get("page", "1")
        sort = values.get("sort", "date_asc")
        if (
            scope not in SCOPE_LABELS
            or zone not in timezone_names()
            or inactive not in {"yes", "no"}
            or not page.isascii()
            or not page.isdecimal()
            or str(int(page)) != page
            or sort not in {"date_asc", "date_desc"}
        ):
            raise ValueError("Invalid report filters.")
        PageWindow(page=int(page))
        return cls(
            scope, zone, inactive == "yes", int(page), sort, "timezone" in values
        )

    def url(self, campaign_id, *, page=None):
        """Preserve validated state and the explicit campaign on every link."""
        values = {
            "scope": self.scope,
            "inactive": "yes" if self.inactive else "no",
            "page": self.page if page is None else page,
            "sort": self.sort,
        }
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
