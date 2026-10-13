"""The Campaign comparison page (#1018), as its view renders it.

Every figure is invented. ``/campaign-comparison`` compares day 12 of a
campaign with three imported years, one of them partial and one from the
old system (so its renewal is "—" with a reason); ``-empty`` has no
imported year and no Financial stewardship. The first is served at the
page's real address too (``PAGE``), since an in-place answer must come
from the page's own path, and Compare on day asks it for day 5
(``DAY_5``), which answers with the same campaign on day 5. ``-crowded``
has seven imported years whose final figures are close together, so their
end labels would collide at phone width unless they are spaced apart.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.comparison import (
    OTHER_ID_SYSTEM,
    Band,
    Comparison,
    Day,
    Distribution,
    Renewal,
    Year,
)
from parishkit.stewardship.reports.comparison_views import page_context

START = date(2054, 10, 4)
# The page's real address, and the one Compare on day asks for day 5 (the
# form's action and field).
PAGE = "/admin/reports/comparison/"
DAY_5 = PAGE + "?day=5"


def _series(active, anchors, last):
    """Days 1 to ``last``, straight between anchor days.

    ``anchors`` maps a day to (families, pledging, pledged); every day
    between two anchors is interpolated, so a day an anchor names has
    exactly its figures (the tiles' tests read day 12's).
    """
    days = sorted(anchors)
    series = []
    for number in range(1, last + 1):
        low = max(day for day in days if day <= number)
        high = min((day for day in days if day >= number), default=low)
        weight = 0 if high == low else (number - low) / (high - low)
        values = [
            a + (b - a) * Decimal(str(weight))
            for a, b in zip(
                map(Decimal, anchors[low]), map(Decimal, anchors[high]), strict=True
            )
        ]
        series.append(
            Day(
                number,
                START + timedelta(days=number - 1),
                int(values[0].to_integral_value(ROUND_HALF_UP)),
                active,
                int(values[1].to_integral_value(ROUND_HALF_UP)),
                values[2].quantize(Decimal("0.01"), ROUND_HALF_UP),
            )
        )
    return tuple(series)


def _distribution(*counts):
    """Bands of these counts, $300 per Family per band step."""
    bands = tuple(
        Band(index + 1, count, Decimal(300 * (index + 1) * count))
        for index, count in enumerate(counts)
    )
    total = sum((band.total for band in bands), Decimal(0))
    return Distribution(
        sum(counts),
        total,
        bands,
        (Decimal("400.00"), Decimal("1000.00"), Decimal("2200.50")),
    )


def _at(series, day, final):
    """A year's figures on ``day``: its own, its last carried, or None."""
    if day <= len(series):
        return series[day - 1]
    if final:
        last = series[-1]
        return Day(
            last.day,
            last.local_date,
            last.families,
            last.active,
            last.pledging,
            last.pledged,
            True,
        )
    return None


def _year(value, series, renewal, day, final=True):
    """An imported year with its data notes: three every year shares, one its own."""
    return Year(
        value,
        f"Stewardship {value}",
        current=False,
        final=final,
        start_date=START,
        days=len(series),
        at_day=_at(series, day, final),
        end=series[-1],
        renewal=renewal,
        distribution=_distribution(40, 60, 90, 70, 60, 30, 12, 4),
        notes=(
            ("Source system", "Invented old system"),
            ("Active Families counted from", "An invented snapshot"),
            ("note.01", "Converted from <three> invented exports"),
            ("note.02", f"Invented note for {value} only"),
        ),
        series=series,
    )


def _without_pledges(series):
    """The series as a campaign without Financial stewardship has it."""
    return tuple(
        Day(d.day, d.local_date, d.families, d.active, None, None) for d in series
    )


def comparison(*, imported=True, financial=True, day=12, crowded=False):
    """The page's figures on ``day`` (12, the last complete day, or earlier)."""
    years = ()
    if imported:
        years = (
            _year(
                2026,
                _series(
                    1500,
                    {
                        1: (20, 15, "20000"),
                        12: (240, 200, "300000"),
                        40: (900, 760, "1300000"),
                    },
                    40,
                ),
                Renewal(150, 800),
                day,
            ),
            _year(
                2025,
                _series(
                    1480,
                    {
                        1: (18, 14, "18000"),
                        12: (220, 190, "280000"),
                        40: (880, 740, "1250000"),
                    },
                    40,
                ),
                Renewal(140, 790),
                day,
            ),
            _year(
                2024,
                _series(
                    1460,
                    {1: (15, 12, "15000"), 9: (150, 120, "180000")},
                    9,
                ),
                Renewal(reason=OTHER_ID_SYSTEM),
                day,
                final=False,
            ),
        )
    if crowded:
        # Four more final years, each ending within a few Families and
        # dollars of the others.
        years += tuple(
            _year(
                value,
                _series(
                    1450,
                    {
                        1: (16, 12, "16000"),
                        12: (200 + offset, 170, "260000"),
                        38: (884 + offset, 745, str(1260000 + 1000 * offset)),
                    },
                    38,
                ),
                Renewal(130, 780),
                day,
            )
            for offset, value in enumerate((2023, 2022, 2021, 2020))
        )
    current_series = _series(
        1520, {1: (25, 22, "30000"), 12: (270, 230, "345000.50")}, 12
    )
    if not financial:
        current_series = _without_pledges(current_series)
        years = tuple(
            Year(
                year.year,
                year.name,
                current=False,
                final=year.final,
                start_date=year.start_date,
                days=year.days,
                at_day=year.at_day,
                end=year.end,
                renewal=year.renewal,
                notes=year.notes,
                series=_without_pledges(year.series),
            )
            for year in years
        )
    current = Year(
        2027,
        "Stewardship 2027",
        current=True,
        final=False,
        start_date=START,
        days=12,
        at_day=current_series[day - 1],
        end=current_series[-1],
        renewal=Renewal(180, 900) if imported else Renewal(reason="No past year."),
        distribution=_distribution(30, 50, 70, 50, 20, 6, 3, 1) if financial else None,
        series=current_series,
    )
    return Comparison(
        "Stewardship 2027",
        day,
        START + timedelta(days=day - 1),
        financial,
        "current",
        current,
        years,
        years[0] if years else None,
        latest_day=12,
    )


def components(context, admin):
    """The page with imported years, on day 5, crowded, and with none."""
    principal = Principal(uuid4(), frozenset({"administrator"}), frozenset())

    def page(found):
        """Render the page for ``found``."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-comparison.html",
                context | {"admin_chrome": admin} | page_context(found, principal),
            ),
        )

    return {
        "/campaign-comparison": page(comparison()),
        PAGE: page(comparison()),
        DAY_5: page(comparison(day=5)),
        "/campaign-comparison-crowded": page(comparison(crowded=True)),
        "/campaign-comparison-empty": page(comparison(imported=False, financial=False)),
    }
