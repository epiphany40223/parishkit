"""The ParishSoft refresh schedule editor in real browsers (#632).

The page script has no copy of the schedule rules: every check here is
answered by the real server code (``refresh_schedule_components``), routed
in place of the live check's address, so the browser sees exactly what the
server says. The tests cover the spec's "Live checks and saving": the line
beside Save is the only live region and never moves Save, Save is
unavailable only while a changed schedule has problems, a failed check
leaves Save available, and rows, the Basic choices and "Edit as text" act
in place. The Basic and Advanced modes (#1122) follow the ui-conventions
spec's "Basic and Advanced modes"; stored quick updates, removed by #1147,
are not shown.
"""

import pytest

from .refresh_schedule_components import (
    BASIC,
    CHECK,
    CLOSE,
    PRODUCTION,
    check_answer,
)
from .test_components import axe_violations
from .waits import has_text, hidden, painted, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

SAVE = "form.panel button[type=submit]"
STATUS = "#refresh-schedule-status"


def answering(page, stored, *, fail=False):
    """Answer the live check with the server's own code; return the requests."""
    from .conftest import NOW

    requests = []

    def handle(route):
        body = route.request.post_data or ""
        requests.append(body)
        if fail:
            route.fulfill(status=500, content_type="text/html", body="")
            return
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
            body=check_answer(stored, body, NOW),
        )

    page.route(f"**{CHECK}", handle)
    return requests


def rule_rows(page):
    """The rule rows now on the page."""
    return page.locator('[data-schedule-rows="rules"] > [data-schedule-row]')


def contains(locator, text):
    """Assert ``locator`` comes to contain ``text``, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_contain_text(text)


def test_an_existing_schedule_shows_as_rules_with_its_problems(
    page, component_origin, axe_source
):
    """Shown converted, with problems at their rows; Save stays available."""
    page.set_viewport_size({"width": 320, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    visible(page.get_by_text("Your current schedule, shown as rules."))
    assert rule_rows(page).count() == 4
    contains(
        page.locator("#rules-0-messages"),
        "00:00 is only 10 minutes after the 23:50 full refresh",
    )
    contains(page.locator("#rules-3-messages"), "Kept from the earlier schedule")
    contains(page.locator(STATUS), "Your current schedule has")
    assert page.locator(SAVE).is_enabled()
    # The page's browser (Los Angeles) is not in the parish's zone.
    contains(page.locator("[data-browser-zone-note]"), "America/Los_Angeles")
    contains(page.locator("details.schedule-day[open]").first, "your time)")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_a_change_is_checked_live_and_blocks_save_until_fixed(page, component_origin):
    """Typing pauses, the server checks, and only the line beside Save speaks."""
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    save = page.locator(SAVE)
    status = page.locator(STATUS)
    assert status.get_attribute("aria-live") == "polite"
    # The Basic / Advanced dialog, a modal, has its own status line.
    outside = "[data-refresh-schedule] :not(dialog *)"
    assert page.locator(f"{outside}[role=status]").count() == 0
    assert (
        page.locator(f"{outside}[aria-live]").count()
        == page.locator("[data-refresh-schedule] .time-reading + [aria-live]").count()
    )

    def gap():
        """How far Save sits below the rule rows, and the line's height.

        Problems appear at their rows, above Save; nothing between the rows
        and Save may change size, and the line beside Save keeps its room.
        """
        rows = page.locator('[data-schedule-rows="rules"]').bounding_box()
        return save.bounding_box()["y"] - rows["y"] - rows[
            "height"
        ], status.bounding_box()["height"]

    box = gap()
    field = page.locator('[name="rules-1-at"]')
    field.fill("08:10")
    recorded(page, requests, 1)
    assert "rules-1-at=08%3A10" in requests[0]
    # Only the editor's fields are sent: never the key or other settings.
    assert "candidate" not in requests[0] and "organization_id" not in requests[0]
    contains(page.locator("#rules-1-messages"), "At: Use :00, :15, :30 or :45.")
    contains(status, "Fix 1 problem before saving:")
    assert save.is_disabled()
    # The line kept its room, and the shorter preview (below Save) moved
    # nothing above it.
    assert within_half_a_pixel(box, gap())
    page.locator(f'{STATUS} a[href="#rules-1"]').click()
    field.fill("09:00")
    recorded(page, requests, 2)
    contains(status, "of ParishSoft time a day")
    has_text(page.locator("#rules-1-messages"), "")
    assert save.is_enabled()
    assert within_half_a_pixel(box, gap())


def answering_a_day_later(page):
    """Answer the live check as the server would a day after the page was
    drawn (past midnight), so the seven-day list moves on by one day;
    return the requests."""
    from datetime import timedelta

    from .conftest import NOW

    requests = []

    def handle(route):
        """The server's answer, one day on."""
        body = route.request.post_data or ""
        requests.append(body)
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
            body=check_answer(PRODUCTION, body, NOW + timedelta(days=1)),
        )

    page.route(f"**{CHECK}", handle)
    return requests


def day_on(page, day):
    """The listed day section for ``day`` (an ISO date)."""
    return page.locator(f'[data-schedule-part="preview"] details[data-day="{day}"]')


def test_opened_days_stay_open_by_date_when_the_list_moves_on(page, component_origin):
    """A check that answers a day later (past midnight) shifts the seven-day
    list by one day: the days the reader opened and closed keep their state
    by date, not by position, an untouched new today opens as the server
    draws it, and the redrawn list has its browser times (the shared
    schedule preview, #1129)."""
    from playwright.sync_api import expect

    requests = answering_a_day_later(page)
    page.goto(component_origin + "/refresh-schedule")
    days = page.locator('[data-schedule-part="preview"] details.schedule-day')
    first, second, third = (days.nth(i).get_attribute("data-day") for i in range(3))
    expect(days.nth(0)).to_have_js_property("open", True)
    # Close today, open the day after tomorrow; tomorrow is left alone.
    days.nth(0).locator("summary").click()
    days.nth(2).locator("summary").click()
    expect(days.nth(0)).to_have_js_property("open", False)
    expect(days.nth(2)).to_have_js_property("open", True)
    page.locator('[name="rules-1-at"]').fill("09:00")
    recorded(page, requests, 1)
    expect(days.nth(0)).to_have_attribute("data-day", second)
    # Position would have opened the new third day; the date keeps the
    # opened day open where it now stands, second. Tomorrow, untouched, is
    # the new today and opens as drawn.
    expect(day_on(page, third)).to_have_js_property("open", True)
    expect(day_on(page, second)).to_have_js_property("open", True)
    expect(days.nth(2)).to_have_js_property("open", False)
    expect(day_on(page, first)).to_have_count(0)
    # The browser (Los Angeles) is not in the schedule's zone: each redrawn
    # time carries the browser's time again.
    contains(day_on(page, third), "your time)")


