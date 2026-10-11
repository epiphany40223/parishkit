"""The Campaign comparison's charts (#1018, slice 5) without a browser.

Each chart is a ``ChartDocument`` built from a ``comparison.Comparison``:
these tests pin what the lines, bars and stacked bars carry, the styles that
tell the years apart, the end labels' spacing, the summaries and the
exact-values tables, and that every spec compiles and renders with the
chart engine's own Vega-Lite (vl-convert). Every value is invented.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
import vl_convert

from parishkit.stewardship.archive.workbook import BAND_EDGES
from parishkit.stewardship.reports import comparison_charts as charts
from parishkit.stewardship.reports.comparison import (
    OLDER_BANDS,
    Band,
    Comparison,
    Day,
    Distribution,
    Renewal,
    Year,
)
from parishkit.stewardship.web.design_tokens import COLORS

START = date(2054, 10, 4)


def series(families, *, active=100, pledged=None):
    """Days 1 to len(families), each with its cumulative Families."""
    return tuple(
        Day(
            number,
            START + timedelta(days=number - 1),
            count,
            active,
            None if pledged is None else count,
            None if pledged is None else Decimal(pledged * count),
        )
        for number, count in enumerate(families, start=1)
    )


def bands(*counts):
    """A distribution with these band counts, $100 per Family per band."""
    rows = tuple(
        Band(index + 1, count, Decimal(100 * (index + 1) * count))
        for index, count in enumerate(counts)
    )
    total = sum((band.total for band in rows), Decimal(0))
    return Distribution(sum(counts), total, rows, (Decimal(1), Decimal(2), Decimal(3)))


def year(value, days, *, final=True, distribution=None, current=False):
    """An imported year (or the current campaign) with its series."""
    return Year(
        value,
        f"Stewardship {value}",
        current=current,
        final=final,
        start_date=START,
        days=len(days),
        at_day=None,
        end=days[-1] if days else None,
        renewal=Renewal(),
        distribution=distribution,
        series=days,
    )


def comparison(*, financial=True, day=3, imported=None, current=None):
    """A comparison on ``day`` of the given years (newest imported first)."""
    imported = (
        imported
        if imported is not None
        else (
            year(
                2054,
                series([10, 20, 30, 40, 50], pledged=100),
                distribution=bands(5, 6),
            ),
            year(2053, series([5, 10], pledged=100), final=False),
            year(2052, series([8, 16, 24], pledged=100), distribution=bands(1, 9)),
        )
    )
    current = (
        current
        if current is not None
        else year(
            2055,
            series([12, 24, 36], pledged=200),
            final=False,
            current=True,
            distribution=bands(3, 4),
        )
    )
    return Comparison(
        "Stewardship 2055",
        day,
        START + timedelta(days=(day or 1) - 1),
        financial,
        "current",
        current,
        tuple(imported),
        imported[0] if imported else None,
        latest_day=3,
    )


def chart_by_key(documents, key):
    """The chart whose key is ``comparison-<key>``."""
    (found,) = [chart for chart in documents if chart.key == f"comparison-{key}"]
    return found


def rows_of(spec, line):
    """The data rows of one drawn line."""
    return [row for row in spec["data"]["values"] if row["line"] == line]


# --------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("largest", "top"),
    [(0, 1.0), (7, 8), (36, 40), (0.178, 0.2), (1_300_000, 1_500_000), (100, 100)],
)
def test_nice_top_is_a_round_ceiling(largest, top):
    """1, 2, 2.5 or 5 times a power of ten, at or above the largest value."""
    assert charts.nice_top(largest) == pytest.approx(top)


def test_spread_keeps_order_and_a_gap_inside_the_plot():
    """Labels too close move apart; a stack at the bottom moves up."""
    placed = charts.spread([100, 101, 102, 50])
    assert placed[3] == 50
    assert placed[:3] == sorted(placed[:3])
    ordered = sorted(placed)
    assert all(b - a >= charts.LABEL_GAP for a, b in pairwise(ordered))
    bottom = charts.spread([charts.HEIGHT] * 4)
    assert max(bottom) == charts.HEIGHT
    assert min(bottom) == charts.HEIGHT - 3 * charts.LABEL_GAP
    top = charts.spread([0, 0])
    assert top == [charts.LABEL_GAP / 2, charts.LABEL_GAP * 1.5]


def test_short_labels_name_partial_and_current_years():
    """A partial year and the current campaign say so wherever they are named."""
    built = comparison()
    assert charts.short_label(built.current) == "2055 (so far)"
    assert charts.short_label(built.imported[1]) == "2053 (partial)"
    assert charts.short_label(built.imported[0]) == "2054"
    assert charts.short_band_label(1, BAND_EDGES) == "Under $250"
    assert charts.short_band_label(4, BAND_EDGES) == "$1,000–1,500"
    assert charts.short_band_label(8, BAND_EDGES) == "$10,000 or more"


# ---------------------------------------------------------- day by day


def test_day_charts_style_each_role_and_carry_final_years():
    """Current thick accent, last year gold dashed, average dark dash-dotted,
    others thin grey; a final year carries to the last day, a partial stops."""
    families = chart_by_key(charts.day_charts(comparison()), "families")
    spec = families.spec
    lines = [layer for layer in spec["layer"] if layer["mark"]["type"] == "line"]
    scale = lines[0]["encoding"]
    domain = scale["color"]["scale"]["domain"]
    assert domain == [
        "2055 (so far)",
        "2054 (last year)",
        "Prior-years average",
        "Other years",
    ]
    assert scale["color"]["scale"]["range"] == [
        COLORS["accent"],
        COLORS["gold"],
        COLORS["ink"],
        COLORS["control-border"],
    ]
    assert scale["strokeWidth"]["scale"] == {
        "domain": domain,
        "range": [3, 2, 2, 1],
        "type": "ordinal",
    }
    assert scale["strokeDash"]["scale"]["range"] == [
        [1, 0],
        [6, 3],
        [8, 3, 2, 3],
        [1, 0],
    ]
    # Drawn bottom to top: the grey years under the highlighted lines.
    assert [layer["transform"][0]["filter"]["equal"] for layer in lines] == [
        "other",
        "average",
        "last",
        "current",
    ]
    # 2052 ended on day 3 and is carried, flat, to day 5, marked final.
    carried = rows_of(spec, "year-2052")
    assert [(row["day"], row["value"]) for row in carried] == [
        (1, 8),
        (2, 16),
        (3, 24),
        (5, 24),
    ]
    assert carried[-1]["shown"] == "24 (final)" and carried[-1]["date"] == "—"
    assert carried[0]["date"] == "Oct 4, 2054"
    # The partial 2053 stops on its last day.
    assert [row["day"] for row in rows_of(spec, "year-2053")] == [1, 2]
    # The comparison day is marked, its label away from the end labels.
    rule = [layer for layer in spec["layer"] if layer["mark"]["type"] == "rule"]
    assert rule[0]["data"]["values"][0]["day"] == 3


def test_average_counts_years_with_a_value_and_carries_final_ones():
    """Day 2: all three years; day 4: 2054 and the carried 2052, not 2053."""
    spec = chart_by_key(charts.day_charts(comparison()), "families").spec
    average = {row["day"]: row for row in rows_of(spec, "average")}
    # Plotted values are rounded past what a pixel shows.
    assert average[2]["value"] == pytest.approx((20 + 10 + 16) / 3, abs=1e-4)
    assert average[4]["value"] == pytest.approx((40 + 24) / 2)
    assert average[2]["shown"] == "15.3" and average[4]["shown"] == "32"
    assert average[2]["date"] == "—"


def test_percent_and_pledge_charts_plot_their_measures():
    """Percentages are fractions on a percent axis; pledges are dollars."""
    built = charts.day_charts(comparison())
    percent = chart_by_key(built, "percent").spec
    assert rows_of(percent, "current")[0]["value"] == pytest.approx(0.12)
    assert rows_of(percent, "current")[0]["shown"] == "12%"
    assert percent["layer"][0]["encoding"]["y"]["axis"]["format"] == ".0%"
    pledged = chart_by_key(built, "pledged")
    assert rows_of(pledged.spec, "current")[2]["shown"] == "$7,200.00"
    assert pledged.notes == (charts.NOMINAL_DOLLARS,)


def test_end_labels_never_sit_closer_than_a_line():
    """Grey years ending on the same value are spaced in pixels."""
    imported = tuple(
        year(value, series([10, 20, 30]), distribution=None)
        for value in (2054, 2053, 2052, 2051, 2050)
    )
    built = comparison(imported=imported)
    spec = chart_by_key(charts.day_charts(built), "families").spec
    (labels,) = [
        layer["data"]["values"]
        for layer in spec["layer"]
        if layer["mark"]["type"] == "text" and layer["mark"].get("dx") == 4
    ]
    assert sorted(label["text"] for label in labels) == ["2050", "2051", "2052", "2053"]
    top = spec["layer"][0]["encoding"]["y"]["scale"]["domain"][1]
    pixels = sorted(charts.HEIGHT * (1 - label["value"] / top) for label in labels)
    assert all(b - a >= charts.LABEL_GAP - 1e-6 for a, b in pairwise(pixels))


def test_day_summary_and_table_read_every_value():
    """The summary names day N's figures; the table every day of every line."""
    families = chart_by_key(charts.day_charts(comparison()), "families")
    assert families.summary == (
        "Families responded by campaign day, day 1 being each campaign's first "
        "invitation day. On day 3: 2055 (so far) 36; 2054 (last year) 30; "
        "2052–2054 average 27."
    )
    table = families.table
    assert table.headings == (
        "Day",
        "2055 (so far)",
        "2054 (last year)",
        "Prior-years average",
        "2052",
        "2053 (partial)",
    )
    assert table.rows[3] == ("4", "—", "40", "32", "24 (final)", "—")
    assert len(table.rows) == 5


