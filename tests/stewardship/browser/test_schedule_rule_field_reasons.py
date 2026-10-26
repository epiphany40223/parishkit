"""Change the repeat names the field behind its unusable dates (#2235).

On Edit scheduled email's Change the repeat (#1808), when every date the new
rule gives that can't be used is too early, From is marked with the first day
that can be used; when every one is after the campaign's end, Until is marked
with the end date; the summary line then names the field. Mixed causes keep
the summary pointing to the list. The page is schedule_list_components'
``EDIT_SERIES_RULE`` (the campaign in New York's zone, the browser in Los
Angeles's, so Reminder 2's 9:00 AM shows as 06:00, the Initial invitation's
moment on its day). Dates come from the campaign factories, and the checks
run at 320 px, where a reserved line that is too short would wrap and move
what follows (UX conventions "No layout shift").
"""

from datetime import date, timedelta

import pytest

from ..campaign_factory import campaign, schedule
from .schedule_list_components import EDIT_SERIES_RULE
from .waits import has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
VALUES = campaign()["values"]
START = date.fromisoformat(VALUES["start_date"])
END = date.fromisoformat(VALUES["end_date"])
# The Initial invitation's day; at Reminder 2's time it is the invitation's
# own moment, so that date is "at or before the Initial invitation".
INITIAL = date.fromisoformat(schedule("campaign")["values"]["date"])
UNUSABLE = "Some dates can't be used; see the list below."


def spoken(day):
    """``day`` as the page writes it in the default parish date format."""
    return f"{day:%B} {day.day}, {day.year}"


def open_rule(page, component_origin):
    """Open Change the repeat for The whole series, at 320 px.

    Returns the form, its From and Until boxes, their error lines and the
    summary line, and the places of Until and the summary line before any
    mark, for the layout check.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + EDIT_SERIES_RULE)
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    form = page.locator("#schedule-entry-form")
    form.get_by_role("radio", name="The whole series").check()
    form.get_by_label("Change the repeat").check()
    parts = {
        "form": form,
        "from": form.locator("#repeat-from"),
        "until": form.locator("#repeat-until"),
        "from_error": form.locator("#repeat-from_error"),
        "until_error": form.locator("#repeat-until_error"),
        "summary": form.locator("[data-repeat-summary]"),
        "failures": failures,
    }
    visible(parts["from"])
    assert form.locator("#repeat-frequency").input_value() == "daily"
    parts["places"] = places(parts)
    return parts


def places(parts):
    """Where Until's box and the summary line's top are, in the document.

    Typing scrolls the field into view, so the places are measured from the
    document's top rather than the viewport's.
    """
    measure = (
        "node => { const box = node.getBoundingClientRect();"
        " return [box.x + window.scrollX, box.y + window.scrollY, box.height]; }"
    )
    return [
        parts["until"].evaluate(measure),
        parts["summary"].evaluate(measure)[1],
    ]


def set_rule(parts, start, until):
    """Type the rule's From and Until and leave the field."""
    parts["until"].fill(until.isoformat())
    parts["from"].fill(start.isoformat())
    parts["from"].blur()


def test_too_early_dates_mark_from(page, component_origin):
    """Dates before the campaign and at the Initial invitation are From's:
    From says the first day that can be used, the summary says "See From.",
    Until stays unmarked, and nothing moves; a later From clears it."""
    parts = open_rule(page, component_origin)
    set_rule(parts, START - timedelta(days=2), INITIAL + timedelta(days=2))
    first_usable = INITIAL + timedelta(days=1)
    has_text(
        parts["from_error"], f"Before {spoken(first_usable)}. Choose a later From."
    )
    assert parts["from"].get_attribute("aria-invalid") == "true"
    described = parts["from"].get_attribute("aria-describedby") or ""
    assert "repeat-from_error" in described.split()
    # Leaving the marked box again keeps it marked (the browser's own
    # blur check sees a valid date).
    parts["from"].focus()
    parts["from"].blur()
    assert parts["from"].get_attribute("aria-invalid") == "true"
    has_text(parts["summary"], "Some dates can't be used. See From.")
    hidden(parts["until_error"])
    assert parts["until"].get_attribute("aria-invalid") != "true"
    assert page.get_by_role("button", name="Review changes").is_disabled()
    assert same_place(places(parts), parts["places"])
    # The list still marks each date.
    visible(parts["form"].get_by_text("Dates of the new repeat:"))
    parts["from"].fill(first_usable.isoformat())
    parts["from"].blur()
    hidden(parts["from_error"])
    assert parts["from"].get_attribute("aria-invalid") == "false"
    has_text(parts["summary"], "The new repeat sends on 2 dates.")
    assert not parts["failures"]


def test_dates_after_the_end_mark_until(page, component_origin):
    """Dates after the campaign's end are Until's: Until says the end date,
    the summary says "See Until.", and nothing moves; an earlier Until
    clears it."""
    parts = open_rule(page, component_origin)
    set_rule(parts, END - timedelta(days=2), END + timedelta(days=2))
    has_text(
        parts["until_error"],
        f"After the campaign ends on {spoken(END)}. Choose an earlier Until.",
    )
    assert parts["until"].get_attribute("aria-invalid") == "true"
    described = parts["until"].get_attribute("aria-describedby") or ""
    assert "repeat-until_error" in described.split()
    # Leaving the marked box again keeps it marked (the browser's own
    # blur check sees a valid date).
    parts["until"].focus()
    parts["until"].blur()
    assert parts["until"].get_attribute("aria-invalid") == "true"
    has_text(parts["summary"], "Some dates can't be used. See Until.")
    hidden(parts["from_error"])
    assert parts["from"].get_attribute("aria-invalid") != "true"
    assert same_place(places(parts), parts["places"])
    parts["until"].fill(END.isoformat())
    parts["until"].blur()
    hidden(parts["until_error"])
    has_text(parts["summary"], "The new repeat sends on 3 dates.")
    assert not parts["failures"]


def test_mixed_causes_keep_the_summary(page, component_origin):
    """A date at the Initial invitation and one after the end: no one field
    explains them, so neither is marked and the summary points to the list."""
    parts = open_rule(page, component_origin)
    set_rule(parts, INITIAL, INITIAL + timedelta(days=3))
    has_text(
        parts["from_error"],
        f"Before {spoken(INITIAL + timedelta(days=1))}. Choose a later From.",
    )
    parts["until"].fill((END + timedelta(days=1)).isoformat())
    parts["until"].blur()
    has_text(parts["summary"], UNUSABLE)
    # The From mark of the earlier rule goes once the causes mix.
    hidden(parts["from_error"])
    hidden(parts["until_error"])
    assert parts["from"].get_attribute("aria-invalid") == "false"
    assert parts["until"].get_attribute("aria-invalid") != "true"
    assert page.get_by_role("button", name="Review changes").is_disabled()
    assert not parts["failures"]
