"""The date-change review's New schedule pane: Once or Repeating (#1944).

One pane adds one schedule at a time: Mail type first, then for a Reminder
Sends (Once | Repeating), which morphs the pane in place. Review changes
covers the pane's one schedule with the proposed dates; after Apply the
page is redrawn in place, the table showing the new reminders and the pane
empty again for the next. See the admin-portal spec, "New schedule on the
date-change review", and the UX conventions' Schedules rules Once or
Repeating and one schedule at a time. test_schedule_fields.py covers the
rule's dates and the posted rows; this file covers the pane's disclosure,
a refused Review, Apply in place and the browser's history.
"""

import re
from urllib.parse import parse_qs

import pytest

from .schedule_list_components import (
    DATES_ADDED,
    DATES_END,
    DATES_INITIAL,
    DATES_PANE,
    DATES_PANE_FRESH,
    DATES_PANE_REFUSED,
    LIST,
    PANE_DAYS,
)
from .test_components import axe_violations
from .waits import available, eventually, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The pane follows the one saved initial invitation.
PANE = "[data-schedule-pane]"
NEW = "schedules-1-"
RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
NOTICE = "The proposed dates below have not been saved."


def review_post(response):
    """Whether a response answers the pane's Review post, not the repeat
    date list's own preview post (#2092), which can land first."""
    request = response.request
    return request.method == "POST" and "/repeat-dates/" not in request.url


def apply_post(request):
    """Whether a request is a dialog's Apply post, not the repeat date
    list's own preview post (#2092): that one is sent a moment after the
    rule's last change, so it can still be sent after the dialog opens,
    including when Apply is clicked (#2243)."""
    return request.method == "POST" and "/repeat-dates/" not in request.url


def page_top(locator):
    """The element's top edge from the top of the page, whatever the scroll."""
    return locator.evaluate("node => node.getBoundingClientRect().top + window.scrollY")


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


def field(page, name, prefix=NEW):
    """The wrapper of one of the pane's row fields."""
    return page.locator(f'[data-schedule-field="{name}"]:has([name="{prefix}{name}"])')


def pane_prefix(page):
    """The pane's field-name prefix: its row follows the saved rows."""
    name = page.locator(PANE).locator('[name$="-kind"]').get_attribute("name")
    return name[: -len("kind")]


def fill_once(page, prefix, day):
    """Fill the pane with one Once reminder at 9:00 AM on ``day``."""
    page.locator(f'[name="{prefix}kind"]').select_option("reminder")
    page.locator(f'[name="{prefix}date"]').fill(day)
    page.locator(f'[name="{prefix}time"]').fill("9am")
    page.locator(f'[name="{prefix}template_version"]').select_option(index=1)


