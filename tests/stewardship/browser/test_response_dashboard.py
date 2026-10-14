"""The response dashboard draws its charts under the CSP, accessibly (#477)."""

import re

import pytest
from django.urls import reverse

from .response_dashboard_components import PATH
from .test_charts import assert_clean, watch
from .waits import eventually, has_text

DIRECTORY = reverse("admin:family_directory")

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


def test_dashboard_renders_tiles_and_both_charts_cleanly(
    page, component_origin, axe_source
):
    """Tiles first, then both charts drawn with no policy violation or error.

    The activity line drops to zero in the quiet hours between busy ones, the
    page fits a phone-width window, and axe finds nothing to report.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    assert page.locator(".stat-tile").count() == 5
    assert page.locator(".stat-value").all_inner_texts() == ["5", "6", "4", "3", "3"]
    # The tiles come before the charts in reading order.
    tiles = page.locator(".stat-tiles").bounding_box()
    funnel = page.locator("[data-chart='response-funnel-spec']").bounding_box()
    assert tiles["y"] < funnel["y"]
    # Four hours, one point per series each: the two quiet hours are zeros.
    activity = page.locator("[data-chart='response-activity-spec']")
    assert activity.locator("svg g.mark-symbol.role-mark path").count() == 12
    assert_clean(page, errors)
    page.set_viewport_size({"width": 390, "height": 844})
    eventually(
        page,
        "document.querySelector(\"[data-chart='response-activity-spec'] svg\")"
        ".getBoundingClientRect().width < 390",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_testing_view_without_a_rehearsal_is_accessible(
    page, component_origin, axe_source
):
    """The empty Testing view says why it is empty and marks the mode chosen."""
    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-testing")
    assert "no Testing responses to show" in page.locator("main").inner_text()
    current = page.locator(".choice-links a[aria-current=page]")
    assert current.all_inner_texts() == ["Testing rehearsal"]
    assert page.locator("[data-chart]").count() == 0
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


@pytest.mark.parametrize(
    ("path", "rules"),
    [("/response-dashboard-empty", 0), ("/response-dashboard-empty-sends", 2)],
)
def test_empty_campaign_renders_cleanly(
    page, component_origin, axe_source, path, rules
):
    """Nothing invited or followed yet: zero tiles, a plain sentence, two charts.

    Both charts still draw (the activity chart with only its send markers,
    when there are any) with no policy violation, and axe is clean.
    """
    from playwright.sync_api import expect

    # The empty activity chart's known warning (#1327).
    errors = watch(page, empty_activity=True)
    page.goto(component_origin + path)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    assert page.locator(".stat-value").all_inner_texts() == ["0"] * 5
    main = page.locator("main").inner_text()
    assert "No invitations have been delivered yet." in main
    assert "compared with invited" not in main
    assert "No link follows, form opens or submissions yet." in main
    activity = page.locator("[data-chart='response-activity-spec']")
    assert activity.locator("svg g.mark-rule.role-mark line").count() == rules
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_switches_refresh_the_dashboard_in_place(page, component_origin):
    """Grain and mode change the page without a reload or a jump.

    The page's own marker survives (no navigation), the scroll position is
    kept, the chart is drawn again at the other grain, the address follows
    the choice (and the region's fragment), keyboard focus stays on the
    chosen switch, and the live region names the view now shown (#519).
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.set_viewport_size({"width": 1000, "height": 700})
    page.goto(component_origin + PATH)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    eventually(page, FITTED)
    page.evaluate("window.__dashboardMarker = 1")
    day = page.locator('[data-in-place="grain-day"]')
    day.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, 100)")
    before = page.evaluate("window.scrollY")
    assert before > 0
    day.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**?grain=day#response-dashboard")
    heading = page.locator("#activity-heading")
    expect(heading).to_have_text("Response activity by day")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    eventually(page, FITTED)
    assert page.evaluate("window.__dashboardMarker") == 1
    assert abs(page.evaluate("window.scrollY") - before) < 2
    assert page.evaluate("document.activeElement.dataset.inPlace") == "grain-day"
    has_text(page.get_by_role("status").filter(has_text="By day"), "By day")
    assert (
        page.locator('[data-in-place="grain-day"]').get_attribute("aria-current")
        == "page"
    )
    # The redrawn chart is the daily one: one day, three points.
    activity = page.locator("[data-chart='response-activity-spec']")
    assert activity.locator("svg g.mark-symbol.role-mark path").count() == 3
    assert_clean(page, errors)
    # The mode switch works the same way; the empty Testing view replaces it.
    page.locator('[data-in-place="mode-testing"]').click()
    page.wait_for_url("**mode=testing*#response-dashboard")
    expect(page.locator("main")).to_contain_text("no Testing responses to show")
    assert page.evaluate("window.__dashboardMarker") == 1
    assert page.evaluate("document.activeElement.dataset.inPlace") == "mode-testing"
    assert page.locator("[data-chart]").count() == 0
    assert_clean(page, errors)


@pytest.mark.parametrize(
    ("title", "response", "check", "columns"),
    [
        ("Families that submitted", "submitted", "any", ["Submissions"]),
        (
            "Invited Families that never opened the form",
            "never-opened",
            "any",
            ["Invitation delivered", "Link followed"],
        ),
        ("ParishSoft data to check", "any", "anything", ["What to check"]),
    ],
)
def test_lists_of_families_open_the_directory_presets(
    page, component_origin, title, response, check, columns
):
    """A Production list link opens the directory with that list's choices.

    The link carries only closed presets (#933); the directory shows them
    chosen, with the list's columns, and a further filter change posts
    privately to the directory's own address, with no query string. The
    answer's data-page-address then replaces the preset in the address bar
    (#536), so Reload or Back cannot bring the preset back: here the answer
    has a search applied, so the address is the bare page.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + PATH)
    page.get_by_role("link", name=title, exact=True).click()
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
    preset = page.url
    assert "?" in preset
    answer = page.request.get(component_origin + "/family-directory").text()
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert f"response={response}" in sent.value.post_data
    assert "?" not in sent.value.url
    expect(page).to_have_url(preset.partition("?")[0])
    assert_clean(page, errors)


def test_testing_links_open_the_directory_testing_switch(page, component_origin):
    """A Testing dashboard link opens the directory in Testing rehearsal (#933).

    The directory shows the Responses shown switch set to Testing rehearsal,
    says so in a notice and leaves out the Production-only Responded column.
    Switching back to Production is a filter like the others: Apply posts
    it privately, the answer replaces the table in place without moving the
    filter bar, and the address bar drops ``mode=testing``.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-rehearsal")
    page.get_by_role("link", name="Families that submitted", exact=True).click()
    expect(page).to_have_url(re.compile(r"[?&]mode=testing$"))
    switch = page.get_by_label("Responses shown", exact=True)
    expect(switch).to_have_value("testing")
    expect(page.locator("[data-testing-responses]")).to_be_visible()
    headings = page.locator("table thead th").all_inner_texts()
    assert not any(text.strip() == "Responded" for text in headings), headings
    production = (
        component_origin
        + DIRECTORY
        + ("?response=submitted&responses=yes&sort=submitted")
    )
    answer = page.request.get(production).text()
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    # The filter bar's place on the page (not in the viewport: the answer
    # may scroll to the table).
    layout = """() => {
        const box = document.querySelector('#table-filters').getBoundingClientRect();
        return [box.x + scrollX, box.y + scrollY, box.width, box.height];
    }"""
    before = page.evaluate(layout)
    switch.select_option("production")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "mode=production" in sent.value.post_data
    assert "?" not in sent.value.url
    expect(page).to_have_url(production)
    expect(page.locator("[data-testing-responses]")).to_have_count(0)
    expect(page.locator("table thead th", has_text="Responded")).to_have_count(1)
    assert page.evaluate(layout) == pytest.approx(before, abs=1)
    assert_clean(page, errors)


def test_switch_sends_mode_once_after_the_rehearsal_ends(page, component_origin):
    """An answer drawn without the switch never doubles the mode (#933).

    If the rehearsal ends after the page loads, the in-place answer has no
    Responses shown switch. The page keeps its own select, and since the
    fresh form carries no hidden ``mode`` either, the next Apply still
    sends ``mode`` once (the server then refuses Testing with its 409),
    never twice, which every later Apply would answer with a 400.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    testing = DIRECTORY + "?response=started&responses=yes&mode=testing"
    page.goto(component_origin + testing)
    answer = page.request.get(component_origin + "/family-directory").text()
    assert 'id="directory-mode"' not in answer
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    for _ in range(2):
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert sent.value.post_data.count("mode=") == 1, sent.value.post_data
        # The answer (no rehearsal notice) is in place before the next Apply.
        expect(page.locator("[data-testing-responses]")).to_have_count(0)
    assert_clean(page, errors)
