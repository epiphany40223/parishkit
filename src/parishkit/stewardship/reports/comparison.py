"""The Campaign comparison report's figures (#1018, slice 3), without I/O.

The campaign-history spec ("Comparison report") defines them. Campaigns are
lined up by **campaign day**: day 1 is a campaign's start date (its first
invitation day), and the **comparison day** N is the current campaign's
last complete local day. At day N the report compares, for the current
campaign, last year and the average of every imported year:

- Families responded and their percentage of the active Families (each
  year's own denominator: this system's cohort, or an archive's measured
  ``active_families``);
- annual pledges and the average pledge (pledges ÷ Families pledging more
  than zero), with the median beside it;
- the renewal of the previous year's responding Families.

``comparison_reads`` reads the stored and live values into the dataclasses
here; this module averages, compares and formats them, and applies the
small-count protection to what leaves the Admin portal
(``outside_document``). Nothing here names or identifies a Family: every
value is an aggregate.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.presentation import number, percentage

CENT = Decimal("0.01")
TENTH = Decimal("0.1")
# A band, or a year's distribution, with fewer Families than this is
# withheld from anything that leaves the Admin portal (spec, "Small-count
# protection").
SMALL_COUNT = 5
FEWER_THAN = "fewer than 5"
# What a download or the PDF summary shows in place of a figure the
# small-count protection keeps inside the Admin portal (a withheld year's
# average pledge, or its quartiles under 5 pledging Families).
WITHHELD = "Withheld"
# What they show in place of quartiles that only the page shows: the
# current campaign's (they change day by day) and a partial year's (spec,
# "The current campaign stays inside the portal").
PAGE_ONLY = "On the page only"
# Why a figure shows "—" (spec, "Metrics": a toggletip gives the reason).
NO_LAST_YEAR = "No past campaign is imported for last year."
NO_FAMILY_ROWS = "Family rows were not kept for that year's import."
OTHER_ID_SYSTEM = (
    "That year's Families are identified differently (old-system ids, not "
    "ParishSoft DUIDs), so they cannot be matched."
)
NO_PREVIOUS_YEAR = "The year before it is not imported."
FINANCIAL_OFF = "This campaign does not include Financial stewardship."
OLDER_BANDS = (
    "Imported with an older pledge band set; import the year again to compare "
    "its bands."
)


@dataclass(frozen=True)
class Day:
    """One year's figures at the end of one campaign day.

    ``pledging`` and ``pledged`` are None when pledge figures are unknown
    (the Financial module is off, or a pledge was missing). ``carried`` marks
    a final year's last day shown for a later day ("(final)").
    """

    day: int
    local_date: date
    families: int
    active: int
    pledging: int | None
    pledged: Decimal | None
    carried: bool = False

    @property
    def percent(self):
        """Families responded as a share of the year's active Families."""
        return Percentage(self.families, self.active)

    @property
    def average(self):
        """Annual pledges ÷ Families pledging more than zero, or None."""
        if not self.pledging or self.pledged is None:
            return None
        return self.pledged / self.pledging


@dataclass(frozen=True)
class Renewal:
    """How many of the previous year's Families responded, or why unknown."""

    renewed: int | None = None
    base: int | None = None
    reason: str | None = None

    @property
    def percent(self):
        """The renewal as a Percentage, or None when it cannot be computed."""
        if self.renewed is None or not self.base:
            return None
        return Percentage(self.renewed, self.base)


@dataclass(frozen=True)
class Band:
    """One pledge band of a year: its Families and their total pledged."""

    band: int
    families: int
    total: Decimal


