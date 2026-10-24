"""New and Edit report email in the browser (#1786, delivery plan item 6).

The editor fits a 320 px phone without a page scroll, in light and dark,
with no accessibility violation. Its recipients picker adds users, groups
and outside addresses as chips; its Blocks list adds, moves and removes
blocks in place, keeping their order in the posted fields; Sends, As of
and Frequency are segmented pairs, and Basic's Weekly writes the Advanced
rule; a listed user's own address adds that user; the line saying the
days are the campaign's shows only where the browser's zone differs; and
the review dialog's "Send after the campaign ends" tick holds Apply,
saying why, until it is ticked. A valid typed time is never left marked,
and a passed one-time send's refusal marks the date and the time and
clears from both on an edit of either (#1963 review). The Introduction is
the shared visual editor. Next sends is a table whose boxes feed Skip and
Send selected sends, each available only for its kind of send; a send the
Campaign calendar names (?send=) has its box ticked and focused. The
Participation charts block's Charts boxes sit under its help, one chart a
line, start all ticked when the block is added, and tick in place (#1956).
Unticking the last chart, or Advanced's last day, says so at the group at
once, moving nothing, and a tick clears it; the server's refusal of either
is drawn at the group and cleared by a tick (#2009).
New's Start from fills every field in place from Daily report or Weekly
report, the time left empty, and asks before replacing typing (#2037).
"""

import re

import pytest

from .report_email_editor_components import (
    EDIT,
    EDIT_CHARTS,
    EDIT_SKIPPED,
    NEW,
    NEW_GROUPS_REFUSED,
    NEW_REFUSED,
    USERS,
    WEEKLY,
    changed_table,
    review_answer,
    skip_answer,
)
from .test_components import axe_violations
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def page_slots(page):
    """The slot keys of the Next sends rows, in order."""
    return page.locator("table.report-sends tbody tr").evaluate_all(
        "rows => rows.map(row => row.dataset.send)"
    )


def send_time(page, key):
    """The instant the Next sends row of ``key`` names."""
    return page.locator(f'tr[data-send="{key}"] time').get_attribute("datetime")


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize(
    "path", [NEW, EDIT, EDIT_SKIPPED, NEW_REFUSED, NEW_GROUPS_REFUSED]
)
def test_the_editor_fits_and_passes_axe(
    page, component_origin, axe_source, width, scheme, path
):
    """No page scroll at any width, and no accessibility violation."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    visible(page.get_by_role("heading", level=1))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def posted(page, name):
    """The values the form would post for ``name``, in order."""
    return page.evaluate(
        "(name) => new FormData(document.getElementById('report-email-form'))"
        ".getAll(name)",
        name,
    )


def test_the_picker_adds_groups_users_and_outside_addresses(page, component_origin):
    """A chosen suggestion or a typed address becomes a removable chip."""
    page.goto(component_origin + NEW)
    box = page.get_by_label("Add a recipient")
    box.fill("All Staff (group, 1 person now)")
    page.get_by_role("button", name="Add", exact=True).first.click()
    box.fill("Council@Example.org")
    box.press("Enter")
    assert posted(page, "recipient") == ["group:staff", "address:council@example.org"]
    visible(page.get_by_text("This address has no portal account."))
    box.fill("Council@example.org")
    box.press("Enter")
    visible(page.get_by_text("That recipient is chosen already."))
    page.get_by_role("button", name="Remove All Staff (group, 1 person now)").click()
    assert posted(page, "recipient") == ["address:council@example.org"]


def test_blocks_are_added_moved_and_removed_in_place(page, component_origin):
    """The posted block order follows the list; focus stays on the control."""
    page.goto(component_origin + EDIT)
    assert posted(page, "block") == ["additional_information", "response_funnel"]
    page.get_by_label("Add a block").select_option("campaign_totals")
    page.get_by_role("button", name="Add", exact=True).last.click()
    assert posted(page, "block") == [
        "additional_information",
        "response_funnel",
        "campaign_totals",
    ]
    up = page.get_by_role("button", name="Move Campaign totals up")
    up.click()
    assert posted(page, "block")[1] == "campaign_totals"
    assert page.evaluate("document.activeElement.textContent.trim()") == "Move up"
    page.get_by_role("button", name="Remove Response funnel").click()
    assert posted(page, "block") == ["additional_information", "campaign_totals"]
    first = page.get_by_role(
        "button", name="Move Open additional information requests up"
    )
    if first.count():
        assert first.get_attribute("aria-disabled") == "true"


def test_binary_choices_are_segmented_pairs(page, component_origin):
    """Sends, As of and Frequency are each a labelled pair of two radios
    drawn as segments (UX conventions, "Binary choices"), never stacked
    radios; the words say Frequency and Daily, never Repeat or "Every
    morning"."""
    page.goto(component_origin + NEW)
    for name in ("Sends", "As of", "Frequency"):
        group = page.get_by_role("group", name=name, exact=True)
        visible(group)
        assert group.evaluate("node => node.classList.contains('choice-pair')")
        assert group.get_by_role("radio").count() == 2
    assert page.locator("#report-email-form .choice input[type=radio]").count() == 0
    for words in ("Every morning", "Repeating schedule", "Repeat"):
        assert page.get_by_text(words, exact=True).count() == 0
    # Choosing a segment shows its fields in place; arrow keys move along
    # the native radio group.
    page.get_by_role("radio", name="Once").check()
    visible(page.get_by_label("Date", exact=True))
    page.get_by_role("radio", name="Once").press("ArrowRight")
    assert page.get_by_role("radio", name="Repeating").is_checked()


