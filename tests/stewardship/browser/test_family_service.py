"""New Family/Member answers in real browsers (issue #247).

Welcome "cannot attend", Member talents, "cannot participate" (locks every
Ministry) and financial "cannot contribute" (hides the pledge fields).
"""

from copy import deepcopy

import pytest

from parishkit.stewardship.responses.service import DEFAULT_TALENTS

from ..test_financial_answers import CHECK
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
    # Talents come last on the Member's page, below the ministry updates,
    # in a panel styled like Ministry participation.
    talents = page.locator(".talents-panel")
    ministries = page.locator(".ministry-panel")
    assert talents.evaluate(
        "(t, m) => Boolean(m.compareDocumentPosition(t) &"
        " Node.DOCUMENT_POSITION_FOLLOWING)",
        ministries.element_handle(),
    )
    expect(talents.get_by_role("heading", level=4)).to_have_text("Talents to share")
    heading_style = (
        "e => [getComputedStyle(e).fontSize, getComputedStyle(e).fontWeight]"
    )
    assert talents.locator("h4").evaluate(heading_style) == ministries.locator(
        "h4"
    ).evaluate(heading_style)
    # The Family's own choice before the lock: join Food pantry.
    page.get_by_text("Click here to join more ministries", exact=True).click()
    page.get_by_label("Search ministries").fill("pantry")
    page.get_by_role("checkbox", name="Food pantry", exact=True).check()
    page.get_by_label(SERVE).check()
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    expect(choir.get_by_label("Stop participating in this ministry")).to_be_checked()
    expect(choir.get_by_label("Stop participating in this ministry")).to_be_disabled()
    # Readable, not inert: rows are disabled fieldsets and the join
    # disclosure is marked disabled, out of the tab order and cannot open.
    assert page.locator(".ministry-choices[inert]").count() == 0
    summary = page.get_by_text("Click here to join more ministries", exact=True)
    expect(summary).to_have_attribute("aria-disabled", "true")
    expect(summary).to_have_attribute("tabindex", "-1")
    summary.click()
    expect(page.locator(".ministry-join")).not_to_have_attribute("open", "")
    # The rebuilt checkbox (focus returns to it) is described by the note.
    note = page.get_by_text("Every current ministry will stop", exact=False)
    expect(page.get_by_label(SERVE)).to_have_attribute(
        "aria-describedby", note.get_attribute("id")
    )
    expect(page.get_by_label(SERVE)).to_have_accessible_description(note.inner_text())
    expect(
        page.locator(".ministry-joining li", has_text="Food pantry").first
    ).to_be_hidden()
    # The talents question is hidden while the Member cannot participate.
    expect(page.locator(".talents-panel")).to_have_count(0)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # Unchecking brings back exactly what the Family had chosen.
    page.get_by_label(SERVE).uncheck()
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    expect(choir.get_by_label("Continue in this ministry")).to_be_checked()
    expect(choir.get_by_label("Continue in this ministry")).to_be_enabled()
    expect(
        page.locator(".ministry-joining li", has_text="Food pantry").first
    ).to_be_visible()
    # ...and the talents the Family had chosen.
    expect(page.get_by_label("Painter")).to_be_checked()
    expect(page.get_by_label("Please describe your talent")).to_have_value(" Organ ")
    # The visible talents panel is accessible too.
    assert page.evaluate(AXE) == []
    page.get_by_label(SERVE).check()
    review(page)
    expect(page.get_by_text(ATTEND, exact=True)).to_be_visible()
    assert page.get_by_text("Talents to share", exact=False).count() == 0
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    answer = submissions[0]
    assert answer["cannot_attend"] is True
    assert answer["ministries"]["members"]["3"] == {"join": [], "leave": [4]}
    assert answer["service"] == {
        "members": {"3": {"cannot_serve": True, "talents": {}}},
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


def refreshing(submissions, fresh):
    """First Submit returns a refreshed form; the next one is accepted."""

    def submit(route):
        submissions.append(route.request.post_data_json["answers"])
        if len(submissions) == 1:
            route.fulfill(status=409, json={"error": "review_required", "form": fresh})
        else:
            route.fulfill(json={"accepted": True})

    return submit


def test_refresh_keeps_set_aside_choices_for_unchecking(page, component_origin):
    """After a refresh, unchecking still restores this tab's own join."""
    form, submissions = service_form(), []
    begin(page, component_origin, form, refreshing(submissions, deepcopy(form)))
    show(page, page.get_by_text("Click here to join more ministries", exact=True))
    page.get_by_text("Click here to join more ministries", exact=True).click()
    page.get_by_label("Search ministries").fill("pantry")
    page.get_by_role("checkbox", name="Food pantry", exact=True).check()
    show(page, page.get_by_label("Painter")).check()
    page.get_by_label(SERVE).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(SERVE))).to_be_checked()
    page.get_by_label(SERVE).uncheck()
    expect(
        page.locator(".ministry-joining li", has_text="Food pantry").first
    ).to_be_visible()
    # The talents set aside by the lock come back after the refresh too.
    expect(page.get_by_label("Painter")).to_be_checked()


