"""The Campaign comparison report's figures without a database (#1018).

Day alignment, averages, comparisons, formatting and the small-count
protection of what leaves the Admin portal. Every value is invented.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.archive.workbook import BAND_EDGES
from parishkit.stewardship.reports import comparison_reads
from parishkit.stewardship.reports.comparison import (
    FEWER_THAN,
    METRICS,
    NO_FAMILY_ROWS,
    NO_LAST_YEAR,
    OLDER_BANDS,
    OTHER_ID_SYSTEM,
    Band,
    Comparison,
    Day,
    Distribution,
    Renewal,
    Year,
    average,
    band_label,
    change,
    change_text,
    distribution_rows,
    outside_document,
    protected,
    table_rows,
    tiles,
    value_text,
)

START = date(2054, 10, 4)
METRIC = {metric.key: metric for metric in METRICS}


def day(number, families, active=100, pledging=None, pledged=None, carried=False):
    """One year's figures at a day."""
    return Day(
        number,
        date(2054, 10, number),
        families,
        active,
        pledging,
        None if pledged is None else Decimal(pledged),
        carried,
    )


def year(value, at_day, *, end=None, renewal=None, distribution=None, final=True):
    """An imported year."""
    return Year(
        value,
        f"Stewardship {value}",
        current=False,
        final=final,
        start_date=START,
        days=20,
        at_day=at_day,
        end=end or at_day,
        renewal=renewal or Renewal(),
        distribution=distribution,
    )


def bands(*counts):
    """A distribution with these band counts, $100 per Family per band."""
    rows = tuple(
        Band(index + 1, count, Decimal(100 * (index + 1) * count))
        for index, count in enumerate(counts)
    )
    return Distribution(
        sum(counts),
        sum((row.total for row in rows), Decimal(0)),
        rows,
        (Decimal("100.00"), Decimal("200.00"), Decimal("300.00")),
    )


# ------------------------------------------------------------- comparisons


@pytest.mark.parametrize(
    "kind, current, reference, expected",
    [
        ("count", 270, 240, "↑ 12.5% more"),
        ("count", 200, 240, "↓ 16.7% fewer"),
        ("money", Decimal("90"), Decimal("100"), "↓ 10% less"),
        ("percent", Decimal("54.2"), Decimal("50"), "↑ 4.2 points more"),
        ("percent", Decimal("49"), Decimal("50"), "↓ 1 point fewer"),
        ("count", 240, 240, "→ the same"),
        ("count", 5, 0, None),
        ("count", None, 5, None),
    ],
)
def test_counts_and_money_change_relatively_and_percentages_in_points(
    kind, current, reference, expected
):
    """Relative change for counts and money, points for percentages."""
    assert change_text(kind, current, reference) == expected


def test_change_never_relies_on_color_alone():
    """Every comparison has an arrow and a word."""
    direction, amount = change("percent", Decimal(60), Decimal(50))
    assert direction == "more" and amount == 10


def test_average_counts_the_years_with_a_value_and_renewal_needs_two():
    """A partial year without a day-N value is left out of the average."""
    years = [year(2054, day(3, 30)), year(2053, day(3, 10)), year(2052, None)]
    assert average(METRIC["families"], years) == 20
    one = [year(2054, day(3, 1), renewal=Renewal(1, 2))]
    assert average(METRIC["renewal"], one) is None
    two = one + [year(2053, day(3, 1), renewal=Renewal(3, 4))]
    assert average(METRIC["renewal"], two) == Decimal(Decimal(50) + 75) / 2


def test_average_pledge_divides_by_families_pledging_more_than_zero():
    """Annual pledges ÷ pledging Families; none pledging is unknown."""
    assert day(1, 10, pledging=4, pledged="1000").average == 250
    assert day(1, 10, pledging=0, pledged="0").average is None


def test_numbers_follow_the_portal_rules():
    """Money with cents, one-decimal percentages, em dash for unknown."""
    assert value_text("money", Decimal("1234.5")) == "$1,234.50"
    assert value_text("count", 12345) == "12,345"
    assert value_text("count", Decimal("220.25")) == "220.3"
    assert value_text("percent", day(1, 1, active=3).percent) == "33.3%"
    assert value_text("percent", day(1, 0, active=0).percent) == "—"
    assert value_text("money", None) == "—"


def test_band_labels_come_from_the_band_set():
    """Each band's range in words, from the one band set."""
    labels = [band_label(band, BAND_EDGES) for band in range(1, 9)]
    assert labels[0] == "Under $250" and labels[1] == "$250 to under $500"
    assert labels[-1] == "$10,000 and more"


# ------------------------------------------------------ day alignment