def test_basic_weekly_writes_the_advanced_rule(page, component_origin):
    """Choosing Weekly on Thursday shows the same rule in Advanced."""
    page.goto(component_origin + NEW)
    page.get_by_role("radio", name="Weekly", exact=True).check()
    page.get_by_label("Day", exact=True).select_option("3")
    page.get_by_label("Advanced").check()
    eventually(
        page,
        "() => document.querySelector('input[name=frequency]:checked')?.value",
        "weekly",
    )
    assert posted(page, "weekdays") == ["3"]
    assert page.get_by_label("Until", exact=True).input_value() == (
        "first_send_after_campaign_end"
    )


def test_a_listed_users_address_adds_that_user(page, component_origin):
    """A user's own address, in any letter case, adds that user's chip, not
    an outside address (the server stores it the same way)."""
    page.goto(component_origin + NEW)
    box = page.get_by_label("Add a recipient")
    box.fill("PAT@Example.org")
    box.press("Enter")
    assert posted(page, "recipient") == [f"user:{USERS[0]['id']}"]
    visible(page.get_by_text("Pat Doe — Staff added to the recipients."))
    assert page.get_by_text("This address has no portal account.").count() == 0
    box.fill("Pat Doe — Staff")
    box.press("Enter")
    visible(page.get_by_text("That recipient is chosen already."))
    # A Ministry leader is never offered, and their address is refused as
    # typed, never taken for an outside address (decision 2).
    box.fill("LEE@example.org")
    box.press("Enter")
    visible(page.get_by_text("lee@example.org is a Ministry leader;"))
    assert posted(page, "recipient") == [f"user:{USERS[0]['id']}"]
    assert page.get_by_text("This address has no portal account.").count() == 0


def test_the_campaign_days_line_follows_the_zone(
    page, browser_engine, component_origin
):
    """The line saying a repeating report's days are the campaign's shows
    only where this browser's zone keeps another clock than the campaign's:
    another name for the campaign's zone is the same zone (#1973)."""
    days = "These are the days where the parish is"
    page.goto(component_origin + NEW)
    page.get_by_role("radio", name="Weekly", exact=True).check()
    # The fixture's browser is in Los Angeles; the campaign in New York.
    visible(page.get_by_text(days).first)
    # America/Detroit keeps New York's clock under another name. (The
    # America/Louisville alias would do too, but Firefox refuses it as a
    # context's zone.)
    for zone in ("America/New_York", "America/Detroit"):
        context = browser_engine.new_context(timezone_id=zone)
        try:
            same = context.new_page()
            same.goto(component_origin + NEW)
            same.get_by_role("radio", name="Weekly", exact=True).check()
            # Each line keeps its hidden attribute (the Advanced panel's
            # line is hidden with its panel either way).
            lines = same.locator("[data-campaign-days]")
            assert lines.evaluate_all("ls => ls.map(l => l.hidden)") == [True, True]
        finally:
            context.close()


def test_a_one_hour_clock_difference_shows_the_campaign_days_line(
    browser_engine, component_origin
):
    """Havana's clock leaves New York's for one hour only, 05:00 to 06:00
    UTC on 2026-11-01, when Havana has left daylight time and New York not
    yet: a campaign of October 2026 (its last send two days after) still
    gets the line (#1973 review: hourly samples, not six-hourly)."""

    def october_2026(route):
        """New report email, with the campaign's dates moved to 2026."""
        body = route.fetch().text()
        for field, day in (("start", "2026-10-01"), ("end", "2026-10-31")):
            body, count = re.subn(
                rf'data-campaign-{field}="[^"]*"',
                f'data-campaign-{field}="{day}"',
                body,
            )
            assert count == 1
        route.fulfill(status=200, body=body, content_type="text/html")

    context = browser_engine.new_context(timezone_id="America/Havana")
    try:
        page = context.new_page()
        page.route(component_origin + NEW, october_2026)
        page.goto(component_origin + NEW)
        page.get_by_role("radio", name="Weekly", exact=True).check()
        visible(page.get_by_text("These are the days where the parish is").first)
    finally:
        context.close()