def test_the_summary_names_the_years_averaged_on_the_day():
    """Like the tiles: a partial year past its last day is not averaged."""
    built = comparison()
    measure = charts.MEASURES[0]
    assert [y.year for y in charts.covered(measure, built.imported, 2)] == [
        2054,
        2053,
        2052,
    ]
    # Day 4: 2053 (partial, two days) is out; 2052 (final, three) carried.
    assert [y.year for y in charts.covered(measure, built.imported, 4)] == [
        2054,
        2052,
    ]
    built = comparison(
        imported=(
            year(2054, series([10, 20, 30], pledged=100)),
            year(2052, series([5, 10], pledged=100), final=False),
        )
    )
    summary = chart_by_key(charts.day_charts(built), "families").summary
    assert summary.endswith("2054 (last year) 30; 2054 average 30.")


def test_a_day_without_figures_breaks_the_line():
    """A missing day inside a line is a null point and "—" in the table."""
    days = series([12, 24, 36])
    current = year(2055, (days[0], days[2]), final=False, current=True)
    built = comparison(current=current)
    families = chart_by_key(charts.day_charts(built), "families")
    rows = rows_of(families.spec, "current")
    assert [(row["day"], row["value"], row["shown"]) for row in rows] == [
        (1, 12, "12"),
        (2, None, "—"),
        (3, 36, "36"),
    ]
    line = [x for x in families.spec["layer"] if x["mark"]["type"] == "line"][0]
    assert line["mark"]["invalid"] == "break-paths-filter-domains"
    assert families.table.rows[1][1] == "—"
    # The summary still reads day 3's value.
    assert "2055 (so far) 36" in families.summary


