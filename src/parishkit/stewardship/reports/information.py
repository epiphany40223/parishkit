"""Bounded, private staff queue queries over live Family submissions."""

import json
from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.models import AdditionalInformationRevision
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import PageWindow, filters

from .weekly_presentation import DISPOSITIONS

ORDERS = {
    "newest": "submitted_at DESC,id",
    "oldest": "submitted_at,id",
    "name": "lower(family_name),id",
    "name_desc": "lower(family_name) DESC,id",
}
PAGE_SIZE = 50


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
    page: int = 1

    @classmethod
    def parse(cls, parameters):
        """Accept single bounded values and canonical parish-local date filters."""
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
        return query

    def form_values(self):
        """Return escaped-by-template values for CSRF-protected page navigation."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page"
        }


def parse_page(value):
    """Reject alternate integer spellings before the shared bounded page helper."""
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value:
        raise ValueError("Invalid information page.")
    PageWindow(page=int(value))
    return int(value)


def information_page(campaign_id, query, *, item_id=None):
    """Detach one coherent source/item page under the caller's campaign guard.

    The shared closed SQL query also owns complete export captures. Every private
    value is bound; one MVCC statement prevents mixed names, workflow versions,
    counts and rows during concurrent Staff/Family edits.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_information_report_v1(%s,%s::jsonb,%s,%s,%s)::text",
            (
                campaign_id,
                json.dumps({"filters": query.form_values(), "history": False}),
                query.page,
                item_id,
                PAGE_SIZE,
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
