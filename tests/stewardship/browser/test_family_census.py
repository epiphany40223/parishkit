"""Household controls in the real tab-local response flow on all browser engines."""

import pytest

from ..census_factory import address
from .test_family_response import expect, form_payload, prepare

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def fill_address(page, name, value):
    """Fill every component through the visible, uniquely labeled controls."""
    for component, text in value.items():
        control = page.locator(f"#family-{name}-{component}")
        if component == "country":
            control.select_option(text)
        else:
            control.fill(text)


@pytest.mark.parametrize("width", [320, 1280])
def test_household_addresses_and_explicit_false_submit_once(
    page, component_origin, width
):
    """Country-aware values survive back/review, with no implicit opt-out default."""
    page.set_viewport_size({"width": width, "height": 900})
    submissions = []

    def submit(route):
        """Capture the sole final aggregate request, not an intermediate draft."""
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    prepare(page, component_origin, submit=submit)
    page.get_by_role("button", name="Begin reviewing").click()
    expect(page.get_by_label("Opt out of all parish emails")).to_have_value("")
    assert "Registration date: Not available" in page.locator("main").inner_text()
    home = address()
    mailing = address(country="GB", region="", postal_code="SW1A 1AA", city="London")
    fill_address(page, "home_address", home)
    fill_address(page, "mailing_address", mailing)
    page.get_by_label("Opt out of all parish emails").select_option("false")
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit to Sample Parish")).to_be_visible()
    assert "SW1A 1AA" in page.locator("main").inner_text()
    assert "[object Object]" not in page.locator("main").inner_text()
    page.get_by_role("button", name="Back to edit").click()
    expect(page.locator("#family-mailing_address-country")).to_have_value("GB")
    assert not submissions
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("heading", name="Thank you!", exact=True)).to_be_visible()
    assert submissions[0]["answers"]["family"] == {
        "home_address": home,
        "mailing_address": mailing,
        "email_opt_out": False,
        "mailing_same_as_home": False,
    }


def test_same_as_home_confirmation_and_separate_draft_restore(page, component_origin):
    """Declining preserves mailing; accepting/back/unchecking restores the draft."""
    prepare(page, component_origin)
    page.get_by_role("button", name="Begin reviewing").click()
    fill_address(page, "home_address", address())
    fill_address(page, "mailing_address", address(line1="456 Separate Street"))
    same = page.get_by_label("Mailing address is the same as home address")
    page.once("dialog", lambda dialog: dialog.dismiss())
    same.click()
    expect(same).not_to_be_checked()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "456 Separate Street"
    )
    page.once("dialog", lambda dialog: dialog.accept())
    same.check()
    expect(page.locator("#family-mailing_address-line1")).to_be_disabled()
    page.locator("#family-home_address-line1").fill("789 Updated Home")
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "789 Updated Home"
    )
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Back to edit").click()
    same.uncheck()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "456 Separate Street"
    )
    expect(page.locator("#family-home_address-line1")).to_have_value("789 Updated Home")


def test_us_rules_do_not_block_international_address(page, component_origin):
    """A US state/ZIP error is cleared when the country genuinely changes."""
    prepare(page, component_origin)
    page.get_by_role("button", name="Begin reviewing").click()
    fill_address(page, "home_address", address(region="", postal_code=""))
    page.get_by_role("button", name="Review response").click()
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    page.locator("#family-home_address-country").select_option("IE")
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit to Sample Parish")).to_be_visible()


def test_stale_household_values_require_explicit_field_choices(page, component_origin):
    """Another adult's new address cannot be silently replaced by this tab's edit."""
    fresh = form_payload()
    fresh["household"]["fields"][0]["value"] = address(line1="Other adult's home")
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    fill_address(page, "home_address", address(line1="My edited home"))
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator('[data-conflict="family.home_address"]')).to_be_visible()
    expect(page.locator("#family-home_address-line1")).to_be_disabled()
    page.get_by_role("button", name="Review response").click()
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    page.get_by_role("radio", name="Use updated records:").check()
    expect(page.locator("#family-home_address-line1")).to_have_value(
        "Other adult's home"
    )
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit to Sample Parish")).to_be_visible()


@pytest.mark.parametrize("prior_same", [False, True])
def test_untouched_same_flag_adopts_other_adults_refreshed_choice(
    page, component_origin, prior_same
):
    """An untouched tab cannot reverse a newer convenience choice."""
    original, fresh = form_payload(), form_payload()
    for form, same in ((original, prior_same), (fresh, not prior_same)):
        form["household"]["fields"][0]["value"] = address()
        form["household"]["fields"][1]["value"] = address()
        form["household"]["mailing_same_as_home"] = same
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ),
    )
    page.route("**/family/form", lambda route: route.fulfill(json={"form": original}))
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.get_by_role("button", name="Review response")).to_be_visible()
    assert page.get_by_label(
        "Mailing address is the same as home address"
    ).is_checked() is (not prior_same)


