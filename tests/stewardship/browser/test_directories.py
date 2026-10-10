"""Three-engine private native directories and accessible contacts."""

from uuid import UUID

import pytest
from django.urls import reverse

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
    for path in (
        "/family-directory",
        "/postal-directory",
        "/directory-empty",
        "/directory-responses",
    ):
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


def test_response_filters_columns_and_inactive_families(page, component_origin):
    """The Response choices, data check and response columns post privately.

    Dates lead the row, then the Family row header, Family DUID and Envelope
    number (#932); times show in the browser's time zone; a Family no longer
    active in ParishSoft is marked and has no Open form link (#933).
    """
    page.emulate_media(reduced_motion="reduce")
    page.goto(component_origin + "/directory-responses")
    headings = page.locator("table thead th").all_inner_texts()
    assert [text.split("\n")[0].strip() for text in headings[:6]] == [
        "Invitation delivered",
        "Link followed",
        "Family",
        "Family DUID",
        "Envelope number",
        "What to check",
    ]
    visible(page.get_by_role("rowheader", name="Example <Family>, Example Head"))
    inactive = page.get_by_role("rowheader", name="Not in the latest ParishSoft data")
    visible(inactive)
    visible(inactive.get_by_text("No longer active in ParishSoft", exact=True))
    assert page.locator("[data-open-form]").count() == 1
    visible(page.get_by_text("HGFEDCBA", exact=True))
    # A missing Link followed reads "No"; times are localized in place.
    row = page.get_by_role("row").filter(has_text="HGFEDCBA")
    assert row.get_by_role("cell").nth(1).inner_text() == "No"
    assert "2026" in page.locator("#directory-summary time").inner_text()
    response = page.get_by_label("Response", exact=True)
    assert response.input_value() == "link-followed"
    assert page.get_by_label("ParishSoft data to check").input_value() == "anything"
    columns = page.get_by_label("Include response columns")
    assert columns.is_checked()
    response.select_option("submitted")
    page.get_by_label("ParishSoft data to check").select_option("envelope")
    page.route("**/families/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    body = sent.value.post_data
    assert "response=submitted" in body and "check=envelope" in body
    assert "responses=yes" in body and "?" not in sent.value.url


def test_directory_pagination_posts_privately(page, component_origin):
    """Paging posts the applied filters in the body, never in the URL."""
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


def test_complete_directory_export_controls_are_private_native_and_gated(
    browser_engine, component_origin
):
    """Export applied filters, not unsaved edits or the current page, on mobile."""
    options = {
        "timezone_id": "America/Detroit",
        "viewport": {"width": 320, "height": 900},
    }
    context = browser_engine.new_context(**options)
    try:
        page = context.new_page()
        page.goto(component_origin + "/family-directory")
        # The export controls are collapsed until asked for.
        assert not page.get_by_label("Export format").is_visible()
        page.get_by_text("Export complete results").click()
        assert page.get_by_label("Export timezone").input_value() == "America/Detroit"
        visible(page.get_by_text("51 estimated matching Families", exact=False))
        page.get_by_label(
            "Search by Family name, any member's name, DUID, envelope number or address"
        ).fill("Unsaved private edit")
        page.get_by_label("Export format").select_option("xlsx")
        page.route(
            "**" + reverse("admin:family_directory_export"),
            lambda route: route.fulfill(body="Export queued"),
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


def test_open_form_link_prefills_the_family_code(page, component_origin):
    """The link opens the sign-in in a new tab with the code in the fragment.

    The sign-in page copies the code into its field and drops the fragment
    without submitting anything, so staff review it and press Continue.
    """
    from playwright.sync_api import expect

    page.goto(component_origin + "/family-directory")
    link = page.locator("[data-open-form]")
    assert link.get_attribute("href") == "/#code=ABCDEFGH"
    assert link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener"
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


def test_mailing_columns_lock_reach_to_postal_mail_only(page, component_origin):
    """Ticking mailing columns shows reach "By postal mail only", locked (#951).

    The reader's own reach is still sent and comes back on unticking, and
    the reason's space is always kept, so nothing on the filter bar moves.
    """
    page.goto(component_origin + "/family-directory")
    reach = page.get_by_label("Campaign mail can reach", exact=True)
    mailing = page.get_by_label("Include mailing columns")
    reason = page.locator("#directory-reach-locked")
    apply = page.get_by_role("button", name="Apply filters")

    def boxes():
        """Where the filter bar's controls are drawn."""
        summary = page.locator("#directory-summary")
        return [
            item.bounding_box() for item in (reach, mailing, reason, apply, summary)
        ]

    assert reach.is_enabled() and reach.input_value() == "any"
    assert reason.evaluate("node => getComputedStyle(node).visibility") == "hidden"
    assert reach.get_attribute("aria-describedby") is None
    reach.select_option("email")
    before = boxes()
    mailing.check()
    assert reach.is_disabled() and reach.input_value() == "mail"
    assert reason.evaluate("node => getComputedStyle(node).visibility") == "visible"
    assert reason.inner_text() == "Set by Include mailing columns."
    assert reach.get_attribute("aria-describedby") == "directory-reach-locked"
    assert boxes() == before
    mailing.uncheck()
    assert reach.is_enabled() and reach.input_value() == "email"
    assert reason.evaluate("node => getComputedStyle(node).visibility") == "hidden"
    assert boxes() == before
    # While locked, the reader's own reach is what the form sends; the server
    # applies "By postal mail only" itself.
    mailing.check()
    page.route("**/families/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        apply.click()
    data = sent.value.post_data
    assert "reach=email" in data and "reach=mail" not in data
    assert "mailing=yes" in data


def test_mailing_columns_page_draws_reach_locked(page, component_origin):
    """With mailing columns on, the page arrives locked; unticking restores Any."""
    page.goto(component_origin + "/postal-directory")
    reach = page.get_by_label("Campaign mail can reach", exact=True)
    reason = page.locator("#directory-reach-locked")
    assert reach.is_disabled() and reach.input_value() == "mail"
    visible(reason)
    before = reach.bounding_box()
    page.get_by_label("Include mailing columns").uncheck()
    assert reach.is_enabled() and reach.input_value() == "any"
    assert reason.evaluate("node => getComputedStyle(node).visibility") == "hidden"
    assert reach.bounding_box() == before
