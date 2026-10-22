"""A send date inside the campaign, checked as it is typed (#1787).

The page script's ``ParishSendWindow.judge`` runs the shared case table
(``fixtures/schedule_window_cases.json``) in browsers in each zone the table
names, and must give exactly the verdicts the server's formset gives
(``test_schedule_window_rule.py``). New scheduled email and the date-change
review then refuse an outside date at the field in the server's words and
hold their button until it is fixed.
"""

import json
from pathlib import Path

import pytest

from parishkit.stewardship.accounts import schedule_local
from parishkit.stewardship.accounts.schedule_forms import OUTSIDE_WINDOW, window_text

from ..campaign_factory import campaign
from .schedule_list_components import NEW
from .waits import available, eventually, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "schedule_window_cases.json").read_text()
)["cases"]
ADD = "Add the scheduled email"
REVIEW = "Review changes"
# The date-change review whose campaign is New York, October 1 to 31, 2054,
# with reminders on October 24 and 26 at 9:00 AM there (#558).
DATES = "/schedule-dates-dst"


def judge_input(case):
    """What the page script judges a case from, as the page carries it.

    A saved row's moment is the one the server draws (``data-saved-at``).
    """
    start, end, zone = case["window"]
    saved = case.get("saved")
    return {
        "date": case["date"],
        "time": case["time"],
        "start": start,
        "end": end,
        "zone": zone,
        "savedDate": saved["date"] if saved else "",
        "savedAt": schedule_local.instant(saved, saved["zone"], None).isoformat()
        if saved
        else "",
    }


def watch(page):
    """Collect uncaught page errors, so a test can assert there were none."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    return failures


@pytest.mark.parametrize("zone", sorted({case["browser"] for case in CASES}))
def test_the_page_judges_every_shared_case_like_the_server(
    browser_engine, component_origin, zone
):
    """In a browser in ``zone``, each case's verdict is the table's."""
    cases = [case for case in CASES if case["browser"] == zone]
    context = browser_engine.new_context(timezone_id=zone)
    try:
        page = context.new_page()
        page.goto(component_origin + DATES)
        verdicts = page.evaluate(
            "(cases) => cases.map((item) => window.ParishSendWindow.judge(item))",
            [judge_input(case) for case in cases],
        )
    finally:
        context.close()
    assert verdicts == [case["inside"] for case in cases]


def test_the_page_judges_nothing_it_cannot_read(page, component_origin):
    """No verdict without a date, a time, readable dates in order or a zone
    this browser knows: the window's own refusal (or the server) decides."""
    page.goto(component_origin + DATES)
    base = {
        "date": "2054-10-10",
        "time": "09:00",
        "start": "2054-10-01",
        "end": "2054-10-31",
        "zone": "America/New_York",
    }
    blanks = [
        {"date": ""},
        {"time": ""},
        {"start": ""},
        {"end": "2054-09-30"},
        {"zone": ""},
        {"zone": "Mars/Olympus_Mons"},
    ]
    verdicts = page.evaluate(
        "(cases) => cases.map((item) => window.ParishSendWindow.judge(item))",
        [base | blank for blank in blanks],
    )
    assert verdicts == [None] * len(blanks)


def top(locator):
    """The element's top edge from the top of the page, whatever the scroll."""
    return locator.evaluate("node => node.getBoundingClientRect().top + window.scrollY")


