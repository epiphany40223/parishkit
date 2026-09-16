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
from PIL import Image

from parishkit.stewardship.reports.charts import (
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


def test_table_and_csv_keep_exact_money_counts_and_pinned_metadata():
    """CSV exports all dates, not a page, and never recomputes or rounds a pledge."""
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
    exported = list(csv.DictReader(io.StringIO(data.decode())))
    assert len(exported) == 3
    for raw, row in zip(rows, exported, strict=True):
        assert row["pledge_usd"] == raw["pledge_usd"]
        assert row["fact_set_id"] == str(value.fact_set_id)
        assert row["input_source_generation"] == "3"
        assert row["input_source_as_of"] == value.source_as_of.isoformat()
        assert row["submission_watermark"] == "1001"
        assert row["campaign_timezone"] == "America/New_York"
    assert "2026-11-01T21:00:00-08:00" in value.as_of_label
    assert "submission cutoff 1,001" in value.as_of_label


def test_figure_coordinates_match_table_and_do_not_mix_units():
    """Only plot coordinates use floats; financial table values stay exact."""
    value = document()
    with participation_figure(value) as figure:
        counts, dollars = figure.axes
        assert list(counts.lines[0].get_ydata()) == [1, 2, 3]
        assert [patch.get_height() for patch in counts.patches] == [1, 1, 1]
        assert list(dollars.lines[0].get_ydata()) == [1234.56, 2234.56, 3234.56]
        assert counts.get_ylabel() == "Families (count)"
        assert dollars.get_ylabel() == "Effective annual pledges (USD)"
        assert dollars.lines[0].get_linestyle() == "--"
        assert counts.patches[0].get_hatch() == "//"
        assert "America/New_York" in counts.get_xlabel()
        assert counts.yaxis.get_major_formatter()(1234, 0) == "1,234"
        assert dollars.yaxis.get_major_formatter()(1234, 0) == "$1,234"
    assert not figure.axes


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
        assert len(figure.axes) == 1


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
        assert math.isnan(figure.axes[0].lines[0].get_ydata()[0])
        assert figure.axes[0].lines[0].get_ydata()[1] == 0
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
