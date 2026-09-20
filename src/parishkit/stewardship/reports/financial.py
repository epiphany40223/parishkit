"""Bounded Admin/Staff financial stewardship detail over effective live responses."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.financial import FREQUENCIES, installment
from parishkit.stewardship.responses.financial_inputs import (
    InvalidFinancialSource,
    financial_definition,
    giving_observation,
)
from parishkit.stewardship.responses.financial_presentation import option_labels
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters

from .information import parse_page
from .money import MoneyAmount, source_cents

PAGE_SIZE = 50
MONEY = re.compile(r"(0|[1-9][0-9]{0,8})(\.[0-9]{2})?")
# A share method is a stable option identity in canonical lowercase form.
SHARE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
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
            and parse_money(query.pledge_min).cents
            > parse_money(query.pledge_max).cents
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


def parse_money(value):
    """Parse decimal text exactly, never through a float; absence stays unavailable.

    Used for SQL's canonical two-decimal money and for an already validated
    whole-dollar or two-decimal pledge filter.
    """
    if value is None:
        return MoneyAmount(None)
    whole, _, fraction = value.partition(".")
    return MoneyAmount(source_cents(f"{whole}.{fraction or '00'}"))


def giving_proof(campaign):
    """Name exactly the snapshot and configuration whose giving read is proven.

    The one completeness rule, shared with the Family form, applied to the same
    snapshot SQL selects: an archived campaign's own pinned source, otherwise the
    current one. These reads are separate statements from the report, so SQL
    honors the proof only for the snapshot and configuration it then selects; a
    promotion or configuration change in between withholds money. Returns None
    when nothing is proven.
    """
    configuration = campaign.active_configuration
    if campaign.state == "archived":
        pinned = CampaignCredentialState.objects.filter(campaign_id=campaign.pk)
        snapshot_id = pinned.values_list("source_snapshot_id", flat=True).first()
    else:
        snapshot_id = SourceCurrent.objects.values_list(
            "snapshot_id", flat=True
        ).first()
    try:
        definition = financial_definition(configuration.values, campaign_id=campaign.pk)
    except InvalidFinancialSource:
        return None
    cursor = (
        SourceSnapshot.objects.filter(pk=snapshot_id)
        .values_list("cursor", flat=True)
        .first()
    )
    if snapshot_id is None or giving_observation(cursor, definition) is None:
        return None
    return {"snapshot": str(snapshot_id), "configuration": str(configuration.pk)}


def share_labels(values, *, campaign_id, parish_name):
    """Word one configuration's options exactly as its Family form did.

    The Family form owns this wording and its validated financial period. A
    report has no single household to address, so it uses that form's variant for
    a household with no listed Member. The browser never evaluates a template.
    """
    try:
        definition = financial_definition(values, campaign_id=campaign_id)
    except InvalidFinancialSource:
        return {}
    return {
        option.id: option_labels(option, definition, parish_name=parish_name)["none"]
        for option in definition.options
    }


def seen_configurations(campaign_id, identifiers):
    """Load the immutable configurations these Families actually answered."""
    rows = CampaignConfiguration.objects.filter(
        record_id=campaign_id, configuration_id__in=identifiers
    )
    return {
        str(key): values
        for key, values in rows.values_list("configuration_id", "values")
    }


def in_offered_order(counts, labels):
    """Order share methods as the parish configured them, unknown ones last.

    Option identities are opaque, so sorting by them would read as random and
    differ from the order the Family saw.
    """
    position = {key: index for index, key in enumerate(labels)}
    return sorted(
        counts.items(), key=lambda item: (position.get(item[0], len(position)), item[0])
    )


def financial_page(
    campaign_id,
    query,
    principal,
    *,
    proof,
    parish_name,
    configuration,
    page_size=PAGE_SIZE,
):
    """Read one coherent page for Admin or Staff; leaders never reach this query."""
    if not allows(principal, Capability.FINANCIAL_DETAIL):
        raise PermissionError("Financial stewardship detail is unavailable.")
    if proof is not None and (
        type(proof) is not dict or set(proof) != {"snapshot", "configuration"}
    ):
        raise TypeError("Giving proof must name its snapshot and configuration.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_financial_report_v1(%s,%s::jsonb,%s,%s)::text",
            [
                campaign_id,
                json.dumps({"filters": query.form_values(), "proof": proof}),
                query.page,
                # SQL pages with this exact size, so the caller's next-page
                # arithmetic can never skip or repeat a Family.
                page_size,
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
        configuration, campaign_id=campaign_id, parish_name=parish_name
    )
    # Labels are versioned with the configuration each Family actually saw, so a
    # later edit of the year label or an option never rewords a retained answer.
    seen = {
        key: share_labels(values, campaign_id=campaign_id, parish_name=parish_name)
        for key, values in seen_configurations(
            campaign_id, {row["configuration_id"] for row in result["rows"]}
        ).items()
    }
    for row in result["rows"]:
        annual = parse_money(row["annual_pledge"])
        row["annual"] = annual
        row["installment"] = (
            installment(annual, row["frequency"])
            if row["frequency"] and annual.available
            else MoneyAmount(None)
        )
        row["frequency_label"] = FREQUENCY_LABELS[row["frequency"] or "none"]
        labels = seen.get(row.pop("configuration_id"), {})
        row["shares"] = [
            {"label": labels.get(key, "Unavailable share method"), "text": text}
            for key, text in in_offered_order(row["shares"], labels)
        ]
        row["source_pledge"] = parse_money(row.pop("pledge_total"))
        row["source_contributions"] = parse_money(row.pop("contribution_total"))
        for field in ("submitted_at", "first_submitted_at"):
            row[field] = datetime.fromisoformat(row[field])
    summary = result["summary"]
    summary["annual_total"] = parse_money(summary["annual_total"])
    summary["frequencies"] = [
        (FREQUENCY_LABELS[key], summary["frequencies"].get(key, 0))
        for key in (*FREQUENCIES, "none")
    ]
    # The summary spans every page, so it can only use the current wording;
    # options no longer offered are counted together rather than guessed.
    shares = {}
    for key, count in in_offered_order(summary["shares"], current):
        label = current.get(key, "Unavailable share method")
        shares[label] = shares.get(label, 0) + count
    summary["shares"] = list(shares.items())
    metadata = result["metadata"]
    metadata["source_as_of"] = datetime.fromisoformat(metadata["source_as_of"])
    metadata["giving_through"] = (
        date.fromisoformat(metadata["giving_through"])
        if metadata["giving_through"]
        else None
    )
    # The filter offers the current options in that same configured order.
    result["share_choices"] = list(current.items())
    return result