def test_the_after_campaign_tick_holds_apply(page, component_origin):
    """Review's answer asks about the sends after the campaign ends: the
    shared review gate holds Apply, saying why, until the tick is ticked,
    which Apply then posts as after_campaign=yes."""
    from playwright.sync_api import expect

    answer = review_answer()

    def review(route):
        """Answer the form's Review with the after-campaign question."""
        route.fulfill(status=200, body=answer, content_type="text/html")

    page.route(component_origin + "/admin/mail/report-emails/new/", review)
    page.goto(component_origin + NEW)
    dialog = open_review(page)
    apply = dialog.locator("[data-confirm-accept]")
    status = dialog.locator("[data-confirm-status]")
    hint = "Tick Send after the campaign ends to apply it."
    expect(apply).to_be_disabled()
    expect(status).to_have_text(hint)
    assert apply.get_attribute("aria-describedby") == status.get_attribute("id")
    dialog.get_by_label("Send after the campaign ends").check()
    expect(apply).to_be_enabled()
    expect(status).to_have_text("")
    assert page.evaluate(TICK_POSTED) == "yes"
    dialog.get_by_label("Send after the campaign ends").uncheck()
    expect(apply).to_be_disabled()
    expect(status).to_have_text(hint)


def open_review(page):
    """Fill in New report email and open its review dialog (the test routes
    the review's answer); returns the dialog."""
    page.get_by_label("Report name", exact=True).fill("Staff daily")
    page.get_by_label("Add a recipient").fill("All Staff (group, 1 person now)")
    page.get_by_role("button", name="Add", exact=True).first.click()
    page.get_by_role("radio", name="Daily", exact=True).check()
    page.get_by_label("Time", exact=True).fill("7:00 AM")
    page.get_by_role("button", name="Add", exact=True).last.click()
    page.get_by_role("button", name="Add the report email").click()
    dialog = page.locator("#report-review")
    visible(dialog)
    return dialog


# The words of the matched, rendered elements that break across lines: a
# word laid out on one line has one client rect, a word broken mid-way two.
BROKEN_WORDS = """els => els.filter(el => el.getClientRects().length).flatMap(el => {
  const broken = [];
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const match of node.data.matchAll(/\\S+/g)) {
      const range = document.createRange();
      range.setStart(node, match.index);
      range.setEnd(node, match.index + match[0].length);
      if (range.getClientRects().length > 1) broken.push(match[0]);
    }
  }
  return broken;
})"""


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("change", [False, True])
def test_words_wrap_only_between_words(page, component_origin, width, change):
    """At phone and desktop width no segment of a pair and no label of the
    review table breaks a word mid-way ("Repeatin" / "g", #1974), and the
    page still never scrolls sideways."""
    answer = review_answer(WEEKLY if change else None)
    page.route(
        component_origin + "/admin/mail/report-emails/new/",
        lambda route: route.fulfill(status=200, body=answer, content_type="text/html"),
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + NEW)
    segments = page.locator(".choice-pair-options > label")
    assert segments.evaluate_all(BROKEN_WORDS) == []
    page.get_by_role("radio", name="Once").check()
    assert segments.evaluate_all(BROKEN_WORDS) == []
    page.get_by_role("radio", name="Repeating").check()
    # Whole words never overflow a pair's frame, which clips what overflows.
    assert page.locator(".choice-pair-options").evaluate_all(
        "els => els.filter(el => el.getClientRects().length)"
        ".every(el => el.scrollWidth <= el.clientWidth)"
    )
    dialog = open_review(page)
    labels = dialog.locator(".report-review-table th")
    assert labels.count() >= 9
    assert labels.evaluate_all(BROKEN_WORDS) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


TICK_POSTED = (
    "() => new FormData(document.querySelector("
    "'#report-review form[data-review-apply]')).get('after_campaign')"
)


def test_the_calendar_named_send_is_ticked_and_focused(page, component_origin):
    """Edit opened as ?send=<slot key> ticks and focuses that send's box, so
    Skip selected sends is available at once."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT)
    slot = page_slots(page)[1]
    assert page.evaluate("document.activeElement === document.body")
    page.goto(f"{component_origin}{EDIT}?send={slot}")
    eventually(
        page,
        "document.activeElement?.name === 'send'"
        f" && document.activeElement.value === {slot!r}"
        " && document.activeElement.checked",
    )
    assert page.get_by_role("button", name="Skip selected sends").is_enabled()


def test_names_say_what_they_are(page, component_origin):
    """Report name is the report's own name, not the From name; the subject
    is the email's subject line (#1962 review, D2)."""
    page.goto(component_origin + NEW)
    visible(page.get_by_label("Report name", exact=True))
    visible(page.get_by_label("Email subject line (optional)", exact=True))
    visible(page.get_by_text("The report's own name, not the From name"))


