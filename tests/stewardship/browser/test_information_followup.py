"""Mobile, keyboard, no-script and accessibility checks for staff follow-up."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_staff_queue_detail_history_and_accessibility(
    page, component_origin, axe_source, width
):
    """The native workflow exposes safe filters and accessible correction history."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/information")
    search = page.get_by_label(
        "Search Family name, DUID, submitted text or Staff notes"
    )
    search.fill("Private name")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "disposition"
    assert page.get_by_role("button", name="Next page").is_visible()
    assert page.locator("form").evaluate_all(
        "nodes => nodes.every(node => node.method === 'post')"
    )
    page.route("**/information/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "search=Private+name" in sent.value.post_data
    assert "Private" not in sent.value.url and "?" not in sent.value.url
    for path in (
        "/information",
        "/information-item",
        "/information-gated",
        "/information-conflict",
    ):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/information-item")
    assert (
        page.get_by_label("Staff notes", exact=True).input_value()
        == "Called; awaiting a response."
    )
    assert page.get_by_label("Follow-up completed", exact=True).is_checked()
    assert page.get_by_label("If clearing completion", exact=False).is_visible()
    assert page.get_by_role("link", name="Open the replacement request").is_visible()
    assert page.get_by_role("heading", name="Staff edit history").is_visible()
    assert page.get_by_text(
        "This correction is resolved in the weekly digest workflow. "
        "Resolution does not necessarily mean an email was delivered.",
        exact=True,
    ).is_visible()
    assert page.get_by_text("A correction has been delivered", exact=False).count() == 0
    assert page.locator("our").count() == 0
    page.goto(component_origin + "/information-unresolved")
    assert page.get_by_text(
        "This change is visible to the weekly digest correction workflow; "
        "the old request is no longer actionable.",
        exact=True,
    ).is_visible()
    page.goto(component_origin + "/information-gated")
    assert page.get_by_role("button", name="Save follow-up").is_disabled()
    page.goto(component_origin + "/information-queue-gated")
    assert page.get_by_role("button", name="Queue complete export").is_disabled()


def export_post(page, component_origin):
    """The same complete-result form works with or without browser scripting."""
    page.goto(component_origin + "/information")
    page.get_by_label("Export format", exact=True).select_option("xlsx")
    page.get_by_label("Export timezone", exact=True).select_option("UTC")
    page.get_by_label("Include complete Staff workflow history").check()
    page.route("**/information/export", lambda route: route.fulfill(body="Queued"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Queue complete export").click()
    assert "search=Sample" in sent.value.post_data
    assert "format=xlsx" in sent.value.post_data
    assert "history=yes" in sent.value.post_data
    assert "browser_timezone=UTC" in sent.value.post_data
    assert "Sample" not in sent.value.url and "?" not in sent.value.url


def test_staff_complete_export_native_post(page, component_origin):
    """Private filters are submitted as a body, never serialized into an export URL."""
    export_post(page, component_origin)


def test_staff_native_workflow_without_scripts(browser_engine, component_origin):
    """JavaScript is optional for reading, searching, editing and confirmation."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        export_post(page, component_origin)
        page.goto(component_origin + "/information")
        assert page.get_by_role("button", name="Apply filters").is_visible()
        page.goto(component_origin + "/information-item")
        page.get_by_label("Follow-up completed", exact=True).uncheck()
        page.get_by_label("If clearing completion", exact=False).check()
        page.get_by_label("Staff notes", exact=True).fill("Reopened")
        assert page.get_by_role("button", name="Save follow-up").is_enabled()
        assert page.locator("input[name=expected_version]").input_value() == "2"
        page.route("**/update", lambda route: route.fulfill(body="Saved"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Save follow-up").click()
        assert "notes=Reopened" in sent.value.post_data
        assert "confirm_clear=yes" in sent.value.post_data
        assert "followed_up=" not in sent.value.post_data
        assert "Reopened" not in sent.value.url
    finally:
        context.close()