def test_refresh_locks_a_limitation_set_in_another_tab(page, component_origin):
    """Another tab's "cannot participate" wins over this tab's pending join."""
    form, submissions = service_form(), []
    fresh = deepcopy(form)
    fresh["ministries"]["members"]["3"] = {"current": [4], "join": [], "leave": [4]}
    fresh["service"]["members"]["3"] = {
        "cannot_serve": True,
        "talents": {OTHER: "Organ"},
    }
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Painter")).check()
    page.get_by_text("Click here to join more ministries", exact=True).click()
    page.get_by_label("Search ministries").fill("pantry")
    page.get_by_role("checkbox", name="Food pantry", exact=True).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(SERVE))).to_be_checked()
    # Hidden while locked; unchecking shows the talents merged one by one:
    # this tab's Painter and the other tab's Other.
    expect(page.locator(".talents-panel")).to_have_count(0)
    page.get_by_label(SERVE).uncheck()
    expect(page.get_by_label("Painter")).to_be_checked()
    expect(page.get_by_label("Please describe your talent")).to_have_value("Organ")
    page.get_by_label(SERVE).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["ministries"]["members"]["3"] == {"join": [], "leave": [4]}
    assert submissions[1]["service"]["members"]["3"] == {
        "cannot_serve": True,
        "talents": {},
    }


def test_refresh_keeps_a_hidden_pledge_for_unchecking(page, component_origin):
    """A pledge hidden by "cannot contribute" comes back after a refresh."""
    form, submissions = financial_form(), []
    begin(page, component_origin, form, refreshing(submissions, deepcopy(form)))
    show(page, page.get_by_label("Annual pledge (USD)")).fill("120")
    page.get_by_label(GIVE).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(GIVE))).to_be_checked()
    page.get_by_label(GIVE).uncheck()
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("120")


def test_refresh_drops_a_note_when_its_option_stops_taking_text(page, component_origin):
    """A local Other note is not resubmitted once Other takes no text."""
    form, submissions = service_form(), []
    fresh = deepcopy(form)
    fresh["service"]["talent_options"][-1]["free_text"] = False
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Other", exact=True)).check()
    page.get_by_label("Please describe your talent").fill("Organ")
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label("Other", exact=True))).to_be_checked()
    assert page.get_by_label("Please describe your talent").count() == 0
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["service"]["members"]["3"]["talents"] == {OTHER: ""}


