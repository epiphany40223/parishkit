"""The ParishSoft refresh schedule editor in real browsers (#632).

The page script has no copy of the schedule rules: every check here is
answered by the real server code (``refresh_schedule_components``), routed
in place of the live check's address, so the browser sees exactly what the
server says. The tests cover the spec's "Live checks and saving": the line
beside Save is the only live region and never moves Save, Save is
unavailable only while a changed schedule has problems, a failed check
leaves Save available, and rows, presets and "Edit as text" act in place.
"""

import pytest

from .refresh_schedule_components import CHECK, CLOSE, PRODUCTION, check_answer
from .test_components import axe_violations
from .waits import has_text, hidden, recorded, visible

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
    contains(page.locator("#rules-1-messages"), "Kept from the earlier schedule")
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
    assert page.locator("[data-refresh-schedule] [role=status]").count() == 0
    assert (
        page.locator("[data-refresh-schedule] [aria-live]").count()
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
    assert gap() == box
    page.locator(f'{STATUS} a[href="#rules-1"]').click()
    field.fill("09:00")
    recorded(page, requests, 2)
    contains(status, "of ParishSoft time a day")
    has_text(page.locator("#rules-1-messages"), "")
    assert save.is_enabled()
    assert gap() == box


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
    page.locator(f'[name="rules-{count}-kind"]').select_option("quick")
    page.locator(f'[name="rules-{count}-start"]').fill("9am")
    page.locator(f'[name="rules-{count}-last"]').fill("5pm")
    contains(page.locator(f"#id_rules-{count}-start_reading"), "Reads as 09:00")
    # Removing the first row renumbers the rest, names and all.
    rule_rows(page).first.get_by_role("button", name="Remove").click()
    assert rule_rows(page).count() == count
    assert page.locator(f'[name="rules-{count - 1}-kind"]').input_value() == "quick"
    assert page.locator(f'[name="rules-{count}-kind"]').count() == 0
    assert page.locator('[name="rules-TOTAL_FORMS"]').input_value() == str(count)
    assert page.evaluate("document.activeElement.dataset.rowAdd") == "rules"
    # Nothing reloaded the page or jumped it to the top.
    assert page.evaluate("window.scrollY") >= start
    contains(page.locator(STATUS), "of ParishSoft time a day")


def test_a_preset_asks_before_replacing_changes(page, component_origin):
    """Unchanged, a preset applies at once; with changes, it asks in place."""
    answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    switch = page.locator('[name="skip_around_family_emails"]')
    switch.check()
    # Ticking the switch is a change: the preset asks first.
    page.get_by_role("button", name="Every 4 hours").click()
    question = page.get_by_text("Replace your changes with this preset?")
    visible(question)
    page.get_by_role("button", name="Keep my changes").click()
    hidden(question)
    assert rule_rows(page).count() == 6
    page.get_by_role("button", name="Every 4 hours").click()
    page.get_by_role("button", name="Replace my changes").click()
    assert rule_rows(page).count() == 1
    assert page.locator('[name="rules-0-every"]').input_value() == "240"
    assert page.locator('[name="rules-0-start"]').input_value() == "00:00"
    # The switch is left as it was.
    assert switch.is_checked()
    # Unchanged since that preset: the next one applies without asking.
    page.get_by_role("button", name="Nightly only").click()
    assert not question.is_visible()
    assert rule_rows(page).count() == 1
    assert page.locator('[name="rules-0-at"]').input_value() == "02:00"
    contains(page.locator(STATUS), "of ParishSoft time a day")


def test_edit_as_text_replaces_the_rules_with_single_times(page, component_origin):
    """The lists follow the rows; using them gives one "at" rule per time."""
    answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    page.get_by_text("Edit as text").click()
    full = page.locator("#refresh-text-full")
    assert full.input_value() == "00:00, 08:00, 12:00, 16:00, 20:00"
    full.fill("2am, 14:00")
    page.locator("#refresh-text-quick").fill("")
    page.get_by_role("button", name="Use these lists").click()
    assert rule_rows(page).count() == 2
    assert page.locator('[name="rules-1-at"]').input_value() == "14:00"
    assert page.locator('[name="skips-TOTAL_FORMS"]').input_value() == "0"
    contains(page.locator(STATUS), "of ParishSoft time a day")
    page.locator('[name="rules-1-at"]').fill("15:00")
    from playwright.sync_api import expect

    expect(full).to_have_value("02:00, 15:00")
    full.fill("2am, 25:00")
    page.get_by_role("button", name="Use these lists").click()
    contains(page.locator("[data-schedule-text-error]"), "has no hour 25")
    assert rule_rows(page).count() == 2


def top(locator):
    """Where ``locator`` sits on the screen (its viewport y)."""
    return locator.bounding_box()["y"]


def test_a_whole_schedule_problem_is_said_beside_save_and_moves_nothing(
    page, component_origin
):
    """Changing the only Full rule to Quick: "Add a Full rule" is said in the
    line beside Save, and nothing above it moves (#736)."""
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    page.get_by_role("button", name="Nightly only").click()
    recorded(page, requests, 1)
    status = page.locator(STATUS)
    contains(status, "of ParishSoft time a day")
    kind = page.locator('[name="rules-0-kind"]')
    add = page.get_by_role("button", name="Add a rule")
    before = top(kind), top(add), top(page.locator(SAVE))
    kind.select_option("quick")
    recorded(page, requests, 2)
    contains(status, "Add a Full rule: keep at least one full refresh a day.")
    assert page.locator(SAVE).is_disabled()
    assert page.locator("[data-schedule-rows] .errorlist").count() == 0
    assert (top(kind), top(add), top(page.locator(SAVE))) == before


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
    for name in ("rules-1-at", "rules-1-kind", "rules-1-shape"):
        control = page.locator(f'[name="{name}"]')
        assert "rules-1-messages" in control.get_attribute("aria-describedby").split()
        assert control.get_attribute("aria-invalid") == "true"
    other = page.locator('[name="rules-0-at"]')
    assert "rules-0-messages" not in (other.get_attribute("aria-describedby") or "")
    assert other.get_attribute("aria-invalid") != "true"
    # Leaving the field keeps the row's mark until the problem is fixed.
    page.locator('[name="rules-0-kind"]').focus()
    assert field.get_attribute("aria-invalid") == "true"
    field.fill("09:00")
    recorded(page, requests, 2)
    has_text(page.locator("#rules-1-messages"), "")
    assert field.get_attribute("aria-invalid") == "false"
    assert "rules-1-messages" not in (field.get_attribute("aria-describedby") or "")


def test_messages_above_the_edited_control_do_not_move_it(page, component_origin):
    """Fixing the kept 23:50 (rule 2) clears the spacing problem drawn at
    rule 1, above it: the page scrolls by the same amount, so the edited
    field stays where it was on the screen."""
    requests = answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    message = page.locator("#rules-0-messages")
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    field = page.locator('[name="rules-1-at"]')
    assert field.input_value() == "23:50"
    page.evaluate(
        "y => window.scrollTo(0, y)",
        page.evaluate("window.scrollY") + top(field) - 300,
    )
    assert page.evaluate("window.scrollY") > message.bounding_box()["height"]
    # Measured once typed, before the check answers (it waits for a pause).
    field.fill("23:30")
    before = top(field)
    recorded(page, requests, 1)
    has_text(message, "")
    # Scrolling moves by whole pixels.
    assert abs(top(field) - before) < 1


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
    field = page.locator('[name="rules-1-at"]')
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
    apply = page.get_by_role("button", name="Use these lists")
    for name in ("#refresh-text-full", "#refresh-text-quick"):
        described = page.locator(name).get_attribute("aria-describedby").split()
        assert "refresh-text-error" in described
    scroll_to(page, apply, 500)
    watched = ("[data-schedule-text-apply]", SAVE)
    before = tops(page, *watched)
    full.fill("2am, 25:00")
    apply.click()
    contains(page.locator("#refresh-text-error"), "has no hour 25")
    assert steady(before, tops(page, *watched))
    full.fill("2am, 14:00")
    page.locator("#refresh-text-quick").fill("")
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
    """Open the too-close schedule on a tall screen, with rule 2's time
    field ``y`` pixels from the top; return the requests, rule 1's long
    message and the field."""
    page.set_viewport_size({"width": 480, "height": 1200})
    requests = answering(page, CLOSE)
    page.goto(component_origin + "/refresh-schedule-close")
    message = page.locator("#rules-0-messages")
    contains(message, "00:00 is only 10 minutes after the 23:50 full refresh")
    assert message.bounding_box()["height"] > 60
    field = page.locator('[name="rules-1-at"]')
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
    kept in place instead, and with it the focused control below."""
    requests, message, field = close_page_with_field_at(page, component_origin, 300)
    rows = rule_rows(page)
    above, below = rows.nth(1).bounding_box(), rows.nth(2).bounding_box()
    gap = below["y"] - (above["y"] + above["height"])
    assert gap > 4
    page.mouse.move(above["x"] + above["width"] / 2, below["y"] - gap / 2)
    assert page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).matches('[data-schedule-rows]')",
        [above["x"] + above["width"] / 2, below["y"] - gap / 2],
    )
    field.fill("23:30")
    focused = page.locator('[name="rules-3-at"]')
    focused.focus()
    watched = ('[name="rules-3-at"]', "#rules-2")
    before = tops(page, *watched)
    recorded(page, requests, 1)
    has_text(message, "")
    assert within_half_a_pixel(before, tops(page, *watched))


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
    """The message under the pointer is replaced by the redraw (rule 2's
    kept-time note goes once its time is on the quarter hour): its row is
    kept in place instead, also with nothing focused."""
    requests, message, field = close_page_with_field_at(page, component_origin, 300)
    note = page.locator("#rules-1-messages > *").first
    contains(note, "Kept from the earlier schedule")
    box = note.bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + box["height"] / 2)
    field.fill("23:30")
    page.evaluate("document.activeElement.blur()")
    before = tops(page, "#rules-1")
    recorded(page, requests, 1)
    has_text(message, "")
    has_text(page.locator("#rules-1-messages"), "")
    assert within_half_a_pixel(before, tops(page, "#rules-1"))
