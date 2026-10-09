"""Page state follows values restored from history (#563).

A browser can bring a page back from its back/forward cache, or restore form
values after load, without the change events the page scripts listen for.
Each test sets a value the way a restore does (no event), sends the
``pageshow`` a restored page receives, and checks that the page agrees with
the value: bulk actions and their hidden hint, campaign modules, the overlap
confirmation and mail schedule rows.
"""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"


def restore(page, selector, script):
    """Change a control without events, as a restore does, then show the page."""
    page.locator(selector).first.evaluate(script)
    page.evaluate(RESTORE)


def takes_no_space(note):
    """Assert the hint is visually hidden (a 1px clip), not shown as a line.

    Playwright counts a ``.visually-hidden`` element as visible, so this
    checks its box instead.
    """
    box = note.bounding_box()
    assert box and box["width"] <= 1 and box["height"] <= 1, box


def page_y(locator):
    """The element's top in page (not viewport) coordinates, whole pixels.

    Firefox can report the same unmoved box a few hundred-thousandths of a
    pixel apart, so positions are compared to the pixel, which still shows
    a line appearing or disappearing.
    """
    return round(
        locator.evaluate("e => e.getBoundingClientRect().top + window.scrollY")
    )


def page_x(locator):
    """The element's left edge in page (not viewport) coordinates, whole pixels."""
    return round(
        locator.evaluate("e => e.getBoundingClientRect().left + window.scrollX")
    )


def explained(action, hint):
    """Whether the disabled action is described by the hidden hint."""
    hint_id = action.page.locator("[data-selection-hint]").first.get_attribute("id")
    return hint_id in (action.get_attribute("aria-describedby") or "").split() and (
        action.get_attribute("title") == hint
    )


@pytest.mark.parametrize(
    "path,button,hint",
    [
        ("/ministries", "Review activation", "Select at least one Ministry to review."),
        ("/hosted-files", "Delete selected", "Select at least one file to delete."),
    ],
)
def test_bulk_actions_say_what_to_select_and_follow_restored_ticks(
    page, component_origin, path, button, hint
):
    """The hidden hint names the missing selection; restored ticks enable it.

    The hint never takes space (the Administrator found a line that came and
    went jarring), so the action button stays put as rows are ticked.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    action = page.get_by_role("button", name=button)
    note = page.locator("[data-selection-hint]")
    assert note.text_content() == hint
    takes_no_space(note)
    assert action.is_disabled()
    assert explained(action, hint)
    box = page.locator("input[data-select-row]").first
    place = page_y(box)
    spot = (page_x(action), page_y(action))
    # Choosing a row drops the description, and neither the box just clicked
    # nor the bulk button moves (no line appears or disappears, and the
    # count's "1 selected" sits after the buttons).
    box.check()
    assert action.is_enabled()
    assert not explained(action, hint)
    assert action.get_attribute("title") is None
    assert page_y(box) == place
    assert (page_x(action), page_y(action)) == spot
    # Ticking every row turns Select all into Clear selection: still no move.
    page.get_by_role("button", name="Select all").click()
    assert (page_x(action), page_y(action)) == spot
    page.get_by_role("button", name="Clear selection").click()
    assert explained(action, hint)
    assert page_y(box) == place
    assert (page_x(action), page_y(action)) == spot
    takes_no_space(note)
    # A tick restored from history, without a change event, counts.
    restore(page, "input[data-select-row]", "box => { box.checked = true; }")
    assert action.is_enabled()
    assert not explained(action, hint)
    visible(page.get_by_text("1 selected"))
    # And a restored empty selection disables the action again.
    restore(page, "input[data-select-row]", "box => { box.checked = false; }")
    assert action.is_disabled()
    assert explained(action, hint)
    takes_no_space(note)
    assert not failures


def test_campaign_modules_follow_a_restored_tick(page, component_origin):
    """A restored Financial tick shows its fields, enabled and sent."""
    page.goto(component_origin + "/campaign-settings")
    financial = page.get_by_role("group", name="Financial periods and funds")
    assert not financial.is_visible()
    restore(page, "#id_financial_enabled", "box => { box.checked = true; }")
    visible(financial)
    start = page.get_by_label("Upcoming financial period start")
    assert start.is_enabled()
    start.fill("2027-01-01")
    posted = page.locator("[data-campaign-form]").evaluate(
        "form => Array.from(new FormData(form).keys())"
    )
    assert "financial_start" in posted
    # Unticked again by a restore: hidden and not sent.
    restore(page, "#id_financial_enabled", "box => { box.checked = false; }")
    assert not financial.is_visible()
    posted = page.locator("[data-campaign-form]").evaluate(
        "form => Array.from(new FormData(form).keys())"
    )
    assert "financial_start" not in posted


def test_overlap_confirmation_follows_restored_dates(page, component_origin):
    """A restored campaign end that overlaps the financial period asks again."""
    page.goto(component_origin + "/setup-schedules-financial")
    group = page.locator("[data-overlap-confirmation]")
    assert group.is_hidden()
    end = '[name="window-end_date"]'
    restore(page, end, "field => { field.value = '2055-01-05'; }")
    visible(group)
    restore(page, end, "field => { field.value = '2054-12-31'; }")
    assert group.is_hidden()


def test_schedule_rows_follow_a_restored_mail_type(page, component_origin):
    """A restored mail type shows that type's fields and only its emails."""
    page.goto(component_origin + "/setup-schedules-mail")
    row = '[name="schedules-1-kind"]'
    weekday = page.locator(
        '[data-schedule-field="weekday"]:has([name="schedules-1-weekday"])'
    )
    date = page.locator('[data-schedule-field="date"]:has([name="schedules-1-date"])')
    assert not weekday.is_visible() and not date.is_visible()
    restore(page, row, "select => { select.value = 'weekly_digest'; }")
    visible(weekday)
    assert not date.is_visible()
    offered = page.locator('[name="schedules-1-template_version"] option').evaluate_all(
        "options => options.map(option => option.dataset.kind || '')"
    )
    assert offered == ["", "weekly_digest"]
    # Back to the type the server drew: the row is blank again.
    restore(page, row, "select => { select.value = ''; }")
    assert not weekday.is_visible()


def test_restored_campaign_modules_update_a_save_gate(page, component_origin):
    """A module shown or hidden by a restore updates the complete gate, so a
    form that adopts data-require-complete never keeps Save stuck."""
    page.goto(component_origin + "/campaign-settings")
    # Opt the form into the gate as a later change would: the financial
    # start becomes required while its module shows.
    page.locator("[data-campaign-form]").evaluate(
        """form => {
            form.setAttribute("data-require-complete", "");
            form.querySelector("[name=financial_start]").required = true;
        }"""
    )
    save = page.get_by_role("button", name="Review changes")
    restore(page, "#id_financial_enabled", "box => { box.checked = true; }")
    assert save.is_disabled()
    restore(page, "#id_financial_enabled", "box => { box.checked = false; }")
    assert save.is_enabled()


def test_chairperson_suggestions_say_what_to_select(page, component_origin):
    """The suggestion table's Review waits for a selection, with its hint."""
    page.goto(component_origin + "/portal-users-suggestions")
    review = page.get_by_role("button", name="Review selected suggestions")
    hint = "Select at least one suggestion to review."
    takes_no_space(page.locator("[data-selection-hint]"))
    assert review.is_disabled()
    assert explained(review, hint)
    restore(page, "input[data-select-row]", "box => { box.checked = true; }")
    assert review.is_enabled()
    assert not explained(review, hint)
