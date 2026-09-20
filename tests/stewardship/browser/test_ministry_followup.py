"""Native private Ministry follow-up queue, edit form and bulk assignment."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGES = (
    "/followup-queue",
    "/followup-all",
    "/followup-empty",
    "/followup-gated",
    "/followup-item",
    "/followup-closed",
    "/followup-item-stale",
    "/followup-item-gated",
    "/followup-error-400",
    "/followup-error-409",
    "/followup-error-503",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_followup_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Every state stays readable, escaped and operable without a JavaScript UI."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/followup-queue")
    assert page.get_by_text("Example <Member>", exact=True).count() == 1
    assert page.get_by_label("Select Example <Member>").is_visible()
    search = page.get_by_label("Search Member or Ministry name")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "ministry"
    # Bulk assignment needs one Ministry's assignees; otherwise it explains why.
    page.goto(component_origin + "/followup-all")
    assert page.get_by_role("button", name="Assign selected").count() == 0
    assert page.get_by_text("Choose one Ministry above", exact=False).is_visible()
    page.goto(component_origin + "/followup-item")
    assert page.get_by_text("Left a <private> voicemail", exact=True).count() == 2
    assert page.get_by_text("No <answer>", exact=False).count() == 1
    assert page.get_by_label("Assigned to").input_value() != ""
    # A revoked assignee is named, never silently dropped to "Unassigned".
    page.goto(component_origin + "/followup-item-stale")
    notice = page.get_by_text("can no longer follow up this Ministry", exact=False)
    assert notice.is_visible() and "leader@example.org" in notice.inner_text()
    assert page.get_by_text("New and Assigned follow the assignee", exact=False).count()
    # Closed outcomes are permanent: history remains, the form does not.
    page.goto(component_origin + "/followup-closed")
    assert page.get_by_role("button", name="Save follow-up").count() == 0
    assert page.get_by_text("Closed outcomes are permanent", exact=False).is_visible()
    for path, control in (
        ("/followup-item-gated", "Save follow-up"),
        ("/followup-gated", "Assign selected"),
    ):
        page.goto(component_origin + path)
        assert page.get_by_role("button", name=control).is_disabled()


def test_followup_filters_without_scripts(browser_engine, component_origin):
    """Identifying filters submit only through native POST, never the URL."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-queue")
        page.get_by_label("Search Member or Ministry name").fill("Private name")
        page.get_by_label("Assigned to").first.select_option("mine")
        page.route("**/follow-up/", lambda route: route.fulfill(body="Filtered"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert "search=Private+name" in sent.value.post_data
        assert "assignee=mine" in sent.value.post_data
        assert "ministry=9" in sent.value.post_data
        assert "Private" not in sent.value.url and "?" not in sent.value.url
    finally:
        context.close()


def test_followup_edit_and_bulk_assignment_without_scripts(
    browser_engine, component_origin
):
    """The edit and the exact versioned selection post natively with no URL state."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-item")
        page.get_by_label("Status").select_option("resolved")
        page.get_by_label("Outcome, required when resolving or closing").select_option(
            "joined"
        )
        page.get_by_label("How").select_option("email")
        page.get_by_label("Date (UTC)").fill("2026-09-19")
        page.get_by_label("Time (UTC)").fill("15:04")
        page.get_by_label("What happened").fill("Private reply")
        page.route("**/update", lambda route: route.fulfill(body="Saved"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Save follow-up").click()
        body = sent.value.post_data
        assert "state=resolved" in body and "outcome=joined" in body
        assert "expected_version=3" in body and "request_key=" in body
        assert "contact_channel=email" in body and "contact_date=2026-09-19" in body
        assert "Private" not in sent.value.url and "?" not in sent.value.url

        page.goto(component_origin + "/followup-queue")
        page.get_by_label("Select Example <Member>").check()
        page.get_by_label("Assign to").select_option(label="leader@example.org")
        page.route("**/assign", lambda route: route.fulfill(body="Assigned"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Assign selected").click()
        body = sent.value.post_data
        # The selection binds each request to the version this page displayed.
        assert "selected=00000000-0000-0000-0000-00000000005d%3A3" in body
        assert "ministry=9" in body and "assignee=00000000" in body
        assert "?" not in sent.value.url
    finally:
        context.close()