def test_loaded_limitation_hides_talents_and_restores_them(page, component_origin):
    """A saved "cannot participate" hides saved talents until it's unchecked."""
    form, submissions = service_form(), []
    form["ministries"]["members"]["3"] = {"current": [4], "join": [], "leave": [4]}
    form["service"]["members"]["3"] = {"cannot_serve": True, "talents": {PAINTER: ""}}
    begin(page, component_origin, form, recorder(submissions))
    expect(show(page, page.get_by_label(SERVE))).to_be_checked()
    expect(page.locator(".talents-panel")).to_have_count(0)
    page.get_by_label(SERVE).uncheck()
    expect(page.get_by_label("Painter")).to_be_checked()
    page.get_by_label(SERVE).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[0]["service"]["members"]["3"] == {
        "cannot_serve": True,
        "talents": {},
    }


def test_refresh_keeps_another_tabs_talents_under_this_tabs_lock(
    page, component_origin
):
    """A lock's set-aside only reapplies this tab's own changes after refresh."""
    form, submissions = service_form(), []
    form["service"]["members"]["3"]["talents"] = {PAINTER: ""}
    fresh = deepcopy(form)
    # Another tab removed Painter and added Other ("Organ").
    fresh["service"]["members"]["3"]["talents"] = {OTHER: "Organ"}
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Florist")).check()
    page.get_by_label(SERVE).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(SERVE))).to_be_checked()
    page.get_by_label(SERVE).uncheck()
    # This tab's Florist, and the other tab's removal and Other, all kept.
    expect(page.get_by_label("Florist")).to_be_checked()
    expect(page.get_by_label("Painter")).not_to_be_checked()
    expect(page.get_by_label("Please describe your talent")).to_have_value("Organ")


def test_refresh_locked_elsewhere_restores_this_tabs_own_choices(
    page, component_origin
):
    """A refreshed form locked by another tab stores the lock's choices; this
    tab's set-aside must come back unchanged when the Family unchecks."""
    form, submissions = service_form(), []
    fresh = deepcopy(form)
    fresh["ministries"]["members"]["3"] = {"current": [4], "join": [], "leave": [4]}
    fresh["service"]["members"]["3"] = {"cannot_serve": True, "talents": {}}
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Painter")).check()
    page.get_by_text("Click here to join more ministries", exact=True).click()
    page.get_by_label("Search ministries").fill("pantry")
    page.get_by_role("checkbox", name="Food pantry", exact=True).check()
    page.get_by_label(SERVE).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(SERVE))).to_be_checked()
    page.get_by_label(SERVE).uncheck()
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    expect(choir.get_by_label("Continue in this ministry")).to_be_checked()
    expect(page.get_by_label("Painter")).to_be_checked()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["ministries"]["members"]["3"] == {"join": [9], "leave": []}
    assert submissions[1]["service"]["members"]["3"] == {
        "cannot_serve": False,
        "talents": {PAINTER: ""},
    }


def test_refresh_unlocked_elsewhere_drops_the_stale_set_aside(page, component_origin):
    """If another tab unlocks the Member, a later lock captures current talents."""
    florist = DEFAULT_TALENTS[1].id
    form, submissions = service_form(), []
    form["ministries"]["members"]["3"] = {"current": [4], "join": [], "leave": [4]}
    form["service"]["members"]["3"] = {"cannot_serve": True, "talents": {PAINTER: ""}}
    fresh = deepcopy(form)
    fresh["ministries"]["members"]["3"] = {"current": [4], "join": [], "leave": []}
    fresh["service"]["members"]["3"] = {"cannot_serve": False, "talents": {florist: ""}}
    begin(page, component_origin, form, refreshing(submissions, fresh))
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(SERVE))).not_to_be_checked()
    expect(page.get_by_label("Florist")).to_be_checked()
    expect(page.get_by_label("Painter")).not_to_be_checked()
    page.get_by_label(SERVE).check()
    page.get_by_label(SERVE).uncheck()
    expect(page.get_by_label("Florist")).to_be_checked()
    expect(page.get_by_label("Painter")).not_to_be_checked()


