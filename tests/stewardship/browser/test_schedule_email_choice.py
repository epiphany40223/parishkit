"""A schedule's editor links to its chosen email in one help line (#446, #2130)."""

import pytest

from .test_components import axe_violations
from .test_schedule_table import PATH, editor
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def summary(locator):
    """The chosen-email summary inside one schedule editor."""
    return locator.locator("[data-email-summary]")


@pytest.mark.parametrize("width", [320, 1280])
def test_the_chosen_email_has_one_line_of_links(
    page, component_origin, axe_source, width
):
    """Both reminders share one email: its option says so, the line links to it.

    The line under the list is one paragraph of links (#2130); who sends the
    email is in the option's own label, and the list's tip says that editing
    it changes every schedule that sends it.
    """
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PATH)
    page.get_by_role("button", name="Change Reminder 1").click()
    reminder = editor(page, "Reminder 1")
    visible(reminder)
    assert summary(reminder).locator("p").count() == 1
    text = summary(reminder).inner_text()
    assert "Sent by" not in text and "Starts:" not in text
    tip = reminder.locator("#id_schedules-1-template_version_tip")
    assert "changes it for every schedule that sends it" in tip.text_content()
    email = reminder.locator('[name="schedules-1-template_version"]')
    chosen = email.input_value()
    test = summary(reminder).get_by_role("link", name="Preview and send a test")
    edit = summary(reminder).get_by_role("link", name="Edit this email")
    assert test.get_attribute("href") == f"/admin/campaign/content/test/{chosen}/"
    assert edit.get_attribute("href") == (
        f"/admin/campaign/content/email/reminder/{chosen}/"
    )
    assert edit.get_attribute("target") == "_blank"
    # The option itself reads who sends it, not only its subject.
    label = email.locator("option:checked").inner_text()
    assert label.endswith("sent by Reminder 1, Reminder 2")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_a_live_campaign_says_why_there_is_no_test_link(page, component_origin):
    """No test link leads to a page that cannot send; the summary says why (#923)."""
    page.goto(component_origin + "/schedule-table-live")
    page.get_by_role("button", name="Change Reminder 1").click()
    reminder = editor(page, "Reminder 1")
    visible(reminder)
    text = summary(reminder).inner_text()
    assert (
        "Test emails can be sent only while the campaign is being tested, or "
        "while live email delivery is paused." in text
    )
    assert (
        summary(reminder).get_by_role("link", name="Preview and send a test").count()
        == 0
    )
    visible(summary(reminder).get_by_role("link", name="Edit this email"))


def test_a_new_schedule_describes_the_email_it_chooses(page, component_origin):
    """The line follows the choice in place, and clears with it.

    New offers Family mail only (#1786); the invitation's email is the one
    the saved Initial invitation sends.
    """
    page.goto(component_origin + PATH)
    row = page.locator("fieldset[data-schedule-row]").filter(
        has=page.locator("legend", has_text="New schedule")
    )
    row.locator('[name="schedules-3-kind"]').select_option("initial")
    email = row.locator('[name="schedules-3-template_version"]')
    assert summary(row).inner_text().strip() == ""
    email.select_option(index=1)
    visible(summary(row).get_by_role("link", name="Edit this email"))
    email.select_option(index=0)
    assert summary(row).inner_text().strip() == ""


@pytest.mark.parametrize("path", ["/schedule-editor-mail", "/clone-settings"])
def test_setup_and_copy_show_at_most_one_line_under_the_list(
    page, component_origin, path
):
    """Setup and Copy show at most the links line under the list (#2130).

    A saved row says nothing about who sends its email, and the list is
    described by its line too.
    """
    page.goto(component_origin + path)
    saved = page.locator("fieldset[data-schedule-row]").first
    assert summary(saved).locator("p").count() <= 1
    assert "Sent by" not in summary(saved).inner_text()
    described = saved.locator('[name="schedules-0-template_version"]')
    summary_id = summary(saved).get_attribute("id")
    assert summary_id in described.get_attribute("aria-describedby").split()
