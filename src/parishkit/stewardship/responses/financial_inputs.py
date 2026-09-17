"""Pinned, Family-only financial observations and campaign definition inputs.

An empty record set is zero only when its snapshot cursor proves that the
configured giving window was loaded. A later Family-only delta retains the
earlier giving observation; its newer watermark must not imply fresher money.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from parishkit.stewardship.reports.money import MoneyAmount, source_total
from parishkit.stewardship.source.cursors import SCHEMA, _parse
from parishkit.stewardship.source.windows import (
    GivingPeriod,
    RefreshWindow,
    refresh_window,
)
from parishkit.stewardship.web.presentation import campaign_year

from .financial import ShareOption


class InvalidFinancialSource(ValueError):
    """Unusable trusted inputs carry no household, transaction or amount values."""


@dataclass(frozen=True)
class FinancialDefinition:
    """Immutable relevant campaign fields, independent of unrelated mail settings."""

    window: RefreshWindow
    year_label: str
    options: tuple[ShareOption, ...]
    campaign_year: str

    @property
    def upcoming(self):
        """The new annual pledge applies to the configured upcoming period."""
        return self.window.periods[0]

    @property
    def comparison(self):
        """Existing source pledge/contributions use the explicitly mapped comparison."""
        return self.window.periods[1]


@dataclass(frozen=True)
class GivingObservation:
    """The last complete giving read, not a later Family-only refresh timestamp."""

    observed_at: datetime
    through_date: date


@dataclass(frozen=True)
class FinancialInputs:
    """Only scoped totals reach the form; individual transaction records never do."""

    family_duid: int
    definition: FinancialDefinition
    pledge: MoneyAmount
    contributions: MoneyAmount
    observation: GivingObservation | None

    def comparison(self):
        """Include every financial display/validation dependency in form admission."""
        return (
            self.family_duid,
            self.definition.window.document(),
            self.definition.year_label,
            self.definition.campaign_year,
            tuple(
                (row.id, row.label, row.free_text) for row in self.definition.options
            ),
            self.pledge.canonical,
            self.contributions.canonical,
            # A newer unchanged observation is display context, not a changed
            # pledge or contribution. The reviewed snapshot retains its as-of.
            self.observation is not None,
        )


def financial_definition(configuration, *, campaign_id):
    """Revalidate the bounded configuration before accepting financial form inputs."""
    try:
        # Draft and active campaigns have identical giving query scope; actual
        # lifecycle/session admission remains the caller's existing locked gate.
        window = refresh_window(
            campaign_id=campaign_id, state="draft", values=configuration
        )
        if len(window.periods) != 2:
            raise ValueError
        start, end = window.periods[0].start, window.periods[0].end
        year = configuration["year_label"] or (
            str(start.year) if start.year == end.year else f"{start.year}–{end.year}"
        )
        options = tuple(
            ShareOption(**value) for value in configuration["share_options"]
        )
        return FinancialDefinition(window, year, options, campaign_year(configuration))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidFinancialSource(
            "The financial form definition is unavailable."
        ) from None


def giving_observation(cursor, definition):
    """Return unavailable unless exact window and retained giving coverage agree."""
    if not isinstance(definition, FinancialDefinition):
        raise TypeError("A trusted financial definition is required.")
    if any(not period.funds for period in definition.window.periods):
        return None
    try:
        if type(cursor) is not dict or cursor.get("schema") != SCHEMA:
            return None
        load = cursor["load"]
        digest = definition.window.digest
        if (
            cursor["window_digest"] != digest
            or type(load) is not dict
            or load.get("schema") != "source-load-v1"
            or load.get("window_digest") != digest
            or str(UUID(cursor["full_snapshot_id"])) != cursor["full_snapshot_id"]
        ):
            return None
        full, watermark = _parse(cursor["full_started_at"]), _parse(cursor["watermark"])
        giving_day = date.fromisoformat(load["giving_as_of_date"])
        latest_day = date.fromisoformat(load["as_of_date"])
        if (
            full > watermark
            or giving_day.isoformat() != load["giving_as_of_date"]
            or latest_day.isoformat() != load["as_of_date"]
            or giving_day > latest_day
            # Source civil dates use parish time, so UTC midnight can differ
            # by one day. Never relabel a delta's timestamp as a new giving read.
            or abs((giving_day - full.date()).days) > 1
            or abs((latest_day - watermark.date()).days) > 1
        ):
            return None
        return GivingObservation(full, giving_day)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def family_total(records, period, *, family_duid, through_date=None):
    """Filter the mapped period, but reject foreign owners rather than hiding them."""
    if not isinstance(period, GivingPeriod) or type(family_duid) is not int:
        raise TypeError("A trusted Family and giving period are required.")
    amounts = []
    try:
        for row in records:
            if (
                type(row) is not dict
                or set(row)
                != {
                    "schema_version",
                    "family_key",
                    "fund_key",
                    "amount",
                    "effective_date",
                }
                or type(row["schema_version"]) is not int
                or row["schema_version"] != 1
                or row["family_key"] != str(family_duid)
            ):
                raise ValueError
            fund = row["fund_key"]
            if type(fund) is not str or not fund.isascii() or not fund.isdecimal():
                raise ValueError
            if not 0 < int(fund) < 2**31 or str(int(fund)) != fund:
                raise ValueError
            day = date.fromisoformat(row["effective_date"])
            if day.isoformat() != row["effective_date"]:
                raise ValueError
            if int(fund) not in period.funds or not period.start <= day <= period.end:
                continue
            if through_date is not None and day > through_date:
                continue
            amounts.append(row["amount"])
        return source_total(amounts, available=True)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise InvalidFinancialSource(
            "The Family financial source is unavailable."
        ) from None


def financial_inputs(definition, observation, *, family_duid, pledges, contributions):
    """Aggregate only a proven complete observation; missing coverage stays missing."""
    if (
        not isinstance(definition, FinancialDefinition)
        or type(family_duid) is not int
        or not 0 < family_duid < 2**31
        or (observation is not None and not isinstance(observation, GivingObservation))
    ):
        raise TypeError("Trusted financial definition and observation are required.")
    if observation is None:
        return FinancialInputs(
            family_duid, definition, MoneyAmount(None), MoneyAmount(None), None
        )
    return FinancialInputs(
        family_duid,
        definition,
        family_total(pledges, definition.comparison, family_duid=family_duid),
        family_total(
            contributions,
            definition.comparison,
            family_duid=family_duid,
            through_date=observation.through_date,
        ),
        observation,
    )
