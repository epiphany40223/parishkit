"""Exact chart/table parity and deterministic files without a database/provider."""

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from matplotlib.text import Text
from PIL import Image

from parishkit.stewardship.reports import charts
from parishkit.stewardship.reports.charts import (
    RENDERER_VERSION,
    axis_top,
    participation_figure,
    render_participation,
)
from parishkit.stewardship.reports.participation import (
    ParticipationDay,
    ParticipationDocument,
    participation_csv,
    participation_table,
)


def document():
    """Fixed identities and a DST weekend make accidental clock/zone changes visible."""
    source = datetime(2026, 11, 2, 5, tzinfo=UTC)
    return ParticipationDocument(
        campaign_id=UUID(int=1),
        fact_set_id=UUID(int=2),
        parish_name="Example Parish",
        campaign_name="Annual stewardship",
        population_scope="historical",
        campaign_timezone="America/New_York",
        browser_timezone="America/Los_Angeles",
        source_generation=3,
        source_as_of=source,
        submission_watermark=1001,
        requested_at=source,
        first_date=date(2026, 10, 31),
        last_date=date(2026, 11, 2),
        financial_enabled=True,
        days=tuple(
            ParticipationDay(
                local_date=date(2026, 10, 31) + timedelta(days=index),
                first_responses=1,
                cumulative_responses=index + 1,
                cohort_denominator=1234,
                source_generation=index + 1,
                source_as_of=source - timedelta(days=2 - index),
                population_available=True,
                pledge_available=True,
                pledge_total=Decimal(f"{index + 1}234.56"),
            )
            for index in range(3)
        ),
    )


def encoded(value, format):
    """Return artifact bytes while confirming ownership of the output stream."""
    stream = io.BytesIO()
    render_participation(value, stream, format=format)
    assert not stream.closed
    return stream.getvalue()


def test_table_and_csv_keep_exact_money_counts_without_internal_ids():
    """CSV exports all dates, not a page, and never recomputes or rounds a pledge.

    Its columns are the shared plain-named list, with no repeated metadata
    or internal UUID, generation or watermark columns (#966).
    """
    value = document()
    with localcontext(prec=2):
        rows = participation_table(value)
        stream = io.BytesIO()
        participation_csv(value, stream)
    assert rows[0]["pledge_usd"] == "1234.56"
    assert rows[0]["participation"] == "1 out of 1,234 (0.1%)"
    assert rows[-1]["date"] == "2026-11-02"
    data = stream.getvalue()
    assert data.count(b"\r\n") == 4
    reader = csv.DictReader(io.StringIO(data.decode()))
    exported = list(reader)
    assert reader.fieldnames == [
        "Campaign date",
        "Population",
        "First submissions",
        "Families that have responded",
        "Families in population",
        "Cumulative participation",
        "Source as of",
        "Cumulative annual pledges (USD)",
    ]
    assert len(exported) == 3
    for raw, row in zip(rows, exported, strict=True):
        assert row["Cumulative annual pledges (USD)"] == raw["pledge_usd"]
        assert row["Cumulative participation"] == raw["participation"]
        assert row["Campaign date"] == raw["date"]
    # ISO 8601 in the stated browser_timezone (Los Angeles), per #221.
    assert exported[-1]["Source as of"].endswith("-08:00")
    assert str(value.fact_set_id).encode() not in data
    assert "November 1, 2026 at 9:00 PM PST" in value.as_of_label
    assert "cutoff" not in value.as_of_label and "#" not in value.as_of_label


