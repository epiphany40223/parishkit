"""Create the campaign's "Families in this campaign" section (#1393).

The page is ``campaign_create_components.FAMILIES``: the real creation
page with the section over a synthetic snapshot. Its count preview's post
is answered here from the same facts (``families_answer``), so the line
shows what the server would count. The server's rule, counts, refusals and
storage are tested in ``database/test_campaign_create_postgresql.py``.
"""

import json

import pytest

from .campaign_create_components import (
    FAMILIES,
    FAMILIES_PREVIEW,
    families_answer,
    posted_fields,
)
from .test_campaign_create import fill, watch
from .test_in_place import MARKED
from .waits import available, eventually, greyed, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
LINE = "[data-families-preview]"
TOP = "node => node.getBoundingClientRect().top + window.scrollY"


def opened(page, origin):
    """The page with its count preview answered; returns the posts seen."""
    posts = []

    def answer(route):
        """Count the posted choice as the view does."""
        posted = posted_fields(route.request)
        posts.append(posted)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(families_answer(posted)),
        )

    page.route(f"**{FAMILIES_PREVIEW}", answer)
    page.goto(origin + FAMILIES)
    return posts


def line_text(page):
    """The count preview's words as shown."""
    return " ".join(page.locator(LINE).inner_text().split())


def test_basic_opens_on_active_parishioners_with_its_count(page, component_origin):
    """The section opens in Basic, Active parishioners chosen, its count
    from the page itself; choosing the Staff preset recounts in place."""
    posts = opened(page, component_origin)
    failures = watch(page)
    assert page.get_by_label("Active parishioners", exact=True).is_checked()
    assert line_text(page).startswith("3 Families match in the ParishSoft data as of")
    assert line_text(page).endswith("; 2 of them can be emailed.")
    toggle = page.locator("#families-mode")
    top = toggle.evaluate(TOP)
    page.get_by_label("Active parishioners and Staff").check()
    eventually(
        page,
        "document.querySelector('[data-families-count]').textContent",
        "4 Families match in the ParishSoft data as of",
    )
    assert posts[-1]["families_preset"] == ["active_parishioners_and_staff"]
    assert abs(toggle.evaluate(TOP) - top) <= 1
    assert page.evaluate(MARKED)
    assert not failures


def test_advanced_holds_the_preset_and_counts_every_change(page, component_origin):
    """Basic → Advanced is lossless (registered Required, the preset's
    groups, Active); an Advanced change recounts; nothing above moves."""
    opened(page, component_origin)
    failures = watch(page)
    line = page.locator(LINE)
    page.locator("#families-mode-advanced").check()
    visible(page.get_by_label("Inactive (1 Family)"))
    # Measured in Advanced: the shown panel itself is above the line.
    top = line.evaluate(TOP)
    assert page.locator("#families-registered_0").is_checked()
    assert page.get_by_label("Active (3 Families)").is_checked()
    assert not page.get_by_label("Staff (1 Family)").is_checked()
    assert page.get_by_label("Active", exact=True).is_checked()
    page.get_by_label("(No Family Group) (1 Family)").check()
    eventually(
        page,
        "document.querySelector('[data-families-count]').textContent",
        "4 Families match in the ParishSoft data as of",
    )
    assert abs(line.evaluate(TOP) - top) <= 1
    assert not failures


def test_no_match_holds_create_with_the_reason(page, component_origin):
    """While nothing matches, Create the campaign is unavailable and the
    hint says why; a choice that matches again releases it."""
    opened(page, component_origin)
    fill(page)
    create = page.get_by_role("button", name="Create the campaign")
    available(create)
    page.locator("#families-mode-advanced").check()
    page.get_by_label("Active", exact=True).uncheck()
    page.get_by_label("Unknown").check()
    page.get_by_label("Active (3 Families)").uncheck()
    page.get_by_label("Staff (1 Family)").check()
    eventually(
        page,
        "document.querySelector('[data-families-count]').textContent",
        "No Families in the latest ParishSoft data match this choice.",
    )
    greyed(create)
    visible(
        page.locator("#settings-complete-hint").get_by_text(
            "No Families in the latest ParishSoft data match this choice."
        )
    )
    hidden(page.locator("[data-families-as-of]"))
    page.get_by_label("Inactive (1 Family)").check()
    eventually(
        page,
        "document.querySelector('[data-families-count]').textContent",
        "1 Family matches in the ParishSoft data as of",
    )
    available(create)


def test_advanced_without_a_basic_equivalent_asks_first(page, component_origin):
    """Advanced → Basic with a custom choice opens the shared dialog; Keep
    Advanced changes nothing."""
    opened(page, component_origin)
    page.locator("#families-mode-advanced").check()
    page.get_by_label("Inactive (1 Family)").check()
    # A click, not check(): the toggle stays on Advanced while the dialog
    # asks, which check() would report as a click that changed nothing.
    page.locator("#families-mode-basic").click()
    dialog = page.locator("#families-mode-no-basic")
    visible(dialog)
    dialog.get_by_role("button", name="Keep Advanced").click()
    hidden(dialog)
    assert page.locator("#families-mode-advanced").is_checked()
    assert page.get_by_label("Inactive (1 Family)").is_checked()


def test_an_incomplete_choice_holds_create_with_what_it_lacks(page, component_origin):
    """With no Family group ticked, the hint says what the status line says
    (a group is missing), never that no Families match (#2135 review)."""
    opened(page, component_origin)
    fill(page)
    create = page.get_by_role("button", name="Create the campaign")
    available(create)
    page.locator("#families-mode-advanced").check()
    page.get_by_label("Active (3 Families)").uncheck()
    eventually(
        page,
        "document.querySelector('[data-families-count]').textContent",
        "Choose at least one Family group.",
    )
    greyed(create)
    hint = page.locator("#settings-complete-hint")
    visible(hint.get_by_text("Choose at least one Family group."))
    assert "No Families" not in hint.inner_text()


def test_a_no_match_hold_still_lets_a_preset_choice_go_back_to_basic(
    page, component_origin
):
    """While a preset's own choice matches nothing (the preview says so),
    Advanced → Basic still finds the preset: the hold is not an Advanced
    field error, so no "no basic equivalent" dialog opens."""
    nothing = "No Families in the latest ParishSoft data match this choice."
    page.route(
        f"**{FAMILIES_PREVIEW}",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"count": nothing, "emailable": "", "matches": 0}),
        ),
    )
    page.goto(component_origin + FAMILIES)
    fill(page)
    page.locator("#families-mode-advanced").check()
    eventually(
        page, "document.querySelector('[data-families-count]').textContent", nothing
    )
    greyed(page.get_by_role("button", name="Create the campaign"))
    page.locator("#families-mode-basic").check()
    hidden(page.locator("#families-mode-no-basic"))
    assert page.locator("#families-mode-basic").is_checked()
    assert page.get_by_label("Active parishioners", exact=True).is_checked()
    visible(page.get_by_label("Active parishioners and Staff"))
