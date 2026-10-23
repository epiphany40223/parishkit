"""New and Edit scheduled email: Sends (Once or Repeating), no modes (#2022).

Following the Administrator's R43 and R44 decisions on the date-change
review (#1944; UX conventions, "Schedules", Once or Repeating): New shows
only Mail type until one is chosen; a Reminder then asks Sends, whose
Repeating replaces the send date with the repeat rule; Edit this
schedule's email shows once a type is chosen. Everything changes in place,
with no reload, nothing above the control just used moving, and hidden
fields disabled so they are not posted. Edit has no Sends and no toggle.
Drawn from ``schedule_list_components``; a refused Review drawn as posted
is test_schedule_entry_sends_postgresql.py's.
"""

from urllib.parse import parse_qs

import pytest

from .schedule_list_components import (
    EDIT,
    EDIT_SERIES,
    NEW,
    NEW_INITIAL_SCHEDULED,
    NEW_INITIAL_SENT,
)
from .test_components import axe_violations
from .waits import eventually, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

ADD = "Add the scheduled email"
REVIEW = "Review changes"
EDIT_EMAIL = "Edit this schedule's email"
RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
# By id: a hidden radio is not in the accessibility tree.
ONCE = "#repeat-sends_0"
REPEATING = "#repeat-sends_1"


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


def choose(page, name, value):
    """Choose ``value`` in the schedule field ``name``."""
    page.locator(f'[name="schedule-{name}"]').select_option(value)


def email_for(page, kind):
    """The value of the first email offered for ``kind``."""
    return page.locator(
        f'[name="schedule-template_version"] option[data-kind="{kind}"]'
    ).first.get_attribute("value")


def field(page, name):
    """The wrapper of one of the schedule's fields."""
    return page.locator(f'[data-schedule-field="{name}"]')


def page_top(locator):
    """The element's top edge from the top of the page, whatever the scroll."""
    return locator.evaluate("node => node.getBoundingClientRect().top + window.scrollY")


def posted(request):
    """A captured POST's fields, as lists of values."""
    return parse_qs(request.post_data or "", keep_blank_values=True)


def held(page, button, hint):
    """``button`` is unavailable, named by the complete gate's ``hint``,
    which shows after it in place of the unchanged hint (#2025)."""
    reason = page.locator("#schedule-complete-hint")
    eventually(
        page, "document.querySelector('#schedule-complete-hint').textContent", hint
    )
    assert button.is_disabled()
    assert "schedule-complete-hint" in (button.get_attribute("aria-describedby") or "")
    assert reason.evaluate("node => getComputedStyle(node).visibility") == "visible"
    hidden(page.locator("[data-unchanged-hint]"))


def released(page, button):
    """``button`` is available, with no reason shown after it."""
    eventually(
        page, "document.querySelector('#schedule-complete-hint').textContent", ""
    )
    assert button.is_enabled()


def no_mode(page):
    """The page draws no Basic and Advanced toggle."""
    assert page.get_by_role("group", name="Mode", exact=True).count() == 0
    assert page.locator('[name="editor_mode"]').count() == 0


