"""A schedule's editor describes its chosen email and links to it (#446)."""

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
def test_the_chosen_email_is_described_with_its_users_and_links(
    page, component_origin, axe_source, width
):
    """Both reminders share one email: the summary says so and links to it."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PATH)
    page.get_by_role("button", name="Change Reminder 1").click()
    reminder = editor(page, "Reminder 1")
    visible(reminder)
    text = summary(reminder).inner_text()
    assert "Sent by: Reminder 1, Reminder 2" in text
    assert "changes it for every schedule that sends it" in text
    assert "Starts: Welcome to {{ parish_name }}." in text
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


def test_a_new_schedule_describes_the_email_it_chooses(page, component_origin):
    """The summary follows the choice in place, and clears with it."""
    page.goto(component_origin + PATH)
    row = page.locator("fieldset[data-schedule-row]").filter(
        has=page.locator("legend", has_text="New schedule")
    )
    row.locator('[name="schedules-4-kind"]').select_option("daily_digest")
    email = row.locator('[name="schedules-4-template_version"]')
    assert summary(row).inner_text().strip() == ""
    email.select_option(index=1)
    text = summary(row).inner_text()
    assert "No saved schedule sends this email yet." in text
    assert "changes it for every schedule" not in text
    visible(summary(row).get_by_role("link", name="Edit this email"))
    email.select_option(index=0)
    assert summary(row).inner_text().strip() == ""


@pytest.mark.parametrize("path", ["/setup-schedules-mail", "/clone-settings"])
def test_a_sole_sender_is_not_warned_on_setup_and_copy(page, component_origin, path):
    """Without the schedules table, a saved row still knows its own name.

    The only schedule sending its email gets no "changes it for every
    schedule" warning, and the list is described by the summary too.
    """
    page.goto(component_origin + path)
    saved = page.locator("fieldset[data-schedule-row]").first
    assert saved.get_attribute("data-schedule-label") == "Initial invitation"
    text = summary(saved).inner_text()
    assert "Sent by: Initial invitation" in text
    assert "changes it for every schedule" not in text
    described = saved.locator('[name="schedules-0-template_version"]')
    summary_id = summary(saved).get_attribute("id")
    assert summary_id in described.get_attribute("aria-describedby").split()
