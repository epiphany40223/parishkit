"""Bounded Admin/Staff financial stewardship detail over effective live responses."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.financial import (
    FREQUENCIES,
    household_pronoun,
    installment,
)
from parishkit.stewardship.responses.financial_inputs import (
    InvalidFinancialSource,
    financial_definition,
    giving_observation,
)
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.web.content import bounded_text, render_template
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.presentation import campaign_year, parish_date

from .information import parse_page
from .money import MoneyAmount, source_cents

PAGE_SIZE = 50
MONEY = re.compile(r"(0|[1-9][0-9]{0,8})(\.[0-9]{2})?")
SHARE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
FREQUENCY_LABELS = {
    "weekly": "Weekly",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "annual": "Annual",
    "none": "No frequency",
}
DATES = ("first_start", "first_end", "latest_start", "latest_end")


@dataclass(frozen=True, repr=False)
class FinancialQuery:
    """Identifying values travel only in CSRF POST bodies, never links or logs."""

    search: str = ""
    active: str = "any"
    first_start: str = ""
    first_end: str = ""
    latest_start: str = ""
    latest_end: str = ""
    pledge_min: str = ""
    pledge_max: str = ""
    amount: str = "any"
    frequency: str = "any"
    share: str = "any"
    sort: str = "name"
    page: int = 1

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values; SQL enforces the same closed shape."""
        if type(parameters) is dict:
            if any(type(value) is not str for value in parameters.values()):
                raise ValueError("Financial filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        values["page"] = parse_page(values.get("page", "1"))
        query = cls(**values)
        bounded_text(query.search)
        if (
            query.active not in {"any", "active", "inactive", "unavailable"}
            or query.amount not in {"any", "zero", "nonzero"}
            or query.frequency not in {"any", "none", *FREQUENCIES}
            or query.sort
            not in {"name", "name_desc", "newest", "oldest", "pledge", "pledge_desc"}
            or (query.share not in {"any", "none"} and not SHARE.fullmatch(query.share))
            or any(
                value and MONEY.fullmatch(value) is None
                for value in (query.pledge_min, query.pledge_max)
            )
        ):
            raise ValueError("Invalid financial filters.")
        for name in DATES:
            value = getattr(query, name)
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Invalid financial date filter.")
        for low, high in (
            (query.first_start, query.first_end),
            (query.latest_start, query.latest_end),
        ):
            if low and high and low > high:
                raise ValueError("Invalid financial date interval.")
        if (
            query.pledge_min
            and query.pledge_max
            and money(query.pledge_min).cents > money(query.pledge_max).cents
        ):
            raise ValueError("Invalid financial pledge range.")
        return query

    def form_values(self):
        """Return escaped-by-template values for CSRF-protected page navigation."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page"
        }


def money(value):
    """Parse canonical SQL text exactly; a missing observation stays unavailable."""
    if value is None:
        return MoneyAmount(None)
    whole, _, fraction = value.partition(".")
    return MoneyAmount(source_cents(f"{whole}.{fraction or '00'}"))


def giving_proven(campaign_id, configuration, snapshot_id):
    """Whether the snapshot's last giving read is complete for this window.

    The one completeness rule, shared with the Family form. It can only withhold
    source money: SQL reads the window and through-date itself.
    """
    try:
        definition = financial_definition(configuration, campaign_id=campaign_id)
    except InvalidFinancialSource:
        return False
    cursor = SourceSnapshot.objects.values_list("cursor", flat=True).get(pk=snapshot_id)
    return giving_observation(cursor, definition) is not None


def share_labels(options, *, configuration, parish_name):
    """Render each stable option's label once, in its neutral household wording.

    A report has no single household to address, so it uses the wording for a
    household with no listed Member. The browser never evaluates a template.
    """
    financial = configuration.get("financial") or {}
    start, end = (
        date.fromisoformat(financial[key]) if financial.get(key) else None
        for key in ("start", "end")
    )
    period = f"{parish_date(start)} – {parish_date(end)}" if start and end else ""
    substitutions = {
        "parish_name": parish_name,
        "campaign_year": campaign_year(configuration),
        "financial_start": parish_date(start) if start else "",
        "financial_end": parish_date(end) if end else "",
        "financial_period": period,
        "pronoun": household_pronoun(0),
    }
    return {
        option["id"]: render_template(option["label"], substitutions)
        for option in options or ()
    }


def financial_page(
    campaign_id, query, principal, *, giving, parish_name, configuration
):
    """Read one coherent page for Admin or Staff; leaders never reach this query."""
    if not allows(principal, Capability.FINANCIAL_DETAIL):
        raise PermissionError("Financial stewardship detail is unavailable.")
    if type(giving) is not bool:
        raise TypeError("Giving availability must be explicit.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_financial_report_v1(%s,%s::jsonb,%s)::text",
            [
                campaign_id,
                json.dumps({"filters": query.form_values(), "giving": giving}),
                query.page,
            ],
        )
        value = cursor.fetchone()
    if value is None or value[0] is None:
        raise ReadUnavailable("Financial report inputs are unavailable.")
    result = json.loads(value[0])
    if result.get("disabled"):
        raise PermissionError("Financial stewardship is not enabled for this campaign.")
    if result.get("unavailable"):
        raise ReadUnavailable("Financial report inputs are unavailable.")
    current = share_labels(
        result["metadata"]["share_options"],
        configuration=configuration,
        parish_name=parish_name,
    )
    for row in result["rows"]:
        annual = money(row["annual_pledge"])
        row["annual"] = annual
        row["installment"] = (
            installment(annual, row["frequency"])
            if row["frequency"] and annual.available
            else MoneyAmount(None)
        )
        row["frequency_label"] = FREQUENCY_LABELS[row["frequency"] or "none"]
        # Labels are versioned with the configuration the Family actually saw.
        seen = share_labels(
            row.pop("share_options"),
            configuration=configuration,
            parish_name=parish_name,
        )
        row["shares"] = [
            {"label": seen.get(key, "Unavailable share method"), "text": text}
            for key, text in sorted(row["shares"].items())
        ]
        row["source_pledge"] = money(row.pop("pledge_total"))
        row["source_contributions"] = money(row.pop("contribution_total"))
        for field in ("submitted_at", "first_submitted_at"):
            row[field] = datetime.fromisoformat(row[field])
    summary = result["summary"]
    summary["annual_total"] = money(summary["annual_total"])
    summary["frequencies"] = [
        (FREQUENCY_LABELS[key], summary["frequencies"].get(key, 0))
        for key in (*FREQUENCIES, "none")
    ]
    summary["shares"] = [
        (current.get(key, "Unavailable share method"), count)
        for key, count in sorted(summary["shares"].items())
    ]
    metadata = result["metadata"]
    metadata["source_as_of"] = datetime.fromisoformat(metadata["source_as_of"])
    metadata["giving_through"] = (
        date.fromisoformat(metadata["giving_through"])
        if metadata["giving_through"]
        else None
    )
    result["share_choices"] = sorted(current.items(), key=lambda item: item[1])
    return result