def test_an_untouched_list_opens_the_new_today_after_midnight(page, component_origin):
    """A reader who opens and closes nothing: after a check that answers past
    midnight, the new today is open and every other day closed, as the
    server draws them (#1129)."""
    from playwright.sync_api import expect

    requests = answering_a_day_later(page)
    page.goto(component_origin + "/refresh-schedule")
    days = page.locator('[data-schedule-part="preview"] details.schedule-day')
    second = days.nth(1).get_attribute("data-day")
    page.locator('[name="rules-1-at"]').fill("09:00")
    recorded(page, requests, 1)
    expect(days.nth(0)).to_have_attribute("data-day", second)
    expect(days.nth(0)).to_have_js_property("open", True)
    expect(page.locator('[data-schedule-part="preview"] details[open]')).to_have_count(
        1
    )


def test_a_failed_check_keeps_the_answer_and_leaves_save_available(
    page, component_origin
):
    """The earlier answer stays, marked as not checked; saving checks again."""
    requests = answering(page, PRODUCTION, fail=True)
    page.goto(component_origin + "/refresh-schedule")
    page.locator('[name="rules-1-at"]').fill("08:10")
    recorded(page, requests, 1)
    visible(page.get_by_text("Not checked since your last change"))
    assert page.locator(SAVE).is_enabled()


def test_rows_are_added_removed_and_renumbered_in_place(page, component_origin):
    """Every row can go; the rest renumber, and each shows only its fields."""
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    start = page.evaluate("window.scrollY")
    count = rule_rows(page).count()
    page.get_by_role("button", name="Add a rule").click()
    # A blank new row is not checked until it is edited.
    page.wait_for_timeout(700)
    assert requests == []
    added = rule_rows(page).nth(count)
    assert added.get_attribute("id") == f"rules-{count}"
    assert page.locator('[name="rules-TOTAL_FORMS"]').input_value() == str(count + 1)
    # A new row starts as "Full at": its "every" fields are hidden and off.
    every = page.locator(f'[name="rules-{count}-every"]')
    assert every.is_disabled() and not every.is_visible()
    page.locator(f'[name="rules-{count}-shape"]').select_option("every")
    assert every.is_enabled() and every.is_visible()
    assert not page.locator(f'[name="rules-{count}-at"]').is_visible()
    page.locator(f'[name="rules-{count}-start"]').fill("9am")
    page.locator(f'[name="rules-{count}-last"]').fill("5pm")
    contains(page.locator(f"#id_rules-{count}-start_reading"), "Reads as 09:00")
    # Removing the first row renumbers the rest, names and all.
    rule_rows(page).first.get_by_role("button", name="Remove").click()
    assert rule_rows(page).count() == count
    assert page.locator(f'[name="rules-{count - 1}-shape"]').input_value() == "every"
    assert page.locator(f'[name="rules-{count}-kind"]').count() == 0
    assert page.locator('[name="rules-TOTAL_FORMS"]').input_value() == str(count)
    assert page.evaluate("document.activeElement.dataset.rowAdd") == "rules"
    # Nothing reloaded the page or jumped it to the top.
    assert page.evaluate("window.scrollY") >= start
    contains(page.locator(STATUS), "of ParishSoft time a day")


MODE = "#refresh-schedule-mode"
BASIC_PANEL = '[data-editor-mode-panel="basic"]'
ADVANCED_PANEL = '[data-editor-mode-panel="advanced"]'
NO_BASIC = "Your custom schedule has no basic equivalent."


def mode(page, name):
    """Choose the Basic or Advanced segment of the editor's toggle.

    Clicked, not checked: with no Basic equivalent the toggle stays on
    Advanced while the dialog asks.
    """
    page.locator(f"{MODE}-{name}").click()


def test_basic_writes_the_rows_and_advanced_finds_the_choice_again(
    page, component_origin, axe_source
):
    """A schedule saved from Basic opens in Basic; a choice is one rule set
    in Advanced, and back in Basic the same choice is chosen (#1122)."""
    requests = answering(page, BASIC)
    page.goto(component_origin + "/refresh-schedule-basic")
    visible(page.locator(BASIC_PANEL))
    hidden(page.locator(ADVANCED_PANEL))
    assert page.locator("#refresh-basic-nightly").is_checked()
    # The switch makes sense in both modes, so it is outside them.
    switch = page.locator('[name="skip_around_family_emails"]')
    assert switch.is_visible() and switch.is_checked()
    assert axe_violations(page, axe_source) == []
    # Choosing hours chooses "Every … hours".
    page.locator("#refresh-basic-hours").select_option("6")
    assert page.locator("#refresh-basic-every").is_checked()
    recorded(page, requests, 1)
    contains(page.locator(STATUS), "of ParishSoft time a day")
    assert page.locator(SAVE).is_enabled()
    mode(page, "advanced")
    visible(page.locator(ADVANCED_PANEL))
    hidden(page.locator(BASIC_PANEL))
    assert rule_rows(page).count() == 1
    assert page.locator('[name="rules-0-shape"]').input_value() == "every"
    assert page.locator('[name="rules-0-every"]').input_value() == "360"
    assert page.locator('[name="rules-0-last"]').input_value() == "18:00"
    assert switch.is_checked()
    assert axe_violations(page, axe_source) == []
    # Unchanged rows: back to Basic without asking, the same choice chosen.
    mode(page, "basic")
    visible(page.locator(BASIC_PANEL))
    assert page.locator("dialog[open]").count() == 0
    assert page.locator("#refresh-basic-every").is_checked()
    assert page.locator("#refresh-basic-hours").input_value() == "6"