def test_end_labels_stay_below_the_day_label():
    """A label at the top moves below the "Day N" label's line."""
    assert charts.spread([0], floor=charts.DAY_LABEL_FLOOR) == [charts.DAY_LABEL_FLOOR]
    built = comparison(
        imported=(
            year(2054, series([10, 20, 30])),
            year(2053, series([10, 40]), final=False),
        )
    )
    spec = chart_by_key(charts.day_charts(built), "families").spec
    (labels,) = [
        layer["data"]["values"]
        for layer in spec["layer"]
        if layer["mark"]["type"] == "text" and layer["mark"].get("dx") == 4
    ]
    top = spec["layer"][0]["encoding"]["y"]["scale"]["domain"][1]
    (label,) = labels
    assert charts.HEIGHT * (1 - label["value"] / top) >= charts.DAY_LABEL_FLOOR - 1e-6


def test_no_complete_day_draws_the_imported_years_without_a_rule():
    """Before day 1 ends: the imported lines, no comparison day and no rule."""
    built = comparison(day=None, current=year(2055, (), final=False, current=True))
    families = chart_by_key(charts.day_charts(built), "families")
    assert not [
        layer for layer in families.spec["layer"] if layer["mark"]["type"] == "rule"
    ]
    assert families.summary.endswith("This campaign has no complete day yet.")
    assert "2055 (so far)" not in families.table.headings


def test_without_financial_stewardship_no_pledge_chart():
    """Pledge charts and pledge bands are left out with the module off."""
    built = comparison(financial=False)
    assert [chart.key for chart in charts.day_charts(built)] == [
        "comparison-families",
        "comparison-percent",
    ]
    assert [chart.key for chart in charts.final_charts(built)] == [
        "comparison-final-families"
    ]
    assert charts.distribution_charts(built, BAND_EDGES) == []


