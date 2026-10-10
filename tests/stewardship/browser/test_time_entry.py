"""Times of day typed in any common form on Admin forms (#631).

The page script's parser runs the same fixture table as the server's
(``tests/stewardship/fixtures/time_entry_cases.json``), and the fields show
their reading live, refuse at the field, normalise on blur and keep Save
unavailable while an entry cannot be read.
"""

import json
from pathlib import Path

import pytest

from .test_components import axe_violations
from .waits import has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "time_entry_cases.json").read_text()
)
TIMES = '[name="times"]'
NEW = "schedules-1-"

# Each case through window.ParishTimeEntry, in the fixture's own shape.
RUN_CASES = """(cases) => {
  const {parseTime, parseTimes, timeReading, canonicalTime} = window.ParishTimeEntry;
  return {
    times: cases.times.map((item) => {
      const result = parseTime(item.entry, item.kept || "");
      return result.error ? {error: result.error, message: result.message}
        : {value: canonicalTime(result.value), reading: timeReading(result.value)};
    }),
    lists: cases.lists.map((item) => {
      const result = parseTimes(item.entry, item.max || 0, item.blank || "");
      return result.error ? {error: result.error, message: result.message}
        : {values: result.values.map(canonicalTime), duplicates: result.duplicates};
    }),
  };
}"""


def expected(case, keys):
    """The fixture case's outcome: its refusal, or the given result keys."""
    if "error" in case:
        return {"error": case["error"], "message": case["message"]}
    return {key: case[key] for key in keys}


def reading(page, selector):
    """The reading line the page script put after a time field."""
    return page.locator(f"{selector} + .time-reading")


def live(page, selector):
    """The visually hidden live region that announces the field's reading."""
    return page.locator(f"{selector} + .time-reading + [aria-live=polite]")


def test_the_page_parser_runs_the_shared_fixture(page, component_origin):
    """Every entry reads, or is refused, exactly as the server's parser does."""
    page.goto(component_origin + "/integration-settings")
    results = page.evaluate(RUN_CASES, CASES)
    assert results["times"] == [
        expected(case, ("value", "reading")) for case in CASES["times"]
    ]
    assert results["lists"] == [
        expected(case, ("values", "duplicates")) for case in CASES["lists"]
    ]


