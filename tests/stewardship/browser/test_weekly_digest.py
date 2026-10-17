"""Private weekly report pages remain usable on phones."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_weekly_detail_is_responsive_accessible_and_escaped(
    page, component_origin, axe_source, width
):
    """Native links and historical status remain understandable at both sizes."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + "/weekly-digest")
    visible(page.get_by_role("link", name="Next page"))
    page.get_by_role(
        "link", name="Open full captured request and current status"
    ).click()
    visible(page.get_by_role("heading", name="Full text at capture"))
    # The trail is the way back to the report (navigation rule 3, #521).
    assert page.get_by_role("link", name="Return to Weekly report").count() == 0
    # The Family DUID is an identifier, shown without grouping.
    assert "1234567" in page.locator("main").inner_text()
    assert "1,234,567" not in page.locator("main").inner_text()
    assert "Superseded by a later response" in page.locator("main").inner_text()
    assert (
        "<script>not executable</script>"
        in page.locator(".content-sample").inner_text()
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact}) => ({id, impact}))""")
    assert violations == []


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("mode", ["production", "testing"])
def test_weekly_report_shows_the_response_funnel(
    page, component_origin, axe_source, width, mode
):
    """The list page shows the funnel after the requests; a request's page doesn't.

    The panel is the daily report's (#477): four stages and one figure,
    readable, accessible and within a phone's width. A Testing report shows
    its Testing notice and, in place of the funnel, the Testing note.
    """
    page.set_viewport_size({"width": width, "height": 900})
    testing = mode == "testing"
    page.goto(
        component_origin + ("/weekly-digest-testing" if testing else "/weekly-digest")
    )
    main = page.locator("main")
    visible(page.get_by_role("link", name="Next page"))
    if testing:
        assert "This report was generated in Testing mode." in main.inner_text()
        assert "Testing digests leave out the response funnel." in main.inner_text()
        assert page.locator("[data-digest-funnel]").count() == 0
    else:
        assert "generated in Testing mode" not in main.inner_text()
        assert "Testing digests leave out" not in main.inner_text()
        funnel = page.locator("[data-digest-funnel]")
        visible(funnel.get_by_role("heading", name="Response funnel"))
        assert funnel.locator("tbody tr").count() == 4
        text = funnel.inner_text()
        assert "Email invites sent" in text and "40%" in text
        # No side figures (#1532).
        assert "more than once" not in text and "of Submitted" not in text
        # After the requests and their pager, outside the in-place region.
        assert page.evaluate(
            """() => {
              const region = document.querySelector('#weekly-report-page');
              const panel = document.querySelector('[data-digest-funnel]');
              return !region.contains(panel) && Boolean(
                region.compareDocumentPosition(panel)
                & Node.DOCUMENT_POSITION_FOLLOWING);
            }"""
        )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact}) => ({id, impact}))""")
        == []
    )
    if not testing:
        page.goto(component_origin + "/weekly-detail")
        visible(page.get_by_role("heading", name="Full text at capture"))
        assert page.locator("[data-digest-funnel]").count() == 0


@pytest.mark.parametrize("width", [320, 1280])
def test_manual_report_requires_accessible_explicit_confirmation(
    page, component_origin, axe_source, width
):
    """The real form retains native confirmation semantics and fits a phone."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/weekly-manual")
    confirmation = page.get_by_role("checkbox")
    assert not page.locator("form.panel").evaluate("form => form.checkValidity()")
    confirmation.focus()
    page.keyboard.press("Space")
    assert confirmation.is_checked()
    assert page.locator("form.panel").evaluate("form => form.checkValidity()")
    visible(page.get_by_role("button", name="Queue new manual report"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact}) => ({id, impact}))""")
        == []
    )
