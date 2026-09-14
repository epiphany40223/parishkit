"""Real mobile Ministry controls and final-only payloads in all browser engines."""

from copy import deepcopy

import pytest

from .test_family_response import expect, form_payload, prepare

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def ministry_form(*, census=True):
    """Match the scoped HTTP contract without external parish or provider data."""
    form = form_payload()
    form["modules"] = ["census", "ministry"] if census else ["ministry"]
    form["ministries"] = {
        "options": [{"id": 4, "name": "Choir"}, {"id": 9, "name": "Food pantry"}],
        "members": {"3": {"current": [4], "join": [], "leave": []}},
        "proposed_members": {},
    }
    if not census:
        form["household"] = None
        form["family"] = {"mailingName": "Sample Family"}
        form["members"][0].update(fields=[], display_name="Alex Example")
        form["new_member_fields"] = []
        form["max_proposed_members"] = 0
    return form


def begin(page, origin, form, submit):
    """Override only the complete authorized form, preserving actual browser JS."""
    prepare(page, origin, submit=submit)
    page.route("**/family/form", lambda route: route.fulfill(json={"form": form}))
    page.get_by_role("button", name="Begin reviewing").click()


@pytest.mark.parametrize("census", [True, False])
def test_mobile_ministry_edit_review_and_definitive_submit(
    page, component_origin, axe_source, census
):
    """Search is on-demand, current roster cannot be joined, and no draft is sent."""
    page.set_viewport_size({"width": 320, "height": 900})
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def submit(route):
        """Record the final aggregate before acknowledging and clearing the tab."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, ministry_form(census=census), submit)
    expect(page.get_by_text("Ministry participation", exact=True)).to_be_visible()
    if not census:
        assert page.get_by_label("First name (required)").count() == 0
        assert page.get_by_label("Household status").count() == 0
        assert page.get_by_role("button", name="Add a household member").count() == 0
        assert "Envelope number" not in page.locator("main").inner_text()
    assert page.get_by_label("Search Ministries").count() == 0
    page.get_by_label("Choir — wishes to stop participating").check()
    page.get_by_text("Join another Ministry", exact=True).click()
    page.get_by_label("Search Ministries").fill("pantry")
    page.get_by_label("Food pantry — interested in joining").check()
    assert page.get_by_label("Choir — interested in joining").count() == 0
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(v => v.id)""")
        == []
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_text("Interested in joining: Food pantry", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Back to edit").click()
    expect(page.get_by_label("Choir — wishes to stop participating")).to_be_checked()
    assert not submissions
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert submissions[0]["ministries"] == {
        "members": {"3": {"join": [9], "leave": [4]}},
        "proposed_members": {},
    }
    if not census:
        assert submissions[0]["family"] == {}
        assert submissions[0]["members"] == {"3": {}}
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert not errors


@pytest.mark.parametrize("withdrawing", [False, True])
def test_stale_hidden_choice_requires_explicit_discard_without_hidden_label(
    page, component_origin, withdrawing
):
    """A removed selection cannot be resubmitted or silently dropped after refresh."""
    form = ministry_form(census=False)
    if withdrawing:
        form["ministries"]["members"]["3"]["join"] = [9]
    fresh = deepcopy(form)
    fresh["ministries"]["options"] = [{"id": 4, "name": "Choir"}]
    fresh["ministries"]["members"]["3"]["join"] = []
    submissions = []

    def submit(route):
        """First Submit refreshes; the second receives the new complete aggregate."""
        submissions.append(route.request.post_data_json["answers"])
        if len(submissions) == 1:
            route.fulfill(status=409, json={"error": "review_required", "form": fresh})
        else:
            route.fulfill(json={"accepted": True})

    begin(page, component_origin, form, submit)
    page.get_by_text("Join another Ministry", exact=True).click()
    page.get_by_label("Food pantry — interested in joining").set_checked(
        not withdrawing
    )
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        page.get_by_role(
            "button", name="Discard unavailable choices and use the current list"
        )
    ).to_be_visible()
    assert "Food pantry" not in page.locator("main").inner_text()
    page.get_by_role("button", name="Review response").click()
    assert len(submissions) == 1
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    page.get_by_role(
        "button", name="Discard unavailable choices and use the current list"
    ).click()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert submissions[1]["ministries"]["members"]["3"] == {"join": [], "leave": []}


def test_terminal_choice_omits_all_in_step_ministry_edits(page, component_origin):
    """Terminal confirmation disables Ministry controls and excludes their payload."""
    submissions = []

    def submit(route):
        """Capture only the definitive aggregate sent by the actual component."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, ministry_form(), submit)
    page.get_by_text("Join another Ministry", exact=True).click()
    page.get_by_label("Food pantry — interested in joining").check()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Household status").select_option("moved_household")
    assert page.get_by_text("Ministry participation", exact=True).count() == 0
    page.get_by_role("button", name="Review response").click()
    assert "Food pantry" not in page.locator("main").inner_text()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submissions[0]["ministries"] == {"members": {}, "proposed_members": {}}


def test_proposed_member_can_select_ministry_without_source_identity(
    page, component_origin
):
    """New local household UUID and Ministry intent travel in the same final answer."""
    from .test_member_requests import fill_new

    submissions = []

    def submit(route):
        """Capture local identity without invoking any upstream Member creation."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, ministry_form(), submit)
    identifier = fill_new(page)
    page.get_by_text("Join another Ministry", exact=True).last.click()
    page.locator(f"#ministry-{identifier}-join-9").check()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submissions[0]["ministries"]["proposed_members"] == {
        identifier: {"join": [9]}
    }
    assert identifier in submissions[0]["proposed_members"]