def test_new_refuses_a_date_outside_the_campaign_at_the_field(page, component_origin):
    """10 PM on October 31 in Los Angeles is November 1 in New York.

    The date is marked in the server's words, in its reserved line (nothing
    below it moves), and Add the scheduled email waits, named by the same
    words; 8:59 PM, still October 31 in New York, clears both.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    failures = watch(page)
    page.goto(component_origin + NEW)
    page.locator('[name="schedule-kind"]').select_option("reminder")
    page.locator('[name="schedule-template_version"]').select_option(
        page.locator(
            '[name="schedule-template_version"] option[data-kind="reminder"]'
        ).first.get_attribute("value")
    )
    date = page.locator('[name="schedule-date"]')
    time = page.locator('[name="schedule-time"]')
    line = page.locator("#id_schedule-date_error")
    button = page.get_by_role("button", name=ADD)
    date.fill("2054-10-31")
    time.fill("8:59 PM")
    available(button)
    below = top(time)
    time.fill("10 PM")
    visible(line)
    words = str(OUTSIDE_WINDOW) % {"window": window_text(campaign()["values"])}
    assert line.inner_text() == words
    assert date.get_attribute("aria-invalid") == "true"
    assert "id_schedule-date_error" in date.get_attribute("aria-describedby")
    assert button.is_disabled()
    assert button.get_attribute("data-window-gated") == ""
    hint = button.get_attribute("aria-describedby").split()
    assert any(page.locator(f"#{name}").text_content() == words for name in hint), hint
    assert top(time) == below
    time.fill("8:59 PM")
    hidden(line)
    assert date.get_attribute("aria-invalid") == "false"
    available(button)
    assert button.get_attribute("data-window-gated") is None
    assert top(time) == below
    # No mail type, no send date to judge: clearing the type clears the
    # refusal. (New offers no digest any more, #1786.)
    time.fill("10 PM")
    visible(line)
    page.locator('[name="schedule-kind"]').select_option("")
    hidden(line)
    assert button.get_attribute("data-window-gated") is None
    assert not failures


def test_the_review_judges_against_the_dates_being_edited(
    browser_engine, component_origin
):
    """A proposed end of October 25 leaves the October 26 reminder outside.

    Its closed editor opens with the date refused and Review changes waits;
    the end put back, the refusal goes and Review waits only for a change.
    A browser in London shows that reminder on October 26 at 13:00.
    """
    context = browser_engine.new_context(timezone_id="Europe/London")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        failures = watch(page)
        page.goto(component_origin + DATES)
        date = page.locator('[name="schedules-2-date"]')
        line = page.locator("#id_schedules-2-date_error")
        review = page.get_by_role("button", name=REVIEW)
        hidden(date)
        end = page.locator('[name="window-end_date"]')
        end.fill("2054-10-25")
        visible(date)
        visible(line)
        assert line.inner_text().startswith("Choose a date within the campaign (")
        assert "October 25, 2054" in line.inner_text()
        assert review.get_attribute("data-window-gated") == ""
        assert review.is_disabled()
        # The October 24 reminder is still inside: its editor stays closed.
        hidden(page.locator('[name="schedules-1-date"]'))
        end.fill("2054-10-26")
        hidden(line)
        eventually(
            page,
            "!document.querySelector('#schedule-dates-form [type=submit]')"
            ".hasAttribute('data-window-gated')",
        )
        # A changed end date is a change to review.
        available(review)
        assert not failures
    finally:
        context.close()


def test_a_date_still_being_typed_opens_no_editor(browser_engine, component_origin):
    """An end date typed segment by segment passes through October 2 on its
    way to October 28: Review waits meanwhile, but no editor opens and no
    refusal shows, since October 28 leaves every reminder inside."""
    context = browser_engine.new_context(timezone_id="Europe/London")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        failures = watch(page)
        page.goto(component_origin + DATES)
        review = page.get_by_role("button", name=REVIEW)
        held = page.evaluate(
            """() => {
                const end = document.querySelector('[name="window-end_date"]');
                const button = document.querySelector(
                    '#schedule-dates-form [type=submit]');
                end.value = "2054-10-02";
                end.dispatchEvent(new Event("input", {bubbles: true}));
                const gated = button.hasAttribute("data-window-gated");
                end.value = "2054-10-28";
                end.dispatchEvent(new Event("input", {bubbles: true}));
                return gated;
            }"""
        )
        assert held
        page.wait_for_timeout(1000)
        for index in (1, 2):
            hidden(page.locator(f'[name="schedules-{index}-date"]'))
            hidden(page.locator(f"#id_schedules-{index}-date_error"))
        available(review)
        assert not failures
    finally:
        context.close()