def test_comparison_day_is_the_last_complete_day_held_at_the_end():
    """Yesterday in the campaign's zone; the end date once closed; capped."""
    zone = "America/New_York"
    # 03:00 UTC on October 10 is still October 9 in New York.
    instant = datetime(2054, 10, 10, 3, tzinfo=UTC)
    assert comparison_reads.comparison_day(
        None, START, date(2054, 11, 30), zone, instant
    ) == (5, date(2054, 10, 8))
    # After the end date, the day stays at the end date's.
    assert comparison_reads.comparison_day(
        None, START, date(2054, 10, 6), zone, instant
    ) == (3, date(2054, 10, 6))
    # A stale fact set caps it at its last day.
    stale = SimpleNamespace(last_date=date(2054, 10, 5))
    assert comparison_reads.comparison_day(
        stale, START, date(2054, 11, 30), zone, instant
    ) == (2, date(2054, 10, 5))
    # Before day 1 has ended: final results only.
    early = datetime(2054, 10, 4, 18, tzinfo=UTC)
    assert (
        comparison_reads.comparison_day(None, START, date(2054, 11, 30), zone, early)
        is None
    )


def archive(year_value, days, *, final=True, kept=True, systems=("parishsoft_duid",)):
    """A stored archive's fields as the reads use them."""
    return SimpleNamespace(
        pk=uuid4(),
        campaign_year=year_value,
        campaign_name=f"Stewardship {year_value}",
        is_final=final,
        start_date=START,
        days=days,
        active_families=50,
        family_rows_kept=kept,
        family_id_systems=list(systems),
        pledging_families=8,
        annual_pledged=Decimal("4000"),
        first_quartile=Decimal("100"),
        median=Decimal("200"),
        third_quartile=Decimal("400"),
        band_set_version=1,
        source_system="Invented",
        active_families_provenance="Invented cohort",
        dedup_rule="Last wins",
        notes={"note.01": "x" * 600},
    )


def rows(count):
    """Daily rows 1 to ``count``, one Family a day, $100 each."""
    return {
        number: SimpleNamespace(
            day=number,
            local_date=date(2054, 10, number),
            cumulative_families=number,
            cumulative_pledging_families=number,
            cumulative_annual_pledged=Decimal(100 * number),
        )
        for number in range(1, count + 1)
    }


def test_years_of_different_lengths_align_by_day(monkeypatch):
    """A final year carries its last day; a partial one is unknown after it."""
    monkeypatch.setattr(comparison_reads, "_json", pytest.fail)
    final, partial = archive(2054, 3, kept=False), archive(2053, 3, final=False)
    days = {final.pk: rows(3), partial.pk: rows(3)}
    years = comparison_reads._archive_years(
        [final, partial], days, {}, 5, financial=True
    )
    assert years[0].at_day.carried and years[0].at_day.families == 3
    assert years[1].at_day is None
    rendered = table_rows(
        Comparison(
            "Now", 5, date(2054, 10, 8), True, "current", None, tuple(years), None
        )
    )
    assert rendered[0]["families"] == "3 (final)" and rendered[1]["families"] == "—"
    # Day 2 is inside both.
    years = comparison_reads._archive_years(
        [final, partial], days, {}, 2, financial=True
    )
    assert [y.at_day.families for y in years] == [2, 2]
    # Notes are cut to 500 characters.
    assert max(len(text) for _, text in years[0].notes) == 500


def test_renewal_reasons():
    """No year before, no Family rows, or different id systems give "—"."""
    duid = archive(2054, 3)
    pds = archive(2053, 3, systems=("pds_fid", "pds_envelope"))
    unkept = archive(2052, 3, kept=False)
    reason = comparison_reads.renewal_reason
    assert reason(None, None, duid_only=True) == NO_LAST_YEAR
    assert reason(None, unkept, duid_only=True) == NO_FAMILY_ROWS
    assert reason(None, pds, duid_only=True) == OTHER_ID_SYSTEM
    assert reason(None, duid, duid_only=True) is None
    assert reason(duid, pds) == OTHER_ID_SYSTEM
    assert reason(pds, archive(2052, 3, systems=("pds_fid",))) is None


def test_older_band_set_asks_for_a_new_import():
    """A year computed with another band-set version shows no bands."""
    old = archive(2054, 3)
    old.band_set_version = 0
    (found,) = comparison_reads._archive_years(
        [old], {old.pk: rows(3)}, {}, None, financial=True
    )
    assert found.distribution.reason == OLDER_BANDS
    (entry,) = distribution_rows(
        Comparison("Now", None, None, True, "current", None, (found,), None),
        BAND_EDGES,
    )
    assert entry["reason"] == OLDER_BANDS and not entry["bands"]


# ------------------------------------------------- small-count protection