def test_figure_coordinates_match_table_and_do_not_mix_units():
    """Only plot coordinates use floats; financial table values stay exact.

    Each series is its own panel on the shared date axis, never a second
    y-axis (#720): the daily bars, the cumulative Families and the pledges.
    """
    value = document()
    with participation_figure(value) as figure:
        daily, cumulative, dollars = figure.axes
        assert [patch.get_height() for patch in daily.patches] == [1, 1, 1]
        assert list(cumulative.lines[0].get_ydata()) == [1, 2, 3]
        assert list(dollars.lines[0].get_ydata()) == [1234.56, 2234.56, 3234.56]
        assert [axes.get_title(loc="left") for axes in figure.axes] == [
            "First submissions that day",
            "Cumulative participating Families",
            "Effective annual pledges",
        ]
        assert [axes.get_ylabel() for axes in figure.axes] == [
            "Families",
            "Families",
            "USD",
        ]
        assert dollars.lines[0].get_linestyle() == "--"
        assert daily.patches[0].get_hatch() == "//"
        # One date axis: shared limits, labelled once under the last panel.
        assert daily.get_shared_x_axes().joined(daily, dollars)
        assert "America/New_York" in dollars.get_xlabel()
        assert daily.get_xlabel() == cumulative.get_xlabel() == ""
        assert not any(label.get_visible() for label in daily.get_xticklabels())
        assert cumulative.yaxis.get_major_formatter()(1234, 0) == "1,234"
        assert dollars.yaxis.get_major_formatter()(1234, 0) == "$1.2K"
        # Panel titles name the series, so there is no legend.
        assert not figure.legends and all(not axes.get_legend() for axes in figure.axes)
    assert not figure.axes


def test_each_panel_fits_its_series_without_drawing_provenance():
    """Each panel tops out near its own data, not the eligible total (#575).

    The fixture's eligible cohort is 1,234 Families but its largest count is 3,
    so an axis at the cohort made the bars and line nearly flat. All axes
    stay linear.
    """
    with participation_figure(document()) as figure:
        daily, cumulative, dollars = figure.axes
        assert daily.get_ylim() == pytest.approx((0, 1.1))
        assert cumulative.get_ylim() == pytest.approx((0, 3.3))
        assert all(tick == int(tick) for tick in cumulative.get_yticks())
        assert {axes.get_yscale() for axes in figure.axes} == {"linear"}
        assert dollars.get_ylim() == pytest.approx((0, 3234.56 * 1.1))
        texts = [text.get_text() for text in figure.texts]
        assert f"{RENDERER_VERSION} · Page 1" in texts
        assert "Family participation — Historical as of day" in texts
        # Neither the eligible total nor the source/request provenance is drawn.
        assert not any(
            word in text
            for text in texts
            for word in ("Eligible", "Source #", "Requested")
        )
    assert RENDERER_VERSION == "participation-v3"


@pytest.mark.parametrize("email", [False, True])
def test_png_plot_box_is_the_hit_test_layout(email):
    """The panels together fill the plot box the pages hit-test (#720).

    The page and email PNGs draw their panels from the top of
    ``PLOT_LAYOUT`` or ``EMAIL_PLOT_LAYOUT`` to its bottom, at 1440 x 840
    pixels, so the hover and date slider map the box's edges to the dates.
    """
    layout = charts.EMAIL_PLOT_LAYOUT if email else charts.PLOT_LAYOUT
    with participation_figure(document(), email=email) as figure:
        boxes = [axes.get_position() for axes in figure.axes]
        assert (
            boxes[0].x0,
            boxes[0].x1,
            boxes[-1].y0,
            boxes[0].y1,
        ) == pytest.approx(
            (layout["left"], layout["right"], layout["bottom"], layout["top"])
        )
        assert all(box.x0 == boxes[0].x0 and box.x1 == boxes[0].x1 for box in boxes)
        assert tuple(figure.get_size_inches() * figure.dpi) == (1440, 840)
        [title] = [
            text
            for text in figure.axes[0].findobj(Text)
            if text.get_text() == "First submissions that day"
        ]
        assert title.get_fontsize() == (11 if email else 10)
        note = figure.texts[-2 if not email else -1]
        assert note.get_position() == pytest.approx((0.09, 0.055))
        texts = [text.get_text() for text in figure.texts]
        assert (f"{RENDERER_VERSION} · Page 1" in texts) is not email


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (0, "$0"),
        (800, "$800"),
        (1000, "$1K"),
        (1500, "$1.5K"),
        (250_000, "$250K"),
        (1_600_000, "$1.6M"),
        (10_000_000, "$10M"),
        # The unit is chosen after rounding, so a value is promoted.
        (999.6, "$1K"),
        (960_000, "$960K"),
        (999_949, "$999.9K"),
        (999_999, "$1M"),
        (999_999_999, "$1B"),
        (1_000_000_000, "$1B"),
        (2_500_000_000_000, "$2,500B"),
        (-250_000, "-$250K"),
        (-800, "-$800"),
    ],
)
def test_dollar_ticks_are_short(value, label):
    """Pledge ticks read in thousands or millions, so the axis title fits."""
    assert charts.short_dollars(value) == label