TIME_STATE = """() => {
  const field = document.getElementById('report-time');
  return {invalid: field.getAttribute('aria-invalid'),
          marked: field.hasAttribute('data-field-error'),
          valid: field.validity.valid,
          value: field.value};
}"""


def test_a_valid_time_is_not_left_marked(page, component_origin):
    """The Time field is never marked while its entry reads (#1962 review,
    item 3): not on load, not on choosing Once or a date, and a time typed
    as 7am reads, is rewritten and is not marked. The cause was the field's
    reserved error list, which the shared time entry read as a server
    refusal."""
    page.goto(component_origin + NEW)
    assert page.evaluate(TIME_STATE)["invalid"] != "true"
    page.get_by_role("radio", name="Once").check()
    page.get_by_label("Date", exact=True).fill("2054-10-25")
    assert page.evaluate(TIME_STATE)["invalid"] != "true"
    time = page.get_by_label("Time", exact=True)
    time.click()
    page.keyboard.type("7am")
    time.press("Tab")
    eventually(page, "() => document.getElementById('report-time').value", "07:00")
    state = page.evaluate(TIME_STATE)
    assert state == {
        "invalid": "false",
        "marked": False,
        "valid": True,
        "value": "07:00",
    }
    # Another field's change checks the time again; it stays unmarked.
    page.get_by_label("Date", exact=True).fill("2054-10-26")
    assert page.evaluate(TIME_STATE)["invalid"] == "false"
    border = page.evaluate(
        "() => getComputedStyle(document.getElementById('report-time')).borderTopWidth"
    )
    assert border == "1px"


@pytest.mark.parametrize("edit", ["date", "time"])
def test_a_passed_send_marks_its_date_and_time_until_either_is_edited(
    page, component_origin, edit
):
    """ "This send time has passed." concerns the date and the time: both are
    marked and described by its one message, after the time, and an edit of
    either clears both, the message and its summary item (UX conventions,
    "Field errors and live checks")."""
    page.goto(component_origin + NEW_REFUSED)
    date = page.locator("#report-once_date")
    time = page.locator("#report-time")
    message = page.locator("#report-time_error")
    visible(message)
    assert message.inner_text() == "This send time has passed."
    for field in (date, time):
        assert field.get_attribute("aria-invalid") == "true"
        assert "report-time_error" in field.get_attribute("aria-describedby")
    if edit == "date":
        date.fill("2054-10-25")
    else:
        time.fill("7am")
        time.press("Tab")
    for field in (date, time):
        eventually(
            page,
            f"() => document.getElementById({field.get_attribute('id')!r})"
            ".getAttribute('aria-invalid')",
            "false",
        )
    assert message.is_hidden()
    assert page.locator("[data-error-summary]").count() == 0


def test_the_introduction_is_the_shared_visual_editor(page, component_origin):
    """The Introduction is the visual editor every page and email uses: its
    pane is shown, the HTML source folds away, and typing in the pane writes
    the HTML field."""
    page.goto(component_origin + NEW)
    pane = page.locator("[data-content-editor]")
    visible(pane)
    assert page.locator("details[data-html-source]").get_attribute("open") is None
    pane.click()
    page.keyboard.type("Thank you")
    eventually(
        page,
        "() => document.querySelector('textarea[name=html]').value"
        ".includes('Thank you')",
    )
    visible(page.get_by_label("Generate plain text from HTML"))


def bulk(page):
    """Skip selected sends, Send selected sends and their reason line."""
    return (
        page.get_by_role("button", name="Skip selected sends"),
        page.get_by_role("button", name="Send selected sends"),
        page.locator("#report-sends-hint"),
    )


def test_next_sends_is_a_table_with_status(page, component_origin):
    """Each send is a row: when, the email, and its Status in words, a
    skipped send struck through (#1962 review, item 5)."""
    page.goto(component_origin + EDIT_SKIPPED)
    table = page.locator("table.report-sends")
    visible(table)
    statuses = table.locator("tbody tr td:last-child").all_inner_texts()
    assert statuses[:2] == ["Will send", "Skipped"]
    skipped = table.locator(f'tr[data-send="{page_slots(page)[1]}"]')
    assert skipped.locator("s").count() == 2
    # New draws no sends until its schedule and time are chosen (#1972),
    # and has no boxes: there is nothing saved to skip.
    page.goto(component_origin + NEW)
    visible(page.get_by_text("Fill in the schedule and time to see its next sends."))
    assert page.locator("table.report-sends").count() == 0
    assert page.locator("[data-select-row]").count() == 0
    assert page.get_by_role("button", name="Skip selected sends").count() == 0


