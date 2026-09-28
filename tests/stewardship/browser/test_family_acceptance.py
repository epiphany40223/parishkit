"""Integrated Family review navigation and representative whole-flow acceptance."""

import json
from copy import deepcopy
from time import monotonic
from uuid import UUID

import pytest

from ..test_financial_answers import CHECK
from .test_family_financial import financial_form
from .test_family_ministry import begin
from .test_family_response import expect, prepare, review, show

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("census", [False, True])
def test_member_intro_is_rendered_once_only_in_census(page, component_origin, census):
    """The shared Member introduction sits after household fields, before Members."""
    form = financial_form(census=census, ministry=True)
    form["content"]["member_census"] = "<p>Review each household member below.</p>"
    begin(
        page,
        component_origin,
        form,
        lambda route: route.fulfill(json={"accepted": True}),
    )
    intro = page.get_by_text("Review each household member below.", exact=True)
    expect(intro).to_have_count(1 if census else 0)
    if census:
        assert intro.evaluate(
            "element => Boolean(element.compareDocumentPosition("
            "document.getElementById('member-section-3')) & "
            "Node.DOCUMENT_POSITION_FOLLOWING)"
        )


@pytest.mark.parametrize("width", [320, 1280])
def test_review_edit_controls_preserve_answers_and_focus_sections(
    page, component_origin, axe_source, tmp_path, width
):
    """Keyboard section navigation keeps every answer tab-local until final Submit."""
    page.set_viewport_size({"width": width, "height": 900})
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def submit(route):
        """Only the final, deliberate Submit reaches the response boundary."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    begin(page, component_origin, financial_form(census=True, ministry=True), submit)
    show(page, page.get_by_label("First name (required)")).fill("Review edit")
    show(page, page.get_by_label("Annual pledge (USD)")).fill("52")
    show(page, page.get_by_label("Pledge frequency")).select_option("weekly")
    show(page, page.locator(f"#financial-option-{CHECK}")).check()
    show(page, page.get_by_label("Additional information (optional)")).fill(
        "Private review note"
    )
    review(page)
    for label, target in [
        ("Family census", "household-section"),
        ("Review edit", "member-section-3"),
        ("Financial stewardship", "financial-section"),
        ("Additional information", "additional-information"),
    ]:
        control = page.get_by_role("button", name="Edit " + label)
        control.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#" + target)).to_be_focused()
        expect(page.get_by_label("First name (required)")).to_have_value("Review edit")
        expect(page.get_by_label("Annual pledge (USD)")).to_have_value("52")
        expect(page.get_by_label("Additional information (optional)")).to_have_value(
            "Private review note"
        )
        assert not submissions
        assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        review(page)
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(v => v.id)""")
        == []
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role(
        "heading", name="Financial stewardship", exact=True
    ).scroll_into_view_if_needed()
    image = tmp_path / "family-review.png"
    page.screenshot(path=str(image))
    print(f"Family review inspection: {image}")
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
    assert len(submissions) == 1
    assert submissions[0]["members"]["3"]["first_name"] == "Review edit"
    assert submissions[0]["financial"]["annual_pledge"] == "52"
    assert "Private review note" not in page.content()
    assert not errors


def test_section_edit_returns_to_a_testing_review(page, component_origin):
    """A section shortcut from Review leads back to a Testing review, not live."""
    attempts = prepare(page, component_origin, testing=True)
    page.get_by_role("button", name="Continue with test").click()
    review(page)
    show(
        page,
        page.get_by_role(
            "button", name="Edit Additional information", include_hidden=True
        ),
    ).click()
    review(page)
    assert len(attempts) == 1
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit test response", include_hidden=True
            ),
        )
    ).to_be_visible()