def test_axis_top_uses_the_series_maximum_and_skips_gaps():
    """A panel's top is its largest value plus 10%; NaN gaps are ignored."""
    nan = float("nan")
    assert axis_top([40, 3]) == pytest.approx(44)
    assert axis_top([1, 2, 3]) == pytest.approx(3.3)
    assert axis_top([nan, 2.5]) == pytest.approx(2.75)
    assert axis_top([nan]) == pytest.approx(1.1)
    assert axis_top([]) == pytest.approx(1.1)


def test_round_family_ticks_for_a_realistic_campaign():
    """A few hundred Families get round 50-step ticks, not 30 or 25."""
    value = document()
    days = tuple(
        replace(day, first_responses=90, cumulative_responses=90 * (index + 1))
        for index, day in enumerate(value.days)
    )
    with participation_figure(replace(value, days=days)) as figure:
        counts = figure.axes[1]
        top = counts.get_ylim()[1]
        assert top == pytest.approx(270 * 1.1)
        # Only the ticks inside the axis are drawn; the locator may add one past it.
        ticks = [tick for tick in counts.get_yticks() if tick <= top]
        assert ticks == [0, 50, 100, 150, 200, 250]


@pytest.mark.parametrize("format", ["png", "pdf"])
def test_provenance_is_file_metadata_not_drawn(format):
    """Source snapshot and request time travel in the file, not the picture.

    PNG carries them in a Description text chunk and PDF in its Info Subject,
    so a downloaded chart still says where its numbers came from (#575).
    """
    value = document()
    data = encoded(value, format)
    expected = value.as_of_label.replace("\n", " ")
    assert expected.startswith("Source as of ") and "Requested" in expected
    if format == "png":
        with Image.open(io.BytesIO(data)) as image:
            assert image.text["Description"] == expected
    else:
        assert b"/Subject (Source as of " in data


@pytest.mark.parametrize("format", ["png", "pdf"])
def test_render_is_byte_reproducible_and_valid(format):
    """No wall-clock timestamp, random PDF identity or pyplot registry leaks in."""
    value = document()
    first = encoded(value, format)
    assert first == encoded(value, format)
    if format == "png":
        with Image.open(io.BytesIO(first)) as image:
            assert image.format == "PNG"
            assert image.size == (1440, 840)
            image.verify()
    else:
        assert first.startswith(b"%PDF-")
        assert first.rstrip().endswith(b"%%EOF")
        assert b"/CreationDate (D:20261102050000Z)" in first


def test_serialized_thread_rendering_is_deterministic():
    """The actual renderer serializes its process-global plotting dependency."""
    value = document()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: encoded(value, "png"), range(4)))
    assert all(result == results[0] for result in results)


@pytest.mark.parametrize("count", [366, 3653])
def test_annual_and_multi_year_documents_render_all_observations(count):
    """Exercise full-size inputs; do not hide scale problems with three-row fixtures."""
    value = document()
    first = date(2026, 1, 1)
    source = datetime(2037, 1, 1, tzinfo=UTC)
    value = replace(
        value,
        source_as_of=source,
        requested_at=source,
        first_date=first,
        last_date=first + timedelta(days=count - 1),
        days=tuple(
            replace(
                value.days[0],
                local_date=first + timedelta(days=index),
                cumulative_responses=index + 1,
                cohort_denominator=5000,
            )
            for index in range(count)
        ),
    )
    assert len(participation_table(value)) == count
    assert encoded(value, "png").startswith(b"\x89PNG")
    assert encoded(value, "pdf").startswith(b"%PDF")


def test_no_financial_column_axis_or_hidden_value_when_disabled():
    """An accidental populated input cannot add financial information to output."""
    value = replace(document(), financial_enabled=False)
    assert all("pledge_usd" not in row for row in participation_table(value))
    stream = io.BytesIO()
    participation_csv(value, stream)
    assert b"pledge" not in stream.getvalue()
    with participation_figure(value) as figure:
        assert [axes.get_title(loc="left") for axes in figure.axes] == [
            "First submissions that day",
            "Cumulative participating Families",
        ]


