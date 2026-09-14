"""Real ordinary Member controls and tab-only review under the production CSP."""

from copy import deepcopy

import pytest

from .test_family_response import expect, form_payload, member_field, prepare

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def start(page, origin, *, form=None, submit=None):
    """Exercise the real entry button, never inject or persist a client draft."""
    prepare(page, origin, submit=submit)
    if form:
        page.route("**/family/form", lambda route: route.fulfill(json={"form": form}))
    page.get_by_role("button", name="Begin reviewing").click()


@pytest.mark.parametrize("width", [320, 1280])
def test_complete_member_controls_review_and_atomic_payload(
    page, component_origin, width
):
    """All ordinary field types survive Back and Submit without intermediate writes."""
    page.set_viewport_size({"width": width, "height": 900})
    submitted = []

    def submit(route):
        """Capture only synthetic data sent at the definitive final action."""
        submitted.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    start(page, component_origin, submit=submit)
    expect(page.get_by_role("group", name="Alex Sample", exact=True)).to_be_visible()
    expect(page.get_by_text("Relationship: Head", exact=True)).to_be_visible()
    for name, value in [
        ("prefix", "Dr."),
        ("nickname", "Al"),
        ("suffix", "Jr."),
        ("maiden_name", "Former"),
        ("home_phone", "+44 20 8366 1177"),
        ("mobile_phone", "202-555-0123 x12"),
        ("work_phone", "+33 1 42 68 53 00"),
    ]:
        page.locator("#member-3-" + name).fill(value)
    page.locator("#member-3-gender").select_option("Female")
    page.locator("#member-3-marital_status").select_option("Widowed")
    page.locator("#member-3-language-choice").select_option("other")
    expect(page.locator("#member-3-language")).to_be_focused()
    page.locator("#member-3-language").fill("French")
    page.locator("#member-3-birth_date").fill("1980-02-29")
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_role("heading", name="Step 2 of 2: Confirm and submit")
    ).to_be_focused()
    expect(page.get_by_text("1980-02-29", exact=False)).to_be_visible()
    assert submitted == []
    page.get_by_role("button", name="Back to edit").click()
    expect(page.locator("#member-3-language-choice")).to_have_value("other")
    expect(page.locator("#member-3-language")).to_have_value("French")
    expect(page.locator("#member-3-birth_date")).to_have_value("1980-02-29")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.get_by_role("heading", name="Thank you!")).to_be_visible()
    assert len(submitted) == 1
    values = submitted[0]["members"]["3"]
    assert values["birth_date"] == "1980-02-29"
    assert values["gender"] == "Female" and values["marital_status"] == "Widowed"
    assert values["language"] == "French" and values["home_phone"] == "+44 20 8366 1177"


def test_missing_birth_requires_explicit_unknown_and_back_preserves_it(
    page, component_origin
):
    form = form_payload()
    member_field(form, "birth_date").update(value="", available=False)
    start(page, component_origin, form=form)
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#member-3-birth_date")).to_be_focused()
    expect(page.locator("#member-3-birth_date-inline-error")).to_be_visible()
    page.locator("#member-3-birth_date-unknown").check()
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_role("heading", name="Step 2 of 2: Confirm and submit")
    ).to_be_visible()
    page.get_by_role("button", name="Back to edit").click()
    expect(page.locator("#member-3-birth_date-unknown")).to_be_checked()
    page.locator("#member-3-birth_date-unknown").uncheck()
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#member-3-birth_date")).to_be_focused()


def test_future_birth_and_language_other_are_validated_on_blur(page, component_origin):
    start(page, component_origin)
    page.locator("#member-3-birth_date").fill("2026-09-14")
    page.locator("#member-3-gender").focus()
    expect(page.locator("#member-3-birth_date-inline-error")).to_contain_text("future")
    page.locator("#member-3-birth_date").fill("2000-01-01")
    page.locator("#member-3-language-choice").select_option("other")
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#member-3-language")).to_be_focused()
    page.locator("#member-3-language").fill("Spanish")
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_role("heading", name="Step 2 of 2: Confirm and submit")
    ).to_be_visible()


@pytest.mark.parametrize("name", ["gender", "language"])
def test_unavailable_required_choice_is_not_defaulted(page, component_origin, name):
    form = form_payload()
    member_field(form, name).update(value="", available=False)
    start(page, component_origin, form=form)
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#member-3-" + name)).to_be_focused()
    if name == "gender":
        page.locator("#member-3-gender").select_option("Unspecified")
    else:
        page.locator("#member-3-language-choice").select_option("English")
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_role("heading", name="Step 2 of 2: Confirm and submit")
    ).to_be_visible()


