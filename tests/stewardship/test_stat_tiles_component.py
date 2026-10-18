"""The shared stat tiles: the builder, its include and the guard (#1540).

ui-conventions "Stat tiles": every Admin headline figure is a tile built by
``web.stat_tiles.tile()`` and drawn by ``components/stat-tiles.html``. These
tests check the builder's formatting and unknown values without rendering,
render the include, check each page's tiles, and keep pages from drawing
tiles by hand again: no template outside ``components/`` may.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_dashboard import family_tiles
from parishkit.stewardship.campaigns.domain import Money, Percentage
from parishkit.stewardship.reports import (
    comparison_views,
    digest_presentation,
    financial_views,
    limitation_views,
    ministry_followup_views,
)
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.web.stat_tiles import StatTile, shown, tile

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
# What hand-drawn stat-tile markup looks like: the tile classes, or the
# saved daily report's comparison figures (the same figures drawn as a dl).
# Single- or double-quoted class attributes, any of the tile's classes.
HAND_DRAWN = re.compile(
    r"""class=(["'])[^"']*\bstat-(?:tiles?|value|label|detail|bar)\b"""
    r"|digest-comparison-figures"
)


def render(tiles, **names):
    """The include drawn for ``tiles`` with the row's accessible name."""
    return render_to_string(
        "stewardship/components/stat-tiles.html", {"tiles": tiles, **names}
    )


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "0"),
        (1234, "1,234"),
        (Decimal("1234.5"), "1,234.5"),
        (Money(98265700), "$982,657.00"),
        (Money(-150), "-$1.50"),
        (Percentage(1, 3), "33.3%"),
        (Percentage(2, 2), "100%"),
    ],
)
def test_the_builder_formats_a_value_by_its_type(value, text):
    """Counts are grouped, money is USD with cents, a share is a percentage
    (ui-conventions "Numbers and money"); a known value has no reason."""
    built = tile("k", "Label", value)
    assert built == StatTile(key="k", label="Label", text=text)


@pytest.mark.parametrize("value", [None, Percentage(0, 0)])
def test_an_unknown_value_reads_a_dash_with_its_reason_never_zero(value):
    """No value, or a share of nothing, reads "—" with the given reason."""
    built = tile("k", "Label", value, unknown_reason="Not counted yet.")
    assert (built.text, built.unknown_reason) == ("—", "Not counted yet.")


def test_the_builder_refuses_an_unknown_value_without_a_reason_and_vice_versa():
    """A missing reason would leave a bare dash; a reason on a known value
    would explain nothing."""
    with pytest.raises(ValueError):
        tile("k", "Label", None)
    with pytest.raises(ValueError):
        tile("k", "Label", 3, unknown_reason="Why?")


@pytest.mark.parametrize("value", [True, 1.5, "12"])
def test_the_builder_refuses_values_it_cannot_format_exactly(value):
    """Booleans, floats and pre-formatted text are not tile values."""
    with pytest.raises(TypeError):
        tile("k", "Label", value)


def test_details_drop_empty_lines_and_stop_at_three():
    """Empty explanation lines are left out; a fourth line is refused."""
    assert tile("k", "L", 1, details=("", "one", None)).details == ("one",)
    assert len(tile("k", "L", 1, details=("a", "b", "c")).details) == 3
    with pytest.raises(ValueError):
        tile("k", "L", 1, details=("a", "b", "c", "d"))


def test_shown_takes_text_as_given_and_a_dash_needs_its_reason():
    """``shown`` keeps a value already formatted elsewhere word for word;
    its "—" is unknown, so it needs a reason, and other text refuses one."""
    assert shown("k", "L", "$1,000.50", details=("Last year: $900.00",)) == (
        StatTile(key="k", label="L", text="$1,000.50", details=("Last year: $900.00",))
    )
    assert shown("k", "L", "—", unknown_reason="Why.").unknown_reason == "Why."
    with pytest.raises(ValueError):
        shown("k", "L", "—")
    with pytest.raises(ValueError):
        shown("k", "L", "12", unknown_reason="Why.")


