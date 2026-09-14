"""Actual mobile financial editor/review/rebase without draft or provider writes."""

from copy import deepcopy

import pytest

from parishkit.stewardship.responses.financial_inputs import (
    financial_definition,
    financial_inputs,
    giving_observation,
)
from parishkit.stewardship.responses.financial_presentation import (
    financial_presentation,
)

from ..financial_factory import CAMPAIGN, configuration, cursor, record
from ..test_financial_answers import CHECK, OTHER
from .test_family_ministry import begin, ministry_form
from .test_family_response import expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def financial_form(*, census=False, ministry=False, available=True):
    """Use the actual scoped Python presenter for the synthetic browser boundary."""
    form = ministry_form(census=census)
    if not ministry:
        form["ministries"] = None
        form["modules"].remove("ministry")
    form["modules"].append("financial")
    values = configuration()
    values["share_options"] = [
        {"id": CHECK, "label": "{{ pronoun }} will send a check", "free_text": False},
        {
            "id": OTHER,
            "label": "{{ pronoun }} will share another way",
            "free_text": True,
        },
    ]
    definition = financial_definition(values, campaign_id=CAMPAIGN)
    inputs = financial_inputs(
        definition,
        giving_observation(cursor(definition), definition) if available else None,
        family_duid=1,
        pledges=[record("1200.00")],
        contributions=[record("500.00")],
    )
    form["financial"] = financial_presentation(
        inputs, None, parish_name="Sample Parish"
    )
    return form


def final_submit(page):
    """Both deliberate actions are necessary, even after a refreshed response."""
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()


@pytest.mark.parametrize(
    "census,ministry,width",
    [
        (False, False, 320),
        (False, False, 1280),
        (True, False, 320),
        (False, True, 320),
        (True, True, 320),
    ],
)
def test_financial_modules_mobile_final_only_and_accessible(
    page, component_origin, axe_source, census, ministry, width
):
    """Every financial module combination uses exact cents and one final payload."""
    page.set_viewport_size({"width": width, "height": 900})
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def submit(route):
        """Capture the sole answer-bearing request from the real component."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(
        page, component_origin, financial_form(census=census, ministry=ministry), submit
    )
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("")
    expect(page.get_by_text("Parish records for", exact=False)).to_contain_text(
        "$1,200.00"
    )
    page.get_by_label("Annual pledge (USD)").fill("1,000.01")
    page.get_by_label("Pledge frequency").select_option("monthly")
    expect(page.locator("#financial-installment")).to_contain_text("$83.33")
    page.get_by_label("I will send a check", exact=True).check()
    page.get_by_label("I will share another way", exact=True).check()
    page.get_by_label("Details for I will share another way").fill("Stock gift")
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
        page.get_by_text("Your annual pledge: $1,000.01", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Back to edit").click()
    assert not submissions
    expect(page.get_by_label("Details for I will share another way")).to_have_value(
        "Stock gift"
    )
    final_submit(page)
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert submissions[0]["financial"] == {
        "annual_pledge": "1,000.01",
        "frequency": "monthly",
        "shares": {CHECK: "", OTHER: "Stock gift"},
    }
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert not errors


def test_financial_validation_zero_and_unavailable(page, component_origin):
    """Invalid amounts block navigation; unknown source does not block a zero pledge."""
    submissions = []

    def submit(route):
        """The zero pledge remains an intentional submitted value, not missing data."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, financial_form(available=False), submit)
    expect(
        page.get_by_text("Financial records are unavailable", exact=False)
    ).to_be_visible()
    assert "$0.00" not in page.locator("main").inner_text()
    for invalid in ("", "-1", "1e2", "1.001", "1,23", "1000000000"):
        page.get_by_label("Annual pledge (USD)").fill(invalid)
        page.get_by_role("button", name="Review response").click()
        assert page.get_by_role("button", name="Submit response").count() == 0
        expect(page.get_by_label("Annual pledge (USD)")).to_have_attribute(
            "aria-invalid", "true"
        )
    page.get_by_label("Annual pledge (USD)").fill("1")
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_label("Pledge frequency")).to_have_attribute(
        "aria-invalid", "true"
    )
    page.get_by_label("Annual pledge (USD)").fill("0")
    final_submit(page)
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submissions[0]["financial"] == {
        "annual_pledge": "0",
        "frequency": "",
        "shares": {},
    }


def test_terminal_and_proposed_counts_preserve_financial_answers(
    page, component_origin
):
    """All-terminal households keep pledge/choices; a proposed Member counts again."""
    from .test_member_requests import fill_new

    begin(
        page,
        component_origin,
        financial_form(census=True),
        lambda route: route.fulfill(json={"accepted": True}),
    )
    page.get_by_label("Annual pledge (USD)").fill("25.00")
    page.get_by_label("Pledge frequency").select_option("annual")
    page.get_by_label("I will send a check", exact=True).check()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Household status").select_option("moved_household")
    expect(
        page.get_by_label("This household will send a check", exact=True)
    ).to_be_checked()
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("25.00")
    fill_new(page)
    expect(page.get_by_label("I will send a check", exact=True)).to_be_checked()
    fill_new(page)
    expect(page.get_by_label("We will send a check", exact=True)).to_be_checked()
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_text("Your annual pledge: $25.00", exact=True)).to_be_visible()


@pytest.mark.parametrize("changed", ["pledge", "removed", "text_requirement"])
def test_financial_stale_response_preserves_edits_and_requires_resolution(
    page, component_origin, changed
):
    """No stale Submit or removed/config-changed choice can silently overwrite data."""
    form = financial_form()
    form["financial"]["answers"] = {
        "annual_pledge": "12.00",
        "frequency": "annual",
        "shares": {},
    }
    fresh = deepcopy(form)
    if changed == "pledge":
        fresh["financial"]["answers"]["annual_pledge"] = "30.00"
    elif changed == "removed":
        fresh["financial"]["options"] = fresh["financial"]["options"][:1]
    else:
        fresh["financial"]["options"][1]["free_text"] = False
    submissions = []

    def submit(route):
        """The refreshed baseline is not an accepted response or an automatic retry."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ) if len(submissions) == 1 else route.fulfill(json={"accepted": True})

    begin(page, component_origin, form, submit)
    page.get_by_label("Annual pledge (USD)").fill("25.00")
    page.get_by_label("I will share another way", exact=True).check()
    page.get_by_label("Details for I will share another way").fill("Keep this note")
    final_submit(page)
    page.get_by_role("button", name="Review response").click()
    assert page.get_by_role("button", name="Submit response").count() == 0
    assert len(submissions) == 1
    if changed == "pledge":
        page.get_by_role("radio", name="Use my edit", exact=False).check()
    elif changed == "removed":
        # Confirmation removes its own control immediately; click, rather than
        # waiting for a checked state on a control that must no longer exist.
        page.get_by_label("Remove this unavailable method before continuing").click()
    else:
        page.get_by_label("Discard this note and keep the selected method").click()
    final_submit(page)
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert submissions[1]["financial"]["annual_pledge"] == "25.00"
    assert submissions[1]["financial"]["shares"] == (
        {}
        if changed == "removed"
        else {OTHER: "" if changed == "text_requirement" else "Keep this note"}
    )
