"""The Campaign comparison page (#1018), as its view renders it.

Every figure is invented. ``/campaign-comparison`` compares day 12 of a
campaign with three imported years, one of them partial and one from the
old system (so its renewal is "—" with a reason); ``-empty`` has no
imported year and no Financial stewardship.
"""

from datetime import date
from decimal import Decimal
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


def _day(number, families, active, pledging, pledged, carried=False):
    """One year's figures at a day."""
    return Day(number, START, families, active, pledging, Decimal(pledged), carried)


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


def _year(value, at_day, end, renewal, final=True):
    """An imported year with its data notes."""
    return Year(
        value,
        f"Stewardship {value}",
        current=False,
        final=final,
        start_date=START,
        days=40,
        at_day=at_day,
        end=end,
        renewal=renewal,
        distribution=_distribution(40, 60, 90, 70, 60, 30, 12, 4),
        notes=(
            ("Source system", "Invented old system"),
            ("Active Families counted from", "An invented snapshot"),
            ("note.01", "Converted from <three> invented exports"),
        ),
    )


def comparison(*, imported=True, financial=True):
    """The page's figures on day 12."""
    years = (
        (
            _year(
                2026,
                _day(12, 240, 1500, 200, "300000"),
                _day(40, 900, 1500, 760, "1300000"),
                Renewal(150, 800),
            ),
            _year(
                2025,
                _day(12, 220, 1480, 190, "280000"),
                _day(40, 880, 1480, 740, "1250000"),
                Renewal(140, 790),
            ),
            _year(
                2024,
                None,
                _day(9, 150, 1460, 120, "180000"),
                Renewal(reason=OTHER_ID_SYSTEM),
                final=False,
            ),
        )
        if imported
        else ()
    )
    current = Year(
        2027,
        "Stewardship 2027",
        current=True,
        final=False,
        start_date=START,
        days=12,
        at_day=_day(12, 270, 1520, 230, "345000.50"),
        end=_day(12, 270, 1520, 230, "345000.50"),
        renewal=Renewal(180, 900) if imported else Renewal(reason="No past year."),
        distribution=_distribution(30, 50, 70, 50, 20, 6, 3, 1) if financial else None,
    )
    return Comparison(
        "Stewardship 2027",
        12,
        date(2054, 10, 15),
        financial,
        "current",
        current,
        years,
        years[0] if years else None,
    )


def components(context, admin):
    """The page with imported years, and with none."""
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
        "/campaign-comparison-empty": page(comparison(imported=False, financial=False)),
    }