def test_a_share_bar_is_drawn_only_against_a_whole_above_zero():
    """``share`` draws the bar; a share of nothing has no bar."""
    assert tile("k", "L", 5, share=(5, 4)).bar == (5, 4)
    assert tile("k", "L", 0, share=(0, 0)).bar is None


def test_the_include_reads_label_value_and_details_in_order():
    """One list named by its heading, a tile per figure keyed for tests, the
    visible text in reading order with nothing hidden from assistive
    technology but the bar and the trend's arrow (its words say the
    direction), and no mouse-only title tooltip."""
    html = render(
        [
            tile(
                "invited",
                "Invited",
                1200,
                details=("out of 1,500 (80%)",),
                trend="↑ 12.5% more",
                share=(1200, 1500),
            )
        ],
        tiles_labelledby="stages-heading",
    )
    assert html.strip() == (
        '<ul class="stat-tiles" aria-labelledby="stages-heading">'
        '<li class="stat-tile" data-stat="invited">'
        '<span class="stat-label">Invited</span> '
        '<span class="stat-value">1,200</span> '
        '<span class="stat-detail">out of 1,500 (80%)</span> '
        '<span class="stat-detail stat-trend">'
        '<span aria-hidden="true">↑</span> 12.5% more</span>'
        '<span class="stat-bar" aria-hidden="true">'
        '<progress value="1200" max="1500"></progress></span></li></ul>'
    )
    assert "title=" not in html


def test_the_include_gives_an_unknown_value_its_toggletip():
    """The dash is followed, inside the value, by a toggletip holding the
    reason; a row without a heading takes its label."""
    html = render(
        [tile("pledges", "Pledges", None, unknown_reason="Not added up yet.")],
        tiles_label="Headline figures",
    )
    assert '<ul class="stat-tiles" aria-label="Headline figures">' in html
    assert '<span class="stat-value">— <span class="toggletip">' in html
    assert 'aria-controls="stat-headline-figures-pledges-tip"' in html
    assert (
        '<span class="toggletip-bubble" id="stat-headline-figures-pledges-tip" hidden>'
        in html
    )
    assert "Not added up yet." in html


def test_no_template_draws_stat_tiles_by_hand():
    """Every headline figure is drawn by the include (#1540): no template
    outside ``components/`` writes the tile classes itself."""
    found = {
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if not path.relative_to(TEMPLATES).as_posix().startswith("components/")
        and HAND_DRAWN.search(path.read_text())
    }
    assert found == set()


def test_the_include_refuses_a_row_with_no_name():
    """An unnamed list would be silent to assistive technology."""
    with pytest.raises(ValueError, match="tiles_labelledby or tiles_label"):
        render([tile("a", "A", 1)])


def test_two_rows_on_one_page_never_share_a_toggletip_id():
    """The id prefix comes from the row, so equal keys in two rows differ."""
    unknown = [tile("same", "Same", None, unknown_reason="Why.")]
    first = render(unknown, tiles_labelledby="first-heading")
    second = render(unknown, tiles_label="Second row")
    third = render(unknown, tiles_label="Third row", tiles_id="custom")
    ids = {
        re.search(r'id="(stat-[^"]+)" hidden', html).group(1)
        for html in (first, second, third)
    }
    assert ids == {
        "stat-first-heading-same-tip",
        "stat-second-row-same-tip",
        "stat-custom-same-tip",
    }


@pytest.mark.parametrize(
    "markup",
    [
        '<div class="stat-tile">',
        "<div class='stat-tile'>",
        '<b class="x stat-detail">',
        "<i class='stat-bar'>",
    ],
)
def test_the_guard_matches_both_quote_styles_and_every_tile_class(markup):
    """Single quotes, and detail or bar used alone, are hand-drawn too."""
    assert HAND_DRAWN.search(markup)


def test_home_family_participation_tiles():
    """Home: active Families, the eligible responses as a share of eligible
    Families with its bar, and every response; no bar against nothing."""
    counts = {"active": 400, "responded": 160, "participation": Percentage(40, 400)}
    active, share, responded = family_tiles(counts)
    assert (active.key, active.text, active.bar) == ("active", "400", None)
    assert (share.text, share.details, share.bar) == (
        "40",
        ("out of 400 (10%)",),
        (40, 400),
    )
    assert (responded.text, responded.bar) == ("160", None)
    none = family_tiles(counts | {"participation": Percentage(0, 0)})[1]
    assert (none.text, none.details, none.bar) == ("0", ("out of 0 (—)",), None)


