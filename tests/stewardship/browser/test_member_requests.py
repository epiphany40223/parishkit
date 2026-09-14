"""Real terminal/local-Member controls, explicit stale resolution and no drafts."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from .test_family_response import expect, form_payload, member_field
from .test_member_census import start

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def capture(submissions):
    """Keep synthetic answers only in the test's definitive Submit capture."""

    def submit(route):
        """Respond without a provider or persistent draft endpoint."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    return submit


def fill_new(page):
    """Find the new local UUID from its labeled control, then fill required data."""
    controls = page.locator('input[id$="-first_name"]')
    before = set(controls.evaluate_all("rows => rows.map(row => row.id)"))
    page.get_by_role("button", name="Add a household member", exact=True).click()
    # Proposed Members sort by stable UUID, not insertion order. Adding a
    # second person must not accidentally refill the existing last person.
    added = set(controls.evaluate_all("rows => rows.map(row => row.id)")) - before
    assert len(added) == 1
    control = page.locator("#" + added.pop())
    identity = control.get_attribute("id")[len("member-") : -len("-first_name")]
    assert str(UUID(identity)) == identity
    control.fill("New")
    page.locator(f"#member-{identity}-last_name").fill("Household")
    page.locator(f"#member-{identity}-birth_date-unknown").check()
    page.locator(f"#member-{identity}-gender").select_option("Unspecified")
    page.locator(f"#member-{identity}-language-choice").select_option("English")
    return identity


def assert_accessible(page, axe_source):
    """Use the test-only debugger injection without relaxing production CSP."""
    page.evaluate(axe_source)
    assert not page.evaluate(
        "async () => (await axe.run(document, {runOnly: {type: 'tag', "
        "values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}})).violations"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_terminal_confirmation_skips_ordinary_fields_review_back_and_submit(
    page, component_origin, axe_source, width
):
    page.set_viewport_size({"width": width, "height": 900})
    submitted, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    start(page, component_origin, submit=capture(submitted))
    page.locator("#member-3-email").fill("invalid and discarded")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_label("Household status").select_option("moved_household")
    expect(page.get_by_label("Household status")).to_have_value("current")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Household status").select_option("deceased_status")
    expect(page.locator("#member-3-email")).to_have_count(0)
    page.get_by_label("Death date (optional)").fill("2026-01-01")
    assert_accessible(page, axe_source)
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_text("Requested change: deceased.", exact=False)).to_be_visible()
    assert submitted == []
    page.get_by_role("button", name="Back to edit").click()
    expect(page.get_by_label("Death date (optional)")).to_have_value("2026-01-01")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submitted[0]["members"] == {
        "3": {"deceased_status": True, "confirmed": True, "death_date": "2026-01-01"}
    }
    assert submitted[0]["proposed_members"] == {}
    assert errors == []


def test_proposed_add_edit_remove_and_submit_are_tab_only(
    page, component_origin, axe_source
):
    submitted = []
    start(page, component_origin, submit=capture(submitted))
    identity = fill_new(page)
    assert_accessible(page, axe_source)
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_role("heading", name="New Household", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Back to edit").click()
    page.locator(f"#member-{identity}-first_name").fill("Corrected")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Remove proposed member").click()
    expect(page.locator(f"#member-{identity}-first_name")).to_have_count(0)
    replacement = fill_new(page)
    assert replacement != identity
    assert submitted == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert set(submitted[0]["proposed_members"]) == {replacement}
    assert submitted[0]["proposed_members"][replacement]["birth_date"] == "unknown"


def test_terminal_request_stale_competition_requires_choice(page, component_origin):
    fresh, submitted = form_payload(), []
    fresh["members"][0]["request"] = {
        "deceased_status": True,
        "confirmed": True,
        "death_date": "",
    }

    def submit(route):
        """Refresh once, then accept the explicit new final choice."""
        submitted.append(route.request.post_data_json["answers"])
        route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ) if len(submitted) == 1 else route.fulfill(json={"accepted": True})

    start(page, component_origin, submit=submit)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Household status").select_option("moved_household")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    group = page.locator('[data-conflict="members.3.request"]')
    expect(group).to_be_visible()
    page.get_by_role("button", name="Review response").click()
    assert len(submitted) == 1
    group.get_by_role("button", name="Use the updated response", exact=False).click()
    expect(page.get_by_label("Household status")).to_have_value("deceased_status")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submitted[-1]["members"]["3"] == fresh["members"][0]["request"]


def test_proposed_concurrent_withdrawal_does_not_silently_revive_edit(
    page, component_origin
):
    form, identity, submitted = form_payload(), str(uuid4()), []
    proposed = deepcopy(form["members"][0])
    proposed.update(id=identity, relationship="Proposed household member")
    form["proposed_members"] = [proposed]
    fresh = deepcopy(form)
    fresh["baseline"] = str(uuid4())
    fresh["proposed_members"] = []

    def submit(route):
        """Return another response's withdrawal, not an automatic overwrite."""
        submitted.append(route.request.post_data_json["answers"])
        route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ) if len(submitted) == 1 else route.fulfill(json={"accepted": True})

    start(page, component_origin, form=form, submit=submit)
    page.locator(f"#member-{identity}-first_name").fill("Edited")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    group = page.locator(f'[data-conflict="proposed_members.{identity}"]')
    expect(group).to_be_visible()
    group.get_by_role("button", name="Use the updated response", exact=False).click()
    expect(page.locator(f"#member-{identity}-first_name")).to_have_count(0)
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submitted[-1]["proposed_members"] == {}