@dataclass(frozen=True)
class Distribution:
    """A year's pledge distribution, or the reason it cannot be shown.

    ``pledging`` and ``total`` are the Families pledging more than zero and
    their sum; ``quartiles`` the first quartile, median and third quartile
    (None with no pledging Family).
    """

    pledging: int
    total: Decimal
    bands: tuple = ()
    quartiles: tuple | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Year:
    """One campaign in the comparison: the current one or an imported year.

    ``at_day`` is its figures at the comparison day (None when unknown: a
    partial year after its last day, or before the current day 1 ends);
    ``end`` its final figures (for the current campaign, so far).
    ``series`` is every day it has figures for, in day order, which the
    day-by-day charts draw (the current campaign's through its latest
    complete day, whatever day is compared; its ``pledging`` is None, since
    the charts need only the fact rows' figures).
    """

    year: int | None
    name: str
    current: bool
    final: bool
    start_date: date
    days: int
    at_day: Day | None
    end: Day | None
    renewal: Renewal = field(default_factory=Renewal)
    distribution: Distribution | None = None
    notes: tuple = ()
    series: tuple = ()

    @property
    def label(self):
        """The year as rows and lines name it."""
        if self.current:
            return f"{self.name} (so far)"
        return str(self.year)


@dataclass(frozen=True)
class Comparison:
    """Every figure of the report at one comparison day.

    ``day`` is None before the current campaign's day 1 has ended, when only
    the imported years' final results are shown. ``imported`` is newest
    first; ``last_year`` is one of them or None. ``latest_day`` is the
    current comparison day, the last day Compare on day can choose (``day``
    is that day or an earlier one). ``checksums`` pairs each imported year
    with its file's SHA-256, which a digest's section records (slice 7).
    """

    campaign_name: str
    day: int | None
    report_date: date | None
    financial: bool
    status: str
    current: Year | None
    imported: tuple
    last_year: Year | None
    latest_day: int | None = None
    checksums: tuple = ()


# ---------------------------------------------------------------- metrics


@dataclass(frozen=True)
class Metric:
    """One headline figure: its key, label, kind and value of a year."""

    key: str
    label: str
    kind: str  # "count", "percent" or "money"
    financial: bool = False

    def value(self, year):
        """This figure for ``year`` at the comparison day, or None."""
        if self.key == "renewal":
            return year.renewal.percent
        day = year.at_day
        if day is None:
            return None
        return {
            "families": day.families,
            "percent": day.percent,
            "pledged": day.pledged,
            "average": day.average,
        }[self.key]


METRICS = (
    Metric("families", "Families responded", "count"),
    Metric("percent", "Percent of active Families", "percent"),
    Metric("pledged", "Annual pledges", "money", financial=True),
    Metric("average", "Average pledge", "money", financial=True),
    Metric("renewal", "Renewal of last year's responders", "percent"),
)


def number_of(value):
    """A comparable Decimal of a metric value (a Percentage's percent)."""
    if value is None:
        return None
    if isinstance(value, Percentage):
        return value.value
    return Decimal(value)


def average(metric, years):
    """The mean of ``metric`` over the years that have it, or None.

    Every imported year with a value at the comparison day counts; renewal
    needs at least two such years (spec, "Prior-years average").
    """
    values = [number_of(metric.value(year)) for year in years]
    values = [value for value in values if value is not None]
    if not values or (metric.key == "renewal" and len(values) < 2):
        return None
    with localcontext(prec=40):
        return sum(values) / len(values)


def change(kind, current, reference):
    """How ``current`` differs from ``reference``: (direction, amount) or None.

    Counts and money compare as a relative change in percent, percentages as
    percentage points. Direction is "more", "fewer" (or "less" for money) or
    "same"; the amount is a Decimal, positive.
    """
    current, reference = number_of(current), number_of(reference)
    if current is None or reference is None:
        return None
    with localcontext(prec=40):
        if kind == "percent":
            amount = current - reference
        elif reference == 0:
            return None
        else:
            amount = (current - reference) * 100 / reference
    rounded = amount.quantize(TENTH, ROUND_HALF_UP)
    if rounded == 0:
        return "same", Decimal(0)
    if rounded > 0:
        return "more", abs(amount)
    return ("less" if kind == "money" else "fewer"), abs(amount)