def test_a_band_under_five_and_its_neighbour_are_withheld_outside():
    """Fewer than 5: no count, total or share; nor its neighbour's."""
    distribution = bands(10, 12, 4, 9, 8, 7, 6, 5)
    document, withheld = protected(distribution)
    assert withheld
    by_band = {row["band"]: row for row in document["bands"]}
    assert by_band[3]["families"] == FEWER_THAN and by_band[3]["total"] is None
    # Its neighbour is the next lower band.
    assert by_band[2]["families"] is None and by_band[2]["total"] is None
    # Every share of pledging Families and the pledging count go too; the
    # year's total and the other bands' totals and shares of total stay.
    assert all(row["family_share"] is None for row in document["bands"])
    assert document["pledging_families"] is None
    assert by_band[1]["total"] == "1000" and by_band[1]["total_share"]
    assert document["annual_pledged"] == str(distribution.total)


def test_the_lowest_bands_neighbour_is_the_next_higher():
    """A withheld lowest band withholds band 2."""
    document, _ = protected(bands(3, 12, 6, 9, 8, 7, 6, 5))
    by_band = {row["band"]: row for row in document["bands"]}
    assert by_band[2]["total"] is None and by_band[3]["total"] == "1800"


def test_fewer_than_five_pledging_shows_no_bands_or_quartiles():
    """A year with under 5 pledging Families keeps only its total."""
    document, withheld = protected(bands(1, 2, 1))
    assert withheld and document["bands"] is None and document["quartiles"] is None


def test_no_small_band_keeps_every_figure():
    """Without a small band nothing is withheld."""
    document, withheld = protected(bands(10, 12, 6, 9, 8, 7, 6, 5))
    assert not withheld and document["pledging_families"] == 63
    assert document["quartiles"] == ["100.00", "200.00", "300.00"]


def comparison(imported, *, current_distribution=None):
    """A comparison on day 3 with a current campaign."""
    current = Year(
        2055,
        "Stewardship 2055",
        current=True,
        final=False,
        start_date=START,
        days=3,
        at_day=day(3, 40, pledging=20, pledged="9000"),
        end=day(3, 40, pledging=20, pledged="9000"),
        renewal=Renewal(5, 10),
        distribution=current_distribution or bands(5, 5, 5, 5),
    )
    return Comparison(
        "Stewardship 2055",
        3,
        date(2054, 10, 6),
        True,
        "current",
        current,
        tuple(imported),
        imported[0] if imported else None,
    )


def test_outside_output_has_no_current_distribution_or_note():
    """The command prints no current bands or quartiles and no note text."""
    small = year(
        2054,
        day(3, 30, pledging=12, pledged="3000"),
        distribution=bands(10, 4, 6, 9, 8, 7, 6, 5),
    )
    plain = year(
        2053,
        day(3, 20, pledging=10, pledged="2000"),
        distribution=bands(10, 12, 6, 9, 8, 7, 6, 5),
    )
    document = outside_document(comparison([small, plain]))
    assert "distribution" not in document["current"]
    assert document["current"]["at_day"]["average_pledge"] == "450.00"
    withheld, shown = document["years"]
    # The withheld year prints no pledging count or average, at any day.
    assert withheld["at_day"]["pledging_families"] is None
    assert withheld["at_day"]["average_pledge"] is None
    assert withheld["final_figures"]["average_pledge"] is None
    assert shown["at_day"]["average_pledge"] == "200.00"
    # The average of averages includes the withheld year, so it is withheld.
    assert document["averages"]["average"] is None
    assert document["averages"]["families"] == "25.0"
    # A partial year prints no distribution.
    partial = year(
        2052, day(3, 5), distribution=bands(10, 12, 6, 9, 8, 7, 6, 5), final=False
    )
    (only,) = outside_document(comparison([partial]))["years"]
    assert only["distribution"] is None


def test_page_shows_every_figure_including_the_current_distribution():
    """Inside the portal nothing is withheld, the current year included."""
    found = comparison(
        [
            year(
                2054,
                day(3, 30, pledging=12, pledged="3000"),
                distribution=bands(10, 4, 6),
            )
        ]
    )
    rows = distribution_rows(found, BAND_EDGES)
    assert rows[0]["year"] == "Stewardship 2055 (so far)"
    assert [band["families"] for band in rows[1]["bands"]] == ["10", "4", "6"]
    shown = {tile["key"]: tile for tile in tiles(found)}
    assert shown["families"]["value"] == "40"
    assert shown["families"]["lines"] == [
        "Last year on day 3: 30 (↑ 33.3% more)",
        "2054 average: 30 (↑ 33.3% more)",
    ]
    assert shown["average"]["median"] == "$200.00"
    assert shown["renewal"]["value"] == "50%"


def test_tiles_leave_pledges_out_without_the_financial_module():
    """Without Financial stewardship there is no pledge tile."""
    found = replace(comparison([]), financial=False)
    assert {tile["key"] for tile in tiles(found)} == {"families", "percent", "renewal"}