def test_nothing_to_draw_draws_nothing():
    """No imported year and no complete day: no chart at all."""
    built = comparison(
        day=None, imported=(), current=year(2055, (), final=False, current=True)
    )
    assert charts.day_charts(built) == []
    assert charts.final_charts(built) == []
    assert charts.distribution_charts(built, BAND_EDGES) == []


# --------------------------------------------------------- final results


def test_final_bars_mark_the_current_campaign_so_far_and_lighter():
    """One bar per year, oldest first; the current one lighter, "so far"."""
    final = chart_by_key(charts.final_charts(comparison()), "final-families")
    rows = final.spec["data"]["values"]
    assert [row["name"] for row in rows] == [
        "2052",
        "2053 (partial)",
        "2054",
        "2055 (so far)",
    ]
    assert [row["opacity"] for row in rows] == [1, 1, 1, 0.35]
    assert rows[-1]["when"] == "So far (day 3)" and rows[1]["when"] == "Partial (day 2)"
    # A long bar's label sits inside it, white on the dark bar but ink on
    # the current campaign's light one; a short bar's label sits after it.
    assert [(row["inside"], row["ink"]) for row in rows] == [
        (False, COLORS["ink"]),
        (False, COLORS["ink"]),
        (True, "#ffffff"),
        (True, COLORS["ink"]),
    ]
    assert final.table.rows[0] == ("2052", "Final", "24")
    money = chart_by_key(charts.final_charts(comparison()), "final-pledged")
    assert money.table.rows[-1] == ("2055 (so far)", "So far (day 3)", "$7,200.00")


# ---------------------------------------------------- pledge distribution


def test_band_charts_stack_shares_from_the_smallest_band():
    """Per year, shares of Families and of dollars by band, adding to 100%."""
    found = charts.distribution_charts(comparison(), BAND_EDGES)
    families, dollars = found
    assert families.key == "comparison-bands-families"
    rows = families.spec["data"]["values"]
    # The current campaign's bands too: this chart stays in the portal.
    assert {row["name"] for row in rows} == {"2052", "2054", "2055 (so far)"}
    for name in ("2052", "2054", "2055 (so far)"):
        shares = [row["share"] for row in rows if row["name"] == name]
        assert sum(shares) == pytest.approx(1)
        assert len(shares) == len(BAND_EDGES) + 1
    current = [row for row in rows if row["name"] == "2055 (so far)"]
    assert current[0]["shown"] == "42.9%" and current[0]["families"] == "3"
    assert families.spec["encoding"]["order"]["field"] == "band"
    assert families.spec["encoding"]["color"]["scale"]["range"] == list(
        charts.BAND_COLORS
    )
    # Dollars: band 1 of 2052 holds $100 of $1,900.
    assert dollars.table.headings[1] == "Under $250"
    assert dollars.table.rows[0][:3] == ("2052", "5.3%", "94.7%")
    assert dollars.notes == (charts.NOMINAL_DOLLARS,)


def test_band_charts_leave_out_a_year_with_older_bands():
    """A year imported with an older band set has no bars, only its reason."""
    older = year(
        2054,
        series([10, 20], pledged=100),
        distribution=Distribution(10, Decimal(1000), (), None, OLDER_BANDS),
    )
    built = comparison(imported=(older,))
    rows = charts.distribution_charts(built, BAND_EDGES)[0].spec["data"]["values"]
    assert {row["name"] for row in rows} == {"2055 (so far)"}


# ----------------------------------------------------------- the engine


def test_every_spec_is_json_compiles_and_renders():
    """The chart engine's Vega-Lite compiles each spec and draws it, wide
    and narrow, with no container (the server renderer's case)."""
    built = comparison()
    documents = (
        charts.day_charts(built)
        + charts.final_charts(built)
        + charts.distribution_charts(built, BAND_EDGES)
    )
    assert len(documents) == 7
    for document in documents:
        text = json.dumps(document.spec)
        vl_convert.vegalite_to_vega(text)
        for width in (charts.WIDTH, 260):
            svg = vl_convert.vegalite_to_svg(
                json.dumps(document.spec | {"width": width})
            )
            assert svg.startswith("<svg")
        assert document.spec["description"] == document.summary
        assert all(
            len(row) == len(document.table.headings) for row in document.table.rows
        )