# --------------------------------------------------------------- formatting


def one_decimal(value):
    """A Decimal with one decimal place unless it is a whole number."""
    rounded = value.quantize(TENTH, ROUND_HALF_UP)
    return number(int(rounded) if rounded == int(rounded) else rounded)


def money_text(value):
    """US dollars and cents, US grouping ("$1,234.50")."""
    cents = Decimal(value).quantize(CENT, ROUND_HALF_UP)
    return ("-" if cents < 0 else "") + "$" + number(abs(cents))


def value_text(kind, value):
    """One metric value as the page shows it; "—" when unknown."""
    if value is None:
        return "—"
    if kind == "percent":
        if isinstance(value, Percentage):
            return percentage(value)
        return one_decimal(value) + "%"
    if kind == "money":
        return money_text(value)
    return number(value) if type(value) is int else one_decimal(Decimal(value))


ARROWS = {"more": "↑", "fewer": "↓", "less": "↓", "same": "→"}


def change_text(kind, current, reference):
    """ "↑ 12.5% more" or "↓ 4.2 points fewer"; None when not comparable."""
    found = change(kind, current, reference)
    if found is None:
        return None
    direction, amount = found
    if direction == "same":
        return "→ the same"
    unit = (
        f"{one_decimal(amount)} points"
        if kind == "percent"
        else f"{one_decimal(amount)}%"
    )
    if kind == "percent" and one_decimal(amount) == "1":
        unit = "1 point"
    return f"{ARROWS[direction]} {unit} {direction}"


def years_label(years):
    """ "2020–2026" or "2025" for the years an average covers."""
    numbers = sorted(year.year for year in years)
    if not numbers:
        return ""
    if numbers[0] == numbers[-1]:
        return str(numbers[0])
    return f"{numbers[0]}–{numbers[-1]}"


def data_notes(years):
    """The data notes consolidated: (shared, per-year) for the page.

    ``shared`` is the (label, text) pairs every imported year has alike, in
    the first year's order, shown once; there are none with fewer than two
    years, since one year's notes are its own. ``per_year`` is (year, pairs)
    for each year, newest first as given, with only the pairs not shared;
    a year with nothing of its own is left out. The Administrator asked for
    one section that states the common notes once (2026-10-13, #1161),
    instead of repeating them under every year.
    """
    if len(years) < 2:
        shared = ()
    else:
        common = set.intersection(*(set(year.notes) for year in years))
        shared = tuple(pair for pair in years[0].notes if pair in common)
    per_year = tuple(
        (year.year, own)
        for year in years
        if (own := tuple(pair for pair in year.notes if pair not in shared))
    )
    return shared, per_year


def tiles(comparison, *, outside=False):
    """The headline tiles, one per metric, in the spec's order.

    Each holds the current value and up to two comparison lines ("Last year
    on day 12: 240 (↑ 12.5% more)", "2020–2026 average: 220 (…)"); ``reason``
    explains a "—" current value. The page draws them as stat tiles
    (``comparison_views.headline_tiles``).

    ``outside`` gives the tiles as they may leave the Admin portal (the PDF
    summary): no median so far, since the current campaign's quartiles stay
    inside, and the small-count protection applied to the average pledge's
    comparisons (``withheld_years``, ``average_withheld``), which then read
    "Withheld".
    """
    current, day = comparison.current, comparison.day
    if current is None or day is None:
        return []
    averaged = [year for year in comparison.imported]
    withheld = withheld_years(comparison) if outside else set()
    result = []
    for metric in METRICS:
        if metric.financial and not comparison.financial:
            continue
        value = metric.value(current)
        lines = []
        shown = value_text(metric.kind, value)
        references = []
        if comparison.last_year is not None:
            last = comparison.last_year
            references.append(
                (
                    f"Last year on day {number(day)}",
                    WITHHELD
                    if metric.key == "average" and last.year in withheld
                    else metric.value(last),
                )
            )
        mean = average(metric, averaged)
        if mean is not None:
            covered = [y for y in averaged if metric.value(y) is not None]
            if average_withheld(metric, averaged, withheld):
                mean = WITHHELD
            references.append((f"{years_label(covered)} average", mean))
        for label, reference in references:
            if isinstance(reference, str):  # WITHHELD
                lines.append(f"{label}: {WITHHELD}")
                continue
            text = value_text(metric.kind, reference)
            difference = change_text(metric.kind, value, reference)
            lines.append(
                f"{label}: {text}" + (f" ({difference})" if difference else "")
            )
        reason = None
        if value is None:
            reason = _reason(metric, current, comparison)
        median = None
        if not outside and metric.key == "average" and current.distribution is not None:
            quartiles = current.distribution.quartiles
            if quartiles is not None:
                median = money_text(quartiles[1])
        result.append(
            {
                "key": metric.key,
                "label": metric.label,
                "value": shown,
                "median": median,
                "lines": lines,
                "reason": reason,
            }
        )
    return result


