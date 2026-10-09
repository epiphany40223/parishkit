"""Home's My Ministries panel across engines and widths (#533 slice 1)."""

import pytest

from .my_ministries_components import EMPTY, PATH
from .test_family_response import expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
QUEUE = "/admin/reports/ministries/follow-up/"
# Lines an element's text takes: its height over its line height.
LINES = """e => {
  const style = getComputedStyle(e);
  const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
  return Math.round(e.getBoundingClientRect().height / line);
}"""


@pytest.mark.parametrize("width", [320, 1280])
def test_my_ministries_reads_on_a_phone_and_links_each_queue(
    page, component_origin, axe_source, width
):
    """Each Ministry stays legible and links its queue by GET."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (PATH, EMPTY):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == [], path
    page.goto(component_origin + PATH)
    panel = page.get_by_role("region", name="My Ministries")
    expect(panel).to_be_visible()
    expect(
        panel.get_by_text("still open (New or In progress)", exact=False)
    ).to_be_visible()
    # Legible at any width: a short name on one line, counts never split
    # mid-word (the review found letters stacked one per line at 320 px).
    pantry = panel.locator('li[data-ministry="9"]')
    assert pantry.locator(".my-ministry-name").evaluate(LINES) == 1
    counts = pantry.locator(".my-ministry-counts")
    expect(counts).to_have_text("3 asking to join · 1 asking to leave")
    assert counts.evaluate(LINES) <= 2
    long_name = panel.locator('li[data-ministry="12"] .my-ministry-name')
    assert long_name.evaluate(LINES) <= (3 if width == 320 else 1)
    # Words wrap whole: no line of the long name is a fragment of a word.
    assert long_name.evaluate("e => e.offsetWidth") >= 150
    link = panel.get_by_role("link", name="Open requests for Food pantry")
    expect(link).to_have_attribute("href", QUEUE + "?ministry=9")
    # A Ministry with nothing open says so and offers no empty queue.
    nothing = panel.locator('li[data-ministry="12"]')
    expect(nothing).to_contain_text("No open requests")
    assert nothing.get_by_role("link").count() == 0
    # A removed Ministry with open requests is still listed, marked.
    expect(panel.locator('li[data-ministry="40"]')).to_contain_text(
        "no longer in this campaign"
    )
    expect(
        panel.get_by_role("link", name="Ministry follow-up", exact=True)
    ).to_have_attribute("href", QUEUE)
    expect(
        panel.get_by_role("link", name="Follow-up packet for my Ministries")
    ).to_have_attribute("href", "/admin/reports/ministries/#ministry-packet-heading")
    page.goto(component_origin + EMPTY)
    expect(
        page.get_by_text("None of your Ministries is part of the current campaign.")
    ).to_be_visible()
    expect(
        page.get_by_role("link", name="Ministry follow-up", exact=True)
    ).to_be_visible()