def test_the_data_line_names_the_years_the_averages_cover():
    """A partial year without a day-N value is left out of the named years."""
    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.reports.comparison_views import page_context

    found = comparison(
        [
            year(2054, day(3, 30)),
            year(2053, day(3, 20)),
            year(2052, None, end=day(2, 5), final=False),
        ]
    )
    principal = Principal(uuid4(), frozenset({"staff"}), frozenset())
    context = page_context(found, principal)
    assert context["average_years"] == "2053–2054"
    assert context["tiles"][0]["lines"][1].startswith("2053–2054 average")
    assert not context["can_import"]


# ------------------------------------------------- slice 5: series and day


def test_years_carry_their_daily_series(monkeypatch):
    """Each imported year's series is its daily rows, in day order."""
    monkeypatch.setattr(comparison_reads, "_json", pytest.fail)
    final = archive(2054, 3, kept=False)
    (found,) = comparison_reads._archive_years(
        [final], {final.pk: rows(3)}, {}, 2, financial=True
    )
    assert [day.day for day in found.series] == [1, 2, 3]
    assert found.series[2].pledged == Decimal(300)
    (found,) = comparison_reads._archive_years(
        [final], {final.pk: rows(3)}, {}, 2, financial=False
    )
    assert found.series[2].pledged is None


def fact(day_number, responses, *, available=True, pledge=None):
    """One fact row of the current campaign's Historical-as-of-day set."""
    return SimpleNamespace(
        local_date=date(2054, 10, 3 + day_number),
        cumulative_responses=responses,
        cohort_denominator=200,
        population_available=available,
        pledge_available=pledge is not None,
        pledge_total=None if pledge is None else Decimal(pledge),
    )


def test_current_series_is_the_fact_rows_through_the_latest_day():
    """Days 1 to the comparison day; unavailable days are gaps."""
    document = SimpleNamespace(
        days=(
            fact(0, 1),  # The day before day 1 is not part of the campaign.
            fact(1, 5, pledge="100"),
            fact(2, 9, available=False),
            fact(3, 12, pledge="400.50"),
            fact(4, 20, pledge="500"),
        )
    )
    series = comparison_reads.current_series(
        document, START, date(2054, 10, 6), financial=True
    )
    assert [(day.day, day.families, day.pledged) for day in series] == [
        (1, 5, Decimal(100)),
        (3, 12, Decimal("400.50")),
    ]
    assert series[0].active == 200 and series[0].pledging is None
    series = comparison_reads.current_series(
        document, START, date(2054, 10, 6), financial=False
    )
    assert [day.pledged for day in series] == [None, None]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", (None, False)),
        ("day=5", (5, False)),
        ("day=1", (1, False)),
        ("day=05", (None, True)),
        ("day=0", (None, True)),
        ("day=-1", (None, True)),
        ("day=1.5", (None, True)),
        ("day=abc", (None, True)),
        ("day=", (None, True)),
        ("day=10000", (None, True)),
    ],
)
def test_compare_on_day_takes_a_whole_day_number(query, expected):
    """Only day=<whole number from 1>; anything else is refused at the field."""
    from django.http import QueryDict

    from parishkit.stewardship.reports.comparison_views import requested_day

    assert requested_day(QueryDict(query)) == expected


@pytest.mark.parametrize("query", ["day=1&day=2", "scope=current", "day=1&x=1"])
def test_compare_on_day_refuses_any_other_parameter(query):
    """Another parameter, or two days, is the plain report-options error."""
    from django.http import QueryDict

    from parishkit.stewardship.reports.comparison_views import requested_day

    with pytest.raises(ValueError, match="Invalid report options"):
        requested_day(QueryDict(query))


def test_a_refused_day_names_the_days_that_can_be_chosen():
    """The field error says which days the campaign has, and links the field."""
    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.reports.comparison_views import day_error, page_context

    assert day_error(12) == "Enter a whole number from 1 to 12."
    assert day_error(1) == "Enter 1: only day 1 of this campaign has ended."
    assert day_error(None).startswith("No day can be compared")
    principal = Principal(uuid4(), frozenset({"staff"}), frozenset())
    built = Comparison(
        "Now", 3, date(2054, 10, 6), False, "current", None, (), None, latest_day=3
    )
    found = page_context(built, principal, refused=True)
    message = "Enter a whole number from 1 to 3."
    assert found["errors"] == [{"field_id": "compare-day", "message": message}]
    assert found["day_error"] == message
    assert page_context(built, principal)["errors"] == []
    # Before day 1 has ended there is no field to link to.
    early = replace(built, day=None, report_date=None, latest_day=None)
    (error,) = page_context(early, principal, refused=True)["errors"]
    assert error["field_id"] is None