@pytest.mark.parametrize("width", [390, 1280])
def test_emptied_talent_list_shows_no_talents_anywhere(
    page, component_origin, axe_source, width
):
    """A campaign whose talent list was emptied shows no Talents panel or line.

    "Cannot participate" still appears and works, and each Member's answer
    is sent with no talents, which the server accepts.
    """
    page.set_viewport_size({"width": width, "height": 900})
    form, submissions, errors = service_form(), [], []
    form["service"]["talent_options"] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, form, recorder(submissions))
    lock = show(page, page.get_by_label(SERVE))
    expect(page.locator(".talents-panel")).to_have_count(0)
    assert page.get_by_text("Talents to share", exact=False).count() == 0
    assert page.get_by_text("special talent", exact=False).count() == 0
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    # Toggling the separate limitation never brings an empty panel back.
    lock.check()
    lock.uncheck()
    expect(page.locator(".talents-panel")).to_have_count(0)
    review(page)
    assert page.get_by_text("Talents to share", exact=False).count() == 0
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[0]["service"] == {
        "members": {"3": {"cannot_serve": False, "talents": {}}},
        "proposed_members": {},
    }
    assert not errors, errors


def test_set_aside_talents_no_longer_offered_are_not_restored(page, component_origin):
    """Unchecking the lock restores only talents the refreshed form still offers.

    This tab sets Painter aside under the lock; another tab saves the Member
    locked and the parish empties the list. Painter must not come back with
    no checkbox left to remove it (the server would refuse it).
    """
    form, submissions = service_form(), []
    fresh = deepcopy(form)
    fresh["ministries"]["members"]["3"] = {"current": [4], "join": [], "leave": [4]}
    fresh["service"]["members"]["3"] = {"cannot_serve": True, "talents": {}}
    fresh["service"]["talent_options"] = []
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Painter")).check()
    page.get_by_label(SERVE).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(SERVE))).to_be_checked()
    page.get_by_label(SERVE).uncheck()
    expect(page.locator(".talents-panel")).to_have_count(0)
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["service"]["members"]["3"] == {
        "cannot_serve": False,
        "talents": {},
    }


def test_list_emptied_mid_session_removes_the_panel(page, component_origin):
    """A refresh after the parish empties the list drops the panel and talents."""
    form, submissions = service_form(), []
    fresh = deepcopy(form)
    fresh["service"]["talent_options"] = []
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Painter")).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(SERVE))).to_be_visible()
    expect(page.locator(".talents-panel")).to_have_count(0)
    review(page)
    assert page.get_by_text("Talents to share", exact=False).count() == 0
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["service"]["members"]["3"] == {
        "cannot_serve": False,
        "talents": {},
    }


def test_a_pledge_conflict_hidden_by_cannot_give_does_not_block_review(
    page, component_origin
):
    """#384 M1: a changed pledge behind "cannot contribute" needs no choice.

    This tab says the Family cannot contribute while another device changed
    the pledge. The refresh records a pledge conflict inside the pledge
    fields "cannot contribute" hides; Review must not stop on (and focus) a
    choice the Family cannot see, and the hidden pledge is not sent.
    """
    form = financial_form()
    form["financial"]["answers"] = {
        "annual_pledge": "12.00",
        "frequency": "annual",
        "shares": {},
    }
    fresh = deepcopy(form)
    fresh["financial"]["answers"]["annual_pledge"] = "30.00"
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label(GIVE)).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    # The refreshed form keeps this tab's "cannot contribute".
    expect(show(page, page.get_by_label(GIVE))).to_be_checked()
    expect(page.get_by_label("Annual pledge (USD)")).to_be_hidden()
    assert review(page)
    expect(page.locator("[data-nav-error]")).to_be_hidden()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["financial"] == {
        "annual_pledge": "",
        "frequency": "",
        "shares": {},
        "cannot_give": True,
    }
    assert not errors, errors