@pytest.mark.parametrize("width", [320, 1280])
def test_sends_morphs_the_pane_and_keeps_each_kinds_values(
    page, component_origin, axe_source, width
):
    """Mail type first; a Reminder adds Sends, Once shows the send date and
    Repeating the rule in its place, each keeping what was typed; an Initial
    invitation is always Once. Nothing reloads, and nothing above the
    control just used moves."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/schedule-editor-mail")
    failures = watch(page)
    pane = page.locator(PANE)
    sends = pane.locator("[data-schedule-sends]")
    rule = pane.locator("[data-repeat-panel]")
    # By id: a hidden radio is not in the accessibility tree.
    once = pane.locator("#schedule-sends_0")
    repeating = pane.locator("#schedule-sends_1")
    kind = page.locator(f'[name="{NEW}kind"]')
    review = page.get_by_role("button", name="Review changes")
    # Only the Mail type shows at first, and Sends posts nothing.
    for part in (sends, rule, pane.locator("[data-schedule-pane-line]")):
        hidden(part)
    assert once.is_disabled() and repeating.is_disabled()
    assert pane.locator("legend").first.inner_text() == "New schedule"
    kind_top = page_top(kind)
    kind.select_option("reminder")
    visible(sends)
    assert once.is_checked() and once.is_enabled()
    visible(field(page, "date"))
    hidden(rule)
    # Review waits while the pane holds part of a schedule, and says why.
    assert review.is_disabled() and review.get_attribute("data-pane-gated") == ""
    visible(pane.get_by_text("Review changes includes this schedule once it is"))
    assert axe_violations(page, axe_source) == []
    page.locator(f'[name="{NEW}date"]').fill("2054-10-12")
    page.locator(f'[name="{NEW}time"]').fill("9am")
    sends_top = page_top(sends)
    repeating.check()
    visible(rule)
    hidden(field(page, "date"))
    # The pair and what is above it stay where they were.
    assert abs(page_top(sends) - sends_top) < 1
    assert abs(page_top(kind) - kind_top) < 1
    rule.locator('[data-repeat="weekday"][value="2"]').check()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # Back to Once: the date typed for it is still there; the shared time too.
    once.check()
    hidden(rule)
    visible(field(page, "date"))
    assert page.locator(f'[name="{NEW}date"]').input_value() == "2054-10-12"
    assert page.locator(f'[name="{NEW}date"]').is_enabled()
    assert page.locator(f'[name="{NEW}time"]').input_value() == "09:00"
    # And Repeating's rule kept its day.
    repeating.check()
    assert rule.locator('[data-repeat="weekday"][value="2"]').is_checked()
    # An Initial invitation is always Once: no Sends, the date shows.
    kind.select_option("initial")
    hidden(sends)
    hidden(rule)
    visible(field(page, "date"))
    assert once.is_disabled() and repeating.is_disabled()
    assert axe_violations(page, axe_source) == []
    # No mail type: the pane is its Mail type again and Review is free of it.
    kind.select_option("")
    hidden(pane.locator("[data-schedule-pane-line]"))
    assert review.get_attribute("data-pane-gated") is None
    assert page.evaluate("window.notReloaded === true")
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_a_refused_review_keeps_the_pane_and_marks_its_field(
    page, component_origin, axe_source, width
):
    """The pane's Once reminder, refused by the server at its date (after the
    campaign ends): no dialog and no reload; the pane keeps its schedule, its
    date marked."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + DATES_ADDED)
    failures = watch(page)
    prefix = pane_prefix(page)
    # Inside the dates for the page; the server refuses its own November 15.
    fill_once(page, prefix, "2054-10-15")
    review = page.get_by_role("button", name="Review changes")
    available(review)
    with page.expect_response(review_post) as answer:
        review.click()
    assert answer.value.status == 400
    visible(page.locator("[data-error-summary]"))
    assert not page.locator("dialog[data-review-dialog][open]").count()
    date = page.locator(f'[name="{prefix}date"]')
    visible(date)
    assert date.get_attribute("aria-invalid") == "true"
    assert page.locator(f'[name="{prefix}kind"]').input_value() == "reminder"
    assert date.input_value() == "2054-10-15"
    assert page.evaluate("window.notReloaded === true")
    assert axe_violations(page, axe_source) == []
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_a_refused_repeating_review_shows_at_until(
    page, component_origin, axe_source, width
):
    """The server refuses the second reminder a Repeating pane posted, a
    row the page never draws: its words show at the rule's Until, led by
    the date it refused, Until is marked, and the error summary's link
    moves focus there. The pane's hidden send date keeps no mark, and the
    first edit of Until clears the mark and the summary."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + DATES_PANE_REFUSED)
    failures = watch(page)
    prefix = pane_prefix(page)
    pane = page.locator(PANE)
    page.locator(f'[name="{prefix}kind"]').select_option("reminder")
    pane.get_by_role("radio", name="Repeating").check()
    rule = pane.locator("[data-repeat-panel]")
    rule.locator('[data-repeat="weekday"][value="0"]').check()
    rule.locator('[data-repeat="from"]').fill("2054-10-06")
    page.locator(f'[name="{prefix}time"]').fill("9am")
    page.locator(f'[name="{prefix}template_version"]').select_option(index=1)
    review = page.get_by_role("button", name="Review changes")
    available(review)
    with page.expect_response(review_post) as answer:
        review.click()
    assert answer.value.status == 400
    summary = page.locator("[data-error-summary]")
    visible(summary)
    assert not page.locator("dialog[data-review-dialog][open]").count()
    until = rule.locator('[data-repeat="until"]')
    line = page.locator("#repeat-until_error")
    visible(line)
    assert line.inner_text().startswith("Monday, October 19, 2054: ")
    assert until.get_attribute("aria-invalid") == "true"
    assert "repeat-until_error" in until.get_attribute("aria-describedby")
    # The pane's own send date is hidden in Repeating and stays unmarked.
    assert page.locator(f'[name="{prefix}date"]').get_attribute("aria-invalid") != (
        "true"
    )
    link = summary.locator('a[href="#repeat-until"]')
    assert link.count() == 1
    link.click()
    assert page.evaluate("document.activeElement.id") == "repeat-until"
    assert axe_violations(page, axe_source) == []
    until.fill("2054-10-20")
    hidden(line)
    assert until.get_attribute("aria-invalid") == "false"
    assert not summary.locator('a[href="#repeat-until"]').count()
    assert page.evaluate("window.notReloaded === true")
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_a_second_initial_invitation_waits_and_its_refusal_stays_put(
    page, component_origin, axe_source, width
):
    """R45: with an Initial invitation already in the table, the pane's
    Initial invitation holds Review changes, named by its reason. Should
    the server still be asked (its one_initial rule is the safeguard), its
    refusal is shown in place: no reload, the summary says the message as
    text, no unrelated field is marked, and choosing another Mail type
    clears it; Sends then shows unmarked."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + DATES_INITIAL)
    failures = watch(page)
    prefix = pane_prefix(page)
    pane = page.locator(PANE)
    kind = page.locator(f'[name="{prefix}kind"]')
    kind.select_option("initial")
    page.locator(f'[name="{prefix}date"]').fill("2054-10-15")
    page.locator(f'[name="{prefix}time"]').fill("9am")
    page.locator(f'[name="{prefix}template_version"]').select_option(index=1)
    review = page.get_by_role("button", name="Review changes")
    taken = (
        "An Initial invitation is already scheduled. "
        "Change or delete it from the table instead."
    )
    has_text(pane.locator("[data-repeat-summary]"), taken)
    assert review.is_disabled() and review.get_attribute("data-pane-gated") == ""
    hint = review.get_attribute("aria-describedby").split()
    assert taken in [
        page.locator(f"#{name}").text_content().strip()
        for name in hint
        if page.locator(f"#{name}").count()
    ]
    assert axe_violations(page, axe_source) == []
    # The server's safeguard: the form is sent anyway, as Enter would once.
    with page.expect_response(review_post) as answer:
        page.evaluate("document.getElementById('schedule-dates-form').requestSubmit()")
    assert answer.value.status == 400
    summary = page.locator("[data-error-summary]")
    visible(summary)
    message = "Only one Initial invitation is allowed. Delete the extra one."
    assert summary.locator("li").all_inner_texts() == [message]
    assert "{'message'" not in page.locator("main").inner_text()
    # Said where the reader is: under Review changes, with focus, so the
    # page does not jump to its top (data-review-summary-after). Focus
    # scrolls it just into view, so its bottom may sit a fraction of a
    # pixel past the edge (WebKit rounds that way at 1280).
    eventually(page, "document.activeElement?.matches('[data-error-summary]')")
    assert page.evaluate(
        "(() => { const box = document.querySelector('[data-error-summary]')"
        ".getBoundingClientRect();"
        " return box.top >= 0 && box.bottom <= innerHeight + 1; })()"
    )
    assert page.evaluate("window.scrollY > 0")
    assert not page.locator("dialog[data-review-dialog][open]").count()
    assert page.evaluate("window.notReloaded === true")
    assert kind.input_value() == "initial"
    # No field of the pane is marked: the rule has no one field.
    assert not pane.locator('[aria-invalid="true"]').count()
    # Another Mail type is another schedule: the refusal goes.
    kind.select_option("reminder")
    hidden(summary)
    sends = pane.locator("[data-schedule-sends]")
    visible(sends)
    assert not sends.locator('[aria-invalid="true"]').count()
    hidden(page.locator("#schedule-sends_error"))
    assert "'field_id'" not in pane.inner_text()
    assert axe_violations(page, axe_source) == []
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_restored_pane_is_worked_out_again(page, component_origin):
    """Values the browser restores without events (Back) redraw the pane on
    pageshow: a restored Reminder sent Repeating shows its rule in place of
    the date and holds Review; a restored empty type hides them again."""
    page.goto(component_origin + "/schedule-editor-mail")
    failures = watch(page)
    pane = page.locator(PANE)
    page.evaluate(
        """() => {
            document.querySelector('[name="schedules-1-kind"]').value = "reminder";
            document.querySelector('#schedule-sends_1').checked = true;
        }"""
    )
    page.evaluate(RESTORE)
    visible(pane.locator("[data-repeat-panel]"))
    hidden(field(page, "date"))
    review = page.get_by_role("button", name="Review changes")
    assert review.get_attribute("data-pane-gated") == ""
    page.evaluate("""document.querySelector('[name="schedules-1-kind"]').value = "" """)
    page.evaluate(RESTORE)
    hidden(pane.locator("[data-repeat-panel]"))
    hidden(pane.locator("[data-schedule-sends]"))
    assert review.get_attribute("data-pane-gated") is None
    assert not failures


