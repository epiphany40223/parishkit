"""Three-engine private native directories, accessible contacts and no-script use."""

from uuid import UUID

import pytest

from .conftest import no_script_context
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_directories_are_accessible_and_keep_filters_in_post(
    page,
    component_origin,
    axe_source,
    width,
):
    """Actual templates expose codes directly and preserve private pagination."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/family-directory", "/postal-directory", "/directory-empty"):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type:'tag', values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/family-directory")
    visible(page.get_by_text("ABCDEFGH", exact=True))
    assert page.locator("Family").count() == 0
    page.get_by_text(
        "Contact details for Example <Family>, Example Head", exact=True
    ).click()
    visible(page.get_by_role("link", name="head@example.org", exact=True))
    visible(page.get_by_text("1 Example Street", exact=False))
    search = page.get_by_label(
        "Search by Family name, any member's name, DUID, envelope number or address"
    )
    search.fill("Private name")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "exact_code"
    page.get_by_label("Exact Family code").fill("abcd-efgh")
    # Mailing columns are one checkbox on the same page, off by default.
    mailing = page.get_by_label("Include mailing columns")
    assert not mailing.is_checked()
    mailing.check()
    page.route("**/families/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "search=Private+name" in sent.value.post_data
    assert "exact_code=abcd-efgh" in sent.value.post_data
    assert "mailing=yes" in sent.value.post_data
    assert "Private" not in sent.value.url and "abcd" not in sent.value.url


def test_directory_pagination_works_without_scripts(browser_engine, component_origin):
    """Codes, contacts and private navigation do not depend on JavaScript."""
    context = no_script_context(browser_engine)
    try:
        page = context.new_page()
        page.goto(component_origin + "/postal-directory")
        visible(page.get_by_text("ABCDEFGH", exact=True))
        visible(page.get_by_role("columnheader", name="Mailing address"))
        page.route("**/families/", lambda route: route.fulfill(body="Next page"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Next", exact=True).first.click()
        # Paging keeps the mailing columns on.
        assert (
            "page=2" in sent.value.post_data
            and "search=Example" in sent.value.post_data
            and "mailing=yes" in sent.value.post_data
        )
        assert "Example" not in sent.value.url and "?" not in sent.value.url
    finally:
        context.close()


@pytest.mark.parametrize("scripts", [True, False])
def test_complete_directory_export_controls_are_private_native_and_gated(
    browser_engine, component_origin, scripts
):
    """Export applied filters, not unsaved edits or the current page, on mobile."""
    options = {
        "timezone_id": "America/Detroit",
        "viewport": {"width": 320, "height": 900},
    }
    context = (
        browser_engine.new_context(**options)
        if scripts
        else no_script_context(browser_engine, **options)
    )
    try:
        page = context.new_page()
        page.goto(component_origin + "/family-directory")
        # The export controls are collapsed until asked for.
        assert not page.get_by_label("Export format").is_visible()
        page.get_by_text("Export complete results").click()
        assert page.get_by_label("Export timezone").input_value() == (
            "America/Detroit" if scripts else "UTC"
        )
        visible(page.get_by_text("51 estimated matching Families", exact=False))
        page.get_by_label(
            "Search by Family name, any member's name, DUID, envelope number or address"
        ).fill("Unsaved private edit")
        page.get_by_label("Export format").select_option("xlsx")
        page.route(
            "**/families/export", lambda route: route.fulfill(body="Export queued")
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Queue complete export").click()
        assert (
            "search=Example" in sent.value.post_data
            and "format=xlsx" in sent.value.post_data
        )
        assert (
            "page=" not in sent.value.post_data
            and "Unsaved" not in sent.value.post_data
        )
        assert "?" not in sent.value.url and "Example" not in sent.value.url
        # Let the routed answer finish loading before the next navigation,
        # which it would otherwise interrupt (#623).
        visible(page.get_by_text("Export queued", exact=True))
        page.goto(component_origin + "/directory-gated")
        assert page.get_by_role(
            "button", name="Queue complete export", include_hidden=True
        ).is_disabled()
    finally:
        context.close()


def test_open_form_posts_a_hand_off(page, component_origin):
    """Open form is a CSRF-protected POST to a new tab, never a code link (#529).

    The code stays out of every URL. The sign-in page still copies a code
    from its fragment into its field and drops the fragment without
    submitting anything.
    """
    from playwright.sync_api import expect

    page.goto(component_origin + "/family-directory")
    form = page.locator("form[data-open-form]")
    assert form.get_attribute("method") == "post"
    assert form.get_attribute("action").endswith(f"/families/{UUID(int=81)}/open-form")
    assert form.get_attribute("target") == "_blank"
    assert form.get_attribute("rel") == "noopener"
    assert form.locator("input[name=csrfmiddlewaretoken]").count() == 1
    assert page.locator("a[href*='#code=']").count() == 0
    posts = []
    page.on("request", lambda sent: sent.method == "POST" and posts.append(sent.url))
    page.goto(component_origin + "/family-login#code=ABCDEFGH")
    expect(page.get_by_label("Family code")).to_have_value("ABCDEFGH")
    assert page.url == component_origin + "/family-login"
    assert posts == []


@pytest.mark.parametrize("width", [320, 1280])
def test_contact_details_show_each_heads_email(
    page, component_origin, axe_source, width
):
    """The opened pane lists each distinct address once with its heads (#604).

    Valid addresses are selectable mailto links; invalid source text is shown
    for correction but is not a link; a head without one reads "No email on
    file".
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/directory-head-emails")
    # The Family name's timeline link (#590) is separate from the pane.
    timeline = page.get_by_role("link", name="Example, Anna, Ben and John", exact=True)
    assert timeline.get_attribute("href").endswith(f"/{UUID(int=82)}/")
    page.get_by_text(
        "Contact details for Example, Anna, Ben and John", exact=True
    ).click()
    emails = page.locator("dd.head-emails")
    visible(emails)
    link = emails.get_by_role("link", name="anna@example.org", exact=True)
    visible(link)
    assert link.get_attribute("href") == "mailto:anna@example.org"
    assert emails.get_by_role("link").count() == 1
    assert emails.inner_text().splitlines() == [
        "Anna Example and Ben Example — anna@example.org",
        "Anna Example — not-an-address (not a valid address; fix in ParishSoft)",
        "John Example — No email on file",
    ]
    # The address can be selected and copied as plain text.
    assert link.evaluate("element => getComputedStyle(element).userSelect") != "none"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type:'tag', values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