@pytest.mark.parametrize("width", [320, 1280])
def test_refresh_times_read_live_refuse_at_the_field_and_gate_save(
    page, component_origin, axe_source, width
):
    """The list reads as typed, reports repeats, and blocks Save while unread."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/time-list")
    field = page.locator(TIMES)
    line = reading(page, TIMES)
    save = page.locator("form.panel button[type=submit]")
    # The saved default reads back on both clocks.
    has_text(line, "Reads as 02:00 (2:00 AM)")
    assert line.get_attribute("id") in field.get_attribute("aria-describedby")
    # The live region is always rendered but says nothing at load; it
    # speaks only for this field's typing, after a pause.
    assert live(page, TIMES).count() == 1
    page.wait_for_timeout(700)
    assert live(page, TIMES).inner_text() == ""
    height = save.bounding_box()["height"]
    reading_height = line.bounding_box()["height"]
    field.fill("")
    has_text(line, "Blank reads as 02:00 (2:00 AM)")
    has_text(live(page, TIMES), "Blank reads as 02:00 (2:00 AM)")
    # Only separators is blank too, never an empty reading with Save open.
    field.fill(" , ; ")
    has_text(line, "Blank reads as 02:00 (2:00 AM)")
    assert save.is_enabled()
    field.fill("2pm, 2 am\n0800; 14:00")
    has_text(
        line,
        "Reads as 14:00 (2:00 PM), 02:00 (2:00 AM), 08:00 (8:00 AM), "
        "14:00 (2:00 PM); 14:00 is listed more than once and saved once",
    )
    assert save.is_enabled()
    # A half-typed entry is not flashed red: Save waits at once, the refusal
    # and the hint that explains it after a pause.
    field.fill("")
    field.press_sequentially("2:")
    assert line.inner_text() == ""
    # Empty, the line keeps one line's height: a reading appearing moves
    # nothing below it.
    assert line.bounding_box()["height"] == reading_height
    assert "is-error" not in (line.get_attribute("class") or "")
    assert field.get_attribute("aria-invalid") == "false"
    assert save.is_disabled()
    assert not page.get_by_text(
        "Fix the time that can't be read to continue."
    ).is_visible()
    has_text(line, "“2:” isn't a time. Try 2:00 PM, 2pm, 14:00 or 1400.")
    has_attribute(field, "aria-invalid", "true")
    visible(page.get_by_text("Fix the time that can't be read to continue."))
    # An unreadable entry is refused at the field, and Save waits.
    field.fill("2pm, 25:00")
    has_text(line, "“25:00” has no hour 25: hours run from 0 to 23.")
    has_text(live(page, TIMES), "“25:00” has no hour 25: hours run from 0 to 23.")
    has_attribute(field, "aria-invalid", "true")
    assert save.is_disabled()
    hint = page.get_by_text("Fix the time that can't be read to continue.")
    visible(hint)
    assert hint.get_attribute("id") in save.get_attribute("aria-describedby")
    # The hint goes below: the Save label still sits on one line.
    assert save.bounding_box()["height"] == height
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # Fixing it clears the refusal and Save at once; blur normalises.
    field.fill("2pm, 2:30 a.m.")
    has_text(line, "Reads as 14:00 (2:00 PM), 02:30 (2:30 AM)")
    assert field.get_attribute("aria-invalid") == "false"
    assert save.is_enabled()
    hidden(hint)
    assert hint.get_attribute("id") not in (
        save.get_attribute("aria-describedby") or ""
    )
    field.blur()
    assert field.input_value() == "14:00, 02:30"
    assert not failures


def test_a_server_refusal_is_cleared_by_the_first_edit(page, component_origin):
    """The server's message shows alone at first, then the live check takes over."""
    page.goto(component_origin + "/time-list-error")
    field = page.locator(TIMES)
    server = page.locator("#id_times_error")
    visible(server)
    assert "has no hour 25" in server.inner_text()
    # No duplicate live message while the server's says the same.
    assert reading(page, TIMES).inner_text() == ""
    assert field.get_attribute("aria-invalid") == "true"
    assert page.locator("form.panel button[type=submit]").is_disabled()
    field.fill("02:00, 2:20")
    hidden(server)
    has_text(reading(page, TIMES), "Reads as 02:00 (2:00 AM), 02:20 (2:20 AM)")
    assert "id_times_error" not in (field.get_attribute("aria-describedby") or "")
    assert field.get_attribute("aria-invalid") == "false"
    assert page.locator("form.panel button[type=submit]").is_enabled()


