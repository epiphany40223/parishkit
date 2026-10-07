"""Bounded, private staff queue queries over live Family submissions."""

import json
from dataclasses import dataclass, replace
from datetime import date, datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.models import AdditionalInformationRevision
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import PageWindow, filters
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.tables import Sorting

from .weekly_presentation import DISPOSITIONS

ORDERS = {
    "newest": "submitted_at DESC,id",
    "oldest": "submitted_at,id",
    "name": "lower(family_name),id",
    "name_desc": "lower(family_name) DESC,id",
}
PAGE_SIZE = 50
# Rows per page the queue offers; the selection accepts 1-100.
PAGE_SIZES = (25, 50, 100)
# The installed selection (stewardship_information_report_v2) orders and
# pages the queue itself (ORDERS above mirrors it for exports), so its closed
# ``sort`` vocabulary is the whole list of column sorts: Family by name and
# Submitted by time, each either way, newest first on a first click.
# Disposition, text, follow-up needed and completion cannot be sorted without
# changing that frozen SQL (schema freeze, #203).
INFORMATION_SORTING = Sorting(
    {
        "name": ("family", False),
        "name_desc": ("family", True),
        "newest": ("submitted", True),
        "oldest": ("submitted", False),
    },
    "newest",
)


@dataclass(frozen=True, repr=False)
class InformationQuery:
    """Search is private POST state, never a query-string or audit payload."""

    search: str = ""
    disposition: str = "current_actionable"
    needed: str = "any"
    completed: str = "any"
    start: str = ""
    end: str = ""
    sort: str = "newest"
    # The browser's IANA time zone, which the page script fills (#558): Start
    # and End are whole days there. None only for a retained export capture
    # from before migration 0017, whose days are the campaign's zone.
    zone: str | None = ""
    page: int = 1

    @classmethod
    def parse(cls, parameters):
        """Accept single bounded values and canonical browser-local date filters.

        A date needs the browser's zone; a blank or unknown one raises
        :class:`UnknownZone` (a tab opened before #558, or a zone outside the
        catalog), never read as another zone. Without dates the zone is
        unused, and one the catalog does not know is dropped.
        """
        if not hasattr(parameters, "getlist"):
            if not isinstance(parameters, dict) or any(
                type(value) is not str for value in parameters.values()
            ):
                raise ValueError("Information filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        query = cls(**(values | {"page": parse_page(values.get("page", "1"))}))
        if (
            query.disposition not in {*DISPOSITIONS, "all"}
            or query.needed not in {"any", "yes", "no"}
            or query.completed not in {"any", "yes", "no"}
            or query.sort not in ORDERS
        ):
            raise ValueError("Invalid information filters.")
        bounded_text(query.search)
        for value in (query.start, query.end):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Invalid date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid date interval.")
        if query.zone not in timezone_names():
            if query.start or query.end:
                raise UnknownZone("Date filters need the browser's time zone.")
            query = replace(query, zone="")
        return query

    @classmethod
    def retained(cls, values):
        """Rebuild the filters of a retained export capture for its retry.

        A capture made before migration 0017 has no ``zone`` key: its days
        were the campaign's zone, and its retry must ask for exactly the same
        filters, so the zone stays None and :meth:`form_values` leaves it out.
        """
        if "zone" in values:
            return cls.parse(values)
        # Any catalog zone lets the old dates through validation; it is then
        # dropped, so the retry's filters equal the retained ones.
        return replace(cls.parse(values | {"zone": "Etc/UTC"}), zone=None)

    def form_values(self):
        """Return escaped-by-template values for CSRF-protected page navigation."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page" and not (key == "zone" and self.zone is None)
        }


def parse_page(value):
    """Reject alternate integer spellings before the shared bounded page helper."""
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value:
        raise ValueError("Invalid information page.")
    PageWindow(page=int(value))
    return int(value)


def information_page(campaign_id, query, *, item_id=None, page_size=PAGE_SIZE):
    """Detach one coherent source/item page under the caller's campaign guard.

    The shared closed SQL query also owns complete export captures. Every private
    value is bound; one MVCC statement prevents mixed names, workflow versions,
    counts and rows during concurrent Staff/Family edits.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_information_report_v2(%s,%s::jsonb,%s,%s,%s)::text",
            (
                campaign_id,
                json.dumps({"filters": query.form_values(), "history": False}),
                query.page,
                item_id,
                page_size,
            ),
        )
        value = cursor.fetchone()
    if value is None or value[0] is None:
        raise ReadUnavailable("Information report inputs are unavailable.")
    result = json.loads(value[0])
    if item_id is not None and not result["rows"]:
        raise ObjectDoesNotExist("Information item is unavailable.")
    for row in result["rows"]:
        row["disposition_label"] = DISPOSITIONS[row["disposition"]]
        for field in ("submitted_at", "followed_up_at"):
            row[field] = datetime.fromisoformat(row[field]) if row[field] else None
    result["metadata"]["source_as_of"] = datetime.fromisoformat(
        result["metadata"]["source_as_of"]
    )
    return result


def information_history(item_id, page, *, version):
    """Bound history to the displayed version, even if another edit now commits."""
    return PageWindow(page=page).rows(
        AdditionalInformationRevision.objects.filter(
            item_id=item_id, expected_version__lt=version
        ).order_by("-expected_version")
    )