@pytest.mark.parametrize("width", [320, 1280])
def test_new_morphs_in_place_from_the_mail_type(
    page, component_origin, axe_source, width
):
    """Mail type first; a Reminder adds Sends and the email tool, Once shows
    the send date and Repeating the rule in its place, each keeping what was
    typed; an Initial invitation has no Sends. Nothing reloads, and nothing
    above the control just used moves."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    no_mode(page)
    panel = page.locator("[data-repeat-panel]")
    rule = page.locator("[data-repeat-rule]")
    box = page.get_by_label(EDIT_EMAIL)
    once, repeating = page.locator(ONCE), page.locator(REPEATING)
    kind = page.locator('[name="schedule-kind"]')
    # Only the Mail type shows at first.
    visible(kind)
    for part in (panel, box, field(page, "date"), field(page, "time")):
        hidden(part)
    hidden(field(page, "template_version"))
    assert once.is_disabled() and repeating.is_disabled()
    assert axe_violations(page, axe_source) == []
    kind_top = page_top(kind)
    choose(page, "kind", "reminder")
    visible(panel)
    visible(page.get_by_role("group", name="Sends"))
    assert once.is_checked() and once.is_enabled()
    for name in ("date", "time", "template_version"):
        visible(field(page, name))
    visible(box)
    hidden(rule)
    assert abs(page_top(kind) - kind_top) < 1
    assert axe_violations(page, axe_source) == []
    page.locator('[name="schedule-date"]').fill("2054-10-12")
    page.locator('[name="schedule-time"]').fill("9am")
    sends_top = page_top(panel)
    repeating.check()
    visible(rule)
    hidden(field(page, "date"))
    # The pair and what is above it stay where they were.
    assert abs(page_top(panel) - sends_top) < 1
    assert abs(page_top(kind) - kind_top) < 1
    # The rule starts on the campaign's own dates.
    assert page.locator("#repeat-from").input_value() == "2054-10-01"
    assert page.locator("#repeat-until").input_value() == "2054-10-31"
    page.get_by_label("Tuesday").check()
    visible(page.locator("[data-repeat-summary]"))
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert axe_violations(page, axe_source) == []
    # Back to Once: the date typed for it is still there; the shared time too.
    once.check()
    hidden(rule)
    visible(field(page, "date"))
    assert page.locator('[name="schedule-date"]').input_value() == "2054-10-12"
    assert page.locator('[name="schedule-date"]').is_enabled()
    assert abs(page_top(panel) - sends_top) < 1
    # Repeating's rule kept its day.
    repeating.check()
    assert page.get_by_label("Tuesday").is_checked()
    # An Initial invitation is always Once: no Sends, the date shows.
    choose(page, "kind", "initial")
    hidden(panel)
    visible(field(page, "date"))
    visible(box)
    assert once.is_disabled() and repeating.is_disabled()
    assert page.locator('[name="schedule-date"]').is_enabled()
    # Reminder again: the choice was kept.
    choose(page, "kind", "reminder")
    assert repeating.is_checked()
    visible(rule)
    # No mail type: only Mail type again.
    choose(page, "kind", "")
    hidden(panel)
    hidden(box)
    assert abs(page_top(kind) - kind_top) < 1
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_hidden_fields_are_not_posted(page, component_origin):
    """Once posts its send date and Sends, never the hidden rule; an
    Initial invitation posts no Sends, whatever was chosen before; there is
    no mode field to post."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    choose(page, "kind", "reminder")
    page.locator(REPEATING).check()
    page.get_by_label("Tuesday").check()
    page.locator(ONCE).check()
    page.locator('[name="schedule-date"]').fill("2054-10-15")
    page.locator('[name="schedule-time"]').fill("9am")
    choose(page, "template_version", email_for(page, "reminder"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=ADD).click()
    fields = posted(sent.value)
    assert fields["repeat-sends"] == ["once"]
    assert fields["schedule-date"] == ["2054-10-15"]
    assert not [
        name for name in fields if name.startswith("repeat-") and name != "repeat-sends"
    ]
    assert "editor_mode" not in fields and "edit_email" not in fields
    page.goto(component_origin + NEW)
    choose(page, "kind", "reminder")
    page.locator(REPEATING).check()
    choose(page, "kind", "initial")
    page.locator('[name="schedule-date"]').fill("2054-10-02")
    page.locator('[name="schedule-time"]').fill("9am")
    choose(page, "template_version", email_for(page, "initial"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=ADD).click()
    fields = posted(sent.value)
    assert fields["schedule-kind"] == ["initial"]
    assert not [name for name in fields if name.startswith("repeat-")]
    assert not failures


def test_a_restored_page_is_worked_out_again(page, component_origin):
    """Values the browser restores without events (Back) redraw the page on
    pageshow: a restored Reminder sent Repeating shows its rule in place of
    the date; a restored empty type hides everything but Mail type."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    page.evaluate(
        """() => {
            document.querySelector('[name="schedule-kind"]').value = "reminder";
            document.querySelector('#repeat-sends_1').checked = true;
        }"""
    )
    page.evaluate(RESTORE)
    visible(page.locator("[data-repeat-rule]"))
    hidden(field(page, "date"))
    visible(page.get_by_label(EDIT_EMAIL))
    assert page.locator(REPEATING).is_enabled()
    page.evaluate("""document.querySelector('[name="schedule-kind"]').value = "" """)
    page.evaluate(RESTORE)
    hidden(page.locator("[data-repeat-panel]"))
    hidden(page.get_by_label(EDIT_EMAIL))
    assert page.locator(REPEATING).is_disabled()
    assert not failures


def test_edit_has_no_toggle_and_no_sends(page, component_origin, axe_source):
    """Edit of a reminder shows its fields and the email tool, a series'
    reminder its scope too."""
    page.set_viewport_size({"width": 1280, "height": 900})
    for path in (EDIT, EDIT_SERIES):
        page.goto(component_origin + path)
        failures = watch(page)
        no_mode(page)
        assert page.locator('[name="repeat-sends"]').count() == 0
        visible(page.get_by_label(EDIT_EMAIL))
        assert axe_violations(page, axe_source) == []
        assert not failures
    visible(page.get_by_role("radio", name="This and following"))


@pytest.mark.parametrize(
    ("zone", "weekday", "until", "clock", "kept", "left_out"),
    [
        # Saturdays at 10:00 PM in Los Angeles: October 31's is 1:00 AM on
        # November 1 in New York, after the campaign ends, so it is left out.
        (
            "America/Los_Angeles",
            "Saturday",
            "2054-10-31",
            "10pm",
            ["2054-10-03", "2054-10-10", "2054-10-17", "2054-10-24"],
            "Saturday, October 31, 2054",
        ),
        # Sundays at 2:00 AM in London: November 1's is 9:00 PM on October
        # 31 in New York, the campaign's last day, so it is kept.
        (
            "Europe/London",
            "Sunday",
            "2054-11-01",
            "2am",
            ["2054-10-04", "2054-10-11", "2054-10-18", "2054-10-25", "2054-11-01"],
            None,
        ),
    ],
)
def test_repeating_judges_each_date_in_the_campaigns_zone(
    browser_engine, component_origin, zone, weekday, until, clock, kept, left_out
):
    """Repeating's dates are judged at the send time in the campaign's zone
    (New York), as the server judges them, whatever the browser's zone, as
    on the date-change review (#1944): a date the browser puts on the
    campaign's last day but New York puts after it is listed as not added,
    and one the browser puts after the last day but New York puts on it is
    posted."""
    context = browser_engine.new_context(timezone_id=zone, reduced_motion="reduce")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(component_origin + NEW)
        failures = watch(page)
        choose(page, "kind", "reminder")
        page.locator(REPEATING).check()
        page.get_by_label(weekday, exact=True).check()
        page.locator("#repeat-until").fill(until)
        page.locator('[name="schedule-time"]').fill(clock)
        page.locator('[name="schedule-time"]').blur()
        choose(page, "template_version", email_for(page, "reminder"))
        summary = page.locator("[data-repeat-summary]")
        dates = page.locator("[data-repeat-dates]")
        if left_out:
            has_text(summary, f"{len(kept)} reminders to add, 1 not added.")
            assert (
                f"{left_out}: not added, outside the campaign dates"
                in dates.inner_text()
            )
        else:
            has_text(summary, f"{len(kept)} reminders to add.")
            assert "not added" not in dates.inner_text()
        eventually(
            page,
            "document.querySelectorAll('input[name=\"repeat-dates\"]').length",
            len(kept),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name=ADD).click()
        fields = posted(sent.value)
        assert fields["repeat-dates"] == kept
        assert fields["repeat-sends"] == ["repeat"]
        assert not failures
    finally:
        context.close()


INITIAL_SCHEDULED_HINT = (
    "This campaign already has an Initial invitation. Change it from Dates "
    "and mail schedules instead."
)
INITIAL_SENT_HINT = (
    "This campaign's Initial invitation has already been sent or is being "
    "prepared, so it can't have another."
)


@pytest.mark.parametrize(
    ("path", "words"),
    [
        (NEW_INITIAL_SCHEDULED, INITIAL_SCHEDULED_HINT),
        (NEW_INITIAL_SENT, INITIAL_SENT_HINT),
    ],
)
def test_a_second_initial_invitation_waits_with_why(
    page, component_origin, path, words
):
    """While the campaign already has its Initial invitation (to send, or
    sent), choosing Initial invitation holds Add the scheduled email at once,
    with the server's words after it, however complete the rest is (#2025;
    UX conventions, "Prerequisite gates"). A Reminder is offered as usual."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + path)
    failures = watch(page)
    add = page.get_by_role("button", name=ADD)
    choose(page, "kind", "initial")
    held(page, add, words)
    page.locator('[name="schedule-date"]').fill("2054-10-12")
    page.locator('[name="schedule-time"]').fill("9am")
    choose(page, "template_version", email_for(page, "initial"))
    held(page, add, words)
    # A Reminder needs its own email; once chosen, Add is available.
    choose(page, "kind", "reminder")
    held(page, add, "Choose the email to send.")
    choose(page, "template_version", email_for(page, "reminder"))
    released(page, add)
    # Initial invitation again waits again, and a restored page (Back, no
    # events) is worked out again the same way.
    choose(page, "kind", "initial")
    held(page, add, words)
    page.evaluate(
        """document.querySelector('[name="schedule-kind"]').value = "reminder" """
    )
    page.evaluate(RESTORE)
    held(page, add, "Choose the email to send.")
    page.evaluate(
        """document.querySelector('[name="schedule-kind"]').value = "initial" """
    )
    page.evaluate(RESTORE)
    held(page, add, words)
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_an_initial_invitation_is_offered_when_the_campaign_has_none(
    page, component_origin
):
    """Without data-initial-taken (the view gives none while the campaign has
    no Initial invitation), a complete Initial invitation can be added."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    add = page.get_by_role("button", name=ADD)
    choose(page, "kind", "initial")
    held(page, add, "Enter the send date.")
    page.locator('[name="schedule-date"]').fill("2054-10-02")
    page.locator('[name="schedule-time"]').fill("9am")
    choose(page, "template_version", email_for(page, "initial"))
    released(page, add)
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_a_reminder_waits_for_every_field_once_or_repeating(
    page, component_origin, width
):
    """A Reminder's Add waits for a whole schedule, its hint naming the first
    field missing, in the unchanged hint's place (#2025): Once, the send
    date, a readable time and an email; Repeating, the time and the email,
    then a rule that gives a date. Emptying a field holds it again. The
    hint changing never moves the button."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    add = page.get_by_role("button", name=ADD)
    date = page.locator('[name="schedule-date"]')
    time = page.locator('[name="schedule-time"]')
    # The blank page says only the unchanged hint.
    visible(page.locator("[data-unchanged-hint]"))
    assert add.is_disabled()
    choose(page, "kind", "reminder")
    held(page, add, "Enter the send date.")
    date.fill("2054-10-12")
    held(page, add, "Enter a send time that can be read, for example 9:00 AM.")
    time.fill("9am")
    time.blur()
    held(page, add, "Choose the email to send.")
    choose(page, "template_version", email_for(page, "reminder"))
    released(page, add)
    # The hint coming and going moves nothing (the fields above it are
    # disclosed by the choices, so only a change of hint alone is measured).
    top = page_top(add)
    time.fill("")
    held(page, add, "Enter a send time that can be read, for example 9:00 AM.")
    assert abs(page_top(add) - top) < 1
    time.fill("9am")
    time.blur()
    released(page, add)
    assert abs(page_top(add) - top) < 1
    # A date outside the campaign says the window check's own words, once
    # it shows them; a time typed back inside releases Add with no blur.
    date.fill("2054-12-01")
    date.blur()
    eventually(
        page,
        "document.querySelector('#schedule-complete-hint').textContent"
        ".startsWith('Choose a date within the campaign')",
        True,
    )
    assert add.is_disabled()
    date.fill("2054-10-12")
    released(page, add)
    # Repeating: the send date no longer counts; the rule must give a date.
    page.locator(REPEATING).check()
    assert add.is_disabled()
    page.get_by_label("Tuesday").check()
    released(page, add)
    choose(page, "template_version", "")
    held(page, add, "Choose the email to send.")
    choose(page, "template_version", email_for(page, "reminder"))
    time.fill("")
    held(page, add, "Enter a send time that can be read, for example 9:00 AM.")
    time.fill("9am")
    time.blur()
    released(page, add)
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_edit_waits_for_a_whole_schedule_too(page, component_origin):
    """Edit's Review changes keeps the unchanged hint until an edit, then
    waits while a field it needs is emptied (#2025)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT)
    failures = watch(page)
    review = page.get_by_role("button", name=REVIEW)
    visible(page.locator("[data-unchanged-hint]"))
    assert review.is_disabled()
    time = page.locator('[name="schedule-time"]')
    time.fill("")
    held(page, review, "Enter a send time that can be read, for example 9:00 AM.")
    time.fill("11am")
    time.blur()
    released(page, review)
    page.locator('[name="schedule-date"]').fill("")
    held(page, review, "Enter the send date.")
    assert not failures
