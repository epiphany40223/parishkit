"""New and Edit scheduled email's Basic and Advanced modes (#1129).

The page is an additive editor (UX conventions, "Basic and Advanced
modes"): both modes show the schedule's fields, and Advanced adds New's
Repeat and "Edit this schedule's email". Basic is the page with both off,
so Advanced → Basic switches at once while they are off and otherwise asks;
Switch to Basic turns them off. Drawn from ``schedule_list_components``
(``NEW_BASIC``, ``EDIT_BASIC`` and ``EDIT_SERIES_BASIC``), as the view
draws a page for a GET.
"""

from urllib.parse import parse_qs

import pytest

from .schedule_list_components import EDIT_BASIC, EDIT_SERIES_BASIC, NEW_BASIC
from .test_components import axe_violations
from .waits import focused, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

ADD = "Add the scheduled email"
REVIEW = "Review changes"
NO_BASIC = "Your custom schedule has no basic equivalent."
EDIT_EMAIL = "Edit this schedule's email"


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


def toggle(page, mode):
    """The Mode toggle's radio for ``mode`` ("Basic" or "Advanced")."""
    return page.get_by_role("group", name="Mode", exact=True).get_by_role(
        "radio", name=mode
    )


def choose(page, name, value):
    """Choose ``value`` in the schedule field ``name``."""
    page.locator(f'[name="schedule-{name}"]').select_option(value)


def email_for(page, kind):
    """The value of the first email offered for ``kind``."""
    return page.locator(
        f'[name="schedule-template_version"] option[data-kind="{kind}"]'
    ).first.get_attribute("value")


def checked(page, name):
    """Whether the checkbox ``name`` is ticked, shown or not."""
    return page.locator(f'input[name="{name}"]').evaluate("box => box.checked")


def posted(request):
    """A captured POST's fields, as lists of values."""
    return parse_qs(request.post_data or "", keep_blank_values=True)


@pytest.mark.parametrize("width", [320, 1280])
def test_new_opens_in_basic_and_advanced_adds_repeat_and_the_email(
    page, component_origin, axe_source, width
):
    """Basic shows the schedule's fields only; Advanced adds Repeat and the
    schedule's own email. The toggle never moves, switching is not a
    change, and Basic comes back at once while both tools are off."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + NEW_BASIC)
    failures = watch(page)
    assert toggle(page, "Basic").is_checked()
    choose(page, "kind", "reminder")
    for name in ("schedule-date", "schedule-time", "schedule-template_version"):
        visible(page.locator(f'[name="{name}"]'))
    repeat = page.get_by_label("Repeat", exact=True)
    email = page.get_by_label(EDIT_EMAIL)
    hidden(repeat)
    hidden(email)
    assert axe_violations(page, axe_source) == []
    # No sideways scrolling on a phone.
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    before = page.locator("#schedule-mode").bounding_box()
    toggle(page, "Advanced").click()
    visible(repeat)
    visible(email)
    assert same_place(page.locator("#schedule-mode").bounding_box(), before)
    # Switching modes alone is nothing to add.
    assert page.get_by_role("button", name=ADD).is_disabled()
    assert axe_violations(page, axe_source) == []
    toggle(page, "Basic").click()
    hidden(repeat)
    hidden(email)
    assert page.get_by_role("dialog", name=NO_BASIC).count() == 0
    assert same_place(page.locator("#schedule-mode").bounding_box(), before)
    assert page.evaluate("window.notReloaded === true")
    assert not failures


@pytest.mark.parametrize("tool", ["repeat-repeat", "edit_email"])
def test_basic_with_a_tool_on_asks_then_turns_it_off(page, component_origin, tool):
    """Keep Advanced changes nothing; Switch to Basic unticks the tool, so
    the page posts only the schedule's fields and the Basic mode."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW_BASIC)
    failures = watch(page)
    choose(page, "kind", "reminder")
    toggle(page, "Advanced").click()
    box = page.locator(f'input[name="{tool}"]')
    box.check()
    toggle(page, "Basic").click()
    dialog = page.get_by_role("dialog", name=NO_BASIC)
    visible(dialog)
    focused(dialog.get_by_role("button", name="Keep Advanced"))
    dialog.get_by_role("button", name="Keep Advanced").click()
    hidden(dialog)
    assert toggle(page, "Advanced").is_checked()
    assert box.is_checked()
    toggle(page, "Basic").click()
    visible(dialog)
    dialog.get_by_role("button", name="Switch to Basic").click()
    hidden(dialog)
    focused(toggle(page, "Basic"))
    hidden(box)
    assert not checked(page, tool)
    # Repeat off brings the send date back.
    date = page.locator('[name="schedule-date"]')
    visible(date)
    date.fill("2054-10-15")
    page.locator('[name="schedule-time"]').fill("9am")
    choose(page, "template_version", email_for(page, "reminder"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=ADD).click()
    fields = posted(sent.value)
    assert fields["editor_mode"] == ["basic"]
    assert fields["schedule-date"] == ["2054-10-15"]
    for name in ("repeat-repeat", "repeat-dates", "edit_email", "subject", "html"):
        assert name not in fields
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_edit_opens_in_basic_with_the_series_scope_shown(page, component_origin):
    """Edit opens in Basic. A series reminder's scope decides what the Edit
    changes, so it stays in both modes; the email tool is Advanced only, and
    switching to Advanced leaves Review changes unavailable."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_SERIES_BASIC)
    failures = watch(page)
    assert toggle(page, "Basic").is_checked()
    visible(page.get_by_role("radio", name="This and following"))
    hidden(page.get_by_label(EDIT_EMAIL))
    page.goto(component_origin + EDIT_BASIC)
    failures = watch(page)
    assert toggle(page, "Basic").is_checked()
    email = page.get_by_label(EDIT_EMAIL)
    hidden(email)
    toggle(page, "Advanced").click()
    visible(email)
    assert page.get_by_role("button", name=REVIEW).is_disabled()
    page.locator('[name="schedule-time"]').fill("10:30")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=REVIEW).click()
    fields = posted(sent.value)
    assert fields["editor_mode"] == ["advanced"]
    assert fields["schedule-time"] == ["10:30"]
    assert "edit_email" not in fields
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_repeat_is_unticked_when_the_mail_type_leaves_reminder(page, component_origin):
    """Repeat is a Reminder's. Changing the mail type hides it and unticks
    it, so nothing hidden is posted and Basic is available at once."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW_BASIC)
    failures = watch(page)
    choose(page, "kind", "reminder")
    toggle(page, "Advanced").click()
    repeat = page.get_by_label("Repeat", exact=True)
    repeat.check()
    choose(page, "kind", "initial")
    hidden(repeat)
    assert not checked(page, "repeat-repeat")
    toggle(page, "Basic").click()
    hidden(repeat)
    assert page.get_by_role("dialog", name=NO_BASIC).count() == 0
    assert toggle(page, "Basic").is_checked()
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_edit_dialog_names_only_the_tool_edit_has(page, component_origin):
    """Edit has no Repeat, so its dialog names only the schedule's email."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_BASIC)
    failures = watch(page)
    toggle(page, "Advanced").click()
    page.get_by_label(EDIT_EMAIL).check()
    toggle(page, "Basic").click()
    dialog = page.get_by_role("dialog", name=NO_BASIC)
    visible(dialog)
    text = dialog.inner_text()
    assert "this schedule's own email" in text
    assert "Repeat" not in text
    assert "repeat" not in text
    dialog.get_by_role("button", name="Switch to Basic").click()
    hidden(dialog)
    assert not checked(page, "edit_email")
    assert not failures