def test_death_date_error_maps_to_control_and_future_is_inline(page, component_origin):
    def submit(route):
        """Return a server-owned birth/death ordering error without private dates."""
        route.fulfill(
            status=422,
            json={
                "error": "validation",
                "fields": {"members.3.death_date": "Death date cannot precede birth."},
            },
        )

    start(page, component_origin, submit=submit)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Household status").select_option("deceased_status")
    page.get_by_label("Death date (optional)").fill("2026-09-14")
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#member-3-death_date-inline-error")).to_contain_text("future")
    page.get_by_label("Death date (optional)").fill("1950-01-01")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator("#member-3-death_date-error")).to_contain_text("precede birth")
    page.get_by_role(
        "link", name="Death date (optional): Death date cannot precede birth."
    ).click()
    expect(page.locator("#member-3-death_date")).to_be_focused()


@pytest.mark.parametrize("keep_edit", [True, False])
def test_ordinary_edit_competing_with_new_terminal_request_needs_choice(
    page, component_origin, keep_edit
):
    """An ordinary edit cannot silently endorse another response's terminal choice."""
    fresh, submitted = form_payload(), []
    fresh["members"][0]["request"] = {"moved_household": True, "confirmed": True}

    def submit(route):
        """Expose the concurrent response once, then record the explicit new Submit."""
        submitted.append(route.request.post_data_json["answers"])
        if len(submitted) == 1:
            route.fulfill(status=409, json={"error": "review_required", "form": fresh})
        else:
            route.fulfill(json={"accepted": True})

    start(page, component_origin, submit=submit)
    page.locator("#member-3-first_name").fill("Edited")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    group = page.locator('[data-conflict="members.3.request"]')
    expect(group).to_be_visible()
    page.get_by_role("button", name="Review response").click()
    assert len(submitted) == 1
    group.get_by_role(
        "button",
        name="Keep my household request" if keep_edit else "Use the updated response",
        exact=False,
    ).click()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    if keep_edit:
        assert submitted[-1]["members"]["3"]["first_name"] == "Edited"
    else:
        assert submitted[-1]["members"]["3"] == fresh["members"][0]["request"]


def test_terminal_toggle_and_review_back_preserve_hidden_field_conflict(
    page, component_origin
):
    """Temporarily hiding a field must not waive its competing-value choice."""
    fresh, submitted = form_payload(), []
    member_field(fresh, "first_name")["value"] = "Updated records"

    def submit(route):
        """Require one refreshed-baseline review with a competing name."""
        submitted.append(route.request.post_data_json["answers"])
        if len(submitted) == 1:
            route.fulfill(status=409, json={"error": "review_required", "form": fresh})
        else:
            route.fulfill(json={"accepted": True})

    start(page, component_origin, submit=submit)
    page.locator("#member-3-first_name").fill("My edit")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator('[data-conflict="members.3.first_name"]')).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Household status").select_option("moved_household")
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_role("heading", name="Step 2 of 2: Confirm and submit")
    ).to_be_visible()
    page.get_by_role("button", name="Back to edit").click()
    page.get_by_label("Household status").select_option("current")
    group = page.locator('[data-conflict="members.3.first_name"]')
    expect(group).to_be_visible()
    expect(page.locator("#member-3-first_name")).to_be_disabled()
    group.get_by_label("Use updated records", exact=False).check()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submitted[-1]["members"]["3"]["first_name"] == "Updated records"