def test_ministry_request_totals_tiles():
    """Ministry requests: the Join and Leave request counts."""
    found = ministry_followup_views.totals_tiles({"joining": 1234, "leaving": 0})
    assert [(t.key, t.label, t.text) for t in found] == [
        ("joining", "Join requests", "1,234"),
        ("leaving", "Leave requests", "0"),
    ]


def test_financial_summary_tiles_never_read_an_unknown_total_as_zero():
    """Financial stewardship: Families, the total in dollars and cents and
    the Families that cannot contribute; a total not added up is "—"."""
    summary = {"families": 51, "annual_total": MoneyAmount(6295950), "cannot_give": 0}
    found = financial_views.summary_tiles(summary)
    assert [(t.key, t.text) for t in found] == [
        ("families", "51"),
        ("annual-total", "$62,959.50"),
        ("cannot-give", "0"),
    ]
    unknown = financial_views.summary_tiles(
        summary | {"annual_total": MoneyAmount(None)}
    )[1]
    assert unknown.text == "—" and "every matching Family" in unknown.unknown_reason


def test_limitations_summary_tiles():
    """Limitations: its two counts."""
    found = limitation_views.summary_tiles({"cannot_serve": 2, "cannot_attend": 0})
    assert [(t.label, t.text) for t in found] == [
        ("Cannot participate in ministries", "2"),
        ("Families that cannot attend Mass", "0"),
    ]


def test_campaign_comparison_tiles_keep_the_shared_wording():
    """Campaign comparison: the PDF's and emails' own text, the median so
    far before the comparison lines, and a "—" with its reason."""
    found = comparison_views.headline_tiles(
        [
            {
                "key": "average",
                "label": "Average pledge",
                "value": "$1,200.00",
                "median": "$1,000.00",
                "lines": ["Last year on day 12: $1,100.00 (↑ 9.1% more)"],
                "reason": None,
            },
            {
                "key": "renewal",
                "label": "Renewal",
                "value": "—",
                "median": None,
                "lines": [],
                "reason": "No past year.",
            },
        ]
    )
    assert found == [
        StatTile(
            key="average",
            label="Average pledge",
            text="$1,200.00",
            details=(
                "Median so far: $1,000.00",
                "Last year on day 12: $1,100.00 (↑ 9.1% more)",
            ),
        ),
        StatTile(
            key="renewal", label="Renewal", text="—", unknown_reason="No past year."
        ),
    ]


def test_saved_daily_report_figures_are_drawn_as_emailed():
    """The saved digest page draws the stored figures word for word; a
    stored "—" says it was not available, since no reason was saved."""
    found = digest_presentation.saved_tiles(
        {
            "tiles": [
                {"label": "Families", "value": "36", "lines": ["Last year: 30"]},
                {"label": "Renewal", "value": "—", "lines": []},
            ]
        }
    )
    assert [(t.key, t.text, t.details) for t in found] == [
        ("figure-1", "36", ("Last year: 30",)),
        ("figure-2", "—", ()),
    ]
    assert found[1].unknown_reason == (
        "This figure was not available on the report day."
    )


def test_the_include_hides_every_arrow_of_a_line_and_escapes_its_text():
    """A comparison line's arrows are hidden from screen readers, which read
    the words; the rest of the line is escaped as plain text (#1637)."""
    html = render(
        [
            shown(
                "families",
                "Families",
                "40",
                details=(
                    "Last year: 30 (↑ 33.3% more)",
                    "<b>2054</b>: 40 (→ the same)",
                ),
            )
        ],
        tiles_label="Headline figures",
    )
    assert (
        '<span class="stat-detail">Last year: 30 '
        '(<span aria-hidden="true">↑</span> 33.3% more)</span>'
    ) in html
    assert (
        '&lt;b&gt;2054&lt;/b&gt;: 40 (<span aria-hidden="true">→</span> the same)'
    ) in html
