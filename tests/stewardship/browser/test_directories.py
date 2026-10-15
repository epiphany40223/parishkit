"""Three-engine private native directories and accessible contacts."""

import re
from uuid import UUID

import pytest
from django.urls import reverse

from .waits import same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def page_box(locator):
    """``locator``'s box in page, not window, coordinates.

    Values are rounded to a hundredth of a pixel: Firefox reports a box
    scrolled into view a few hundred-thousandths of a pixel off (its
    fractional ``scrollY``), which is not a layout shift.
    """
    box = locator.evaluate(
        "node => { const r = node.getBoundingClientRect();"
        " return {x: r.x, y: r.y + scrollY, width: r.width, height: r.height}; }"
    )
    return {key: round(value, 2) for key, value in box.items()}


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

    The Family row header leads, then Family DUID, Envelope number, Family
    code, What to check and the dates (#932), and Family and Family DUID
    both sort; times
    show in the browser's time zone; a Family no longer active in ParishSoft
    is marked and has no Open form link (#933).
    """
    page.emulate_media(reduced_motion="reduce")
    page.goto(component_origin + "/directory-responses")
    headings = page.locator("table thead th").all_inner_texts()
    assert [text.split("\n")[0].strip() for text in headings[:7]] == [
        "Family",
        "Family DUID",
        "Envelope number",
        "Family code",
        "What to check",
        "Invitation delivered",
        "Link followed",
    ]
    # Family and Family DUID both sort: each heading is a sort control.
    for index in (0, 1):
        assert page.locator("table thead th").nth(index).get_by_role("button").count()
    visible(page.get_by_role("rowheader", name="Example <Family>, Example Head"))
    # The shared marks (#1216): a Family the data no longer has shows only
    # Not in ParishSoft; one it marks inactive keeps its name, badge beside.
    gone = page.get_by_role("rowheader", name="Not in ParishSoft", exact=True)
    visible(gone.locator('[data-source-status="missing"]'))
    assert gone.locator('[data-source-status="inactive"]').count() == 0
    faded = page.get_by_role(
        "rowheader", name="Faded, Old Head Inactive in ParishSoft", exact=True
    )
    visible(faded.locator('.status-badge[data-source-status="inactive"]'))
    assert page.locator("[data-open-form]").count() == 1
    visible(page.get_by_text("HGFEDCBA", exact=True))
    # A missing Link followed reads "No"; times are localized in place.
    row = page.get_by_role("row").filter(has_text="HGFEDCBA")
    assert row.get_by_role("cell").nth(5).inner_text() == "No"
    # Its email availability says what its name cell says.
    visible(row.get_by_text("(Not in ParishSoft)", exact=True))
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
        # A code list without response columns has no times in its rows, so
        # it asks for no zone and sends the browser's unasked (#1127).
        assert page.get_by_label("Export timezone").count() == 0
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
        assert sent.value.post_data.count("browser_timezone=") == 1
        assert "browser_timezone=America%2FDetroit" in sent.value.post_data
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
    assert form.get_attribute("action").endswith(f"/families/{UUID(int=81)}/open-form/")
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


def test_open_form_lands_on_the_family_form_in_a_new_tab(page, component_origin):
    """Clicking Open form opens a tab that posts its hand-off and lands on
    the Family form (#529, open-form-v1.js), with the directory's address as
    where Back returns."""
    page.goto(component_origin + "/family-directory")
    form = page.locator("form[data-open-form]")
    assert (
        form.locator("input[name=next]").get_attribute("value") == "/family-directory"
    )
    posts = []
    page.context.on(
        "request",
        lambda sent: sent.method == "POST" and posts.append((sent.url, sent.post_data)),
    )
    with page.context.expect_page() as opened:
        form.get_by_role("button", name="Open form", exact=False).click()
    tab = opened.value
    tab.wait_for_url(component_origin + "/family/")
    assert tab.get_by_role("heading", name="Family form").count() == 1
    sent = [url.removeprefix(component_origin) for url, _ in posts]
    assert sent == [
        f"/admin/reports/families/{UUID(int=81)}/open-form/",
        "/family/assisted",
    ]
    # The hand-off travels in the POST body, never in a URL.
    assert posts[1][1] == "handoff=" + "A" * 43
    tab.close()


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

    The reader's own reach is still sent and comes back on unticking. No help
    line sits under either field, so nothing on the filter bar moves and the
    fields stay bottom-aligned with their neighbours (pk-local batch 5).
    """
    page.goto(component_origin + "/family-directory")
    reach = page.get_by_label("Campaign mail can reach", exact=True)
    mailing = page.get_by_label("Include mailing columns")
    phone = page.get_by_label("Phone available", exact=True)
    apply = page.get_by_role("button", name="Apply filters")

    def boxes():
        """Where the filter bar's controls are drawn, in page coordinates.

        Ticking a box below the window scrolls it into view first, which is
        not a layout shift.
        """
        summary = page.locator("#directory-summary")
        return [page_box(item) for item in (reach, mailing, phone, apply, summary)]

    assert reach.is_enabled() and reach.input_value() == "any"
    assert reach.get_attribute("aria-describedby") == "directory-reach-tip"
    # No field of the bar carries a help line (#945's Include response
    # columns included); each explains itself in its toggletip. The Apply
    # gate's hint (#563) is the bar's own line, under the fields.
    assert page.locator("#table-filters p.help:not([data-complete-hint])").count() == 0
    assert page.locator("#directory-responses-tip").count() == 1
    # Nothing sits under the reach select in its field, so the bottom-aligned
    # bar keeps it level with its neighbours.
    reach_box = reach.bounding_box()
    field_box = reach.locator("xpath=..").bounding_box()
    bottom = reach_box["y"] + reach_box["height"]
    assert abs(field_box["y"] + field_box["height"] - bottom) < 1
    reach.select_option("email")
    before = boxes()
    mailing.check()
    assert reach.is_disabled() and reach.input_value() == "mail"
    assert boxes() == before
    mailing.uncheck()
    assert reach.is_enabled() and reach.input_value() == "email"
    assert boxes() == before
    # The toggletips say what the box does and why the select is set.
    page.locator('[aria-controls="directory-reach-tip"]').click()
    assert "By postal mail only" in page.locator("#directory-reach-tip").inner_text()
    page.locator('[aria-controls="directory-mailing-tip"]').click()
    assert "mail-merge" in page.locator("#directory-mailing-tip").inner_text()
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
    assert reach.is_disabled() and reach.input_value() == "mail"
    before = page_box(reach)
    page.get_by_label("Include mailing columns").uncheck()
    assert reach.is_enabled() and reach.input_value() == "any"
    assert page_box(reach) == before


def test_export_time_zone_follows_the_applied_response_columns(page, component_origin):
    """Only the code list with response columns asks for a time zone (#1127).

    Applying Include response columns brings the export's time zone field
    in; applying without them takes it away again, and the browser's zone
    is then sent unasked. The field is the last of its row with its help in
    a toggletip, so the format menu and the Queue button do not move within
    the export options (no layout shift); a re-sort keeps the zone the reader
    chose. The columns line above the form
    lists more columns with the response columns, so positions are compared
    within the options fieldset.
    """
    from playwright.sync_api import expect

    page.goto(component_origin + "/family-directory")
    page.get_by_text("Export complete results").click()
    zone = page.get_by_label("Export timezone")
    options = page.locator("#table-export fieldset")
    controls = (
        page.get_by_label("Export format"),
        page.get_by_role("button", name="Queue complete export"),
        # The format help's "i" button (#1158), beside the format label.
        page.locator("#table-export .toggletip-button").first,
    )

    def boxes():
        """Where the export options' controls sit within their fieldset.

        One flat list, so callers compare it with a sub-pixel tolerance:
        Firefox can lay the same box out a few hundred-thousandths of a
        pixel apart after a redraw, which no reader sees as a shift.
        """
        frame = options.bounding_box()
        return [
            value
            for box in (item.bounding_box() for item in controls)
            for value in (
                box["x"] - frame["x"],
                box["y"] - frame["y"],
                box["width"],
                box["height"],
            )
        ]

    def apply(fixture):
        """Apply the filters, answered with the ``fixture`` page."""
        page.unroute("**/families/")
        page.route(
            "**/families/",
            lambda route: route.fulfill(
                response=route.fetch(url=component_origin + fixture, method="GET")
            ),
        )
        page.get_by_role("button", name="Apply filters").click()

    assert zone.count() == 0
    before = boxes()
    page.get_by_label("Include response columns").check()
    apply("/directory-responses")
    visible(zone)
    assert page.locator("#table-export input[name=responses]").input_value() == "yes"
    assert boxes() == pytest.approx(before, abs=0.5)
    # A re-sort redraws the zone slot with the rows; the form is marked
    # data-keep-choices, so the zone the reader chose is put back.
    zone.select_option("America/Detroit")
    zone.evaluate("node => { node.dataset.drawn = 'before'; }")
    page.get_by_role("button", name=re.compile(r"^Family \(sort")).click()
    expect(page.locator("#directory-export-timezone:not([data-drawn])")).to_have_count(
        1
    )
    expect(zone).to_have_value("America/Detroit")
    page.get_by_label("Include response columns").uncheck()
    apply("/family-directory")
    expect(zone).to_have_count(0)
    assert boxes() == pytest.approx(before, abs=0.5)
    # The browser's zone goes unasked, once.
    hidden = page.locator("#table-export input[name=browser_timezone]")
    assert hidden.count() == 1 and hidden.input_value() == page.evaluate(
        "Intl.DateTimeFormat().resolvedOptions().timeZone"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_export_format_help_is_a_toggletip(page, component_origin, width):
    """The format's dates-and-money help opens beside its label (#1158).

    As a line under the menu, the help wrapped into a tall narrow column in
    the filter bar's short cell. As a toggletip the cell stays one field
    tall, the menu is still described by the full help, and opening the tip
    moves no control in the export options.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/family-directory")
    page.get_by_text("Export complete results").click()
    menu = page.get_by_label("Export format")
    visible(menu)
    assert menu.get_attribute("aria-describedby") == "directory-format-tip"
    tip = page.locator("#directory-format-tip")
    assert tip.is_hidden() and "ISO 8601" in tip.text_content()
    assert "$ sign" in tip.text_content()
    assert page.locator("#table-export p.help", has_text="ISO 8601").count() == 0
    cell = page.locator("#table-export .filter-bar > div.short").first
    # A label row and a menu (about 6rem); the old help line made it 15+ lines.
    assert cell.bounding_box()["height"] < 8 * 16
    controls = page.locator("#table-export :is(label, select, button)")

    options = page.locator("#table-export fieldset")

    def boxes():
        """Where every export label and control sits within the options.

        Relative to the fieldset, since opening the tip may scroll the page.
        """
        frame = options.bounding_box()
        found = (controls.nth(i).bounding_box() for i in range(controls.count()))
        return [
            (box["x"] - frame["x"], box["y"] - frame["y"], box["width"], box["height"])
            for box in found
            if box
        ]

    before = boxes()
    button = page.locator("#table-export .toggletip-button").first
    button.click()
    visible(tip)
    # Linux Firefox may report the same place a ten-thousandth of a pixel apart.
    assert same_place(boxes(), before)
    page.keyboard.press("Escape")
    assert tip.is_hidden()


@pytest.mark.parametrize("width", [320, 375, 1280])
def test_filter_bar_tips_sit_beside_their_labels(page, component_origin, width):
    """Every filter-bar "i" sits on its label's line (pk-local batch 12).

    In the filter bar's narrow short cell a long label filled the cell, so
    its "i" wrapped under it: Include mailing columns, Include response
    columns and Campaign mail can reach. Those cells take an ordinary
    field's width, as Include history on Census changes does, so each label
    and its "i" share one line on a phone and on a desktop. Every tip label
    in the filter bar is checked, so a new long one in a short cell fails
    here too, and opening a tip still moves nothing.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/family-directory")
    labels = page.locator("#table-filters .label-row > label")
    ids = [labels.nth(i).get_attribute("id") for i in range(labels.count())]
    assert {"directory-reach-label", "directory-mailing-label"} <= set(ids)
    for label_id in ids:
        label = page.locator(f"#{label_id}")
        button = page.locator(f"#{label_id} + .toggletip .toggletip-button")
        visible(button)
        text, tip = label.bounding_box(), button.bounding_box()
        # One line: the label is at most one 44px row and the "i" is beside
        # it, vertically inside the label's box rather than under it.
        assert text["height"] < 50, label_id
        assert tip["x"] >= text["x"] + text["width"], label_id
        assert (
            text["y"] <= tip["y"] + tip["height"] / 2 <= text["y"] + text["height"]
        ), label_id

    def boxes():
        """Every filter-bar field and control's page box, to catch any shift.

        Page, not viewport, coordinates: on a phone the click scrolls the
        "i" into view first, which is not a layout shift.
        """
        return page.locator(
            "#table-filters > div, #table-filters select, #table-filters button"
        ).evaluate_all(
            "els => els.filter(e => !e.closest('.toggletip')).map(e => {"
            " const r = e.getBoundingClientRect();"
            " return [r.x, r.y + scrollY, r.width, r.height]; })"
        )

    before = boxes()
    page.locator("#directory-reach-label + .toggletip .toggletip-button").click()
    visible(page.locator("#directory-reach-tip"))
    # Linux Firefox may report the same place a ten-thousandth of a pixel apart.
    assert same_place(boxes(), before)
    page.keyboard.press("Escape")