def test_unavailable_is_gap_but_observed_zero_and_empty_are_distinct():
    """A missing source day never masquerades as a zero-population observation."""
    value = document()
    missing = replace(
        value.days[0],
        first_responses=0,
        cumulative_responses=0,
        cohort_denominator=0,
        source_generation=None,
        source_as_of=None,
        population_available=False,
        pledge_available=False,
        pledge_total=None,
    )
    zero = replace(
        value.days[1],
        first_responses=0,
        cumulative_responses=0,
        cohort_denominator=0,
        pledge_total=Decimal("0.00"),
    )
    value = replace(value, days=(missing, zero, value.days[2]))
    rows = participation_table(value)
    assert rows[0]["first_responses"] is None
    assert rows[0]["participation"] == "Unavailable"
    assert rows[0]["pledge_usd"] is None
    assert rows[1]["participation"] == "0 out of 0 (—)"
    assert rows[1]["pledge_usd"] == "0.00"
    with participation_figure(value) as figure:
        cumulative = figure.axes[1].lines[0].get_ydata()
        assert math.isnan(cumulative[0])
        assert cumulative[1] == 0
        assert math.isnan(figure.axes[0].patches[0].get_height())
        assert any("Missing observations" in text.get_text() for text in figure.texts)
    empty = replace(value, days=(), first_date=None, last_date=None)
    with participation_figure(empty) as figure:
        assert "No campaign days" in figure.axes[0].texts[0].get_text()
    stream = io.BytesIO()
    participation_csv(empty, stream)
    assert stream.getvalue().count(b"\r\n") == 1


@pytest.mark.parametrize(
    "change",
    [
        {"first_responses": 5},
        {"cohort_denominator": 0},
        {"source_generation": None},
        {"source_as_of": None},
        {"population_available": False},
        {"pledge_available": False},
        {"pledge_total": None},
        {"pledge_total": Decimal("NaN")},
    ],
)
def test_invalid_day_is_rejected_before_rendering(change):
    """The detached boundary does not rely on the caller having used the ORM."""
    with pytest.raises(ValueError):
        replace(document().days[0], **change)


@pytest.mark.parametrize(
    "change",
    [
        {"campaign_id": "1"},
        {"fact_set_id": None},
        {"parish_name": ""},
        {"campaign_name": "bad\nlabel"},
        {"parish_name": "x" * 255},
        {"population_scope": "other"},
        {"population_scope": []},
        {"campaign_timezone": "bad/zone"},
        {"browser_timezone": None},
        {"source_generation": True},
        {"submission_watermark": -1},
        {"financial_enabled": 1},
        {"source_as_of": datetime(2026, 1, 1)},
        {"requested_at": None},
        {"first_date": None},
        {"last_date": date(2025, 1, 1)},
        {"days": []},
        {"days": (None,)},
        {"days": ()},
        {"source_generation": 1},
    ],
)
def test_invalid_document_is_rejected(change):
    """Malformed or incomplete generation identity cannot be silently repaired."""
    with pytest.raises(ValueError):
        replace(document(), **change)


def test_reordered_dates_and_future_daily_cutoffs_are_rejected():
    """A chart cannot combine shuffled or newer inputs with its pinned generation."""
    value = document()
    with pytest.raises(ValueError):
        replace(value, days=tuple(reversed(value.days)))
    with pytest.raises(ValueError):
        replace(value, source_as_of=value.source_as_of - timedelta(seconds=1))
    with pytest.raises(ValueError):
        replace(value, days=(), first_date=None)


def test_invalid_render_contract_is_rejected_and_style_restored():
    """Caller styles and external TeX cannot affect an authorized artifact."""
    import matplotlib as mpl

    with pytest.raises(ValueError):
        render_participation(document(), io.BytesIO(), format="svg")
    with pytest.raises(TypeError):
        participation_table(None)
    with pytest.raises(TypeError), participation_figure(None):
        pass
    with mpl.rc_context({"text.usetex": True, "font.family": "Comic Sans MS"}):
        with participation_figure(document()):
            assert not mpl.rcParams["text.usetex"]
            assert mpl.rcParams["font.family"] == ["DejaVu Sans"]
        assert mpl.rcParams["text.usetex"]
