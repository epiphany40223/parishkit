"""Exact display rules and escaped, localization-ready component contracts."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.domain import Money, Percentage
from parishkit.stewardship.web.presentation import (
    instant,
    number,
    out_of,
    parish_date,
    percentage,
    usd,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0"),
        (1234567, "1,234,567"),
        (-1000, "-1,000"),
        (Decimal("1000.125"), "1,000.125"),
    ],
)
def test_exact_number_grouping(value, expected):
    assert number(value) == expected


@pytest.mark.parametrize(
    "value",
    [True, 1.5, "1000", Decimal("NaN"), Decimal("Infinity"), 10**30, Decimal("1e-99")],
)
def test_number_rejects_lossy_or_unbounded_inputs(value):
    with pytest.raises(ValueError):
        number(value)


@pytest.mark.parametrize(
    "cents,expected", [(0, "$0.00"), (123456789, "$1,234,567.89"), (-101, "-$1.01")]
)
def test_currency_uses_exact_cents(cents, expected):
    assert usd(Money(cents)) == expected


@pytest.mark.parametrize(
    "x,y,expected",
    [
        (0, 0, "—"),
        (0, 1, "0%"),
        (1, 3, "33.3%"),
        (2, 3, "66.7%"),
        (1, 8, "12.5%"),
        (1, 1, "100%"),
        (2, 1, "200%"),
    ],
)
def test_percentages(x, y, expected):
    assert percentage(Percentage(x, y)) == expected


def test_out_of_includes_grouped_counts_and_zero_denominator():
    assert out_of(Percentage(1000, 3000)) == "1,000 out of 3,000 (33.3%)"
    assert out_of(Percentage(0, 0)) == "0 out of 0 (—)"


@pytest.mark.parametrize("formatter", [usd, out_of, percentage])
def test_formatters_require_canonical_values(formatter):
    with pytest.raises(TypeError):
        formatter(1)


def test_instants_are_normalized_but_campaign_dates_never_shift():
    moment = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9)))
    assert instant(moment) == "2025-12-31T15:00:00+00:00"
    assert parish_date(date(2026, 1, 1)) == "January 1, 2026"
    with pytest.raises(ValueError):
        instant(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        parish_date(moment)


def test_components_escape_content_and_link_error_fields():
    html = render_to_string(
        "stewardship/components/errors.html",
        {
            "errors": [
                {"field_id": "family-code", "message": "<script>private</script>"}
            ]
        },
    )
    assert 'href="#family-code"' in html and "<script>" not in html
    assert "&lt;script&gt;" in html
    html = render_to_string(
        "stewardship/components/table.html",
        {
            "table_caption": "Results",
            "table_headings": ["Name", "Count"],
            "table_rows": [["<unsafe>", "1,000"]],
        },
    )
    assert "&lt;unsafe&gt;" in html
    assert 'scope="row"' in html and 'scope="col"' in html


def test_family_shell_has_no_inline_assets_and_keeps_noscript_fallback():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    html = render_to_string(
        "stewardship/family.html",
        {
            "server_now": now,
            "deadline": now + timedelta(hours=1),
            "absolute_deadline": now + timedelta(hours=4),
        },
    )
    assert "<noscript>" in html and "data-local-instant" in html
    assert 'src="/static/stewardship/ui-v1.js"' in html
    assert "<script>" not in html and "<style>" not in html
    assert 'href="#main"' in html


@pytest.mark.parametrize("count", [None, "bad", 0, 1000])
def test_progress_component_handles_missing_count_without_render_failure(count):
    """Future progress consumers get a safe empty state or exact grouped count."""
    context = {"progress_total": 2000, "progress_title": "Progress"}
    if count is not None:
        context["progress_count"] = count
    html = render_to_string("stewardship/components/progress.html", context)
    assert ("<progress " in html) == isinstance(count, int)
    if count == 1000:
        assert ">1,000</progress>" in html