def test_each_action_is_available_only_for_its_sends(page, component_origin):
    """Nothing ticked: both actions unavailable, saying to select. A send
    that will send fits Skip only, a skipped one Send only, and a mix
    neither, each with its reason in the reserved line."""
    from playwright.sync_api import expect

    page.goto(component_origin + EDIT_SKIPPED)
    skip, send, hint = bulk(page)
    expect(skip).to_be_disabled()
    expect(send).to_be_disabled()
    expect(hint).to_have_text("Select at least one send first.")
    first, second = page_slots(page)[:2]
    box = page.locator(f'input[data-select-row][value="{first}"]')
    other = page.locator(f'input[data-select-row][value="{second}"]')
    box.check()
    expect(skip).to_be_enabled()
    expect(send).to_be_disabled()
    expect(hint).to_contain_text("Send selected sends is for skipped sends")
    other.check()
    expect(skip).to_be_disabled()
    expect(send).to_be_disabled()
    expect(hint).to_contain_text("Select only sends that will send")
    box.uncheck()
    expect(skip).to_be_disabled()
    expect(send).to_be_enabled()
    expect(hint).to_contain_text("Skip selected sends is for sends that will send")


@pytest.mark.parametrize("kind", ["skip", "unskip"])
def test_selected_sends_open_one_review(page, component_origin, kind):
    """Skip (or Send) selected sends posts every ticked send in one review,
    whose dialog names each send."""
    seen = {}

    def review(route):
        """Answer the review, noting what it posted."""
        seen["body"] = route.request.post_data
        route.fulfill(
            status=200,
            body=skip_answer(kind, [(key, send_time(page, key)) for key in keys]),
            content_type="text/html",
        )

    page.route(component_origin + "/admin/mail/report-emails/new/", review)
    page.goto(component_origin + EDIT_SKIPPED)
    keys = page_slots(page)[2:4] if kind == "skip" else [page_slots(page)[1]]
    for key in keys:
        page.locator(f'input[data-select-row][value="{key}"]').check()
    skip, send, _hint = bulk(page)
    (skip if kind == "skip" else send).click()
    dialog = page.locator("#report-skip-review")
    visible(dialog)
    from urllib.parse import parse_qs

    body = parse_qs(seen["body"])
    assert body["action"] == [f"{kind}_preview"]
    assert sorted(body["send"]) == sorted(keys)
    assert dialog.locator("ul.report-review-sends li").count() == len(keys)


def test_a_changed_schedule_holds_the_boxes(page, component_origin):
    """Once the schedule fields differ from the saved schedule, the live
    table has no boxes and its line says to apply the change first; the
    redrawn table is wired again."""
    from playwright.sync_api import expect

    answer = changed_table()

    def preview(route):
        """The live table for the changed schedule."""
        route.fulfill(status=200, body=answer, content_type="text/html")

    page.route(
        component_origin + "/admin/mail/report-emails/schedule-preview/", preview
    )
    page.goto(component_origin + EDIT)
    page.get_by_label("Time", exact=True).fill("9:00 AM")
    page.get_by_label("Time", exact=True).press("Tab")
    _skip, _send, hint = bulk(page)
    expect(hint).to_have_text(
        "Review and apply your schedule change first: skipping and sending act "
        "on the saved schedule."
    )
    assert page.locator("[data-select-row]").count() == 0
    expect(page.get_by_role("button", name="Skip selected sends")).to_be_disabled()


def test_a_removed_recipient_or_moved_block_is_a_change(page, component_origin):
    """The recipients and blocks are hidden fields the change gate counts
    (data-change-setting): on Edit, removing a recipient or moving a block
    makes Review changes available, and undoing it makes it wait again."""
    from playwright.sync_api import expect

    page.goto(component_origin + EDIT)
    review = page.get_by_role("button", name="Review changes")
    expect(review).to_be_disabled()
    page.get_by_role("button", name="Move Response funnel up").click()
    expect(review).to_be_enabled()
    page.get_by_role("button", name="Move Response funnel down").click()
    expect(review).to_be_disabled()
    page.get_by_role("button", name="Remove All Administrators", exact=False).click()
    expect(review).to_be_enabled()


ALL_CHARTS = ["first_submissions", "cumulative_families", "pledges"]


