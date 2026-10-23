"""A refused time's Save hint moves nothing on a phone (#2082).

Every Admin time form holds its buttons while a shown time cannot be read
(UX conventions, "Time entry"). The refusal itself shows in the field's
reading line; the hint by the buttons is either visually hidden (the
time-entry gate) or, on a data-require-complete form, the fixed one-line
"Fix the time above.", so typing a refused time never moves a control
below the hint. This shared test types a readable time and then the
longest refusal (seconds, with the entry quoted) on each time form at
320 px, and checks that every control after the field's reading line moves
by exactly as much as that line grew, and no more: nothing between them,
the hint included, changed height.
"""

import pytest

from .waits import eventually, has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

TIME_HINT = "Fix the time above."
# The longest refusal (two lines or more at 320 px), with the entry it quotes.
REFUSED = "14:30:15"
REFUSAL = (
    f"“{REFUSED}” has seconds: these times are to the minute, so leave the seconds out."
)
PANE = "schedules-1-"


def new_scheduled_email(page):
    """New scheduled email, an initial invitation with a date and its email
    (an empty required email would be marked as focus moves, which is not
    the hint's doing)."""
    page.locator('[name="schedule-kind"]').select_option("initial")
    page.locator('[name="schedule-date"]').fill("2054-10-02")
    email = page.locator('[name="schedule-template_version"]')
    first = email.locator('option[data-kind="initial"]').first
    email.select_option(first.get_attribute("value"))
    return page.locator('[name="schedule-time"]')


def edit_scheduled_email(page):
    """Edit scheduled email, as saved."""
    return page.locator('[name="schedule-time"]')


def date_change_pane(page):
    """The date-change review's New schedule pane, a Once reminder."""
    page.locator(f'[name="{PANE}kind"]').select_option("reminder")
    page.locator(f'[name="{PANE}date"]').fill("2054-10-12")
    return page.locator(f'[name="{PANE}time"]')


def report_email_editor(page):
    """Edit of a saved report email, which sends daily at a time."""
    return page.locator("#report-time")


def wizard_suggested_reports(page):
    """The campaign wizard's suggested report emails (#2038), Daily report's
    time, with Weekly report's time readable (so the complete gate's hint
    has only Daily's refusal to name)."""
    page.locator("#suggested-report_weekly_time").fill("8:30 AM")
    return page.locator("#suggested-report_daily_time")


FORMS = [
    pytest.param("/schedule-new", new_scheduled_email, id="new-scheduled-email"),
    pytest.param("/schedule-edit", edit_scheduled_email, id="edit-scheduled-email"),
    pytest.param("/schedule-editor-mail", date_change_pane, id="date-change-pane"),
    pytest.param("/report-email-edit", report_email_editor, id="report-email-editor"),
    pytest.param(
        "/campaign-stepper-suggested",
        wizard_suggested_reports,
        id="wizard-suggested-reports",
    ),
]

# The bottom of the field's reading line, and the top of every visible
# control after it in document order, from the top of the page.
LAYOUT = """(field) => {
  const reading = document.getElementById(`${field.id}_reading`);
  const after = (node) => reading.compareDocumentPosition(node)
    & Node.DOCUMENT_POSITION_FOLLOWING;
  const top = (node) => node.getBoundingClientRect().top + window.scrollY;
  const all = document.querySelectorAll("button, input, select, textarea, a[href]");
  const controls = [...all].filter((node) => after(node) && node.getClientRects().length
      && getComputedStyle(node).visibility !== "hidden");
  return {line: reading.getBoundingClientRect().bottom + window.scrollY,
          tops: controls.map(top)};
}"""


@pytest.mark.parametrize(("path", "open_field"), FORMS)
def test_a_refused_time_moves_no_control_below_the_hint(
    page, component_origin, path, open_field
):
    """Typing a refused time holds the buttons and moves nothing after the
    reading line beyond that line's own growth; a complete gate's hint says
    the fixed one-line words."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + path)
    time = open_field(page)
    time.fill("9am")
    time.press("Tab")
    eventually(page, "(field) => field.value", "09:00", arg=time.element_handle())
    before = time.evaluate(LAYOUT)
    form = time.locator("xpath=ancestor::form")
    hint = form.locator("[data-complete-hint]")
    # A complete gate's hint keeps one line reserved while empty (the
    # Report email editor's): its height then is the one line it may take.
    reserved = hint.bounding_box()["height"] if hint.count() else None
    time.fill(REFUSED)
    time.press("Tab")
    reading = page.locator(f"#{time.get_attribute('id')}_reading")
    has_text(reading, REFUSAL)
    eventually(
        page,
        "(form) => [...form.querySelectorAll('button[type=submit]')]"
        ".filter((node) => !node.formNoValidate).every((node) => node.disabled)",
        arg=form.element_handle(),
    )
    if reserved is not None:
        has_text(hint, TIME_HINT)
        # Still the one reserved line.
        assert hint.bounding_box()["height"] == pytest.approx(reserved, abs=1)
    after = time.evaluate(LAYOUT)
    grew = after["line"] - before["line"]
    moved = [new - old for old, new in zip(before["tops"], after["tops"], strict=True)]
    assert moved == pytest.approx([grew] * len(moved), abs=1)