@pytest.mark.parametrize(
    "path",
    ["members.3", "ministries.members.3.join", "service.members.3.talents"],
)
def test_a_members_section_error_names_the_member_and_links_to_them(
    page, component_origin, path
):
    """#384 L2: an error with no single field points at that Member's section."""
    begin(
        page,
        component_origin,
        service_form(),
        lambda route: route.fulfill(
            status=422,
            json={"error": "validation", "fields": {path: "Review this person."}},
        ),
    )
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    link = page.locator('#family-flow-message a[href="#member-section-3"]')
    expect(link).to_be_visible()
    text = link.text_content()
    assert text.endswith(": Review this person.") and not text.startswith(
        ("Field", "Household member")
    ), text
    # The section itself carries the message, so focusing it reads the error.
    expect(page.locator("#member-section-3-error")).to_have_text("Review this person.")
    expect(page.locator("#member-section-3")).to_have_attribute(
        "aria-describedby", "member-section-3-error"
    )
    link.click()
    expect(page.locator("#member-section-3")).to_be_focused()
    # A change in the section is the correction: the line and its
    # description go.
    page.get_by_label("Painter").check()
    expect(page.locator("#member-section-3-error")).to_have_count(0)
    expect(page.locator("#member-section-3")).not_to_have_attribute(
        "aria-describedby", "member-section-3-error"
    )


def pledged(annual_pledge="12.00", frequency="annual"):
    """The financial form with a recorded pledge, and a copy for the refresh."""
    form = financial_form()
    form["financial"]["answers"] = {
        "annual_pledge": annual_pledge,
        "frequency": frequency,
        # A positive pledge needs a way to give.
        "shares": {CHECK: ""},
    }
    return form, deepcopy(form)


def test_a_hidden_zero_pledge_frequency_conflict_does_not_block_review(
    page, component_origin
):
    """#384 M1: a frequency choice hidden by a zero pledge needs no choice."""
    form, fresh = pledged()
    fresh["financial"]["answers"]["frequency"] = "monthly"
    submissions = []
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label("Annual pledge (USD)"))).to_have_value("0")
    expect(page.get_by_label("Pledge frequency (required)")).to_be_hidden()
    assert review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["financial"]["annual_pledge"] == "0"
    assert submissions[1]["financial"]["frequency"] == ""


@pytest.mark.parametrize("edited", [False, True])
def test_unchecking_cannot_give_after_a_refresh_never_restores_a_stale_pledge(
    page, component_origin, edited
):
    """#784: the pledge set aside by "cannot contribute" meets the refresh.

    Recorded 12; another device changes it to 30; this tab checks "cannot
    contribute" (after changing the pledge to 20 itself, when ``edited``)
    and submits; the refresh comes back; the Family unchecks the box. An
    untouched pledge shows the refreshed 30; one this tab changed asks which
    to keep. 12 is never restored and sent.
    """
    form, fresh = pledged()
    fresh["financial"]["answers"]["annual_pledge"] = "30.00"
    submissions = []
    begin(page, component_origin, form, refreshing(submissions, fresh))
    if edited:
        show(page, page.get_by_label("Annual pledge (USD)")).fill("20.00")
    show(page, page.get_by_label(GIVE)).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(show(page, page.get_by_label(GIVE))).to_be_checked()
    page.get_by_label(GIVE).uncheck()
    pledge = page.get_by_label("Annual pledge (USD)")
    if edited:
        choice = page.get_by_role("radio", name="Use my edit", exact=False)
        expect(choice).to_be_visible()
        assert (
            not review(page)
            or page.get_by_role("button", name="Submit to Sample Parish").count() == 0
        )
        show(page, choice).check()
        expect(pledge).to_have_value("20.00")
    else:
        expect(pledge).to_have_value("30.00")
        expect(
            page.get_by_role("radio", name="Use my edit", exact=False)
        ).to_have_count(0)
    assert review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["financial"]["annual_pledge"] == (
        "20.00" if edited else "30.00"
    )


def refreshing_twice(submissions, *fresh):
    """Each Submit returns the next refreshed form; the last one is accepted."""

    def submit(route):
        submissions.append(route.request.post_data_json["answers"])
        if len(submissions) <= len(fresh):
            form = fresh[len(submissions) - 1]
            route.fulfill(status=409, json={"error": "review_required", "form": form})
        else:
            route.fulfill(json={"accepted": True})

    return submit