def test_a_custom_schedule_asks_before_switching_to_basic(page, component_origin):
    """No Basic equivalent: the dialog asks; Keep Advanced changes nothing,
    Switch to Basic leaves no choice and holds Save until one is chosen."""
    requests = answering(page, BASIC)
    page.goto(component_origin + "/refresh-schedule-basic")
    mode(page, "advanced")
    field = page.locator('[name="rules-0-at"]')
    field.fill("03:00")
    # Leave the field now, so its change event joins the typing's check.
    # Left later (an engine that focuses the toggle on click, as Linux
    # WebKit does), the change would ask again half a second on, a third
    # check racing the Basic choice's below.
    field.blur()
    recorded(page, requests, 1)
    contains(page.locator(STATUS), "of ParishSoft time a day")
    mode(page, "basic")
    dialog = page.locator("dialog[open]")
    contains(dialog, NO_BASIC)
    assert page.evaluate("document.activeElement.hasAttribute('data-confirm-cancel')")
    dialog.get_by_role("button", name="Keep Advanced").click()
    hidden(page.locator("dialog[open]"))
    visible(page.locator(ADVANCED_PANEL))
    assert page.locator(f"{MODE}-advanced").is_checked()
    assert page.locator('[name="rules-0-at"]').input_value() == "03:00"
    mode(page, "basic")
    page.locator("dialog[open]").get_by_role("button", name="Switch to Basic").click()
    visible(page.locator(BASIC_PANEL))
    assert not page.locator('[name="refresh_basic"]:checked').count()
    contains(page.locator(STATUS), "Choose a basic schedule to review it.")
    assert page.locator(SAVE).is_disabled()
    page.locator("#refresh-basic-nightly").check()
    recorded(page, requests, 2)
    contains(page.locator(STATUS), "of ParishSoft time a day")
    assert page.locator(SAVE).is_enabled()
    assert page.locator('[name="rules-0-at"]').input_value() == "02:00"


def test_stored_quick_updates_are_not_shown(page, component_origin, axe_source):
    """Production's stored quarter-hour quick updates no longer run (#1147):
    the editor shows its full times alone, in Advanced, with no quick rule,
    note or action, and the grid and summary name none."""
    from playwright.sync_api import expect

    answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    visible(page.locator(ADVANCED_PANEL))
    assert page.locator(f"{MODE}-advanced").is_checked()
    assert rule_rows(page).count() == len(PRODUCTION["full_refresh_times"])
    for selector in ("[data-quick-rule]", "[data-quick-notice]", ".grid-quick"):
        assert page.locator(selector).count() == 0
    assert page.get_by_role("button", name="Remove quick updates").count() == 0
    expect(page.locator(".schedule-grid-legend")).not_to_contain_text("Quick")
    expect(page.locator('[data-schedule-part="summary"]')).not_to_contain_text("quick")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("width", [320, 1280])
def test_switching_modes_moves_nothing_above_the_editor(page, component_origin, width):
    """Only what is below the toggle changes (no layout shift)."""
    page.set_viewport_size({"width": width, "height": 900})
    answering(page, BASIC)
    page.goto(component_origin + "/refresh-schedule-basic")
    scroll_to(page, page.locator(MODE), 300)
    watched = ("#refresh-schedule-heading", MODE)
    before = tops(page, *watched)
    mode(page, "advanced")
    visible(page.locator(ADVANCED_PANEL))
    assert steady(before, tops(page, *watched))
    mode(page, "basic")
    visible(page.locator(BASIC_PANEL))
    assert steady(before, tops(page, *watched))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_edit_as_text_replaces_the_rules_with_single_times(page, component_origin):
    """The lists follow the rows; using them gives one "at" rule per time."""
    answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    page.get_by_text("Edit as text").click()
    full = page.locator("#refresh-text-full")
    assert full.input_value() == "00:00, 08:00, 12:00, 16:00, 20:00"
    full.fill("2am, 14:00")
    page.get_by_role("button", name="Use this list").click()
    # The list replaces every rule.
    assert rule_rows(page).count() == 2
    assert page.locator('[name="rules-1-at"]').input_value() == "14:00"
    assert page.locator('[name="skips-TOTAL_FORMS"]').input_value() == "0"
    contains(page.locator(STATUS), "of ParishSoft time a day")
    page.locator('[name="rules-1-at"]').fill("15:00")
    from playwright.sync_api import expect

    expect(full).to_have_value("02:00, 15:00")
    full.fill("2am, 25:00")
    page.get_by_role("button", name="Use this list").click()
    contains(page.locator("[data-schedule-text-error]"), "has no hour 25")
    assert rule_rows(page).count() == 2


def top(locator):
    """Where ``locator`` sits on the screen (its viewport y)."""
    return locator.bounding_box()["y"]


def test_a_whole_schedule_problem_is_said_beside_save_and_moves_nothing(
    page, component_origin
):
    """Removing the only rule: "Add a rule" is said in the line beside Save,
    and nothing above it moves (#736)."""
    from .conftest import NOW

    held = []
    page.route(f"**{CHECK}", lambda route: held.append(route))
    page.goto(component_origin + "/refresh-schedule-basic")
    mode(page, "advanced")
    status = page.locator(STATUS)
    rule_rows(page).first.get_by_role("button", name="Remove").click()
    add = page.get_by_role("button", name="Add a rule")
    before = top(add), top(page.locator(SAVE))
    # The check's answer, held until now, says the problem.
    while not held:
        page.wait_for_timeout(50)
    route = held.pop()
    route.fulfill(
        status=200,
        content_type="text/html; charset=utf-8",
        body=check_answer(BASIC, route.request.post_data or "", NOW),
    )
    contains(status, "Add a rule: keep at least one full refresh a day.")
    assert page.locator(SAVE).is_disabled()
    assert page.locator("[data-schedule-rows] .errorlist").count() == 0
    assert (top(add), top(page.locator(SAVE))) == before


