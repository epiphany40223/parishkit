"""Mail › Report emails in the browser (#1786, delivery plan item 5).

The table fits a 320 px phone without a page scroll, in light and dark; a
Paused status stands out in the Warning look (#1966); its
row actions are named icons, the unavailable ones staying in place with
their reason; each dialog names the report it acts on (Resume names its next
send's day in this browser's zone); a past sends list opens in place; Staff
see no actions.
"""

import pytest

from .report_emails_components import PAGE, STAFF
from .test_components import axe_violations
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 1280])
def test_the_list_fits_and_passes_axe(
    page, component_origin, axe_source, width, scheme
):
    """No page scroll at any width, and no accessibility violation."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PAGE)
    visible(page.get_by_role("heading", name="Report emails", level=1))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 1280])
def test_a_paused_status_stands_out(page, component_origin, axe_source, width, scheme):
    """A Paused report's status badge takes the Warning look (#1966): the
    warning icon before the word, which stays, and the scheme's warning
    colours; other statuses keep the plain badge, and axe finds no contrast
    violation in either scheme."""
    from parishkit.stewardship.web.design_tokens import COLORS, DARK_COLORS

    from .test_dark_scheme import rgb

    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PAGE)
    paused = page.locator('tbody[data-report-status="paused"] .status-badge')
    visible(paused)
    assert paused.inner_text().strip() == "Paused"
    icon = paused.locator("svg.level-icon-warning")
    assert icon.count() == 1 and icon.get_attribute("aria-hidden") == "true"
    colors = DARK_COLORS if scheme == "dark" else COLORS
    look = paused.evaluate(
        "node => { const style = getComputedStyle(node);"
        " return [style.color, style.backgroundColor, style.borderTopColor]; }"
    )
    assert look == [
        rgb(colors["warning-ink"]),
        rgb(colors["warning-bg"]),
        rgb(colors["warning-border"]),
    ]
    upcoming = page.locator('tbody[data-report-status="upcoming"] .status-badge')
    assert upcoming.locator("svg").count() == 0
    assert "status-warning" not in upcoming.get_attribute("class")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_unavailable_actions_stay_in_place_with_their_reason(page, component_origin):
    """A send in progress greys its row's actions; they open nothing."""
    page.goto(component_origin + PAGE)
    pause = page.get_by_role("button", name="Pause Finance council report")
    visible(pause)
    assert pause.get_attribute("aria-disabled") == "true"
    assert "A send of this report is in progress" in pause.get_attribute("title")
    # aria-disabled keeps it focusable; Playwright waits on it unless forced.
    pause.click(force=True)
    assert not page.locator("#report-pause-dialog").evaluate("dialog => dialog.open")


def test_each_dialog_names_its_report(page, component_origin):
    """Send now names the report and its recipients; Resume its next send's day."""
    page.goto(component_origin + PAGE)
    page.get_by_role("button", name="Send Weekly report now").click()
    dialog = page.locator("#report-send-now-dialog")
    visible(dialog.get_by_role("heading", name="Send Weekly report now?"))
    assert "3 recipients" in dialog.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Resume Daily report").click()
    dialog = page.locator("#report-resume-dialog")
    visible(dialog.get_by_role("heading", name="Resume Daily report?"))
    text = dialog.inner_text()
    # The instant is written as this browser's day, never as UTC text.
    assert "Its next send is then on" in text and "T11:00" not in text


def test_past_sends_open_in_place(page, component_origin):
    """A report's past sends are a disclosure under its row."""
    page.goto(component_origin + PAGE)
    summary = page.get_by_text("Past sends of Weekly report (3)")
    visible(summary)
    summary.click()
    sends = page.locator(f"#report-sends-{'00000000-0000-0000-0000-000000000065'}")
    visible(sends.get_by_text("12 open requests"))
    assert sends.get_by_role("link", name="Saved copy").count() == 3


def test_staff_see_no_actions(page, component_origin):
    """Staff read the list and past sends only."""
    page.goto(component_origin + STAFF)
    visible(page.get_by_role("heading", name="Report emails", level=1))
    assert page.locator("[data-confirm-open]").count() == 0
    assert page.get_by_role("columnheader", name="Recipients").count() == 0