def test_schedule_send_times_read_live_normalise_and_gate_save(
    page, component_origin, axe_source
):
    """A new schedule row and an added one each take any common form."""
    page.set_viewport_size({"width": 320, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-schedules-mail")
    save = page.get_by_role("button", name="Save and continue")
    # The saved row shows its time canonically, with its reading.
    saved = '[name="schedules-0-time"]'
    assert page.locator(saved).input_value() == "09:00"
    has_text(reading(page, saved), "Reads as 09:00 (9:00 AM)")
    page.locator(f'[name="{NEW}kind"]').select_option("reminder")
    time = f'[name="{NEW}time"]'
    page.locator(time).fill("9p")
    has_text(reading(page, time), "Reads as 21:00 (9:00 PM)")
    page.locator(time).fill("21:60")
    has_text(
        reading(page, time),
        "“21:60” has no minute 60: minutes run from 00 to 59.",
    )
    assert save.is_disabled()
    assert axe_violations(page, axe_source) == []
    page.locator(time).fill("7")
    has_text(reading(page, time), "Reads as 07:00 (7:00 AM)")
    assert save.is_enabled()
    page.locator(time).blur()
    assert page.locator(time).input_value() == "07:00"
    # A bad time in a row whose type then hides it no longer holds Save.
    page.locator(time).fill("nonsense")
    assert save.is_disabled()
    page.locator(f'[name="{NEW}kind"]').select_option("")
    assert save.is_enabled()
    # An added row is wired too.
    page.get_by_role("button", name="Add another schedule").click()
    page.locator('[name="schedules-2-kind"]').select_option("daily_digest")
    added = '[name="schedules-2-time"]'
    page.locator(added).fill("6:15 PM")
    has_text(reading(page, added), "Reads as 18:15 (6:15 PM)")
    assert reading(page, added).get_attribute("id") == "id_schedules-2-time_reading"
    page.locator(added).fill("13pm")
    assert save.is_disabled()
    has_text(reading(page, added), "“13pm”: with AM or PM the hour runs from 1 to 12.")
    page.get_by_role("button", name="Remove this new schedule").click()
    assert save.is_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not failures


def test_a_value_restored_without_an_event_is_checked_on_pageshow(
    page, component_origin
):
    """The back/forward cache can restore a value silently; pageshow re-checks."""
    page.goto(component_origin + "/time-list")
    save = page.locator("form.panel button[type=submit]")
    page.evaluate(
        """() => {
          document.querySelector('[name="times"]').value = "25:00";
          window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: true}));
        }"""
    )
    has_text(reading(page, TIMES), "“25:00” has no hour 25: hours run from 0 to 23.")
    assert save.is_disabled()


def test_a_schedule_time_refused_by_the_server_clears_on_edit(page, component_origin):
    """The send time's server error shows alone, then the live check takes over."""
    page.goto(component_origin + "/setup-schedules-time-error")
    time = '[name="schedules-0-time"]'
    server = page.locator("#id_schedules-0-time_error")
    visible(server)
    assert "has no hour 25" in server.inner_text()
    assert reading(page, time).inner_text() == ""
    save = page.get_by_role("button", name="Save and continue")
    assert save.is_disabled()
    page.locator(time).fill("9:15 am")
    hidden(server)
    has_text(reading(page, time), "Reads as 09:15 (9:15 AM)")
    assert save.is_enabled()


def test_a_saved_time_with_seconds_reads_and_saves_unchanged(page, component_origin):
    """A schedule saved with seconds keeps them; typing new seconds is refused."""
    page.goto(component_origin + "/setup-schedules-kept")
    time = '[name="schedules-0-time"]'
    save = page.get_by_role("button", name="Save and continue")
    assert page.locator(time).input_value() == "09:00:37"
    has_text(reading(page, time), "Reads as 09:00:37 (9:00:37 AM)")
    assert save.is_enabled()
    page.locator(time).fill("09:00:38")
    has_text(
        reading(page, time),
        "“09:00:38” has seconds: these times are to the minute, so leave the "
        "seconds out.",
    )
    assert save.is_disabled()
    page.locator(time).fill("09:00:37")
    page.locator(time).blur()
    assert page.locator(time).input_value() == "09:00:37"
    assert save.is_enabled()


def test_another_controls_change_announces_no_time_field(page, component_origin):
    """Choosing a mail type re-checks the time fields silently."""
    page.goto(component_origin + "/setup-schedules-mail")
    saved = '[name="schedules-0-time"]'
    has_text(reading(page, saved), "Reads as 09:00 (9:00 AM)")
    page.locator(f'[name="{NEW}kind"]').select_option("reminder")
    page.locator(f'[name="{NEW}kind"]').select_option("daily_digest")
    page.wait_for_timeout(700)
    assert page.locator(".time-reading + [aria-live=polite]").evaluate_all(
        "nodes => nodes.map(node => node.textContent)"
    ) == ["", ""]
    # This field's own typing is announced after the pause, once.
    page.locator(f'[name="{NEW}time"]').fill("7pm")
    has_text(live(page, f'[name="{NEW}time"]'), "Reads as 19:00 (7:00 PM)")
    page.locator(f'[name="{NEW}time"]').blur()
    has_text(live(page, f'[name="{NEW}time"]'), "Reads as 19:00 (7:00 PM)")