def test_the_edited_control_stays_put_and_is_tied_to_its_row_problems(
    page, component_origin
):
    """A row's problems describe its controls and mark them invalid, also
    after leaving the field; messages drawn above the edited control do not
    move it on the screen."""
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    field = page.locator('[name="rules-1-at"]')
    field.scroll_into_view_if_needed()
    before = top(field)
    field.fill("08:10")
    recorded(page, requests, 1)
    contains(page.locator("#rules-1-messages"), "At: Use :00, :15, :30 or :45.")
    assert top(field) == before
    for name in ("rules-1-at", "rules-1-shape"):
        control = page.locator(f'[name="{name}"]')
        assert "rules-1-messages" in control.get_attribute("aria-describedby").split()
        assert control.get_attribute("aria-invalid") == "true"
    other = page.locator('[name="rules-0-at"]')
    assert "rules-0-messages" not in (other.get_attribute("aria-describedby") or "")
    assert other.get_attribute("aria-invalid") != "true"
    # Leaving the field keeps the row's mark until the problem is fixed.
    page.locator('[name="rules-0-at"]').focus()
    assert field.get_attribute("aria-invalid") == "true"
    field.fill("09:00")
    recorded(page, requests, 2)
    has_text(page.locator("#rules-1-messages"), "")
    assert field.get_attribute("aria-invalid") == "false"
    assert "rules-1-messages" not in (field.get_attribute("aria-describedby") or "")


def test_messages_above_the_edited_control_do_not_move_it(page, component_origin):
    """Fixing the kept 23:50 (rule 4) clears the spacing problem drawn at
    rule 1, above it: the page scrolls by the same amount, so the edited
    field stays where it was on the screen."""
    requests = answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    message = page.locator("#rules-0-messages")
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    field = page.locator('[name="rules-3-at"]')
    assert field.input_value() == "23:50"
    page.evaluate(
        "y => window.scrollTo(0, y)",
        page.evaluate("window.scrollY") + top(field) - 300,
    )
    assert page.evaluate("window.scrollY") > message.bounding_box()["height"]
    # The reader points at the field they edit. Without this the pointer is
    # left to chance: headless Chromium on Linux sometimes sends a
    # pointermove at (0, 0) while the page loads, and the editor then rightly
    # keeps what is at the top of the screen in place instead of the field
    # (#1012).
    field.hover()
    # Measured once typed, before the check answers (it waits for a pause).
    field.fill("23:30")
    before = top(field)
    recorded(page, requests, 1)
    has_text(message, "")
    # Scrolling moves by whole pixels.
    assert abs(top(field) - before) < 1


def bottom(locator):
    """Where ``locator``'s bottom edge sits on the screen (its viewport y)."""
    box = locator.bounding_box()
    return box["y"] + box["height"]


def tops(page, *locators):
    """Where each of ``locators`` sits on the screen, as a tuple."""
    return tuple(top(page.locator(item)) for item in locators)


def steady(before, after):
    """Whether nothing moved (scrolling moves by whole pixels)."""
    return all(abs(a - b) < 1 for a, b in zip(before, after, strict=True))


def scroll_to(page, locator, y):
    """Scroll so ``locator`` sits ``y`` pixels from the top of the screen."""
    page.evaluate(
        "y => window.scrollTo(0, y)", page.evaluate("window.scrollY") + top(locator) - y
    )


BELOW_RULES = (
    '[data-row-add="rules"]',
    '[data-row-add="skips"]',
    '[name="skip_around_family_emails"]',
    SAVE,
)


def test_a_row_message_moves_nothing_below_its_row(page, component_origin):
    """A row keeps room for a line of messages: a bad time's message, and
    its clearing, leave Add, the skips, the switch and Save where they are
    (#736)."""
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    field = page.locator('[name="rules-1-at"]')
    field.scroll_into_view_if_needed()
    before = tops(page, *BELOW_RULES)
    field.fill("08:10")
    recorded(page, requests, 1)
    contains(page.locator("#rules-1-messages"), "At: Use :00, :15, :30 or :45.")
    assert steady(before, tops(page, *BELOW_RULES))
    field.fill("08:15")
    recorded(page, requests, 2)
    has_text(page.locator("#rules-1-messages"), "")
    assert steady(before, tops(page, *BELOW_RULES))


def test_a_longer_message_keeps_the_control_under_the_pointer(page, component_origin):
    """A message longer than its row's kept line goes: the page scrolls so
    the control under the pointer stays put, also once the edited field has
    lost focus (nothing focused to steady)."""
    page.set_viewport_size({"width": 480, "height": 1200})
    requests = answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    message = page.locator("#rules-0-messages")
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    assert message.bounding_box()["height"] > 60
    add = page.locator('[data-row-add="rules"]')
    # The field and Add both on the screen, so neither action scrolls.
    field = page.locator('[name="rules-3-at"]')
    scroll_to(page, field, 100)
    add.hover()
    before = top(add)
    # Typed without the pointer, then left before the check answers.
    field.fill("23:30")
    page.evaluate("document.activeElement.blur()")
    recorded(page, requests, 1)
    has_text(message, "")
    assert abs(top(add) - before) < 1


def test_edit_as_text_moves_nothing_under_the_pointer(page, component_origin):
    """The lists' error is drawn after Use these lists in kept room, and
    using the lists keeps the button (and Save after it) in place while the
    rows above it change."""
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    page.get_by_text("Edit as text").click()
    full = page.locator("#refresh-text-full")
    apply = page.get_by_role("button", name="Use this list")
    described = full.get_attribute("aria-describedby").split()
    assert "refresh-text-error" in described
    scroll_to(page, apply, 500)
    watched = ("[data-schedule-text-apply]", SAVE)
    before = tops(page, *watched)
    full.fill("2am, 25:00")
    apply.click()
    contains(page.locator("#refresh-text-error"), "has no hour 25")
    assert steady(before, tops(page, *watched))
    full.fill("2am, 14:00")
    apply.click()
    assert rule_rows(page).count() == 2
    has_text(page.locator("#refresh-text-error"), "")
    assert steady(before, tops(page, *watched))
    recorded(page, requests, 1)
    from playwright.sync_api import expect

    # The answer is in place once the seven-day list loses 16:00.
    expect(page.locator('[data-schedule-part="preview"]')).not_to_contain_text("16:00")
    assert steady(before, tops(page, *watched))