def test_stale_address_choices_do_not_destroy_separate_mailing_draft(
    page, component_origin
):
    """Resolve records, recheck the convenience flag, then recover the draft."""
    fresh = form_payload()
    fresh["household"]["fields"][0]["value"] = address(line1="Other home")
    fresh["household"]["fields"][1]["value"] = address()
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    fill_address(page, "home_address", address())
    fill_address(page, "mailing_address", address(line1="Separate draft"))
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Mailing address is the same as home address").check()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    page.get_by_role("radio", name="Use my edit:").check()
    page.get_by_role("radio", name="Keep addresses separate").check()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "Separate draft"
    )
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Back to edit").click()
    same = page.get_by_label("Mailing address is the same as home address")
    page.once("dialog", lambda dialog: dialog.accept())
    same.check()
    same.uncheck()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "Separate draft"
    )


def test_household_blur_has_visible_linked_error_and_country_change_clears_it(
    page, component_origin
):
    """Assistive technology receives an inline explanation, not color alone."""
    prepare(page, component_origin)
    page.get_by_role("button", name="Begin reviewing").click()
    fill_address(page, "home_address", address(postal_code="invalid"))
    page.locator("#family-home_address-postal_code").focus()
    page.locator("#family-home_address-country").focus()
    error = page.locator("#family-home_address-postal_code-constraint")
    expect(error).to_have_text("Enter a five-digit ZIP or ZIP+4 code.")
    expect(error).to_be_visible()
    expect(page.locator("#family-home_address-postal_code")).to_have_attribute(
        "aria-describedby",
        "family-home_address-status family-home_address-postal_code-constraint",
    )
    page.locator("#family-home_address-country").select_option("GB")
    expect(error).to_be_hidden()
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit to Sample Parish")).to_be_visible()


@pytest.mark.parametrize(
    "last_choice", ["Keep addresses separate", "Copy home to mailing"]
)
@pytest.mark.parametrize("copy_first", [False, True])
def test_copy_then_change_home_resolution_restores_separate_value(
    page, component_origin, last_choice, copy_first
):
    """R2: an abandoned home copy never becomes the separate mailing address."""
    fresh = form_payload()
    fresh["household"]["fields"][0]["value"] = address(line1="Other home")
    fresh["household"]["fields"][1]["value"] = address()
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    fill_address(page, "home_address", address())
    fill_address(page, "mailing_address", address(line1="Retained separate draft"))
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("Mailing address is the same as home address").check()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    if copy_first:
        page.get_by_role("radio", name="Use my edit:").check()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("radio", name="Copy home to mailing").check()
    page.get_by_role("radio", name="Use updated records:").check()
    if last_choice == "Copy home to mailing":
        page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("radio", name=last_choice).check()
    if last_choice == "Copy home to mailing":
        expect(page.locator("#family-mailing_address-line1")).to_have_value(
            "Other home"
        )
        page.get_by_role("radio", name="Keep addresses separate").check()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "Retained separate draft"
    )
    page.get_by_role("button", name="Review response").click()
    expect(page.get_by_role("button", name="Submit to Sample Parish")).to_be_visible()
    assert "Retained separate draft" in page.locator("main").inner_text()


@pytest.mark.parametrize("component", ["line1", "line2"])
def test_server_address_line_errors_are_linked_and_focusable(
    page, component_origin, component
):
    """R2: digit-bearing component paths retain static inline and summary errors."""
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=422,
            json={
                "error": "validation",
                "fields": {
                    f"family.home_address.{component}": "Review this address line."
                },
            },
        ),
    )
    page.get_by_role("button", name="Begin reviewing").click()
    page.get_by_role("button", name="Review response").click()
    page.get_by_role("button", name="Submit to Sample Parish").click()
    identifier = f"family-home_address-{component}"
    link = page.locator(f'#family-flow-message a[href="#{identifier}"]')
    expect(link).to_be_visible()
    expect(page.locator(f"#{identifier}-error")).to_have_text(
        "Review this address line."
    )
    link.click()
    expect(page.locator(f"#{identifier}")).to_be_focused()
    expect(page.locator(f"#{identifier}")).to_have_attribute("aria-invalid", "true")


def test_untouched_address_fields_show_errors_only_on_blur_or_review(
    page, component_origin
):
    """R2: entering a delivery line does not announce unrelated required errors."""
    prepare(page, component_origin)
    page.get_by_role("button", name="Begin reviewing").click()
    page.locator("#family-home_address-line1").fill("A delivery line")
    for component in ("city", "country"):
        expect(
            page.locator(f"#family-home_address-{component}-constraint")
        ).to_be_hidden()
        expect(page.locator(f"#family-home_address-{component}")).to_have_attribute(
            "aria-invalid", "false"
        )
    page.locator("#family-home_address-city").focus()
    page.locator("#family-home_address-line1").focus()
    expect(page.locator("#family-home_address-city-constraint")).to_be_visible()
    expect(page.locator("#family-home_address-country-constraint")).to_be_hidden()
    page.get_by_role("button", name="Review response").click()
    expect(page.locator("#family-home_address-country-constraint")).to_be_visible()
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
