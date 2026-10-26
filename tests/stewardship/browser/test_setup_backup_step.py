"""The setup wizard's optional Off-site backups step (#2200).

It comes just before Finish setup. While Copy each backup to Google Drive is
unticked the primary reads Continue without off-site backups for now and
saves the step as skipped; ticked, the folder link and the private-key
checkbox appear, both required, and the primary reads Save and continue,
unavailable with the reason until both are filled in. The switch moves
neither the box nor the button, and the hint below the row keeps its line.
"""

import pytest

from parishkit.stewardship.accounts.setup_forms import BACKUP_MISSING

from .backup_test_components import TESTED
from .setup_backup_components import FINISH, LINK, LOCAL, SAVED, STEP
from .waits import available, greyed, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

SKIP = "Continue without off-site backups for now"
SAVE = "Save and continue"
PRIMARY = '.setup-actions [data-nav-slot="next"]'
HINT = "#setup-complete-hint"


def label(page):
    """The primary button's visible label (the shown one of its two)."""
    return page.locator(f"{PRIMARY} .setup-label-switch > :not([hidden])").inner_text()


def box(page, selector):
    """Where ``selector`` is drawn on the page, with its size."""
    return page.locator(selector).first.evaluate(
        "node => { const box = node.getBoundingClientRect();"
        " return [box.left + scrollX, box.top + scrollY, box.width, box.height]; }"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_the_step_comes_before_finish_and_its_primary_follows_the_box(
    page, component_origin, axe_source, width
):
    """Order, Back, the skip wording, the required checkbox and no shift."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + STEP)
    has_text(page.locator("h1"), "Off-site backups (optional)")
    labels = page.locator(".setup-track a").evaluate_all(
        "links => links.map(link => link.dataset.tip.split(': ')[1].split(' — ')[0])"
    )
    assert labels[-3:] == [
        "Check parish data",
        "Off-site backups (optional)",
        "Finish setup",
    ]
    back = page.locator(".setup-actions a.setup-back")
    assert back.get_attribute("href") == "/admin/setup/source"
    # Unticked: the primary skips the step, and is available.
    assert label(page) == SKIP
    primary = page.locator(PRIMARY)
    available(primary)
    folder = page.get_by_label("Google Drive folder link")
    key = page.get_by_label("I know where the parish keeps the backup private key")
    hidden(folder)
    hidden(key)
    switch = page.get_by_label("Copy each backup to Google Drive")
    before = {
        "switch": box(page, "#id_enabled"),
        "primary": box(page, PRIMARY)[2:],
        "hint": box(page, HINT)[3],
    }
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    switch.check()
    visible(folder)
    visible(key)
    assert label(page) == SAVE
    # Both fields are required while shown: the primary waits, saying why.
    greyed(primary)
    has_text(page.locator(HINT), str(BACKUP_MISSING))
    folder.fill(LINK)
    greyed(primary)
    key.check()
    available(primary)
    assert page.locator(HINT).inner_text() == ""
    # The box never moved; the button kept its size and the hint its line.
    assert same_place(box(page, "#id_enabled"), before["switch"])
    assert same_place(box(page, PRIMARY)[2:], before["primary"])
    assert same_place(box(page, HINT)[3], before["hint"])
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    switch.uncheck()
    hidden(folder)
    assert label(page) == SKIP
    available(primary)
    assert same_place(box(page, PRIMARY)[2:], before["primary"])


def test_continue_without_saves_and_opens_finish_setup(page, component_origin):
    """Continue without off-site backups for now opens Finish setup."""
    page.goto(component_origin + STEP)
    page.get_by_role("button", name=SKIP).click()
    page.wait_for_url(component_origin + FINISH)
    has_text(page.locator("h1"), "Finish setup")


def test_a_step_saved_on_shows_its_folder_and_save_and_continue(page, component_origin):
    """A revisited step shows what was saved, with Save and continue ready."""
    page.goto(component_origin + SAVED)
    assert page.get_by_label("Copy each backup to Google Drive").is_checked()
    assert page.get_by_label("Google Drive folder link").input_value() == LINK
    assert label(page) == SAVE
    available(page.locator(PRIMARY))
    page.get_by_role("button", name=SAVE).click()
    page.wait_for_url(component_origin + FINISH)


def test_a_local_environment_without_drive_offers_only_the_skip(page, component_origin):
    """The switch is unavailable, with why, and the step can still be skipped."""
    page.goto(component_origin + LOCAL)
    switch = page.get_by_label("Copy each backup to Google Drive")
    assert switch.is_disabled() and not switch.is_checked()
    # The reason is the switch's own visible help, which describes it.
    reason = page.locator("#id_enabled_helptext")
    visible(reason)
    assert "not available in this local environment" in reason.inner_text()
    assert switch.get_attribute("aria-describedby") == "id_enabled_helptext"
    assert label(page) == SKIP
    available(page.locator(PRIMARY))


def test_the_check_queued_at_completion_shows_on_off_site_backups(
    page, component_origin
):
    """After setup the Off-site backups page shows the queued check, as it
    shows any pending Test access, until the installer answers it."""
    page.goto(component_origin + TESTED)
    visible(page.get_by_text("Checking access to the folder…").first)