def close_page_with_field_at(page, component_origin, y):
    """Open the too-close schedule on a tall screen, with rule 4's time
    field (the kept 23:50) ``y`` pixels from the top; return the requests,
    rule 1's long message and the field."""
    page.set_viewport_size({"width": 480, "height": 1200})
    requests = answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    message = page.locator("#rules-0-messages")
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    assert message.bounding_box()["height"] > 60
    field = page.locator('[name="rules-3-at"]')
    scroll_to(page, field, y)
    return requests, message, field


def within_half_a_pixel(before, after):
    """Whether each of ``after`` is within 0.5px of ``before``."""
    return all(abs(a - b) <= 0.5 for a, b in zip(before, after, strict=True))


def test_a_pointer_in_a_gap_between_rows_keeps_the_rows_in_place(
    page, component_origin
):
    """Over the blank space between two rows the pointer points at the rows'
    container, whose top never moves: the next row below the pointer is
    kept in place instead, not the focused control below it. That row is
    rule 4, the one edited: its kept-time note goes too, so the focused
    control moves up by exactly that row's own shrink and no more."""
    requests, message, field = close_page_with_field_at(page, component_origin, 300)
    rows = rule_rows(page)
    above, below = rows.nth(2).bounding_box(), rows.nth(3).bounding_box()
    gap = below["y"] - (above["y"] + above["height"])
    assert gap > 4
    page.mouse.move(above["x"] + above["width"] / 2, below["y"] - gap / 2)
    assert page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).matches('[data-schedule-rows]')",
        [above["x"] + above["width"] / 2, below["y"] - gap / 2],
    )
    field.fill("23:30")
    focused = page.locator('[data-row-add="rules"]')
    focused.focus()
    row = page.locator("#rules-3")
    before = (top(row), top(focused) - bottom(row))
    recorded(page, requests, 1)
    has_text(message, "")
    has_text(page.locator("#rules-3-messages"), "")
    assert within_half_a_pixel(before, (top(row), top(focused) - bottom(row)))


def test_a_cancelled_touch_leaves_no_stale_pointer(page, component_origin):
    """A touch that turns into a pan ends with pointercancel, not pointerup:
    it no longer points anywhere, so the focused control is kept in place,
    not rule 1 where the touch began."""
    requests, message, field = close_page_with_field_at(page, component_origin, 600)
    first = rule_rows(page).nth(0).bounding_box()
    page.evaluate(
        """([x, y]) => {
          const at = {clientX: x, clientY: y, pointerType: "touch", bubbles: true};
          document.dispatchEvent(new PointerEvent("pointerdown", at));
          document.dispatchEvent(new PointerEvent("pointercancel", at));
        }""",
        [first["x"] + first["width"] / 2, first["y"] + 10],
    )
    field.fill("23:30")
    before = top(field)
    recorded(page, requests, 1)
    has_text(message, "")
    assert within_half_a_pixel((before,), (top(field),))


def test_a_pointer_on_a_replaced_message_keeps_its_row_in_place(page, component_origin):
    """The message under the pointer is replaced by the redraw (rule 4's
    kept-time note goes once its time is on the quarter hour): its row is
    kept in place instead, also with nothing focused."""
    requests, message, field = close_page_with_field_at(page, component_origin, 300)
    note = page.locator("#rules-3-messages > *").first
    contains(note, "Kept from the earlier schedule")
    box = note.bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + box["height"] / 2)
    field.fill("23:30")
    page.evaluate("document.activeElement.blur()")
    before = tops(page, "#rules-3")
    recorded(page, requests, 1)
    has_text(message, "")
    has_text(page.locator("#rules-3-messages"), "")
    assert within_half_a_pixel(before, tops(page, "#rules-3"))


@pytest.mark.parametrize("focused", [False, True], ids=["unfocused", "focused"])
def test_a_pointer_above_the_switch_keeps_the_switch_in_place(
    page, component_origin, focused
):
    """Just above the switch (no skips) the pointer points at the editor,
    whose top never moves: the first block below the pointer, the switch,
    is kept in place while rule 1's long message clears above it, whether
    or not a field is focused."""
    requests, message, field = close_page_with_field_at(page, component_origin, 300)
    assert page.locator('[data-schedule-rows="skips"] > *').count() == 0
    switch = page.locator(".schedule-switch")
    scroll_to(page, switch, 700)
    box = switch.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] - 3
    page.mouse.move(x, y)
    pointed = "([x, y]) => document.elementFromPoint(x, y)"
    assert page.evaluate(f"{pointed}.matches('[data-refresh-schedule]')", [x, y])
    field.fill("23:30")
    # Firefox scrolls rule 4's caret into view a frame after the fill.
    painted(page)
    if not focused:
        page.evaluate("document.activeElement.blur()")
    before = tops(page, ".schedule-switch")
    recorded(page, requests, 1)
    has_text(message, "")
    assert within_half_a_pixel(before, tops(page, ".schedule-switch"))


def test_a_pointer_outside_the_form_keeps_what_it_points_at_in_place(
    page, component_origin
):
    """Over the page heading, outside the form, the heading itself is kept
    in place (nothing outside the form is redrawn), not the focused field
    whose row's message grows above it."""
    requests, message, field = close_page_with_field_at(page, component_origin, 300)
    field.fill("23:30")
    recorded(page, requests, 1)
    has_text(message, "")
    field.fill("23:50")
    # Firefox scrolls rule 4's caret into view a frame after the fill, which
    # would undo the scroll to the top below.
    painted(page)
    # Add a rule is focused without scrolling it into view, with the
    # heading at the top of the screen under the pointer.
    page.evaluate(
        """() => {
          document.querySelector('[data-row-add="rules"]').focus({preventScroll: true});
          window.scrollTo(0, 0);
        }"""
    )
    box = page.locator("h1").bounding_box()
    x, y = box["x"] + 5, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    assert page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).matches('h1')", [x, y]
    )
    before = tops(page, "h1")
    recorded(page, requests, 2)
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    assert message.bounding_box()["height"] > 60
    assert within_half_a_pixel(before, tops(page, "h1"))