def page_place(locator):
    """Where ``locator`` sits in the page (not the viewport, which a click
    may scroll): its left and top edges."""
    return locator.evaluate(
        "node => { const box = node.getBoundingClientRect();"
        " return [box.left + window.scrollX, box.top + window.scrollY]; }"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_the_charts_boxes_tick_in_place(page, component_origin, axe_source, width):
    """Edit draws the saved choice; each box shares a line with its label, the
    page does not scroll sideways, and ticking moves nothing (no layout
    shift) yet is a change to review."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + EDIT_CHARTS)
    group = page.get_by_role("group", name="Charts")
    visible(group)
    boxes = group.get_by_role("checkbox")
    assert boxes.count() == 3
    assert posted(page, "charts") == ["first_submissions", "pledges"]
    for index in range(3):
        box = boxes.nth(index).bounding_box()
        label = group.locator("label").nth(index).bounding_box()
        # The box and its words on one line, the box first.
        assert box["x"] < label["x"]
        assert abs(
            (box["y"] + box["height"] / 2) - (label["y"] + label["height"] / 2)
        ) < max(box["height"], label["height"])
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    review = page.get_by_role("button", name="Review changes")
    expect(review).to_be_disabled()
    remove = page.get_by_role("button", name="Remove Participation charts")
    before = page_place(remove)
    page.get_by_label("Cumulative participating Families").check()
    assert posted(page, "charts") == ALL_CHARTS
    # Within half a pixel: Firefox's sub-pixel layout can read the same place
    # a few ten-thousandths of a pixel apart, which is no shift.
    assert page_place(remove) == pytest.approx(before, abs=0.5)
    expect(review).to_be_enabled()
    page.get_by_label("Cumulative participating Families").uncheck()
    expect(review).to_be_disabled()


def test_an_added_charts_block_starts_with_every_chart(page, component_origin):
    """Added from Add a block, the block ticks every chart the campaign offers
    (no pledges without financial stewardship); Remove takes its boxes too."""
    page.goto(component_origin + NEW)
    assert posted(page, "charts") == []
    page.get_by_label("Add a block").select_option("participation_charts")
    page.get_by_role("button", name="Add", exact=True).last.click()
    visible(page.get_by_role("group", name="Charts"))
    assert posted(page, "charts") == ["first_submissions", "cumulative_families"]
    page.get_by_role("button", name="Remove Participation charts").click()
    assert posted(page, "charts") == []


def group_state(page, group_id):
    """The live-check state of the group ``group_id``: its error line's words
    (empty while hidden), whether the group is described by the line, and
    each box's aria-invalid."""
    return page.evaluate(
        """(id) => {
            const group = document.getElementById(id);
            const line = document.getElementById(`${id}_error`);
            return {
                words: line.hidden ? "" : line.textContent.trim(),
                described: (group.getAttribute("aria-describedby") || "")
                    .split(/\\s+/).includes(line.id),
                invalid: [...group.querySelectorAll("input[type=checkbox]")]
                    .map((box) => box.getAttribute("aria-invalid")),
            };
        }""",
        group_id,
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_unticking_every_chart_says_so_at_the_group(
    page, component_origin, axe_source, width
):
    """Unticking the last chart shows the server's words at the Charts group
    at once, without Review and without moving anything (its line is
    reserved), and holds Review; ticking one clears it (#2009)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + EDIT_CHARTS)
    group = page.get_by_role("group", name="Charts")
    visible(group)
    remove = page.get_by_role("button", name="Remove Participation charts")
    review = page.get_by_role("button", name="Review changes")
    before = page_place(remove)
    page.get_by_label("First submissions of the day").uncheck()
    assert group_state(page, "report-charts")["words"] == ""
    page.get_by_label("Effective annual pledges").uncheck()
    eventually(
        page,
        "() => document.getElementById('report-charts_error').textContent.trim()",
        "Choose at least one chart.",
    )
    assert group_state(page, "report-charts") == {
        "words": "Choose at least one chart.",
        "described": True,
        "invalid": ["true", "true", "true"],
    }
    assert page_place(remove) == pytest.approx(before, abs=0.5)
    expect(review).to_be_disabled()
    assert page.locator("[data-error-summary]").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    page.get_by_label("Cumulative participating Families").check()
    eventually(
        page,
        "() => document.getElementById('report-charts_error').hidden",
    )
    assert group_state(page, "report-charts") == {
        "words": "",
        "described": False,
        "invalid": ["false", "false", "false"],
    }
    assert page_place(remove) == pytest.approx(before, abs=0.5)
    expect(review).to_be_enabled()


@pytest.mark.parametrize("width", [320, 1280])
def test_unticking_every_day_says_so_at_the_group(
    page, component_origin, axe_source, width
):
    """Advanced's On these days says "Choose at least one day." at once when
    its last day is unticked, moving nothing, and clears on a tick; hiding
    the days (Daily) takes the mark away too (#2009)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + EDIT)
    page.get_by_label("Advanced").check()
    group = page.get_by_role("group", name="On these days")
    visible(group)
    after = page.get_by_label("From", exact=True)
    before = page_place(after)
    group.get_by_label("Monday").uncheck()
    eventually(
        page,
        "() => document.getElementById('report-weekdays_error').textContent.trim()",
        "Choose at least one day.",
    )
    state = group_state(page, "report-weekdays")
    assert state["described"] and set(state["invalid"]) == {"true"}
    assert page_place(after) == pytest.approx(before, abs=0.5)
    expect(page.get_by_role("button", name="Review changes")).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    group.get_by_label("Tuesday").check()
    eventually(page, "() => document.getElementById('report-weekdays_error').hidden")
    state = group_state(page, "report-weekdays")
    assert not state["described"] and set(state["invalid"]) == {"false"}
    assert page_place(after) == pytest.approx(before, abs=0.5)
    group.get_by_label("Tuesday").uncheck()
    eventually(
        page,
        "() => document.getElementById('report-weekdays_error').hidden",
        False,
    )
    page.locator("#report-frequency_0").check()
    eventually(page, "() => document.getElementById('report-weekdays_error').hidden")
    assert set(group_state(page, "report-weekdays")["invalid"]) == {"false"}


def test_the_servers_group_refusals_clear_on_a_tick(page, component_origin):
    """A Review the server refused for no day and no chart (a post the page
    would have held) draws both at their groups with the error summary; a
    tick clears that group's refusal and its summary item, and unticking it
    again is said live, without a summary item (#2009)."""
    page.goto(component_origin + NEW_GROUPS_REFUSED)
    summary = page.locator("[data-error-summary]")
    visible(summary)
    for group_id, words in (
        ("report-weekdays", "Choose at least one day."),
        ("report-charts", "Choose at least one chart."),
    ):
        state = group_state(page, group_id)
        assert state["words"] == words and state["described"]
        assert set(state["invalid"]) == {"true"}
    assert summary.get_by_role("link").count() == 2
    days = page.get_by_role("group", name="On these days")
    days.get_by_label("Friday").check()
    eventually(page, "() => document.getElementById('report-weekdays_error').hidden")
    assert summary.get_by_role("link").count() == 1
    assert group_state(page, "report-charts")["words"] == "Choose at least one chart."
    days.get_by_label("Friday").uncheck()
    eventually(
        page,
        "() => document.getElementById('report-weekdays_error').textContent.trim()",
        "Choose at least one day.",
    )
    assert summary.get_by_role("link").count() == 1


def start_from(page, name):
    """New's Start from radio named ``name``."""
    return page.get_by_role("group", name="Start from").get_by_role(
        "radio", name=name, exact=True
    )


def checked(page, name):
    """The value of the checked radio ``name`` posts, or None."""
    return page.evaluate(
        "(name) => document.querySelector(`input[name=${name}]:checked`)?.value"
        " ?? null",
        name,
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_start_from_fills_the_form_in_place(page, component_origin, width):
    """Daily report and Weekly report fill every field in place, with no
    reload and nothing above the form moving; the time stays empty and
    focus stays on the choice (#2037)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + NEW)
    group = page.get_by_role("group", name="Start from")
    visible(group)
    assert group.evaluate("node => node.classList.contains('choice-pair')")
    assert start_from(page, "Blank").is_checked()
    page.evaluate("window.parishNoReload = true")
    name = page.get_by_label("Report name")
    before = (group.bounding_box(), name.bounding_box()["y"])
    start_from(page, "Daily report").check()
    assert name.input_value() == "Daily report"
    assert page.get_by_label("Email subject line (optional)").input_value() == (
        "{{ campaign_name }} — daily report"
    )
    assert posted(page, "recipient") == ["group:administrators"]
    assert posted(page, "block") == [
        "campaign_totals",
        "response_funnel",
        "participation_charts",
        "day_by_day",
    ]
    assert posted(page, "charts") == ["first_submissions", "cumulative_families"]
    assert posted(page, "days_shown") == ["5"]
    assert checked(page, "sends") == "repeat"
    assert checked(page, "preset") == "daily"
    assert checked(page, "as_of") == "previous_day_end"
    assert not page.get_by_label(
        "Send only when there is something to report"
    ).is_checked()
    assert page.get_by_label("Time", exact=True).input_value() == ""
    assert page.evaluate("document.activeElement.value") == "daily"
    visible(page.get_by_text("Filled in from Daily report."))
    assert (group.bounding_box(), name.bounding_box()["y"]) == before
    # Weekly next: the form is as Daily filled it, so it is filled at once.
    start_from(page, "Weekly report").check()
    assert name.input_value() == "Weekly report"
    assert posted(page, "block") == ["additional_information", "response_funnel"]
    assert checked(page, "preset") == "weekly"
    assert page.get_by_label("Day", exact=True).input_value() == "0"
    assert checked(page, "as_of") == "send_time"
    assert page.get_by_label("Send only when there is something to report").is_checked()
    assert page.get_by_role("dialog").count() == 0
    assert page.evaluate("window.parishNoReload") is True
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_start_from_asks_before_replacing_typing(page, component_origin, axe_source):
    """A form holding typing asks first; Keep what I entered changes nothing
    and puts Blank back; Replace fills it. Blank empties the form again, and
    switching to Advanced alone is not typing (#2037)."""
    page.goto(component_origin + NEW)
    name = page.get_by_label("Report name")
    name.fill("My report")
    start_from(page, "Daily report").check()
    dialog = page.get_by_role("dialog", name="Replace what you have entered?")
    visible(dialog)
    keep = dialog.get_by_role("button", name="Keep what I entered")
    assert page.evaluate("document.activeElement.textContent.trim()") == (
        "Keep what I entered"
    )
    assert axe_violations(page, axe_source) == []
    # The shared dialog's Tab wrap: Tab from its last button returns to its
    # first, and Shift+Tab from the first goes to the last.
    buttons = dialog.get_by_role("button")
    buttons.last.focus()
    page.keyboard.press("Tab")
    assert buttons.first.evaluate("node => node === document.activeElement")
    page.keyboard.press("Shift+Tab")
    assert buttons.last.evaluate("node => node === document.activeElement")
    keep.click()
    eventually(page, "() => document.querySelector('dialog[open]') === null", True)
    assert name.input_value() == "My report"
    assert start_from(page, "Blank").is_checked()
    assert page.evaluate("document.activeElement.value") == "blank"
    start_from(page, "Daily report").check()
    dialog.get_by_role("button", name="Replace").click()
    assert name.input_value() == "Daily report"
    assert start_from(page, "Daily report").is_checked()
    # Unchanged since Daily filled it: Blank empties the form at once.
    start_from(page, "Blank").check()
    assert page.get_by_role("dialog").count() == 0
    assert name.input_value() == ""
    assert posted(page, "recipient") == []
    assert posted(page, "block") == []
    visible(page.get_by_text("The form is blank again."))
    # The mode alone is no change; Weekly then opens the schedule in Basic.
    page.get_by_label("Advanced").check()
    start_from(page, "Weekly report").check()
    assert page.get_by_role("dialog").count() == 0
    assert page.get_by_label("Basic", exact=True).is_checked()
    visible(page.get_by_label("Day", exact=True))
    assert posted(page, "weekdays") == ["0"]
    assert posted(page, "editor_mode") == ["basic"]


def test_a_refused_add_asks_before_start_from(page, component_origin):
    """A refused Add holds what was typed: no choice is made, and choosing
    one asks first; Escape or Keep what I entered leaves none chosen, with
    focus on the segment just chosen (#2037)."""
    page.goto(component_origin + NEW_REFUSED)
    assert checked(page, "start_from") is None
    start_from(page, "Weekly report").check()
    dialog = page.get_by_role("dialog", name="Replace what you have entered?")
    visible(dialog)
    page.keyboard.press("Escape")
    # The dialog's close event puts the choice back a moment after it shuts.
    eventually(
        page,
        "() => !document.querySelector('dialog[open]')"
        " && !document.querySelector('input[name=start_from]:checked')",
        True,
    )
    assert page.get_by_label("Report name").input_value() == "Staff requests"
    assert page.evaluate("document.activeElement.value") == "weekly"
    start_from(page, "Daily report").check()
    visible(dialog)
    dialog.get_by_role("button", name="Keep what I entered").click()
    eventually(page, "() => document.querySelector('dialog[open]') === null", True)
    assert checked(page, "start_from") is None
    assert page.evaluate("document.activeElement.value") == "daily"
    assert page.get_by_label("Report name").input_value() == "Staff requests"


def test_a_one_time_report_time_reads_the_zone_on_its_send_date(page, component_origin):
    """The Time reading names the zone on the one-time send date: a December
    send reads PST in this Los Angeles browser while it is on PDT (#2129)."""
    from playwright.sync_api import expect

    page.goto(component_origin + NEW)
    page.get_by_role("radio", name="Once").check()
    page.get_by_label("Time", exact=True).fill("7")
    note = page.locator("#report-time_reading")
    page.get_by_label("Date", exact=True).fill("2054-10-05")
    expect(note).to_have_text("Reads as 7:00 AM PDT")
    page.get_by_label("Date", exact=True).fill("2054-12-07")
    expect(note).to_have_text("Reads as 7:00 AM PST")
