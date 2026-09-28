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
from .test_family_response import expect, review, show

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
    form["effective_member_count"] = 1
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
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()


@pytest.mark.parametrize("stale_edit", [False, True])
def test_retained_terminal_ministry_eligibility_without_census(
    page, component_origin, stale_edit
):
    """Private eligibility excludes Ministry answers; stale edits need discard."""
    fresh = financial_form(ministry=True)
    fresh["effective_member_count"] = 0
    fresh["ministries"]["members"] = {}
    form = financial_form(ministry=True) if stale_edit else fresh
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def submit(route):
        """A refreshed form never automatically submits the remaining pledge."""
        submissions.append(route.request.post_data_json["answers"])
        if stale_edit and len(submissions) == 1:
            route.fulfill(status=409, json={"error": "review_required", "form": fresh})
        else:
            route.fulfill(json={"accepted": True})

    begin(page, component_origin, form, submit)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("25")
    show(page, page.get_by_label("Pledge frequency")).select_option("annual")
    show(page, page.locator(f"#financial-option-{CHECK}")).check()
    if stale_edit:
        show(
            page,
            page.get_by_role("group", name="Choir", include_hidden=True).get_by_label(
                "Stop participating"
            ),
        ).check()
        final_submit(page)
        expect(
            show(
                page,
                page.get_by_role(
                    "button",
                    name="Discard unavailable Ministry edits",
                    include_hidden=True,
                ),
            )
        ).to_be_visible()
        review(page)
        assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
        assert len(submissions) == 1
        show(
            page,
            page.get_by_role(
                "button", name="Discard unavailable Ministry edits", include_hidden=True
            ),
        ).click()
    assert page.get_by_label("Household status").count() == 0
    assert page.get_by_text("Ministry participation", exact=True).count() == 0
    final_submit(page)
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[-1]["ministries"] == {"members": {}, "proposed_members": {}}
    assert submissions[-1]["members"] == {"3": {}}
    assert submissions[-1]["financial"]["annual_pledge"] == "25"
    assert not errors


@pytest.mark.parametrize("kind", ["moved_household", "deceased_status"])
@pytest.mark.parametrize("action", ["join", "leave"])
def test_concurrent_terminal_request_requires_explicit_ministry_discard(
    page, component_origin, kind, action
):
    """An unchanged household status cannot silently discard a stale Ministry edit."""
    form = financial_form(census=True, ministry=True)
    fresh = deepcopy(form)
    fresh["members"][0]["request"] = {kind: True, "confirmed": True}
    if kind == "deceased_status":
        fresh["members"][0]["request"]["death_date"] = ""
    submissions = []

    def submit(route):
        """The first attempt returns another response's now-terminal household."""
        submissions.append(route.request.post_data_json["answers"])
        if len(submissions) == 1:
            route.fulfill(status=409, json={"error": "review_required", "form": fresh})
        else:
            route.fulfill(json={"accepted": True})

    begin(page, component_origin, form, submit)
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    if action == "join":
        show(
            page, page.get_by_text("Click here to join more ministries", exact=True)
        ).click()
        show(
            page,
            page.get_by_role(
                "checkbox", name="Food pantry", exact=True, include_hidden=True
            ),
        ).check()
    else:
        show(
            page,
            page.get_by_role("group", name="Choir", include_hidden=True).get_by_label(
                "Stop participating"
            ),
        ).check()
    final_submit(page)
    expect(page.get_by_label("Household status")).to_have_value(kind)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Discard unavailable Ministry edits", include_hidden=True
            ),
        )
    ).to_be_visible()
    review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    assert len(submissions) == 1
    show(
        page,
        page.get_by_role(
            "button", name="Discard unavailable Ministry edits", include_hidden=True
        ),
    ).click()
    final_submit(page)
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[-1]["ministries"]["members"] == {}
    assert submissions[-1]["members"]["3"][kind] is True


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
    # Family pages show whole-dollar amounts without cents (#256).
    expect(page.get_by_text("Parish records for", exact=False)).to_contain_text(
        "pledge $1,200;"
    )
    show(page, page.get_by_label("Annual pledge (USD)")).fill("1,000.01")
    show(page, page.get_by_label("Pledge frequency")).select_option("monthly")
    expect(page.locator("#financial-installment")).to_contain_text("$83.33")
    show(page, page.get_by_label("I will send a check", exact=True)).check()
    show(page, page.get_by_label("I will share another way", exact=True)).check()
    show(page, page.get_by_label("Details for I will share another way")).fill(
        "Stock gift"
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
        show(page, page.get_by_text("Your annual pledge: $1,000.01", exact=True))
    ).to_be_visible()
    page.get_by_role("button", name="Back to edit").click()
    assert not submissions
    expect(page.get_by_label("Details for I will share another way")).to_have_value(
        "Stock gift"
    )
    final_submit(page)
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
    assert submissions[0]["financial"] == {
        "annual_pledge": "1,000.01",
        "frequency": "monthly",
        "shares": {CHECK: "", OTHER: "Stock gift"},
        "cannot_give": False,
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
        show(page, page.get_by_text("Financial records are unavailable", exact=False))
    ).to_be_visible()
    assert "$0.00" not in page.locator("main").inner_text()
    for invalid in ("", "-1", "1e2", "1.001", "1,23", "1000000000"):
        show(page, page.get_by_label("Annual pledge (USD)")).fill(invalid)
        review(page)
        assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
        expect(page.get_by_label("Annual pledge (USD)")).to_have_attribute(
            "aria-invalid", "true"
        )
    show(page, page.get_by_label("Annual pledge (USD)")).fill("1")
    review(page)
    expect(page.get_by_label("Pledge frequency")).to_have_attribute(
        "aria-invalid", "true"
    )
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    final_submit(page)
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[0]["financial"] == {
        "annual_pledge": "0",
        "frequency": "",
        "shares": {},
        "cannot_give": False,
    }