def test_a_focused_invalid_field_shows_the_focus_ring_in_forced_colors(
    page, component_origin
):
    """In forced colors the invalid mark is an outline, which must not hide
    the 3px keyboard focus ring of an invalid field."""
    page.emulate_media(forced_colors="active")
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    field = page.locator('[name="rules-1-at"]')
    field.fill("08:10")
    recorded(page, requests, 1)
    contains(page.locator("#rules-1-messages"), "At: Use :00, :15, :30 or :45.")
    page.evaluate("document.activeElement.blur()")
    assert field.get_attribute("aria-invalid") == "true"
    assert page.evaluate("matchMedia('(forced-colors: active)').matches")
    assert field.evaluate("node => getComputedStyle(node).outlineWidth") == "1px"
    field.focus()
    assert field.evaluate("node => node.matches(':focus-visible')")
    assert field.evaluate("node => getComputedStyle(node).outlineWidth") == "3px"


def type_quietly(page, name, value):
    """Type ``value`` into the field ``name`` the way a reader does, but
    without scrolling the page or moving the pointer (Playwright's fill
    scrolls the field into view first)."""
    page.locator(f'[name="{name}"]').evaluate(
        """(node, value) => {
          node.value = value;
          node.dispatchEvent(new Event("input", {bubbles: true}));
        }""",
        value,
    )


def point_at_height(page, x, y):
    """Move the pointer to (``x``, ``y``) and return what is under it."""
    page.mouse.move(x, y)
    return page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).className", [x, y]
    )


def test_a_pointer_over_blank_outlook_space_keeps_the_seven_day_list_in_place(
    page, component_origin
):
    """Over the blank space between the summary and "Next seven days", the
    summary emptying above the pointer moves nothing under it."""
    page.set_viewport_size({"width": 1280, "height": 500})
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    heading = page.locator("#refresh-schedule-outlook h2").nth(1)
    contains(heading, "Next seven days")
    summary = page.locator('[data-schedule-part="summary"]')
    scroll_to(page, heading, 400)
    gap = summary.bounding_box()
    y = (gap["y"] + gap["height"] + top(heading)) / 2
    assert top(heading) - y > 2
    pointed = point_at_height(page, gap["x"] + 5, y)
    assert "refresh-schedule-outlook" in pointed
    seven = "#refresh-schedule-outlook h2 >> nth=1"
    before = tops(page, seven)
    type_quietly(page, "rules-1-at", "08:10")
    recorded(page, requests, 1)
    has_text(summary, "")
    assert within_half_a_pixel(before, tops(page, seven))


@pytest.mark.parametrize("where", ["gap", "right", "sidebar"])
def test_a_pointer_beside_the_main_column_keeps_its_height_in_place(
    page, component_origin, where
):
    """In the two-column Admin layout only the pointer's height matters: in
    the gap between the sidebar and the page, or right of the page, the
    rows at that height stay put while rule 1's long message clears above.
    Over the sticky sidebar, which does not scroll, with nothing focused,
    the sidebar under the pointer stays put whatever the page does."""
    page.set_viewport_size({"width": 1000, "height": 900})
    requests = answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    message = page.locator("#rules-0-messages")
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    height = message.bounding_box()["height"]
    scroll_to(page, page.locator("#rules-2"), 300)
    sidebar = page.locator(".admin-sidebar").bounding_box()
    main = page.locator("main").bounding_box()
    x = {
        "gap": (sidebar["x"] + sidebar["width"] + main["x"]) / 2,
        "right": main["x"] + main["width"] + 4,
        "sidebar": sidebar["x"] + sidebar["width"] / 2,
    }[where]
    pointed = point_at_height(page, x, 310)
    assert ("admin-sidebar" in pointed) == (where == "sidebar")
    under = page.evaluate_handle(f"document.elementFromPoint({x}, 310)")
    held = under.bounding_box()["y"]
    before = tops(page, "#rules-2")
    type_quietly(page, "rules-3-at", "23:30")
    recorded(page, requests, 1)
    has_text(message, "")
    assert height - message.bounding_box()["height"] > 10
    if where == "sidebar":
        assert abs(under.bounding_box()["y"] - held) <= 0.5
    else:
        assert within_half_a_pixel(before, tops(page, "#rules-2"))


def test_a_pointer_below_the_save_line_keeps_the_outlook_in_place(
    page, component_origin
):
    """Below the Save line, in the form's own blank space (or the reserved
    status line of the review dialog under the form, #750), the line growing
    with more problems moves nothing under the pointer (#736)."""
    page.set_viewport_size({"width": 360, "height": 640})
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    status = page.locator(STATUS)
    type_quietly(page, "rules-1-at", "08:10")
    recorded(page, requests, 1)
    contains(status, "Fix 1 problem before saving:")
    outlook = page.locator("#refresh-schedule-outlook")
    scroll_to(page, outlook, 460)
    line = page.locator(".schedule-save").bounding_box()
    y = (line["y"] + line["height"] + top(outlook)) / 2
    assert top(outlook) - y > 2
    pointed = point_at_height(page, 30, y)
    assert "panel" in pointed or "review-outcome" in pointed
    height = status.bounding_box()["height"]
    before = tops(page, "#refresh-schedule-outlook")
    for index in (0, 2, 3, 4):
        type_quietly(page, f"rules-{index}-at", f"0{index}:10")
    recorded(page, requests, 2)
    contains(status, "Fix 5 problems before saving:")
    assert status.bounding_box()["height"] - height > 50
    assert within_half_a_pixel(before, tops(page, "#refresh-schedule-outlook"))