def _reason(metric, current, comparison):
    """Why the current campaign's ``metric`` is "—"."""
    if metric.key == "renewal":
        return current.renewal.reason
    if metric.financial and not comparison.financial:
        return FINANCIAL_OFF
    return "No figure is available for this day yet."


def table_rows(comparison):
    """The "Every number" table: one row per year, the current campaign first.

    Every cell is display text; "—" marks an unknown value, and a final
    year's value carried past its last day is marked "(final)".
    """
    years = ([comparison.current] if comparison.current else []) + list(
        comparison.imported
    )
    rows = []
    for year in years:
        at, end = year.at_day, year.end

        def cell(kind, day, key):
            """One figure of ``day``, with the carried-forward mark."""
            value = None if day is None else getattr(day, key)
            text = value_text(kind, value)
            if day is not None and day.carried and value is not None:
                text += " (final)"
            return text

        quartiles = year.distribution.quartiles if year.distribution else None
        rows.append(
            {
                "year": year.label,
                "current": year.current,
                "day_1": format_date(year.start_date, compact=True),
                "days": number(year.days),
                "families": cell("count", at, "families"),
                "percent": cell("percent", at, "percent"),
                "pledged": cell("money", at, "pledged"),
                "average": cell("money", at, "average"),
                "renewal": value_text("percent", year.renewal.percent),
                "final_families": cell("count", end, "families"),
                "final_percent": cell("percent", end, "percent"),
                "final_pledged": cell("money", end, "pledged"),
                "final_average": cell("money", end, "average"),
                "quartiles": [
                    money_text(value) if quartiles else "—"
                    for value in (quartiles or (None, None, None))
                ],
            }
        )
    return rows


def distribution_rows(comparison, edges):
    """The pledge-distribution tables: per year, its bands and quartile line.

    ``edges`` are the band set's upper edges (``archive.workbook.BAND_EDGES``).
    The Admin portal page shows every figure; the small-count protection is
    applied only to outside outputs (``protected``).
    """
    years = ([comparison.current] if comparison.current else []) + list(
        comparison.imported
    )
    result = []
    for year in years:
        found = year.distribution
        if found is None:
            continue
        entry = {"year": year.label, "reason": found.reason, "bands": []}
        if found.reason is None:
            for band in found.bands:
                entry["bands"].append(
                    {
                        "label": band_label(band.band, edges),
                        "families": number(band.families),
                        "family_share": share_text(band.families, found.pledging),
                        "total": money_text(band.total),
                        "total_share": share_text(band.total, found.total),
                    }
                )
            quartiles = found.quartiles
            entry["quartiles"] = (
                [money_text(value) for value in quartiles] if quartiles else None
            )
            entry["mean"] = (
                money_text(found.total / found.pledging) if found.pledging else "—"
            )
            entry["pledging"] = number(found.pledging)
        result.append(entry)
    return result


