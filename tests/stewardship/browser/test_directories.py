"""Three-engine private native directories and accessible contacts."""

from urllib.parse import urlencode
from uuid import UUID

import pytest
from django.urls import reverse

from .waits import eventually, has_text, same_place, visible

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


def contact_summary(page, names):
    """Return the Contact details summary for a Family (#1654).

    The visible text is just "Contact details"; the Family's names follow in
    visually hidden text so the accessible name still begins with the visible
    label (label-in-name) and stays distinguishable between rows.
    """
    summary = page.locator("summary", has_text=f"Contact details for {names}")
    assert summary.evaluate("el => el.firstChild.textContent") == "Contact details"
    assert summary.locator(".visually-hidden").text_content() == f" for {names}"
    assert summary.text_content() == f"Contact details for {names}"
    return summary


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
    summary = contact_summary(page, "Example <Family>, Example Head")
    summary.click()
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
        "rowheader", name="Faded, Old Head No longer in this campaign", exact=True
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
        # The export row (#1679) is shown below the table, with no format
        # menu and no zone question: the browser's zone is sent unasked.
        assert page.get_by_label("Export format").count() == 0
        assert page.get_by_label("Export timezone").count() == 0
        # No count line above the buttons (R37).
        assert page.get_by_text("estimated matching", exact=False).count() == 0
        page.get_by_label(
            "Search by Family name, any member's name, DUID, envelope number or address"
        ).fill("Unsaved private edit")
        page.route(
            "**" + reverse("admin:family_directory_export"),
            lambda route: route.fulfill(
                body="file",
                headers={"Content-Disposition": 'attachment; filename="families.xlsx"'},
            ),
        )
        with (
            page.expect_request(lambda request: request.method == "POST") as sent,
            page.expect_download() as download,
        ):
            page.get_by_role("button", name="Export XLSX").click()
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
        # The file downloads in place (#1778), before the next navigation.
        assert download.value.suggested_filename == "families.xlsx"
        page.goto(component_origin + "/directory-gated")
        for name in ("Export XLSX", "Export CSV", "Export PDF"):
            assert page.get_by_role("button", name=name).is_disabled()
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
    contact_summary(page, "Example, Anna, Ben and John").click()
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


def test_the_export_row_never_asks_for_a_zone(page, component_origin):
    """No directory file asks for a time zone (#1679), the code list with
    response columns included: applying Include response columns adds no
    field and moves no export button (no layout shift), and the browser's
    zone is sent unasked, once.
    """
    from playwright.sync_api import expect

    page.goto(component_origin + "/family-directory")
    buttons = page.locator("#directory-export-buttons button")

    def boxes():
        """Where the export buttons sit, relative to their row.

        One flat list, compared with a sub-pixel tolerance: Firefox can lay
        the same box out a few hundred-thousandths of a pixel apart after a
        redraw, which no reader sees as a shift.
        """
        frame = page.locator("#table-export .export-actions").bounding_box()
        return [
            value
            for box in (buttons.nth(index).bounding_box() for index in range(3))
            for value in (
                box["x"] - frame["x"],
                box["y"] - frame["y"],
                box["width"],
                box["height"],
            )
        ]

    before = boxes()
    page.route(
        "**/families/",
        lambda route: route.fulfill(
            response=route.fetch(
                url=component_origin + "/directory-responses", method="GET"
            )
        ),
    )
    page.get_by_label("Include response columns").check()
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("#table-export input[name=responses]")).to_have_value("yes")
    assert page.get_by_label("Export timezone").count() == 0
    assert boxes() == pytest.approx(before, abs=0.5)
    hidden = page.locator("#table-export input[name=browser_timezone]")
    assert hidden.count() == 1 and hidden.input_value() == page.evaluate(
        "Intl.DateTimeFormat().resolvedOptions().timeZone"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_export_buttons_have_no_dates_explanation(page, component_origin, width):
    """The export is its row of buttons only (#1538, #1679).

    The dates-and-money explanation (#221, #1158) was removed from every
    export: each button keeps its label as its accessible name, and the row
    stays one button tall on a desktop and wraps on a phone without widening
    the page.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/family-directory")
    row = page.locator("#table-export .export-actions")
    visible(row)
    assert "ISO 8601" not in page.locator("#table-export").text_content()
    xlsx = page.get_by_role("button", name="Export XLSX")
    assert xlsx.get_attribute("aria-describedby") is None
    height = xlsx.bounding_box()["height"]
    assert 24 <= height < 44
    if width == 1280:
        assert row.bounding_box()["height"] < 2 * height
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


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


def test_responses_shown_carries_the_applied_filters(page, component_origin):
    """The Responses shown segments (#1535) carry the filters the page shows.

    An in-place Apply filters redraws them with the filters it applied;
    choosing Production then posts those filters privately with the mode
    once, back on the first page, without a reload, and the filter form
    shows what that answer applied and carries Production.
    """
    directory = reverse("admin:family_directory")

    # The directory presets the Response dashboard used to link (#1561
    # removed them from the page with its Lists of Families panel).
    presets = {
        "submitted": {"response": "submitted", "responses": "yes", "sort": "submitted"},
        "started": {"response": "started", "responses": "yes"},
    }

    def preset(key, **extra):
        """A directory preset's address, as the response dashboard links it."""
        return f"{directory}?{urlencode(presets[key] | extra)}"

    answers = [
        page.request.get(component_origin + preset("started", mode="testing")).text(),
        page.request.get(component_origin + preset("started")).text(),
    ]
    posts = []

    def answer(route):
        """Answer each POST with the next page, in order."""
        posts.append(route.request.post_data)
        route.fulfill(body=answers[len(posts) - 1], content_type="text/html")

    page.goto(component_origin + preset("submitted", mode="testing"))
    page.evaluate("document.querySelector('h1').dataset.mark = 'kept'")
    page.route("**/families/", answer)
    production = "#directory-mode form[data-in-place=mode-production]"
    assert page.locator(f"{production} input[name=response]").input_value() == (
        "submitted"
    )
    page.get_by_label("Response", exact=True).select_option("started")
    page.get_by_role("button", name="Apply filters").click()
    eventually(
        page,
        f"() => document.querySelector('{production} input[name=response]')"
        ".value === 'started'",
    )
    page.get_by_role("group", name="Responses shown", exact=True).get_by_role(
        "button", name="Production"
    ).click()
    has_text(page.locator("#directory-mode [aria-current=page]"), "Production")
    assert len(posts) == 2
    sent = dict(pair.split("=", 1) for pair in posts[1].split("&"))
    assert posts[1].count("mode=") == 1 and sent["mode"] == "production"
    assert sent["response"] == "started" and "page" not in sent
    assert page.locator("#table-filters input[name=mode]").input_value() == "production"
    assert page.get_by_label("Response", exact=True).input_value() == "started"
    assert page.evaluate("document.querySelector('h1').dataset.mark") == "kept"