@pytest.mark.parametrize(
    ("time", "height"),
    [(".schedule-grid-row >> nth=3", 640), (".schedule-day[open] li >> nth=3", 900)],
    ids=["grid", "list"],
)
def test_the_not_checked_line_moves_nothing_under_the_pointer(
    page, component_origin, time, height
):
    """On a narrow screen the "Not checked" line takes several lines when a
    check fails: the seven-day preview under the pointer stays put. With the
    line on the screen (below "Next seven days"), the browser's own scroll
    anchoring would keep the heading, not the preview. The pointer is on a
    day's label in the grid, which comes first in the preview (slice 4) and
    is sticky only sideways, so it still moves with the page, or on a time
    in the list below the grid (a taller window keeps it on the screen)."""
    page.set_viewport_size({"width": 360, "height": height})
    requests = answering(page, PRODUCTION, fail=True)
    page.goto(component_origin + "/refresh-schedule")
    scroll_to(page, page.locator("#refresh-schedule-outlook h2").nth(1), 100)
    box = page.locator(time).bounding_box()
    assert box["y"] < height - 40
    point_at_height(page, box["x"] + 5, box["y"] + box["height"] / 2)
    line = page.locator("[data-schedule-stale]")
    height = line.bounding_box()["height"]
    before = tops(page, time)
    type_quietly(page, "rules-1-at", "08:10")
    recorded(page, requests, 1)
    visible(page.get_by_text("Not checked since your last change"))
    assert line.bounding_box()["height"] - height > 20
    assert within_half_a_pixel(before, tops(page, time))
    # The next check answers: the line goes and the preview is redrawn,
    # and the day under the pointer still stays put.
    page.unroute(f"**{CHECK}")
    requests = answering(page, PRODUCTION)
    type_quietly(page, "rules-1-at", "08:15")
    recorded(page, requests, 1)
    # Hidden in the same change that redraws the preview.
    hidden(page.get_by_text("Not checked since your last change"))
    assert within_half_a_pixel(before, tops(page, time))


def point_inside(page, selector, y_offset=12):
    """Rest the pointer just inside the top of ``selector``; return its handle."""
    box = page.locator(selector).bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + y_offset)
    return page.locator(selector).element_handle()


def handle_top(handle):
    """Where the element ``handle`` sits on the screen, wherever it went."""
    return handle.bounding_box()["y"]


def press_without_scrolling(page, selector, key="Enter"):
    """Focus ``selector`` without scrolling (a keyboard reader tabbing
    there), then press ``key``."""
    page.locator(selector).evaluate("node => node.focus({preventScroll: true})")
    page.keyboard.press(key)


@pytest.mark.parametrize("where", ["row", "margin"])
def test_keyboard_remove_keeps_the_row_under_the_pointer(page, component_origin, where):
    """Removing rule 1 from the keyboard, with the pointer resting on rule 4
    (or in the margin just above it): rule 4 is followed by identity, not
    by its old position, so it stays put while the rows above it shrink."""
    page.set_viewport_size({"width": 480, "height": 900})
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    scroll_to(page, page.locator("#rules-3"), 500)
    # Rule 1 starts above the screen, so the page can scroll up by its height.
    assert top(page.locator("#rules-0")) < 0
    if where == "row":
        watched = point_inside(page, "#rules-3")
    else:
        above = page.locator("#rules-2").bounding_box()
        below = page.locator("#rules-3").bounding_box()
        x, y = below["x"] + 20, (above["y"] + above["height"] + below["y"]) / 2
        page.mouse.move(x, y)
        pointed = "([x, y]) => document.elementFromPoint(x, y)"
        assert page.evaluate(f"{pointed}.matches('[data-schedule-rows]')", [x, y])
        watched = page.locator("#rules-3").element_handle()
    before = handle_top(watched)
    press_without_scrolling(page, "#rules-0 [data-row-remove]")
    assert rule_rows(page).count() == 4
    assert abs(handle_top(watched) - before) <= 0.5
    assert page.evaluate("document.activeElement.dataset.rowAdd") == "rules"
    recorded(page, requests, 1)
    contains(page.locator(STATUS), "of ParishSoft time a day")
    assert abs(handle_top(watched) - before) <= 0.5


def test_clicking_remove_does_not_scroll_the_page(page, component_origin):
    """On a phone, Remove moves focus to Add a rule, off the screen: focus
    moves without scrolling what is under the mouse."""
    page.set_viewport_size({"width": 360, "height": 640})
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    scroll_to(page, page.locator("#rules-0"), 100)
    assert top(page.locator('[data-row-add="rules"]')) > 640
    start = page.evaluate("window.scrollY")
    before = top(page.locator("#rules-0"))
    box = page.locator("#rules-0 [data-row-remove]").bounding_box()
    page.mouse.click(box["x"] + 10, box["y"] + 10)
    assert page.evaluate("document.activeElement.dataset.rowAdd") == "rules"
    assert page.evaluate("window.scrollY") == start
    # The next rule takes the removed one's place under the mouse.
    assert abs(top(page.locator("#rules-0")) - before) <= 0.5
    recorded(page, requests, 1)
    contains(page.locator(STATUS), "of ParishSoft time a day")


def test_a_header_note_above_main_moves_no_rule(page, component_origin):
    """The header's polls show notes above main (here the delivery warning):
    the browser's scroll anchoring, left on for the page, keeps the rule
    under the pointer in place."""
    page.set_viewport_size({"width": 480, "height": 900})
    answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    scroll_to(page, page.locator("#rules-3"), 400)
    watched = point_inside(page, "#rules-3")
    before = handle_top(watched)
    warning = page.locator("[data-delivery-warning]")
    warning.evaluate("node => { node.hidden = false; }")
    assert warning.bounding_box()["height"] > 20
    assert top(warning) < top(page.locator("main"))
    page.wait_for_timeout(100)
    assert abs(handle_top(watched) - before) <= 0.5


# Removes ui-v1.css's rules for the regions the editor redraws, to compare
# with the plain blocks they would otherwise be.
REGION_RULES = """() => {
  for (const sheet of document.styleSheets) {
    for (let index = sheet.cssRules.length - 1; index >= 0; index -= 1) {
      if ((sheet.cssRules[index].selectorText || "").includes("[data-schedule-rows]")) {
        sheet.deleteRule(index);
      }
    }
  }
}"""


