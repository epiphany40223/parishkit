"""The Response dashboard draws its charts under the CSP, accessibly (#477, #1537)."""

from urllib.parse import urlencode

import pytest
from django.urls import reverse

from ..test_directories import RESPONSE_LIST_PRESETS
from .test_charts import assert_clean, watch
from .waits import visible

DIRECTORY = reverse("admin:family_directory")
ZONE = "timezone=America%2FLos_Angeles"

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Every drawn chart is as wide as its panel (chart-v1.js refits a chart whose
# panel changed width after it was drawn).
FITTED = """() => [...document.querySelectorAll("[data-chart-view] svg")].every(
  svg => Math.abs(
    Number(svg.getAttribute("width")) - svg.parentElement.clientWidth
  ) <= 1)"""

AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact, nodes}) => ({
  id, impact, html: nodes.map(node => node.html.slice(0, 300))
}))"""


# The Summary's two rows of tiles (#1537).
POPULATION = '#response-summary [aria-label="Population"]'
STAGES = '#response-summary [aria-label="Families by stage"]'


def test_dashboard_renders_one_summary_and_the_daily_chart_cleanly(
    page, component_origin, axe_source
):
    """One Summary panel, its Population row of four tiles over its
    Families by stage row of five, then the daily chart, all drawn with no
    policy violation or error (Administrator, 2026-10-18, #1537).

    No funnel chart and no "Responses and email reach" chart: the tiles
    give their figures. The page fits a phone-width window, and axe finds
    nothing to report.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard")
    # The daily chart is the page's only chart.
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert page.locator("[data-chart]").count() == 1
    assert page.locator("#participation-chart [data-chart]").count() == 1
    summary = page.locator("#response-summary")
    assert page.locator("section.panel .stat-tiles").count() == 2
    assert summary.locator(".stat-tiles").count() == 2
    assert page.locator(POPULATION + " .stat-label").all_inner_texts() == [
        "Families",
        "Active Members",
        "Annual pledges",
        "Last year's pledges (all Families)",
    ]
    assert page.locator(STAGES + " .stat-label").all_inner_texts() == [
        "Invited",
        "Link followed",
        "Form opened",
        "Progressed past the first step",
        "Submitted",
    ]
    assert page.locator(STAGES + " .stat-value").all_inner_texts() == [
        "5",
        "6",
        "4",
        "3",
        "3",
    ]
    link = page.locator(STAGES + ' [data-stat="link_followed"]').inner_text()
    assert "Includes mail-scanner prefetches" in link
    # Row 1 over row 2, then "Data as of", then the daily chart.
    population = page.locator(POPULATION).bounding_box()
    stages = page.locator(STAGES).bounding_box()
    as_of = summary.get_by_text("Data as of").bounding_box()
    chart = page.locator("#participation-chart").bounding_box()
    assert population["y"] + population["height"] <= stages["y"]
    assert stages["y"] < as_of["y"] < chart["y"]
    # No activity chart, no Also counted figures, no lists of Families, no
    # funnel and no reach chart (#1537).
    main = page.locator("main").inner_text()
    for gone in (
        "Also counted",
        "Lists of Families",
        "Response funnel",
        "Responses and email reach",
        "Families with eligible email",
    ):
        assert gone not in main, gone
    assert_clean(page, errors)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


# Each Summary tile: its row, its box, and whether its value fits inside it
# (the value's own scroll width within its box, and its box within the
# tile's padding box).
SUMMARY_TILES = """() => [...document.querySelectorAll(
    '#response-summary .stat-tile')].map((tile) => {
  const value = tile.querySelector('.stat-value');
  const t = tile.getBoundingClientRect(), v = value.getBoundingClientRect();
  return {row: tile.parentElement.getAttribute('aria-label'),
          key: tile.dataset.stat, top: Math.round(t.top), left: t.left,
          right: t.right, width: t.width,
          fits: value.scrollWidth <= Math.ceil(v.width)
            && v.left >= t.left && v.right <= t.right};
})"""


# How many tiles each Summary row puts on each line, by window width (with
# the Admin sidebar from 1100 pixels): a phone stacks, and otherwise a row
# never leaves one tile alone on a line, so four go 4 or 2 + 2 and five go
# 5 or 3 + 2 (ui-conventions "Stat tiles", #1537).
SUMMARY_LINES = {
    320: ([1, 1, 1, 1], [1, 1, 1, 1, 1]),
    760: ([2, 2], [3, 2]),
    1100: ([2, 2], [3, 2]),
    1280: ([4], [3, 2]),
    1600: ([4], [5]),
}


@pytest.mark.parametrize("width", sorted(SUMMARY_LINES))
def test_summary_rows_fit_and_balance(page, component_origin, width):
    """At every window width each Summary value stays inside its tile, no
    tile is narrower than 12.5rem unless the row stacks on a phone, tiles on
    one line never overlap, each row's lines are balanced, and the page
    never scrolls sideways (ui-conventions "Stat tiles", #1537)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/response-dashboard")
    tiles = page.evaluate(SUMMARY_TILES)
    assert len(tiles) == 9
    assert all(tile["fits"] for tile in tiles), tiles
    rem = page.evaluate(
        "parseFloat(getComputedStyle(document.documentElement).fontSize)"
    )
    for name, expected in zip(
        ("Population", "Families by stage"), SUMMARY_LINES[width], strict=True
    ):
        lines = {}
        for tile in (tile for tile in tiles if tile["row"] == name):
            lines.setdefault(tile["top"], []).append(tile)
        assert [len(line) for line in lines.values()] == expected, (name, lines)
        for line in lines.values():
            line.sort(key=lambda tile: tile["left"])
            assert all(
                a["right"] <= b["left"] for a, b in zip(line, line[1:], strict=False)
            )
            if width > 40 * rem:
                assert all(tile["width"] >= 12.5 * rem - 1 for tile in line), line
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


# Each row's balance thresholds (in rem) with the lines it draws just under
# and just over them: four tiles fit on one line from 52.25rem; five from
# 65.5rem, and three of them from 39rem (#1537).
THRESHOLDS = (
    ("Population", 52.25, [2, 2], [4]),
    ("Families by stage", 65.5, [3, 2], [5]),
    ("Families by stage", 39, [1, 1, 1, 1, 1], [3, 2]),
)

# Sets one Summary row's width and returns how many tiles each of its lines
# holds, top to bottom.
ROW_LINES = """([name, width]) => {
  const row = document.querySelector(
    `#response-summary [aria-label="${name}"]`);
  row.style.width = width;
  const lines = new Map();
  for (const tile of row.children) {
    const top = Math.round(tile.getBoundingClientRect().top);
    lines.set(top, (lines.get(top) || 0) + 1);
  }
  return [...lines.keys()].sort((a, b) => a - b).map((top) => lines.get(top));
}"""


@pytest.mark.parametrize("font", ["100%", "150%"])
def test_summary_rows_balance_at_fractional_widths(page, component_origin, font):
    """A row whose width is a fraction of a pixel under or over one of its
    thresholds still never leaves a tile alone on a line (#1537 review).

    Window widths land on whole pixels, but a row's width is often
    fractional (sidebar, zoom, scrollbar), and the CSS's "(W - 100%) * k"
    term is only huge once the row is far enough under W. Widths 0.02px
    and 0.25px either side of each threshold cover the band a too-small k
    left (up to 0.4px), at the reader's default font size and at 150%.
    """
    page.set_viewport_size({"width": 1700, "height": 900})
    page.goto(component_origin + "/response-dashboard")
    page.evaluate(f"document.documentElement.style.fontSize = '{font}'")
    for name, threshold, under, over in THRESHOLDS:
        for delta in (0.02, 0.25):
            for sign, expected in (("-", under), ("+", over)):
                width = f"calc({threshold}rem {sign} {delta}px)"
                lines = page.evaluate(ROW_LINES, [name, width])
                assert lines == expected, (name, width, lines)


# The longest value of each StatTile.fit size: 9, 13 and 17 characters.
LONGEST_VALUES = ("$9,999.99", "$9,999,999.99", "$9,999,999,999.99")

# Writes ``text`` (at its StatTile.fit size) into every Summary tile and
# returns, for each, how far its text runs past the tile's border box (0
# when inside), how many lines it takes, and its value's height in rem.
# The text is measured on a range: a tile is a flex column, so the value's
# element is stretched to the tile's width (test_participation_in_place).
PHONE_FIT = """([text, fit]) => [...document.querySelectorAll(
    '#response-summary .stat-tile[data-stat]')].map((tile) => {
  const value = tile.querySelector('.stat-value');
  value.textContent = text;
  value.className = ('stat-value ' + fit).trim();
  const outer = tile.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(value);
  const inner = range.getBoundingClientRect();
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return {
    overflow: Math.max(0, outer.left - inner.left, inner.right - outer.right),
    lines: new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size,
    height: Math.round(value.getBoundingClientRect().height / rem * 100) / 100,
  };
})"""


@pytest.mark.parametrize("width", [320, 331, 390])
@pytest.mark.parametrize("font", ["100%", "150%", "200%"])
def test_values_fit_a_phone_with_large_text(page, component_origin, width, font):
    """On a phone-width window with large text the stacked tiles stay
    inside the window: each value, at every size's longest length, is drawn
    smaller instead of making its tile and the page wider, still on one
    line of the same height, so the page never scrolls sideways (#1667).

    At 320px and 150% the full-size "$2,234.56" in Linux's DejaVu font made
    the Population tiles about 12px wider than their panel; 150% and
    200% stand in for a reader's larger default font size.
    """
    from parishkit.stewardship.web.stat_tiles import StatTile

    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + "/response-dashboard")
    page.evaluate(f"document.documentElement.style.fontSize = '{font}'")
    tiles = page.evaluate(SUMMARY_TILES)
    assert len(tiles) == 9
    assert all(tile["fits"] for tile in tiles), tiles
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for text in LONGEST_VALUES:
        fit = StatTile(key="probe", label="Probe", text=text).fit
        drawn = page.evaluate(PHONE_FIT, [text, fit])
        assert len(drawn) == 9
        assert drawn == [{"overflow": 0, "lines": 1, "height": 2.1}] * 9, (text, drawn)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), text


def test_testing_view_without_a_rehearsal_is_accessible(
    page, component_origin, axe_source
):
    """A Testing page whose rehearsal ended while it was read says why it is
    empty; no choice is offered, since the site's mode decides (#2324)."""
    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-testing")
    main = page.locator("main").inner_text()
    assert "no Testing responses to show" in main
    assert "count Production responses only" in main
    assert page.locator(".segmented-choice").count() == 0
    assert page.locator("[data-chart]").count() == 0
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_testing_view_shows_the_rehearsal_stages_only(
    page, component_origin, axe_source
):
    """During a rehearsal the Summary's Families by stage row shows it; the
    Population row and the Production-only panels are left out, with
    one line saying why (#1537)."""
    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-rehearsal")
    assert page.locator("[data-chart]").count() == 0
    assert page.locator(STAGES + " .stat-tile").count() == 5
    assert page.locator(POPULATION).count() == 0
    visible(page.get_by_text("never counted in Production"))
    visible(page.locator("#production-only"))
    for gone in ("#participation-chart", "#participation-export form"):
        assert page.locator(gone).count() == 0
    assert page.get_by_role("group", name="Families counted").count() == 0
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_empty_campaign_renders_cleanly(page, component_origin, axe_source):
    """Nothing invited or followed yet: zero tiles and a plain sentence,
    with no policy violation; axe is clean."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-empty")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert page.locator(STAGES + " .stat-value").all_inner_texts() == ["0"] * 5
    main = page.locator("#response-summary").inner_text()
    assert "No invitations have been delivered yet." in main
    assert "compared with invited" not in main
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_families_counted_shows_the_spinner_while_it_reads(page, component_origin):
    """Choosing In this campaign today (#2324) re-reads the page in place: the
    shared working spinner shows beside the choice while the read is under
    way, in space the pair always keeps (nothing moves), then the Summary's
    tiles are the current population's, with no reload or jump."""
    from playwright.sync_api import expect

    from .report_components import SORTED

    errors = watch(page)
    page.set_viewport_size({"width": 1000, "height": 700})
    page.goto(component_origin + SORTED.url())
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    page.evaluate("window.__dashboardMarker = 1")
    choice = page.get_by_role("group", name="Families counted")
    spinner = choice.locator(".spinner")
    expect(spinner).to_be_hidden()
    layout = choice.evaluate(
        "(node) => [...node.children].map((child) => "
        "JSON.stringify(child.getBoundingClientRect()))"
    )
    families = page.locator(POPULATION + ' [data-stat="active_families"] .stat-value')
    expect(families).to_have_text("1,010")
    before = page.evaluate("window.scrollY")
    # The answer is slow (report_components.SLOW_GETS).
    choice.get_by_role("link", name="In this campaign today").click()
    expect(spinner).to_be_visible()
    expect(page.locator("#response-dashboard")).to_have_attribute("aria-busy", "true")
    # No layout shift: the spinner fills space that was already kept.
    assert (
        choice.evaluate(
            "(node) => [...node.children].map((child) => "
            "JSON.stringify(child.getBoundingClientRect()))"
        )
        == layout
    )
    expect(families).to_have_text("1,000")
    expect(
        page.get_by_role("group", name="Families counted").locator(".spinner")
    ).to_be_hidden()
    expect(
        page.get_by_role("group", name="Families counted").locator(
            "a[aria-current=page]"
        )
    ).to_have_text("In this campaign today")
    assert page.evaluate("window.__dashboardMarker") == 1
    assert abs(page.evaluate("window.scrollY") - before) < 2
    assert "scope=current" in page.url
    assert_clean(page, errors)


@pytest.mark.parametrize(
    ("preset", "response", "check", "columns"),
    [
        ("submitted", "submitted", "any", ["Submissions"]),
        (
            "not-opened",
            "never-opened",
            "any",
            ["Invitation delivered", "Link followed"],
        ),
        ("data-quality", "any", "anything", ["What to check"]),
    ],
)
def test_list_presets_open_the_directory_with_their_choices(
    page, component_origin, preset, response, check, columns
):
    """A saved list link opens the directory with that list's choices.

    The link carries only closed presets (#933); the directory shows them
    chosen, with the list's columns, and a further filter change posts
    privately to the directory's own address, with no query string. The
    answer's data-page-address then replaces the preset in the address bar
    (#536), so Reload or Back cannot bring the preset back: here the answer
    has a search applied, so the address is the bare page.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    query = urlencode(RESPONSE_LIST_PRESETS[preset])
    page.goto(component_origin + DIRECTORY + "?" + query)
    expect(page.get_by_label("Response", exact=True)).to_have_value(response)
    assert page.get_by_label("ParishSoft data to check").input_value() == check
    assert page.get_by_label("Include response columns").is_checked() is (
        response != "any"
    )
    headings = [
        text.split("\n")[0].strip()
        for text in page.locator("table thead th").all_inner_texts()
    ]
    assert all(column in headings for column in columns), headings
    preset_url = page.url
    assert "?" in preset_url
    answer = page.request.get(component_origin + "/family-directory").text()
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert f"response={response}" in sent.value.post_data
    assert "?" not in sent.value.url
    expect(page).to_have_url(preset_url.partition("?")[0])
    assert_clean(page, errors)


def test_testing_site_directory_has_no_switch(page, component_origin):
    """A site in Testing mode shows the directory's Testing responses
    without a switch (#2324, #933): a notice says so, the Production-only
    Responded column is left out, and Apply filters sends no mode, since
    the site's mode decides."""
    from playwright.sync_api import expect

    from .directory_components import TESTING_DIRECTORY

    errors = watch(page)
    page.goto(component_origin + TESTING_DIRECTORY)
    expect(page.locator("[data-testing-responses]")).to_be_visible()
    assert page.get_by_role("group", name="Responses shown").count() == 0
    assert page.locator("#directory-mode, .segmented-choice").count() == 0
    headings = page.locator("table thead th").all_inner_texts()
    assert not any(text.strip() == "Responded" for text in headings), headings
    answer = page.content()
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "mode=" not in sent.value.post_data, sent.value.post_data
    assert "response=started" in sent.value.post_data
    assert_clean(page, errors)


# Each stage tile's share bar, with the tile's and the bar's boxes, read from
# the page itself so no assertion depends on content above the tiles.
TILE_BARS = """() => [...document.querySelectorAll('STAGES .stat-tile')]
  .map(tile => {
  const bar = tile.querySelector('.stat-bar progress');
  const t = tile.getBoundingClientRect(), b = bar.getBoundingClientRect();
  return {value: bar.value, max: bar.max,
          given: Number(bar.getAttribute('value')),
          hidden: bar.closest('[aria-hidden=true]') !== null,
          top: Math.round(t.top), tileBottom: t.bottom, barTop: b.top,
          barBottom: b.bottom, barWidth: b.width, tileWidth: t.width};
})""".replace("STAGES", STAGES)


@pytest.mark.parametrize("width", [1280, 390])
def test_stage_tiles_draw_their_share_bars_at_their_feet(page, component_origin, width):
    """Every tile has a share bar of Invited, at its foot, hidden from AT (#720).

    Invited's bar is full, and so is Link followed, whose share passes
    100%: the fixture has 6 links followed for 5 invited, so the bar is
    given 6 and the browser draws it full (its value reads back clamped to
    5). Tiles in one row stretch to one height, so their bars line up
    whatever their labels' wrapping.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/response-dashboard")
    bars = page.evaluate(TILE_BARS)
    assert [(bar["value"], bar["max"]) for bar in bars] == [
        (5, 5),
        (5, 5),
        (4, 5),
        (3, 5),
        (3, 5),
    ]
    assert [bar["given"] for bar in bars] == [5, 6, 4, 3, 3]
    assert all(bar["hidden"] for bar in bars)
    for bar in bars:
        # Inside the tile, near its foot, and nearly as wide as the tile.
        assert bar["barBottom"] <= bar["tileBottom"]
        assert bar["tileBottom"] - bar["barBottom"] < 40
        assert bar["barWidth"] > bar["tileWidth"] * 0.6
    rows = {}
    for bar in bars:
        rows.setdefault(bar["top"], []).append(round(bar["barTop"]))
    for tops in rows.values():
        assert max(tops) - min(tops) <= 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
