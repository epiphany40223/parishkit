"""New Family/Member answers in real browsers (issue #247).

Welcome "cannot attend", Member talents, "cannot participate" (locks every
Ministry) and financial "cannot contribute" (hides the pledge fields).
"""

import pytest

from parishkit.stewardship.responses.service import DEFAULT_TALENTS

from .test_family_financial import financial_form
from .test_family_ministry import begin, ministry_form
from .test_family_response import expect, review, show

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAINTER, OTHER = DEFAULT_TALENTS[0].id, DEFAULT_TALENTS[-1].id
ATTEND = (
    "Because of physical limitations, I/we cannot attend Mass or prayer services "
    "at this time."
)
SERVE = (
    "Because of physical limitations, I/we cannot participate in any ministries "
    "at this time."
)
GIVE = (
    "Because of financial limitations, I/we cannot contribute financially at this time."
)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(v => v.id)"""


def service_form(**kwargs):
    """The Ministry fixture plus the talents the server now offers."""
    form = ministry_form(**kwargs)
    form["cannot_attend"] = False
    form["service"] = {
        "talent_options": [
            {"id": option.id, "label": option.label, "free_text": option.free_text}
            for option in DEFAULT_TALENTS
        ],
        "talent_text_limit": 200,
        "members": {"3": {"cannot_serve": False, "talents": {}}},
        "proposed_members": {},
    }
    return form


def recorder(submissions):
    """Record the final answers, then accept them."""

    def submit(route):
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    return submit


@pytest.mark.parametrize("width", [390, 1280])
def test_talents_and_ministry_lock_restore_and_submit(
    page, component_origin, axe_source, width
):
    """Checking the lock stops every Ministry; unchecking restores the choices."""
    page.set_viewport_size({"width": width, "height": 900})
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, service_form(), recorder(submissions))
    show(page, page.get_by_label(ATTEND)).check()
    show(page, page.get_by_label("Painter")).check()
    page.get_by_label("Other", exact=True).check()
    page.get_by_label("Please describe your talent").fill(" Organ ")
    # The Family's own choice before the lock: join Food pantry.
    page.get_by_text("Click here to join another ministry", exact=True).click()
    page.get_by_label("Search ministries").fill("pantry")
    page.get_by_role("checkbox", name="Food pantry", exact=True).check()
    page.get_by_label(SERVE).check()
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    expect(choir.get_by_label("Stop participating")).to_be_checked()
    expect(choir.get_by_label("Stop participating")).to_be_disabled()
    expect(page.locator(".ministry-choices")).to_have_attribute("inert", "")
    expect(page.get_by_text("Joining: Food pantry", exact=True)).to_be_hidden()
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # Unchecking brings back exactly what the Family had chosen.
    page.get_by_label(SERVE).uncheck()
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    expect(choir.get_by_label("Continuing")).to_be_checked()
    expect(choir.get_by_label("Continuing")).to_be_enabled()
    expect(page.get_by_text("Joining: Food pantry", exact=True)).to_be_visible()
    page.get_by_label(SERVE).check()
    review(page)
    expect(page.get_by_text(ATTEND, exact=True)).to_be_visible()
    expect(
        page.get_by_text("Talents to share: Painter, Other: Organ", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    answer = submissions[0]
    assert answer["cannot_attend"] is True
    assert answer["ministries"]["members"]["3"] == {"join": [], "leave": [4]}
    assert answer["service"] == {
        "members": {
            "3": {"cannot_serve": True, "talents": {PAINTER: "", OTHER: " Organ "}}
        },
        "proposed_members": {},
    }
    assert not errors


def test_keyboard_reaches_every_new_control(page, component_origin):
    """Talents and the lock are ordinary, labeled, keyboard-operable checkboxes."""
    page.set_viewport_size({"width": 390, "height": 900})
    begin(page, component_origin, service_form(), recorder([]))
    lock = show(page, page.get_by_label(SERVE))
    lock.focus()
    page.keyboard.press("Space")
    expect(page.get_by_label(SERVE)).to_be_checked()
    expect(page.get_by_label(SERVE)).to_be_focused()


@pytest.mark.parametrize("width", [390, 1280])
def test_cannot_give_hides_pledge_fields_and_restores_them(
    page, component_origin, axe_source, width
):
    """The pledge, frequency and methods disappear and come back unchanged."""
    page.set_viewport_size({"width": width, "height": 900})
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, financial_form(), recorder(submissions))
    pledge = show(page, page.get_by_label("Annual pledge (USD)"))
    pledge.fill("120")
    page.get_by_label(GIVE).check()
    expect(page.get_by_label("Annual pledge (USD)")).to_be_hidden()
    expect(page.get_by_label("Pledge frequency (required)")).to_be_hidden()
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.get_by_label(GIVE).uncheck()
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("120")
    page.get_by_label(GIVE).check()
    assert not errors, errors
    review(page)
    expect(page.get_by_text(GIVE, exact=True)).to_be_visible()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[0]["financial"] == {
        "annual_pledge": "",
        "frequency": "",
        "shares": {},
        "cannot_give": True,
    }