@pytest.mark.parametrize("width", [360, 1280])
def test_the_redrawn_regions_keep_the_spacing_of_plain_blocks(
    page, component_origin, width
):
    """Holding their margins (flow-root) adds no room: rule 1, Add, Save,
    the seven-day heading and list sit where plain blocks put them."""
    page.set_viewport_size({"width": width, "height": 800})
    answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    watched = (
        "#rules-0",
        "#rules-4",
        '[data-row-add="rules"]',
        SAVE,
        "#refresh-schedule-outlook > h2:nth-of-type(2)",
        '[data-schedule-part="preview"] details',
    )

    def placed():
        """Each watched element's page y, then where the editor's and the
        outlook's panels end."""
        return tuple(
            page.evaluate(
                """selectors => selectors.map((selector) => {
                  const box = document.querySelector(selector).getBoundingClientRect();
                  return box.top + window.scrollY;
                }).concat(["#refresh-schedule", "#refresh-schedule-outlook"].map(
                  (selector) => document.querySelector(selector)
                    .getBoundingClientRect().bottom + window.scrollY))""",
                list(watched),
            )
        )

    rows = page.locator('[data-schedule-rows="rules"]')
    display = "node => getComputedStyle(node).display"
    now = placed()
    assert rows.evaluate(display) == "flow-root"
    page.evaluate(REGION_RULES)
    assert rows.evaluate(display) == "block"
    assert within_half_a_pixel(now, placed()), (now, placed())


def test_a_growing_time_reading_moves_no_rule_under_the_pointer(page, component_origin):
    """At 320 pixels a time's reading line can take three lines: rule 1's
    reading growing (and its check's message) keeps rule 2, under the
    pointer, in place."""
    page.set_viewport_size({"width": 320, "height": 900})
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    scroll_to(page, page.locator("#rules-1"), 600)
    reading = page.locator("#id_rules-0-at_reading")
    assert top(reading) > 0
    height = reading.bounding_box()["height"]
    watched = point_inside(page, "#rules-1")
    before = handle_top(watched)
    contains(page.locator(STATUS), "of ParishSoft time a day")
    type_quietly(page, "rules-0-at", "8:10 in the evening")
    # A refusal shows once typing pauses, as the check is sent.
    page.wait_for_timeout(700)
    assert reading.bounding_box()["height"] - height > 20
    assert abs(handle_top(watched) - before) <= 0.5
    recorded(page, requests, 1)
    from playwright.sync_api import expect

    # Once answered, the line beside Save says the problem.
    expect(page.locator(STATUS)).not_to_contain_text("of ParishSoft time a day")
    assert abs(handle_top(watched) - before) <= 0.5
    type_quietly(page, "rules-0-at", "9")
    contains(reading, "Reads as 09:00")
    assert abs(handle_top(watched) - before) <= 0.5


def test_a_pointer_over_the_sidebar_keeps_the_focused_field_in_place(
    page, component_origin
):
    """Over the sticky sidebar, which does not scroll, the focused control
    is what stays in place while rule 1's long message clears above it."""
    page.set_viewport_size({"width": 1000, "height": 900})
    requests = answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    message = page.locator("#rules-0-messages")
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    height = message.bounding_box()["height"]
    scroll_to(page, page.locator("#rules-2"), 300)
    page.locator('[data-row-add="rules"]').evaluate(
        "node => node.focus({preventScroll: true})"
    )
    sidebar = page.locator(".admin-sidebar").bounding_box()
    assert "admin-sidebar" in point_at_height(
        page, sidebar["x"] + sidebar["width"] / 2, 310
    )
    before = tops(page, '[data-row-add="rules"]')
    type_quietly(page, "rules-3-at", "23:30")
    recorded(page, requests, 1)
    has_text(message, "")
    assert height - message.bounding_box()["height"] > 10
    assert within_half_a_pixel(before, tops(page, '[data-row-add="rules"]'))


@pytest.mark.parametrize(
    "path",
    [
        "/refresh-schedule",
        "/refresh-schedule-gated",
        "/refresh-schedule-basic",
        "/refresh-schedule-loaded",
    ],
)
def test_pasting_a_key_posts_the_shown_schedule_unchanged(page, component_origin, path):
    """A new key pasted with the schedule untouched posts the schedule as the
    page showed it, so the key's review never reads as a key plus a schedule
    change (``key_with_schedule_change``, #1492). The review is posted
    without the key (#1568). A locked organization ID is not sent at all."""
    from django.http import QueryDict

    from parishkit.stewardship.accounts.refresh_schedule_forms import ScheduleEditor

    from .refresh_schedule_components import STORED

    stored = STORED.get(path, PRODUCTION)
    answering(page, stored)
    posts = []

    def submitted(route):
        """Record the page's own Save post (the key's Review); let the
        page's draw through."""
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.post_data or "")
        route.fulfill(status=204, body="")

    page.route(f"**{path}", submitted)
    page.goto(component_origin + path)
    painted(page)
    page.locator("input[name=candidate]").fill("a-new-parishsoft-key")
    page.get_by_role("button", name="Save", exact=True).click()
    recorded(page, posts, 1)
    data = QueryDict(posts[0])
    assert data["key_review"] == "yes" and "candidate" not in data
    assert ("organization_id" in data) is not path.endswith("-loaded")
    editor = ScheduleEditor(data, stored=stored)
    assert editor.posted and not editor.changed, editor.document()


def test_a_locked_organization_id_is_inactive_with_its_reason(page, component_origin):
    """Once its data is loaded, the organization ID is a disabled field that
    still shows the ID, with its one-line reason drawn by the server under
    the label, so nothing moves when it is clicked (#1492; ui-conventions,
    No layout shift)."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/refresh-schedule-loaded")
    field = page.locator("#id_organization_id")
    expect(field).to_be_disabled()
    expect(field).to_have_value("12345")
    reason = page.get_by_text(
        "Locked: ParishSoft data for this organization is loaded."
    )
    visible(reason)
    painted(page)
    before = field.bounding_box(), reason.bounding_box()
    field.click(force=True)
    assert page.evaluate("document.activeElement.id") != "id_organization_id"
    assert (field.bounding_box(), reason.bounding_box()) == before
