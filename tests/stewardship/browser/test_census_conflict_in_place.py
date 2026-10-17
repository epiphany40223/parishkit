"""Census changes' Use the Family's value anyway (#528, ADM-09.03), in place.

In Chromium and WebKit. The real page is served by ``census_components``.
On an Automatic Conflict, the row offers a Use the Family's value anyway
icon (and no Approve), whose dialog says what it does (#879). It posts
with the dialog's note and answers with the page, where the row is
approved and To do and the icon is gone. A mark outside every region
shows the page never reloaded, and the scroll position holds.
"""

import pytest

from .census_components import CONFLICT, CONFLICT_PAGE, CONFLICT_RESOLVED
from .test_census_edit_in_place import centre
from .test_table_sorting import MARK, MARKED
from .waits import has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

HELP = (
    "ParishSoft changed after the Family answered. Use the Family's value "
    "anyway approves this change: publication writes the Family's answer over "
    "the value in ParishSoft now, but only while ParishSoft still has that "
    "value. If ParishSoft changes again first, the approval is withdrawn and "
    "the change needs review again. Ignore keeps ParishSoft's value."
)


def test_use_the_familys_value_anyway_answers_in_place(page, component_origin):
    """The Conflict row's Review offers the resolution with its help line; the
    answer is the page, with the notice and the row To do, and no reload."""
    from playwright.sync_api import expect

    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + CONFLICT)
    page.evaluate(MARK)
    posts = []

    def answer(route):
        """Answer the resolution's post with the page after it."""
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.post_data)
        route.fulfill(
            response=route.fetch(url=component_origin + CONFLICT_PAGE, method="GET")
        )

    page.route("**" + CONFLICT, answer)
    page.set_viewport_size({"width": 1280, "height": 500})
    first = page.locator("#table tbody tr").first
    expect(first).to_contain_text("Conflict")
    name = "Use the Family's value anyway for Mobile phone for Member 00"
    use = first.get_by_role("button", name=name)
    assert (
        first.get_by_role("button", name="Approve Mobile phone for Member 00").count()
        == 0
    )
    assert first.get_by_role(
        "button", name="Ignore Mobile phone for Member 00"
    ).is_enabled()
    scrolled = centre(page, use)
    use.click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), f"{name}?")
    # The dialog's body, which describes it, says what it does.
    body = dialog.locator("[data-confirm-body]")
    has_text(body, HELP)
    assert dialog.get_attribute("aria-describedby") == body.get_attribute("id")
    dialog.get_by_label("Note (optional)").fill("Asked the Family")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), CONFLICT_RESOLVED)
    assert abs(page.evaluate("window.scrollY") - scrolled) <= 1
    assert "resolve=use_family" in posts[0] and "note=Asked+the+Family" in posts[0]
    assert "value=" not in posts[0] and len(posts) == 1
    first = page.locator("#table tbody tr").first
    expect(first).to_contain_text("To do")
    assert first.get_by_role("button", name=name).count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert errors == []