@pytest.mark.parametrize("use_edit", [False, True])
def test_stale_birth_unknown_choice_requires_explicit_resolution(
    page, component_origin, use_edit
):
    """The date/Unknown widget resolves both ways without hidden stale field values."""
    fresh = form_payload()
    member_field(fresh, "birth_date")["value"] = "1970-01-01"
    submissions = []

    def submit(route):
        submissions.append(deepcopy(route.request.post_data_json))
        route.fulfill(status=409, json={"error": "review_required", "form": fresh})

    start(page, component_origin, submit=submit)
    page.locator("#member-3-birth_date-unknown").check()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    expect(page.locator("#member-3-birth_date-unknown")).to_be_disabled()
    group = page.locator('[data-conflict="members.3.birth_date"]')
    group.get_by_label(
        "Use my edit" if use_edit else "Use updated records", exact=False
    ).check()
    expect(page.locator("#member-3-birth_date-unknown")).to_be_enabled()
    expect(page.locator("#member-3-birth_date-unknown")).to_be_checked(checked=use_edit)
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit response").click()
    assert submissions[-1]["answers"]["members"]["3"]["birth_date"] == (
        "unknown" if use_edit else "1970-01-01"
    )


def test_member_inline_errors_do_not_show_on_initial_render(page, component_origin):
    form = form_payload()
    member_field(form, "email")["value"] = "invalid"
    start(page, component_origin, form=form)
    expect(page.locator("#member-3-email-inline-error")).to_be_hidden()
    page.locator("#member-3-email").focus()
    page.locator("#member-3-first_name").focus()
    expect(page.locator("#member-3-email-inline-error")).to_be_visible()
    expect(page.locator("#member-3-email")).to_have_attribute("aria-invalid", "true")


def test_phone_formatting_alone_does_not_mark_a_change(page, component_origin):
    """Equivalent national/international display does not manufacture new intent."""
    form = form_payload()
    member_field(form, "home_phone")["value"] = "2025550123"
    start(page, component_origin, form=form)
    page.locator("#member-3-home_phone").fill("+1 (202) 555-0123")
    page.locator("#member-3-first_name").focus()
    expect(page.locator("#member-3-home_phone-status")).to_have_text("")
    page.locator("#member-3-home_phone").fill("+1 (202) 555-0123 x1")
    expect(page.locator("#member-3-home_phone-status")).to_contain_text("Changed")


def test_short_national_phone_is_invalid_but_short_international_is_not(
    page, component_origin
):
    """Do not apply the national ten-digit expectation to international numbers."""
    start(page, component_origin)
    page.locator("#member-3-home_phone").fill("555")
    page.locator("#member-3-first_name").focus()
    expect(page.locator("#member-3-home_phone-inline-error")).to_be_visible()
    page.locator("#member-3-home_phone").fill("+43 1 234")
    page.locator("#member-3-first_name").focus()
    expect(page.locator("#member-3-home_phone-inline-error")).to_be_hidden()


def test_empty_same_as_home_shows_an_inline_error(page, component_origin):
    start(page, component_origin)
    page.locator("#family-mailing_same_as_home").check()
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#family-mailing_same_as_home")).to_be_focused()
    expect(page.locator("#family-mailing_same_as_home-constraint")).to_contain_text(
        "Provide a home address"
    )
    page.locator("#family-mailing_same_as_home").uncheck()
    expect(page.locator("#family-mailing_same_as_home-constraint")).to_be_hidden()


@pytest.mark.parametrize(
    "value", ["before\u0085after", "before\u2028after", "before\u2029after"]
)
def test_member_separators_have_inline_errors_before_review(
    page, component_origin, value
):
    start(page, component_origin)
    page.locator("#member-3-nickname").fill(value)
    page.locator("#member-3-first_name").focus()
    expect(page.locator("#member-3-nickname-inline-error")).to_be_visible()


def test_unknown_birth_copy_explains_removal_request(page, component_origin):
    start(page, component_origin)
    expect(
        page.get_by_text(
            "Choosing Unknown requests removal of any recorded birth date, "
            "subject to parish review.",
            exact=True,
        )
    ).to_be_visible()
    page.locator("#member-3-birth_date-unknown").check()
    page.get_by_role("button", name="Review response").click()
    expect(
        page.get_by_text(
            "Unknown — request parish review of removing any recorded birth date",
            exact=False,
        )
    ).to_be_visible()


@pytest.mark.parametrize(
    "value", ['["international",', '["international","12025550123",""]']
)
def test_phone_json_text_is_invalid_and_distinct_from_a_real_phone(
    page, component_origin, value
):
    """Opaque user text must never be parsed as the browser's comparison tuple."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    form = form_payload()
    member_field(form, "home_phone")["value"] = "2025550123"
    start(page, component_origin, form=form)
    page.locator("#member-3-home_phone").fill(value)
    page.locator("#member-3-first_name").focus()
    expect(page.locator("#member-3-home_phone-status")).to_contain_text("Changed")
    expect(page.locator("#member-3-home_phone-inline-error")).to_be_visible()
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#member-3-home_phone")).to_be_focused()
    assert errors == []