def band_label(band, edges):
    """A band's dollar range in words, such as "$250 to under $500"."""

    def whole(value):
        """A whole-dollar edge with US grouping."""
        return "$" + number(int(value))

    if band == 1:
        return f"Under {whole(edges[0])}"
    if band > len(edges):
        return f"{whole(edges[-1])} and more"
    return f"{whole(edges[band - 2])} to under {whole(edges[band - 1])}"


def share_text(part, whole):
    """``part`` as a percentage of ``whole``, one decimal; "—" for zero."""
    if not whole:
        return "—"
    with localcontext(prec=40):
        return one_decimal(Decimal(part) * 100 / Decimal(whole)) + "%"


# ------------------------------------------------- outside the Admin portal


def protected(distribution):
    """A final year's distribution as it may leave the Admin portal.

    Applies the small-count protection (spec, "Pledge distribution"): a band
    of fewer than 5 Families shows "fewer than 5" and no total; when any is
    withheld, so are its neighbour band (the next lower, or the next higher
    for the lowest), the year's pledging-Family count, every average and
    every share of pledging Families. The year's total and the visible
    bands' shares of it stay. Under 5 pledging Families, nothing but the
    total is shown. Returns (document, withheld?).
    """
    if distribution.pledging < SMALL_COUNT:
        return {
            "pledging_families": FEWER_THAN,
            "annual_pledged": str(distribution.total),
            "quartiles": None,
            "bands": None,
        }, True
    small = {band.band for band in distribution.bands if band.families < SMALL_COUNT}
    hidden = set(small)
    numbers = [band.band for band in distribution.bands]
    for band in small:
        index = numbers.index(band)
        # The next lower band, or the next higher one for the lowest.
        hidden.add(numbers[index - 1] if index > 0 else numbers[1])
    withheld = bool(small)
    bands = []
    for band in distribution.bands:
        if band.band in hidden:
            bands.append(
                {
                    "band": band.band,
                    "families": FEWER_THAN if band.band in small else None,
                    "family_share": None,
                    "total": None,
                    "total_share": None,
                }
            )
            continue
        bands.append(
            {
                "band": band.band,
                "families": band.families,
                "family_share": None
                if withheld
                else share_text(band.families, distribution.pledging),
                "total": str(band.total),
                "total_share": share_text(band.total, distribution.total),
            }
        )
    quartiles = distribution.quartiles
    return {
        "pledging_families": None if withheld else distribution.pledging,
        "annual_pledged": str(distribution.total),
        "quartiles": [str(value) for value in quartiles] if quartiles else None,
        "bands": bands,
    }, withheld


def outside_distribution(comparison, year):
    """``year``'s distribution as it may leave the portal: (document, withheld?).

    Only a final imported year's distribution leaves the Admin portal (spec,
    "The current campaign stays inside the portal"), and only with Financial
    stewardship; any other year is (None, False).
    """
    found = year.distribution
    if (
        comparison.financial
        and not year.current
        and year.final
        and found is not None
        and found.reason is None
    ):
        return protected(found)
    return None, False


def withheld_years(comparison):
    """The imported years whose pledging count and averages stay inside.

    A year with a withheld band (or fewer than 5 pledging Families) withholds
    its number of pledging Families and every average pledge outside the
    portal, at any day, since the year's total divided by its average gives
    that number (spec, "Small-count protection"). A final year with fewer
    than 5 pledging Families is withheld whatever its distribution's
    ``reason`` (an older band set), since its count alone is small enough
    to single Families out.
    """

    def small(year):
        """A final imported year with fewer than 5 pledging Families."""
        found = year.distribution
        return (
            comparison.financial
            and not year.current
            and year.final
            and found is not None
            and found.pledging < SMALL_COUNT
        )

    return {
        year.year
        for year in comparison.imported
        if small(year) or outside_distribution(comparison, year)[1]
    }


