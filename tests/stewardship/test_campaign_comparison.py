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
from parishkit.stewardship.reports import comparison_reads, comparison_views
from parishkit.stewardship.reports.comparison import (
    FEWER_THAN,
    METRICS,
    NO_ACTIVE_FAMILIES,
    NO_FAMILY_ROWS,
    NO_FIGURE_YET,
    NO_LAST_YEAR,
    NO_PRIOR_RESPONDERS,
    OLDER_BANDS,
    OTHER_ID_SYSTEM,
    Band,
    Comparison,
    Day,
    Distribution,
    LateEntries,
    Renewal,
    Year,
    average,
    band_label,
    change,
    change_text,
    distribution_rows,
    late_entries,
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
    assert context["tiles"][0].details[1].startswith("2053–2054 average")
    assert not context["can_import"]


def test_data_notes_show_the_shared_notes_once_then_each_years_own():
    """Notes every year has alike show once; each year keeps only its own.

    The Administrator asked for one consolidated section (2026-10-13,
    #1161). A note with a shared label but a different text is the year's
    own; a year with nothing of its own is left out; a lone year's notes
    are all its own.
    """
    from parishkit.stewardship.reports.comparison import data_notes

    source = ("Source system", "Old system")
    counted = ("Campaign Families counted from", "A snapshot")
    years = [
        replace(year(2054, None), notes=(source, counted, ("note.a", "Late file"))),
        replace(year(2053, None), notes=(counted, source)),
        replace(
            year(2052, None),
            notes=(source, counted, ("Repeat submissions", "Kept the last")),
        ),
    ]
    assert data_notes(years) == (
        (source, counted),
        (
            (2054, (("note.a", "Late file"),)),
            (2052, (("Repeat submissions", "Kept the last"),)),
        ),
    )
    other = replace(years[1], notes=(source, ("Campaign Families counted from", "B")))
    assert data_notes([years[0], other]) == (
        (source,),
        (
            (2054, (counted, ("note.a", "Late file"))),
            (2053, (("Campaign Families counted from", "B"),)),
        ),
    )
    assert data_notes(years[:1]) == ((), ((2054, years[0].notes),))
    assert data_notes([]) == ((), ())


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
    # A refused day's page is itself a 400, so its figures status is drawn
    # but never watched: its own re-read would only be refused again (#1586).
    assert found["figures_status"]["follow"] is False
    assert page_context(built, principal)["figures_status"]["follow"] is True
    # Before day 1 has ended there is no field to link to.
    early = replace(built, day=None, report_date=None, latest_day=None)
    (error,) = page_context(early, principal, refused=True)["errors"]
    assert error["field_id"] is None


# ------------------------------------------------- every "—" has a reason


def current_with(found, **changes):
    """``found`` with its current campaign's fields replaced."""
    return replace(found, current=replace(found.current, **changes))


def test_a_day_without_figures_explains_every_dash_renewal_included():
    """No figures for the day and a DUID-only last year (no renewal reason,
    no renewal found): every tile reads "—" with a reason (#1637)."""
    found = current_with(
        comparison([year(2054, day(3, 30))]), at_day=None, renewal=Renewal()
    )
    shown = {tile["key"]: tile for tile in tiles(found)}
    assert {key: tile["value"] for key, tile in shown.items()} == dict.fromkeys(
        ("families", "percent", "pledged", "average", "renewal"), "—"
    )
    assert {tile["reason"] for tile in shown.values()} == {NO_FIGURE_YET}
    comparison_views.headline_tiles(tiles(found))


def test_no_responders_last_year_explains_the_renewal_dash():
    """Last year had no responders, so there is no renewal share."""
    found = current_with(comparison([year(2054, day(3, 0))]), renewal=Renewal(0, 0))
    (renewal,) = [tile for tile in tiles(found) if tile["key"] == "renewal"]
    assert (renewal["value"], renewal["reason"]) == ("—", NO_PRIOR_RESPONDERS)
    comparison_views.headline_tiles(tiles(found))


def test_no_active_families_explains_the_percent_dash():
    """A share of no active Families is a value that still reads "—"."""
    found = current_with(comparison([]), at_day=day(3, 0, active=0))
    (percent,) = [tile for tile in tiles(found) if tile["key"] == "percent"]
    assert (percent["value"], percent["reason"]) == ("—", NO_ACTIVE_FAMILIES)
    comparison_views.headline_tiles(tiles(found))


def test_headline_tiles_never_raise_for_any_missing_or_zero_figure():
    """Every mix of unknown and zero figures, for this year and last, draws
    tiles: a "—" always has a reason and a figure never has one."""
    days = (
        None,
        day(3, 0, active=0),
        day(3, 0, pledging=0, pledged="0"),
        day(3, 5, pledging=None, pledged=None),
        day(3, 5, pledging=2, pledged="100"),
    )
    renewals = (Renewal(), Renewal(reason=NO_LAST_YEAR), Renewal(0, 0), Renewal(3, 6))
    for financial in (True, False):
        for at_day in days:
            for renewal in renewals:
                for last_day in days:
                    imported = [year(2054, last_day, end=last_day or day(3, 1))]
                    found = replace(
                        current_with(
                            comparison(imported), at_day=at_day, renewal=renewal
                        ),
                        financial=financial,
                    )
                    for figure in comparison_views.headline_tiles(tiles(found)):
                        assert (figure.text == "—") == bool(figure.unknown_reason)


def test_pledge_tiles_show_whole_dollars_at_the_other_tiles_size():
    """The Annual pledges tile and its comparison lines are whole dollars,
    rounded half up, so a large total keeps the row's full size (#2090);
    the Every number table keeps the exact cents."""
    found = comparison(
        [
            year(2054, day(3, 30, pledging=380, pledged="876543.49")),
            year(2053, day(3, 20, pledging=360, pledged="800000.51")),
        ]
    )
    at = day(3, 40, pledging=400, pledged="987654.50")
    found = replace(found, current=replace(found.current, at_day=at, end=at))
    for outside in (False, True):  # the page, and the PDF summary and emails
        pledged = {tile["key"]: tile for tile in tiles(found, outside=outside)}[
            "pledged"
        ]
        assert pledged["value"] == "$987,655"
        assert pledged["lines"] == [
            "Last year on day 3: $876,543 (↑ 12.7% more)",
            "2053–2054 average: $838,272 (↑ 17.8% more)",
        ]
    drawn = comparison_views.headline_tiles(tiles(found))
    assert [tile.key for tile in drawn] == [metric.key for metric in METRICS]
    assert [tile.fit for tile in drawn] == [""] * 5
    assert "$987,654.50" in str(table_rows(found)[0])


def test_a_pledge_comparison_line_rounds_half_up_to_the_dollar():
    """A comparison line's amount at exactly 50 cents rounds up (#2090)."""
    found = comparison([year(2054, day(3, 30, pledging=380, pledged="800000.50"))])
    pledged = {tile["key"]: tile for tile in tiles(found)}["pledged"]
    assert pledged["lines"][0].startswith("Last year on day 3: $800,001 (")


@pytest.mark.parametrize(
    "pledged, shown",
    [
        ("987654.50", "$987,655"),
        ("999999.49", "$999,999"),
        ("999999.50", "$1.00M"),
        ("1000000", "$1.00M"),
        ("1234567.89", "$1.23M"),
        ("1235000", "$1.24M"),
        ("9995000", "$10.00M"),
        ("12345678", "$12.35M"),
        ("123456789", "$123.46M"),
        ("999990000", "$999.99M"),  # the longest likely form, still full size
        ("999994999.99", "$999.99M"),
        ("999995000", "$1.00B"),
        ("999999999.50", "$1.00B"),
        ("1234567890", "$1.23B"),
        ("12345678901", "$12.35B"),
    ],
)
def test_a_pledge_total_of_a_million_or_more_has_two_decimal_places(pledged, shown):
    """From $1,000,000 (rounded to the dollar) the Annual pledges tile's
    value is in millions, then billions, with two decimal places, half up,
    so it keeps the row's full size; its comparison lines keep whole
    dollars and the Every number table the cents (#2090)."""
    found = comparison([year(2054, day(3, 30, pledging=380, pledged="876543.49"))])
    # Many pledging Families keep the Average pledge tile short, so every
    # tile's size is decided by its own value.
    at = day(3, 40, pledging=10**8, pledged=pledged)
    found = replace(found, current=replace(found.current, at_day=at, end=at))
    for outside in (False, True):  # the page, and the PDF summary and emails
        pledged_tile = {t["key"]: t for t in tiles(found, outside=outside)}["pledged"]
        assert pledged_tile["value"] == shown
        assert pledged_tile["lines"][0].startswith("Last year on day 3: $876,543 (")
    drawn = comparison_views.headline_tiles(tiles(found))
    assert [tile.fit for tile in drawn] == [""] * 5
    assert f"${Decimal(pledged):,.2f}" in str(table_rows(found)[0])


def test_late_entries_are_the_tail_after_the_end_date():
    """Families who first responded after the end date, and the pledge change
    since it, from the series' last day (#2298); None while the series stops
    at the end date, or when either day's population is unavailable."""
    end = date(2054, 10, 5)
    days = (
        fact(1, 10, pledge="1000.00"),
        fact(2, 12, pledge="1500.00"),  # The end date.
        fact(3, 12, pledge="1700.50"),  # A changed pledge, entered late.
        fact(4, 13, pledge="1900.50"),  # A first-ever late response.
    )
    assert days[1].local_date == end
    assert late_entries(days, end, True) == LateEntries(end, 1, Decimal("400.50"))
    assert late_entries(days, end, False) == LateEntries(end, 1, None)
    assert late_entries(days[:2], end, True) is None
    gap = (*days[:3], fact(4, 13, available=False, pledge="1900.50"))
    assert late_entries(gap, end, True) is None
    unknown = (*days[:3], fact(4, 13))
    assert late_entries(unknown, end, True) == LateEntries(end, 1, None)
    assert LateEntries(end, 1, Decimal("400.50")).pledges_text == "+$400.50"
    assert LateEntries(end, 0, Decimal("-40")).pledges_text == "-$40.00"
    assert LateEntries(end, 0, Decimal("0")).pledges_text is None
    assert LateEntries(end, 2).pledges_text is None


@pytest.mark.parametrize(
    ("late", "shown"),
    [
        (
            LateEntries(date(2054, 11, 2), 2, Decimal("400.50")),
            "Entered after the campaign closed on November 2, 2054: 2 more "
            "Families responded, and effective annual pledges changed by "
            "+$400.50.",
        ),
        (
            LateEntries(date(2054, 11, 2), 1),
            "Entered after the campaign closed on November 2, 2054: 1 more "
            "Family responded.",
        ),
        # Only changed responses, and no pledge change: neither "0 more
        # Families" nor "changed by $0.00".
        (
            LateEntries(date(2054, 11, 2), 0, Decimal("0")),
            "Responses were entered after the campaign closed on November 2, "
            "2054, but no more Families responded.",
        ),
        (
            LateEntries(date(2054, 11, 2), 0, Decimal("-40")),
            "Responses were entered after the campaign closed on November 2, "
            "2054, but no more Families responded, and effective annual "
            "pledges changed by -$40.00.",
        ),
    ],
)
def test_the_page_states_the_late_entries_without_empty_figures(late, shown):
    """The late entries line (#2298) reads naturally for every tail: a
    Family count only when one more responded, a pledge change only when it
    is not zero."""
    import re

    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.reports.comparison_views import page_context

    found = replace(comparison([year(2054, day(3, 30))]), late=late)
    principal = Principal(uuid4(), frozenset({"staff"}), frozenset())
    html = render_to_string(
        "stewardship/campaign-comparison.html",
        page_context(found, principal) | {"csrf_token": "a" * 64},
    )
    (line,) = re.findall(r"<p>[^<]*after the campaign closed on[^<]*</p>", html)
    tail = (
        "The figures below compare every year up to its end date, so they "
        "leave these out."
    )
    assert line == f"<p>{shown} {tail}</p>"