def test_back_and_forward_keep_the_pane_in_step(page, component_origin):
    """Leaving the page and coming back shows the pane as its values are,
    whether the browser keeps the page or draws it again: Sends shows only
    for a Reminder, and the posted row count is the saved rows and the pane."""
    page.goto(component_origin + "/schedule-editor-mail")
    total = page.locator('[name="schedules-TOTAL_FORMS"]')
    first = int(total.input_value())
    page.locator(f'[name="{NEW}kind"]').select_option("reminder")
    page.locator(PANE).get_by_role("radio", name="Repeating").check()
    for _ in range(2):
        page.goto(component_origin + "/schedule-settings")
        page.go_back()
        page.wait_for_load_state()
        eventually(page, "document.querySelector('[data-schedule-pane]') !== null")
        assert re.search(r"/schedule-editor-mail$", page.url)
        assert total.input_value() == str(first)
        kind = page.locator(f'[name="{NEW}kind"]').input_value()
        sends = page.locator(PANE).locator("[data-schedule-sends]")
        if kind == "reminder":
            visible(sends)
        else:
            hidden(sends)
        page.go_forward()


def answer_fresh(page, component_origin):
    """Answer the page's own address, read again after Apply, with the page
    as it is once the change is saved (``DATES_PANE_FRESH``); record each
    Review's posted fields."""
    posts = []

    def handle(route):
        """Serve the saved page for a read; record a Review, then pass it on."""
        if route.request.method == "GET":
            route.fulfill(response=route.fetch(url=component_origin + DATES_PANE_FRESH))
        else:
            route.continue_()

    def record(request):
        """Keep each Review's fields."""
        if request.method == "POST" and request.url.endswith("/review"):
            posts.append(parse_qs(request.post_data or "", keep_blank_values=True))

    page.route(component_origin + DATES_PANE, handle)
    page.on("request", record)
    return posts


