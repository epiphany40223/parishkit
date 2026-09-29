"""Real mobile Ministry controls and final-only payloads in all browser engines."""

from copy import deepcopy

import pytest

from .test_family_response import expect, form_payload, prepare, review, show

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
    expect(
        show(page, page.get_by_text("Ministry participation", exact=True))
    ).to_be_visible()
    if not census:
        assert page.get_by_label("First name (required)").count() == 0
        assert page.get_by_label("Household status").count() == 0
        assert page.get_by_role("button", name="Add a household member").count() == 0
        assert "Envelope number" not in page.locator("main").inner_text()
    assert page.get_by_label("Search ministries").count() == 0
    show(
        page,
        page.get_by_role("group", name="Choir", include_hidden=True).get_by_label(
            "Stop participating in this ministry"
        ),
    ).check()
    show(
        page, page.get_by_text("Click here to join more ministries", exact=True)
    ).click()
    show(page, page.get_by_label("Search ministries")).fill("pantry")
    show(
        page,
        page.get_by_role(
            "checkbox", name="Food pantry", exact=True, include_hidden=True
        ),
    ).check()
    assert (
        page.get_by_role(
            "checkbox", name="Choir", exact=True, include_hidden=True
        ).count()
        == 0
    )
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(v => v.id)""")
        == []
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    review(page)
    expect(
        show(page, page.locator(".ministry-joining li", has_text="Food pantry").first)
    ).to_be_visible()
    page.get_by_role("button", name="Back to edit").click()
    expect(
        page.get_by_role("group", name="Choir", include_hidden=True).get_by_label(
            "Stop participating in this ministry"
        )
    ).to_be_checked()
    assert not submissions
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
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
    show(
        page, page.get_by_text("Click here to join more ministries", exact=True)
    ).click()
    show(
        page,
        page.get_by_role(
            "checkbox", name="Food pantry", exact=True, include_hidden=True
        ),
    ).set_checked(not withdrawing)
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(
            page,
            page.get_by_role(
                "button",
                name="Discard unavailable choices and use the current list",
                include_hidden=True,
            ),
        )
    ).to_be_visible()
    assert "Food pantry" not in page.locator("main").inner_text()
    review(page)
    assert len(submissions) == 1
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    show(
        page,
        page.get_by_role(
            "button",
            name="Discard unavailable choices and use the current list",
            include_hidden=True,
        ),
    ).click()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
    assert submissions[1]["ministries"]["members"]["3"] == {"join": [], "leave": []}


def test_terminal_choice_omits_all_in_step_ministry_edits(page, component_origin):
    """Terminal confirmation disables Ministry controls and excludes their payload."""
    submissions = []

    def submit(route):
        """Capture only the definitive aggregate sent by the actual component."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, ministry_form(), submit)
    show(
        page, page.get_by_text("Click here to join more ministries", exact=True)
    ).click()
    show(
        page,
        page.get_by_role(
            "checkbox", name="Food pantry", exact=True, include_hidden=True
        ),
    ).check()
    page.once("dialog", lambda dialog: dialog.accept())
    show(page, page.get_by_label("Household status")).select_option("moved_household")
    assert page.get_by_text("Ministry participation", exact=True).count() == 0
    review(page)
    assert "Food pantry" not in page.locator("main").inner_text()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
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
    show(
        page, page.get_by_text("Click here to join more ministries", exact=True).last
    ).click()
    show(page, page.locator(f"#ministry-{identifier}-join-9")).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[0]["ministries"]["proposed_members"] == {
        identifier: {"join": [9]}
    }
    assert identifier in submissions[0]["proposed_members"]


def test_stopping_is_amber_and_joins_are_a_bulleted_list(page, component_origin):
    """Stopping uses the attention colour; each joined ministry is its own line."""
    form = ministry_form(census=False)
    form["ministries"]["options"].append({"id": 11, "name": "Greeters"})
    begin(page, component_origin, form, None)
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    show(page, choir.get_by_label("Stop participating in this ministry")).check()
    expect(choir).to_have_class("ministry-row stopping")
    amber = page.evaluate(
        "getComputedStyle(document.documentElement)"
        ".getPropertyValue('--warning-bg').trim()"
    )
    background = choir.evaluate("e => getComputedStyle(e).backgroundColor")
    probe = page.evaluate(
        "c => { const e = document.createElement('div'); e.style.background = c;"
        " document.body.append(e); const v = getComputedStyle(e).backgroundColor;"
        " e.remove(); return v; }",
        amber,
    )
    assert background == probe
    page.get_by_text("Click here to join more ministries", exact=True).click()
    for name in ("Food pantry", "Greeters"):
        page.get_by_role("checkbox", name=name, exact=True).check()
    items = page.locator(".ministry-joining li")
    expect(items).to_have_text(["Food pantry", "Greeters"])


def test_join_disclosure_says_tap_on_a_touch_only_device(page, component_origin):
    """A coarse pointer with no fine pointer reads "Tap"; otherwise "Click"."""
    page.add_init_script(
        """(() => {
          const real = window.matchMedia.bind(window);
          window.matchMedia = (query) => query === '(pointer: coarse)' ?
            {matches: true, media: query} : query === '(any-pointer: fine)' ?
            {matches: false, media: query} : real(query);
        })();"""
    )
    begin(page, component_origin, ministry_form(census=False), None)
    expect(
        show(page, page.get_by_text("Tap here to join more ministries", exact=True))
    ).to_be_visible()