def average_withheld(metric, years, withheld):
    """Whether ``metric``'s prior-years average must stay inside the portal.

    The average pledge averaged over a year whose averages are withheld would
    give that year's average back from the other years' (and so its number of
    pledging Families), so the whole average is withheld outside.
    """
    return metric.key == "average" and any(
        year.year in withheld and metric.value(year) is not None for year in years
    )


def _day_document(day, *, withhold_average, financial=True):
    """One year's figures at a day, as the command prints them.

    Without Financial stewardship no pledge figure is printed; a year whose
    pledge bands are withheld prints no pledging-Family count or average.
    """
    if day is None:
        return None
    if not financial:
        day = Day(
            day.day, day.local_date, day.families, day.active, None, None, day.carried
        )
    average_value = day.average
    return {
        "day": day.day,
        "date": day.local_date.isoformat(),
        "families": day.families,
        "active_families": day.active,
        "percent": None
        if day.percent.value is None
        else one_decimal(day.percent.value),
        "annual_pledged": None if day.pledged is None else str(day.pledged),
        "pledging_families": None if withhold_average else day.pledging,
        "average_pledge": None
        if withhold_average or average_value is None
        else str(average_value.quantize(CENT, ROUND_HALF_UP)),
        "final_carried": day.carried,
    }


def outside_document(comparison):
    """The report as JSON-ready values for ``pk-admin report comparison``.

    Aggregates only, with the small-count protection applied, since command
    output leaves the Admin portal: no note text, no Family value, and no
    distribution or quartile for the current campaign (they change day by
    day, and two days' difference could expose one Family's pledge).
    Distributions and quartiles appear only for final imported years.
    """
    withheld = withheld_years(comparison)
    years = []
    for year in comparison.imported:
        distribution, _ = outside_distribution(comparison, year)
        withhold = year.year in withheld
        financial = comparison.financial
        years.append(
            {
                "year": year.year,
                "campaign": year.name,
                "final": year.final,
                "day_1": year.start_date.isoformat(),
                "days": year.days,
                "at_day": _day_document(
                    year.at_day, withhold_average=withhold, financial=financial
                ),
                "final_figures": _day_document(
                    year.end, withhold_average=withhold, financial=financial
                ),
                "renewal": _renewal_document(year.renewal),
                "distribution": distribution,
            }
        )
    current = comparison.current
    averages = {}
    for metric in METRICS:
        if metric.financial and not comparison.financial:
            continue
        counted = list(comparison.imported)
        if average_withheld(metric, counted, withheld):
            averages[metric.key] = None
            continue
        mean = average(metric, counted)
        places = CENT if metric.kind == "money" else TENTH
        averages[metric.key] = (
            None if mean is None else str(mean.quantize(places, ROUND_HALF_UP))
        )
    return {
        "day": comparison.day,
        "date": comparison.report_date.isoformat() if comparison.report_date else None,
        "status": comparison.status,
        "financial_enabled": comparison.financial,
        "last_year": comparison.last_year.year if comparison.last_year else None,
        "current": None
        if current is None
        else {
            "campaign": current.name,
            "day_1": current.start_date.isoformat(),
            "days": current.days,
            "at_day": _day_document(
                current.at_day, withhold_average=False, financial=comparison.financial
            ),
            "so_far": _day_document(
                current.end, withhold_average=False, financial=comparison.financial
            ),
            "renewal": _renewal_document(current.renewal),
        },
        "years": years,
        "averages": averages,
    }


def _renewal_document(renewal):
    """A renewal's counts, or the reason it is unknown."""
    return {
        "renewed": renewal.renewed,
        "previous_families": renewal.base,
        "percent": None
        if renewal.percent is None or renewal.percent.value is None
        else one_decimal(renewal.percent.value),
        "reason": renewal.reason,
    }