def submit_again(page):
    """Review and Submit, expecting the form to come back refreshed."""
    assert review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()


def finish(page, submissions):
    """Review, Submit and return the accepted financial answers."""
    assert review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    return submissions[-1]["financial"]


def choose_my_edit(page):
    """The changed-record choice is shown, blocks Review, and keeps this tab's."""
    choice = page.get_by_role("radio", name="Use my edit", exact=False).first
    expect(choice).to_be_visible()
    choice.check()


@pytest.mark.parametrize("hidden_first", [True, False])
def test_two_refreshes_never_send_a_hidden_stale_pledge(
    page, component_origin, hidden_first
):
    """#778 review: an open pledge choice survives a second refresh.

    Recorded 12; this tab types 20; another device sets 30. Either this tab
    checks "cannot contribute" before the first refresh (the choice is
    recorded hidden), or after it (a visible choice, then hidden). A second
    refresh follows; unchecking must still ask, never send 20 over 30.
    """
    form, fresh = pledged()
    fresh["financial"]["answers"]["annual_pledge"] = "30.00"
    submissions = []
    begin(
        page,
        component_origin,
        form,
        refreshing_twice(submissions, fresh, deepcopy(fresh)),
    )
    show(page, page.get_by_label("Annual pledge (USD)")).fill("20.00")
    if hidden_first:
        page.get_by_label(GIVE).check()
    submit_again(page)
    if not hidden_first:
        expect(
            show(page, page.get_by_role("radio", name="Use my edit", exact=False))
        ).to_be_visible()
        page.get_by_label(GIVE).check()
    submit_again(page)
    expect(show(page, page.get_by_label(GIVE))).to_be_checked()
    page.get_by_label(GIVE).uncheck()
    choose_my_edit(page)
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("20.00")
    assert finish(page, submissions)["annual_pledge"] == "20.00"


def test_cannot_give_set_on_another_device_asks_before_dropping_a_pledge(
    page, component_origin
):
    """#778 review: another device's "cannot contribute" is a choice here."""
    form, fresh = pledged()
    fresh["financial"]["answers"] = {
        "annual_pledge": "",
        "frequency": "",
        "shares": {},
        "cannot_give": True,
    }
    submissions = []
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Annual pledge (USD)")).fill("20.00")
    submit_again(page)
    expect(show(page, page.get_by_label(GIVE))).to_be_checked()
    # Review stops on the choice instead of silently dropping 20.
    review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    # The choice rebuilds the page, so click rather than wait on its state.
    show(page, page.get_by_label("Use my edit: my pledge")).click()
    expect(page.get_by_label(GIVE)).not_to_be_checked()
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("20.00")
    financial = finish(page, submissions)
    assert financial["annual_pledge"] == "20.00" and not financial["cannot_give"]


def test_a_zero_pledge_from_another_device_asks_before_dropping_a_frequency(
    page, component_origin
):
    """#778 review: another device's zero pledge does not hide this tab's edit."""
    form, fresh = pledged()
    fresh["financial"]["answers"] = {
        "annual_pledge": "0",
        "frequency": "",
        "shares": {},
    }
    submissions = []
    begin(page, component_origin, form, refreshing(submissions, fresh))
    show(page, page.get_by_label("Pledge frequency (required)")).select_option(
        "monthly"
    )
    submit_again(page)
    review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    # Keep this tab's pledge, then its frequency.
    choose_my_edit(page)
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("12.00")
    choose_my_edit(page)
    # The way to give was not this tab's edit, so it follows the record
    # (none); a positive pledge needs one again.
    show(page, page.locator(f"#financial-option-{CHECK}")).check()
    financial = finish(page, submissions)
    assert (financial["annual_pledge"], financial["frequency"]) == ("12.00", "monthly")