def test_reload_and_cancel_never_restore_a_private_draft(page, component_origin):
    """Leaving a dirty tab warns; a new page can retrieve only submitted state."""
    submissions, dialogs = [], []
    prepare(
        page, component_origin, submit=lambda route: submissions.append(route.request)
    )
    page.get_by_role("button", name="Begin reviewing").click()
    show(page, page.get_by_label("First name (required)")).fill(
        "Unsubmitted private edit"
    )

    def acknowledge(dialog):
        """Record actual browser loss warnings without suppressing the handler."""
        dialogs.append(dialog.type)
        dialog.accept()

    page.on("dialog", acknowledge)
    page.reload()
    assert dialogs == ["beforeunload"]
    assert "Unsubmitted private edit" not in page.content()
    page.get_by_role("button", name="Begin reviewing").click()
    expect(page.get_by_label("First name (required)")).to_have_value("Alex")
    show(page, page.get_by_label("First name (required)")).fill("Another private edit")
    page.route("**/family/logout", lambda route: route.fulfill(body="Signed out"))
    show(
        page,
        page.get_by_role("button", name="Cancel and sign out", include_hidden=True),
    ).click()
    expect(page.locator("body")).to_have_text("Signed out")
    assert dialogs == ["beforeunload", "confirm"]
    assert not submissions
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_large_household_and_on_demand_ministry_list(page, component_origin):
    """Measure the 100-proposed-member limit with 20 existing and 500 Ministries.

    Five hundred Ministries is the architecture's reference parish size, not
    an invented production cap. The test keeps the final JSON within the real
    256-KiB boundary and exercises the actual JS, not a synthetic renderer.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    form = financial_form(census=True, ministry=True)
    template = deepcopy(form["members"][0])
    form["members"] = [
        dict(deepcopy(template), id=str(index)) for index in range(3, 23)
    ]
    form["proposed_members"] = [
        dict(deepcopy(template), id=str(UUID(int=index + 1)))
        for index in range(form["max_proposed_members"])
    ]
    form["ministries"] = {
        "options": [
            {"id": index, "name": f"Ministry {index:03d}"} for index in range(1, 501)
        ],
        "members": {
            row["id"]: {"current": [1], "join": [], "leave": []}
            for row in form["members"]
        },
        "proposed_members": {
            row["id"]: {"join": []} for row in form["proposed_members"]
        },
    }
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def submit(route):
        """Capture the one complete real browser aggregate and its encoded size."""
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    started = monotonic()
    begin(page, component_origin, form, submit)
    expect(
        page.get_by_role("button", name="Add a household member", include_hidden=True)
    ).to_be_disabled()
    render_seconds = monotonic() - started
    assert page.get_by_label("Search ministries").count() == 0
    show(
        page, page.get_by_text("Click here to join more ministries", exact=True).first
    ).click()
    show(page, page.get_by_label("Search ministries")).fill("500")
    show(
        page,
        page.get_by_role(
            "checkbox", name="Ministry 500", exact=True, include_hidden=True
        ),
    ).check()
    assert page.locator('[id*="-join-"]').count() == 1
    show(page, page.get_by_label("Annual pledge (USD)")).fill("0")
    started = monotonic()
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()
    review_seconds = monotonic() - started
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(
            page,
            page.get_by_role(
                "heading", name="Thank you!", exact=True, include_hidden=True
            ),
        )
    ).to_be_visible()
    assert len(submissions) == 1
    answers = submissions[0]["answers"]
    assert len(answers["members"]) == 20
    assert len(answers["proposed_members"]) == 100
    assert answers["ministries"]["members"]["3"]["join"] == [500]
    encoded_bytes = len(
        json.dumps(submissions[0], ensure_ascii=False, separators=(",", ":")).encode()
    )
    assert encoded_bytes < 256 * 1024
    print(
        f"Large Family: form_bytes={len(json.dumps(form).encode())} "
        f"submit_bytes={encoded_bytes} render_seconds={render_seconds:.2f} "
        f"review_seconds={review_seconds:.2f}"
    )
    assert render_seconds < 15 and review_seconds < 15
    assert not errors
