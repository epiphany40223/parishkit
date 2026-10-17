"""Census changes' Edit value (#528, ADM-09.04), in place, in Chromium and WebKit.

The real page is served by ``census_components``. Save value waits until the
value differs from the one drawn and while the browser can tell it is not
allowed; a value the server refuses is answered with its reason in place
after the form, and the field is marked in error until it is edited; an
accepted one answers with the page after it, where the row has its edit and
Clear edit. A mark outside every region shows the page never reloaded, and
the scroll position holds.
"""

import json

import pytest

from .census_components import (
    AUTOMATIC,
    AUTOMATIC_REDRAWS,
    EDITED,
    EDITED_PAGE,
    EDITED_VALUE,
)
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

REFUSAL = "ParishSoft takes at most 25 characters in this field."
FIX = "Correct the value, then save it again."


def centre(page, button):
    """Put ``button`` mid-window (so clicking it scrolls nothing) and return
    the scroll position, which the answer must leave as it is. Measured just
    before the click: Playwright's own fill scrolls its field into view."""
    button.evaluate("node => node.scrollIntoView({block: 'center'})")
    scrolled = page.evaluate("window.scrollY")
    assert scrolled > 0
    return scrolled


def test_edit_value_is_saved_in_place(page, component_origin):
    """Save value is unavailable, with its reason, until the value changes (a
    note alone is no change); a refusal shows its reason after the form; the
    saved edit shows beside the Family's answer, and Clear edit then posts
    without needing a change."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + AUTOMATIC)
    page.evaluate(MARK)
    posts = []

    def answer(route):
        """Refuse the first Save value; answer later posts with the page."""
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.post_data)
        if len(posts) == 1:
            route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps(
                    {
                        "errors": [
                            {
                                "code": "invalid",
                                "field_id": "value",
                                "message": "Check this value.",
                            }
                        ],
                        "refusal": {
                            "message": REFUSAL,
                            "fix": FIX,
                            "link": None,
                        },
                    }
                ),
            )
            return
        route.fulfill(
            response=route.fetch(url=component_origin + EDITED_PAGE, method="GET")
        )

    page.route("**" + AUTOMATIC, answer)
    # A short window, scrolled so the row's Review sits mid-page: the answers
    # must leave the scroll position where it is.
    page.set_viewport_size({"width": 1280, "height": 500})
    review = page.locator("details.census-record").first
    review.locator("summary").click()
    form = review.locator("form.census-edit")
    value = form.get_by_label("Value to publish")
    save = form.get_by_role("button", name="Save value")
    hint = form.locator("[data-unchanged-hint]")
    assert value.input_value() == "555-0000"
    assert value.get_attribute("maxlength") == "25"
    assert form.get_by_role("button", name="Clear edit").count() == 0
    assert save.is_disabled()
    has_text(hint, "Change the value to save it.")
    form.get_by_label("Note (optional)").fill("Typo")
    assert save.is_disabled()
    # A value the browser can tell is not allowed holds Save, saying why.
    value.fill("   ")
    assert save.is_disabled()
    has_text(
        form.locator("[data-complete-hint]"),
        "Enter the value to publish, within the length shown.",
    )
    value.fill("317 555 0198")
    assert save.is_enabled()
    scrolled = centre(page, save)
    save.click()
    refusal = form.locator("[data-in-place-refusal]")
    has_text(refusal, f"{REFUSAL} {FIX}")
    # The refused field is marked in error, described by the refusal, and
    # both clear on its first edit.
    assert value.get_attribute("aria-invalid") == "true"
    refusal_id = refusal.get_attribute("id")
    assert refusal_id and refusal_id in value.get_attribute("aria-describedby")
    assert page.evaluate("window.scrollY") == scrolled
    # (fill, not the End key: End scrolls the page in Chromium.)
    value.fill("317 555 01989")
    assert value.get_attribute("aria-invalid") == "false"
    assert refusal_id not in (value.get_attribute("aria-describedby") or "")
    assert form.locator("[data-in-place-refusal]").count() == 0
    assert "resolve=edit" in posts[0] and "note=Typo" in posts[0]
    assert "value=317+555+0198" in posts[0] and "status=open" in posts[0]
    value.fill(EDITED_VALUE)
    scrolled = centre(page, save)
    save.click()
    has_text(page.locator("#census-notice"), EDITED)
    has_text(
        page.get_by_role("status").filter(has_text="now has an edited value"), EDITED
    )
    assert page.evaluate("window.scrollY") == scrolled
    # The redrawn row shows the edit beside the Family's answer, and its
    # Edit value starts from the edit, with Clear edit available as drawn.
    first = page.locator("#table tbody tr").first
    has_text(first.locator("td").nth(5), "555-0000")
    has_text(first.locator("td").nth(6), EDITED_VALUE)
    review = page.locator("details.census-record").first
    if not review.evaluate("node => node.open"):
        review.locator("summary").click()
    form = review.locator("form.census-edit")
    assert form.get_by_label("Value to publish").input_value() == EDITED_VALUE
    assert form.get_by_role("button", name="Save value").is_disabled()
    clear = form.get_by_role("button", name="Clear edit")
    assert clear.is_enabled()
    with page.expect_request(lambda request: request.method == "POST"):
        clear.click()
    has_text(page.locator("#census-notice"), EDITED)
    assert "resolve=clear_edit" in posts[-1] and len(posts) == 3
    assert page.evaluate(MARKED) == "kept"
    assert errors == []


def test_a_redrawn_table_holds_save_value(page, component_origin):
    """The table redrawn in place at another page size, sort or filter draws
    each row's Edit value anew at the same version, carrying the new view
    settings as hidden fields: they are no change, so Save value stays
    unavailable until the value itself changes (#1334 re-review)."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + AUTOMATIC)
    page.evaluate(MARK)
    answers = iter(AUTOMATIC_REDRAWS)
    fulfil_post_with(page, "**" + AUTOMATIC, component_origin, lambda _: next(answers))
    form = page.locator("#edit-0")
    save = form.get_by_role("button", name="Save value", include_hidden=True)
    expect(save).to_be_disabled()
    for _path in AUTOMATIC_REDRAWS:
        page.evaluate("document.getElementById('edit-0').dataset.drawn = 'old'")
        page.locator("#table thead button").first.click()
        # The row's form is a new element, drawn with the new view settings.
        expect(form).not_to_have_attribute("data-drawn", "old")
        expect(save).to_be_disabled()
    assert form.locator('input[type=hidden][name="route"]').input_value() == "automatic"
    # The value itself still counts, and undoing it holds Save again.
    page.locator("details.census-record").filter(has=form).locator("summary").click()
    value = form.get_by_label("Value to publish")
    drawn = value.input_value()
    value.fill("317 555 0198")
    expect(save).to_be_enabled()
    value.fill(drawn)
    expect(save).to_be_disabled()
    assert page.evaluate(MARKED) == "kept"
    assert errors == []
