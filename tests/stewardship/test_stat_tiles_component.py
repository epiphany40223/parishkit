"""The shared stat tiles: the builder, its include and the guard (#1540).

ui-conventions "Stat tiles": every Admin headline figure is a tile built by
``web.stat_tiles.tile()`` and drawn by ``components/stat-tiles.html``. These
tests check the builder's formatting and unknown values without rendering,
render the include, and keep pages from drawing tiles by hand again: the
templates that still do are listed, each with why, and the list fails on a
stale entry too.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.domain import Money, Percentage
from parishkit.stewardship.web.stat_tiles import StatTile, tile

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
COMPONENT = "components/stat-tiles.html"
# What hand-drawn stat-tile markup looks like: the tile classes, or the
# saved daily report's comparison figures (the same figures drawn as a dl).
HAND_DRAWN = re.compile(
    r'class="[^"]*\bstat-(?:tiles?|value|label)\b|digest-comparison-figures'
)
# Templates that still draw stat tiles by hand, each with why. A template
# leaves this list when it draws the include; the list may only shrink.
STILL_HAND_DRAWN = {
    # Home's Family participation, Ministry requests and Financial
    # stewardship move onto the component in #1540 slice 2.
    "home.html",
    "ministry-followup.html",
    "financial-report.html",
    # Campaign comparison and the saved daily report's comparison figures
    # move in #1540 slice 4, with the trend line.
    "campaign-comparison.html",
    "daily-digest.html",
    # Participation's statistics move onto the component next in this
    # slice.
    "participation.html",
    # The Response dashboard's stage tiles move onto the component next in
    # this slice.
    "response-dashboard.html",
}


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


def test_details_drop_empty_lines_and_stop_at_two():
    """Empty explanation lines are left out; a third line is refused."""
    assert tile("k", "L", 1, details=("", "one", None)).details == ("one",)
    with pytest.raises(ValueError):
        tile("k", "L", 1, details=("a", "b", "c"))


def test_a_share_bar_is_drawn_only_against_a_whole_above_zero():
    """``share`` draws the bar; a share of nothing has no bar."""
    assert tile("k", "L", 5, share=(5, 4)).bar == (5, 4)
    assert tile("k", "L", 0, share=(0, 0)).bar is None


def test_the_include_reads_label_value_and_details_in_order():
    """One list named by its heading, a tile per figure keyed for tests, the
    visible text in reading order with nothing hidden from assistive
    technology but the bar, and no mouse-only title tooltip."""
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
        '<span class="stat-detail stat-trend">↑ 12.5% more</span>'
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
    assert 'aria-controls="stat-pledges-tip"' in html
    assert '<span class="toggletip-bubble" id="stat-pledges-tip" hidden>' in html
    assert "Not added up yet." in html


def test_no_new_template_draws_stat_tiles_by_hand():
    """Only the listed templates draw their own tiles; a stale entry fails."""
    found = {
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if path.relative_to(TEMPLATES).as_posix() != COMPONENT
        and HAND_DRAWN.search(path.read_text())
    }
    assert found == STILL_HAND_DRAWN