@pytest.mark.parametrize("width", [320, 1280])
def test_apply_updates_the_page_in_place_and_empties_the_pane(
    page, component_origin, axe_source, width
):
    """A Repeating schedule with the proposed end: Review posts one reminder
    row per date and the dialog lists each; Apply redraws the page in place
    (no reload, no jump): the table lists the new reminders, the notice goes
    and the pane is just its Mail type again. A second schedule is reviewed
    and applied right after, without a reload."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + DATES_PANE)
    failures = watch(page)
    posts = answer_fresh(page, component_origin)
    visible(page.get_by_text(NOTICE))
    rows = page.locator("tr[data-schedule-choose], [data-schedule-table] tbody tr")
    before = rows.count()
    prefix = pane_prefix(page)
    index = int(prefix.split("-")[1])
    pane = page.locator(PANE)
    page.locator(f'[name="{prefix}kind"]').select_option("reminder")
    pane.get_by_role("radio", name="Repeating").check()
    rule = pane.locator("[data-repeat-panel]")
    rule.locator('[data-repeat="weekday"][value="0"]').check()
    rule.locator('[data-repeat="from"]').fill("2054-10-06")
    page.locator(f'[name="{prefix}time"]').fill("9am")
    page.locator(f'[name="{prefix}template_version"]').select_option(index=1)
    review = page.get_by_role("button", name="Review changes")
    available(review)
    review.click()
    dialog = page.get_by_role("dialog", name="Review dates and mail schedules")
    visible(dialog)
    fields = {name: values[0] for name, values in posts[-1].items()}
    assert fields["schedules-TOTAL_FORMS"] == str(index + len(PANE_DAYS))
    assert [
        fields[f"schedules-{index + offset}-date"] for offset in range(len(PANE_DAYS))
    ] == list(PANE_DAYS)
    assert {fields[f"schedules-{index + at}-kind"] for at in (0, 1)} == {"reminder"}
    assert fields["window-end_date"] == DATES_END
    assert "schedule_sends" not in fields
    assert not any(name.startswith("repeat") for name in fields)
    # A short confirmation of what is added (R45): no schedule-by-schedule
    # list, the proposed end date on a line of its own.
    text = dialog.inner_text()
    assert "Add 2 reminders:" in text
    assert "October 12, 2054" in text and "October 19, 2054" in text
    assert "Campaign end date:" in text
    assert "Current schedule" not in text and "None (not scheduled)" not in text
    assert axe_violations(page, axe_source) == []
    with page.expect_request(apply_post) as sent:
        dialog.get_by_role("button", name="Apply").click()
    assert sent.value.url.endswith(LIST)
    line = page.locator("#schedule-dates-outcome")
    eventually(
        page, "document.getElementById('schedule-dates-outcome').textContent !== ''"
    )
    assert line.inner_text().startswith("Dates and mail schedules changed. ")
    hidden(dialog)
    # Redrawn in place: same address, no reload, and the reader stays at
    # the status line under Review changes rather than the top of the page.
    assert page.url.endswith(DATES_PANE)
    assert page.evaluate("window.notReloaded === true")
    assert page.evaluate("document.activeElement.id") == "schedule-dates-outcome"
    assert page.evaluate(
        "(() => { const box = document.getElementById('schedule-dates-outcome')"
        ".getBoundingClientRect();"
        " return box.top >= 0 && box.bottom <= innerHeight; })()"
    )
    hidden(page.get_by_text(NOTICE))
    assert rows.count() == before + len(PANE_DAYS)
    assert page.locator('[name="window-end_date"]').input_value() == DATES_END
    # The pane is just its Mail type again, at the next index.
    fresh = pane_prefix(page)
    assert fresh == f"schedules-{index + len(PANE_DAYS)}-"
    assert page.locator(f'[name="{fresh}kind"]').input_value() == ""
    hidden(pane.locator("[data-schedule-sends]"))
    hidden(pane.locator("[data-repeat-panel]"))
    hidden(pane.locator("[data-schedule-pane-line]"))
    assert review.is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # A second schedule right after, with no reload: the fresh pane works.
    kind_top = page_top(page.locator(f'[name="{fresh}kind"]'))
    fill_once(page, fresh, "2054-10-14")
    assert abs(page_top(page.locator(f'[name="{fresh}kind"]')) - kind_top) < 1
    available(review)
    review.click()
    visible(dialog)
    fields = {name: values[0] for name, values in posts[-1].items()}
    assert fields[f"{fresh}kind"] == "reminder"
    assert fields[f"{fresh}date"] == "2054-10-14"
    assert fields["schedules-TOTAL_FORMS"] == str(index + len(PANE_DAYS) + 1)
    assert fields["base_digest"] == "b" * 64
    page.evaluate("document.getElementById('schedule-dates-outcome').textContent = ''")
    dialog.get_by_role("button", name="Apply").click()
    eventually(
        page, "document.getElementById('schedule-dates-outcome').textContent !== ''"
    )
    hidden(dialog)
    assert page.locator(f'[name="{fresh}kind"]').input_value() == ""
    assert page.evaluate("window.notReloaded === true")
    assert not failures