def test_terminal_and_proposed_counts_preserve_financial_answers(
    page, component_origin
):
    """All-terminal households keep pledge/choices; a proposed Member counts again."""
    from .test_member_requests import fill_new

    begin(
        page,
        component_origin,
        financial_form(census=True, ministry=True),
        lambda route: route.fulfill(json={"accepted": True}),
    )
    show(page, page.get_by_label("Annual pledge (USD)")).fill("25.00")
    show(page, page.get_by_label("Pledge frequency")).select_option("annual")
    show(page, page.get_by_label("I will send a check", exact=True)).check()
    show(
        page,
        page.get_by_role("group", name="Choir", include_hidden=True).get_by_label(
            "Stop participating"
        ),
    ).check()
    review(page)
    page.get_by_role("button", name="Back to edit").click()
    page.once("dialog", lambda dialog: dialog.accept())
    show(page, page.get_by_label("Household status")).select_option("moved_household")
    assert page.get_by_text("Ministry participation", exact=True).count() == 0
    expect(
        page.get_by_label("This household will send a check", exact=True)
    ).to_be_checked()
    expect(page.get_by_label("Annual pledge (USD)")).to_have_value("25.00")
    fill_new(page)
    expect(page.get_by_label("I will send a check", exact=True)).to_be_checked()
    fill_new(page)
    expect(page.get_by_label("We will send a check", exact=True)).to_be_checked()
    review(page)
    expect(
        show(page, page.get_by_text("Your annual pledge: $25", exact=True))
    ).to_be_visible()


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
    show(page, page.get_by_label("Annual pledge (USD)")).fill("25.00")
    show(page, page.get_by_label("I will share another way", exact=True)).check()
    show(page, page.get_by_label("Details for I will share another way")).fill(
        "Keep this note"
    )
    final_submit(page)
    review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    assert len(submissions) == 1
    if changed == "pledge":
        show(
            page,
            page.get_by_role(
                "radio", name="Use my edit", exact=False, include_hidden=True
            ),
        ).check()
    elif changed == "removed":
        # Confirmation removes its own control immediately; click, rather than
        # waiting for a checked state on a control that must no longer exist.
        show(
            page, page.get_by_label("Remove this unavailable method before continuing")
        ).click()
        # A positive pledge still needs a share method once the old one is gone.
        show(page, page.locator(f"#financial-option-{CHECK}")).check()
    else:
        show(
            page, page.get_by_label("Discard this note and keep the selected method")
        ).click()
    final_submit(page)
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["financial"]["annual_pledge"] == "25.00"
    assert submissions[1]["financial"]["shares"] == (
        {CHECK: ""}
        if changed == "removed"
        else {OTHER: "" if changed == "text_requirement" else "Keep this note"}
    )


def test_deselected_removed_method_cannot_create_an_unresolvable_conflict(
    page, component_origin
):
    """A concurrent note edit cannot block a valid withdrawal of a removed method."""
    form = financial_form()
    form["financial"]["answers"] = {
        "annual_pledge": "0.00",
        "frequency": "",
        "shares": {OTHER: "Old note"},
    }
    fresh = deepcopy(form)
    fresh["financial"]["options"] = fresh["financial"]["options"][:1]
    fresh["financial"]["answers"]["shares"][OTHER] = "Another session note"
    submissions = []

    def submit(route):
        """A new explicit Submit is required even when removal resolves itself."""
        submissions.append(route.request.post_data_json["answers"])
        if len(submissions) == 1:
            route.fulfill(status=409, json={"error": "review_required", "form": fresh})
        else:
            route.fulfill(json={"accepted": True})

    begin(page, component_origin, form, submit)
    # Share methods apply to a positive pledge; replace the old note's method.
    show(page, page.get_by_label("Annual pledge (USD)")).fill("10")
    show(page, page.get_by_label("Pledge frequency")).select_option("annual")
    show(page, page.locator(f"#financial-option-{CHECK}")).check()
    show(page, page.get_by_label("I will share another way", exact=True)).uncheck()
    final_submit(page)
    expect(page.locator(".family-nav")).to_be_visible()
    assert len(submissions) == 1
    final_submit(page)
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()
    assert submissions[1]["financial"]["shares"] == {CHECK: ""}


@pytest.mark.parametrize("count,label", [(0, "This household"), (1, "I"), (2, "We")])
def test_financial_only_uses_private_effective_count(
    page, component_origin, count, label
):
    """A disabled census exposes no private requests, but wording counts them."""
    form = financial_form()
    form["effective_member_count"] = count
    begin(
        page,
        component_origin,
        form,
        lambda route: route.fulfill(json={"accepted": True}),
    )
    # Share methods appear only for a positive pledge.
    show(page, page.get_by_label("Annual pledge (USD)")).fill("10")
    expect(
        show(page, page.get_by_label(label + " will send a check", exact=True))
    ).to_be_visible()
