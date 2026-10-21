"""Household controls in the real tab-local response flow on all browser engines."""

import pytest

from parishkit.stewardship.responses.census import blank_address, us_regions

from ..census_factory import address
from .keyboard import tab_to_next
from .test_family_response import expect, form_payload, prepare, review, show

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def fill_address(page, name, value):
    """Fill an address as a Family would: Country first, then its parts.

    Country decides the region control (#1777): a US State is chosen from the
    drop-down, any other region is typed. Each control is found by the shared
    id that its visible label names.
    """
    show(page, page.locator(f"#family-{name}-country")).select_option(value["country"])
    for component, text in value.items():
        control = page.locator(f"#family-{name}-{component}")
        if component == "country":
            continue
        if component == "region" and value["country"] == "US":
            control.select_option(text)
        else:
            control.fill(text)


def home_group(page):
    """The home address fieldset, opened on its form page."""
    group = page.locator("fieldset").filter(
        has=page.locator("#family-home_address-country")
    )
    show(page, group)
    return group


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
    expect(show(page, page.get_by_label("Opt out of all parish emails"))).to_have_value(
        ""
    )
    assert "Registration date: Not available" in page.locator("main").inner_text()
    home = address()
    mailing = address(country="GB", region="", postal_code="SW1A 1AA", city="London")
    fill_address(page, "home_address", home)
    fill_address(page, "mailing_address", mailing)
    show(page, page.get_by_label("Opt out of all parish emails")).select_option("false")
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()
    assert "SW1A 1AA" in page.locator("main").inner_text()
    assert "[object Object]" not in page.locator("main").inner_text()
    page.get_by_role("button", name="Back to edit").click()
    expect(page.locator("#family-mailing_address-country")).to_have_value("GB")
    assert not submissions
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
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
    assert submissions[0]["answers"]["family"] == {
        "home_address": home,
        "mailing_address": mailing,
        "email_opt_out": False,
        "mailing_same_as_home": False,
    }


def test_same_as_home_confirmation_and_separate_draft_restore(page, component_origin):
    """Declining preserves mailing; accepting/back/unchecking restores the draft."""
    prepare(page, component_origin)
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
    show(page, page.locator("#family-home_address-line1")).fill("789 Updated Home")
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "789 Updated Home"
    )
    review(page)
    page.get_by_role("button", name="Back to edit").click()
    show(page, same).uncheck()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "456 Separate Street"
    )
    expect(page.locator("#family-home_address-line1")).to_have_value("789 Updated Home")


def test_us_rules_do_not_block_international_address(page, component_origin):
    """A US state/ZIP error is cleared when the country genuinely changes."""
    prepare(page, component_origin)
    fill_address(page, "home_address", address(region="", postal_code=""))
    assert not review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    show(page, page.locator("#family-home_address-country")).select_option("IE")
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()


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
    fill_address(page, "home_address", address(line1="My edited home"))
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.locator('[data-conflict="family.home_address"]'))
    ).to_be_visible()
    expect(page.locator("#family-home_address-line1")).to_be_disabled()
    review(page)
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0
    show(
        page,
        page.get_by_role("radio", name="Use updated records:", include_hidden=True),
    ).check()
    expect(page.locator("#family-home_address-line1")).to_have_value(
        "Other adult's home"
    )
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()


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
        form=original,
        submit=lambda route: route.fulfill(
            status=409, json={"error": "review_required", "form": fresh}
        ),
    )
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(page.locator(".family-nav")).to_be_visible()
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
    fill_address(page, "home_address", address())
    fill_address(page, "mailing_address", address(line1="Separate draft"))
    page.once("dialog", lambda dialog: dialog.accept())
    show(page, page.get_by_label("Mailing address is the same as home address")).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    show(
        page, page.get_by_role("radio", name="Use my edit:", include_hidden=True)
    ).check()
    show(
        page,
        page.get_by_role("radio", name="Keep addresses separate", include_hidden=True),
    ).check()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "Separate draft"
    )
    review(page)
    page.get_by_role("button", name="Back to edit").click()
    same = show(page, page.get_by_label("Mailing address is the same as home address"))
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
    fill_address(page, "home_address", address(postal_code="invalid"))
    show(page, page.locator("#family-home_address-postal_code")).focus()
    show(page, page.locator("#family-home_address-country")).focus()
    error = page.locator("#family-home_address-postal_code-constraint")
    expect(error).to_have_text("Enter a five-digit ZIP code or ZIP+4.")
    expect(error).to_be_visible()
    expect(page.locator("#family-home_address-postal_code")).to_have_attribute(
        "aria-describedby",
        "family-home_address-status family-home_address-postal_code-constraint",
    )
    show(page, page.locator("#family-home_address-country")).select_option("GB")
    expect(error).to_be_hidden()
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()


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
    fill_address(page, "home_address", address())
    fill_address(page, "mailing_address", address(line1="Retained separate draft"))
    page.once("dialog", lambda dialog: dialog.accept())
    show(page, page.get_by_label("Mailing address is the same as home address")).check()
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    if copy_first:
        show(
            page, page.get_by_role("radio", name="Use my edit:", include_hidden=True)
        ).check()
        page.once("dialog", lambda dialog: dialog.accept())
        show(
            page,
            page.get_by_role("radio", name="Copy home to mailing", include_hidden=True),
        ).check()
    show(
        page,
        page.get_by_role("radio", name="Use updated records:", include_hidden=True),
    ).check()
    if last_choice == "Copy home to mailing":
        page.once("dialog", lambda dialog: dialog.accept())
    show(page, page.get_by_role("radio", name=last_choice, include_hidden=True)).check()
    if last_choice == "Copy home to mailing":
        expect(page.locator("#family-mailing_address-line1")).to_have_value(
            "Other home"
        )
        show(
            page,
            page.get_by_role(
                "radio", name="Keep addresses separate", include_hidden=True
            ),
        ).check()
    expect(page.locator("#family-mailing_address-line1")).to_have_value(
        "Retained separate draft"
    )
    review(page)
    expect(
        show(
            page,
            page.get_by_role(
                "button", name="Submit to Sample Parish", include_hidden=True
            ),
        )
    ).to_be_visible()
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
    review(page)
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
    show(page, page.locator("#family-home_address-line1")).fill("A delivery line")
    # Country defaults to United States, so the US State is what is missing.
    expect(page.locator("#family-home_address-country")).to_have_value("US")
    for component in ("city", "region"):
        expect(
            page.locator(f"#family-home_address-{component}-constraint")
        ).to_be_hidden()
        expect(page.locator(f"#family-home_address-{component}")).to_have_attribute(
            "aria-invalid", "false"
        )
    show(page, page.locator("#family-home_address-city")).focus()
    show(page, page.locator("#family-home_address-line1")).focus()
    expect(
        show(page, page.locator("#family-home_address-city-constraint"))
    ).to_be_visible()
    expect(page.locator("#family-home_address-region-constraint")).to_be_hidden()
    assert not review(page)
    expect(
        show(page, page.locator("#family-home_address-region-constraint"))
    ).to_have_text("Choose a state from the list.")
    assert page.get_by_role("button", name="Submit to Sample Parish").count() == 0


def address_violations(page, axe_source):
    """Run the pinned axe scanner on the Family census controls only."""
    page.evaluate(axe_source)
    return page.evaluate("""async () => (await axe.run(
        {include: [['#household-section']]},
        {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}}
    )).violations.map(({id, nodes}) => ({id, targets: nodes.map(n => n.target)}))""")


def offsets(group):
    """Each address control's top, measured from the fieldset's top.

    Relative to the fieldset, so a scroll the browser makes while a select
    closes cannot hide or fake a shift of the controls themselves.
    """
    return group.evaluate(
        """(fieldset) => {
            const top = fieldset.getBoundingClientRect().top;
            return [...fieldset.querySelectorAll('input, select')]
                .filter((control) => !control.hidden)
                .map((control) => [control.id,
                    Math.round(control.getBoundingClientRect().top - top)]);
        }"""
    )


def test_country_comes_first_with_a_us_state_drop_down(
    page, component_origin, axe_source
):
    """#1777: Country leads and defaults to US; State offers the accepted codes."""
    prepare(page, component_origin)
    group = home_group(page)
    labels = group.locator("label").all_inner_texts()
    assert labels == [
        "Country",
        "Address line 1",
        "Address line 2",
        "City or locality",
        "State",
        "ZIP code",
    ]
    country = group.get_by_label("Country", exact=True)
    expect(country).to_have_value("US")
    state = group.get_by_label("State", exact=True)
    expect(state).to_have_js_property("tagName", "SELECT")
    expect(state).to_have_attribute("id", "family-home_address-region")
    expect(state.locator('option[value="KY"]')).to_have_text("Kentucky (KY)")
    offered = state.locator("option").evaluate_all(
        "options => options.map((option) => option.value).filter(Boolean)"
    )
    assert sorted(offered) == sorted(us_regions())
    # Only the region control for the country is in the page, and the
    # label names it (each control's own labels, as assistive technology
    # reads them, not only the label's target).
    expect(group.locator("#family-home_address-region")).to_have_count(1)
    assert state.evaluate(
        "(control) => [...control.labels].map((l) => l.textContent)"
    ) == ["State"]
    # The keyboard meets the fields in the order they are shown.
    country.focus()
    for label in ("Address line 1", "Address line 2", "City or locality", "State"):
        tab_to_next(page)
        expect(group.get_by_label(label, exact=True)).to_be_focused()
    tab_to_next(page)
    expect(group.get_by_label("ZIP code", exact=True)).to_be_focused()
    assert address_violations(page, axe_source) == []
    country.select_option("CA")
    assert address_violations(page, axe_source) == []
    # A typed state name carries over to the drop-down too.
    group.get_by_label("State, province or region", exact=True).fill("Kentucky")
    country.select_option("US")
    expect(state).to_have_value("KY")


def test_switching_country_swaps_controls_in_place_and_keeps_values(
    page, component_origin
):
    """US and Canada entries each survive a switch; nothing moves or is lost."""
    submissions = []

    def submit(route):
        """Capture the final aggregate request."""
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    prepare(page, component_origin, submit=submit)
    group = home_group(page)
    country = group.get_by_label("Country", exact=True)
    group.get_by_label("Address line 1", exact=True).fill("24 Sussex Drive")
    group.get_by_label("City or locality", exact=True).fill("Ottawa")
    before = offsets(group)

    # A typed abbreviation the State list offers carries over to it.
    country.select_option("CA")
    region = group.get_by_label("State, province or region", exact=True)
    expect(region).to_have_js_property("tagName", "INPUT")
    expect(group.get_by_label("Postal code", exact=True)).to_be_visible()
    assert offsets(group) == before
    region.fill("ky")
    country.select_option("US")
    state = group.get_by_label("State", exact=True)
    expect(state).to_have_value("KY")
    # The longest choice does not move anything either.
    state.select_option("UM")
    assert offsets(group) == before
    state.select_option("KY")
    zip_code = group.get_by_label("ZIP code", exact=True)
    zip_code.fill("40223")

    # A missing State is explained at the drop-down itself.
    state.select_option("")
    state.blur()
    error = group.locator("#family-home_address-region-constraint")
    expect(error).to_have_text("Choose a state from the list.")
    expect(state).to_have_attribute("aria-invalid", "true")
    expect(state).to_have_attribute(
        "aria-describedby",
        "family-home_address-status family-home_address-region-constraint",
    )
    state.select_option("KY")

    # Each country's region is kept while the other is showing.
    country.select_option("CA")
    expect(error).to_be_hidden()
    expect(region).to_have_value("ky")
    region.fill("ON")
    country.select_option("US")
    expect(state).to_have_value("KY")
    expect(zip_code).to_have_value("40223")
    country.select_option("CA")
    expect(region).to_have_value("ON")
    group.get_by_label("Postal code", exact=True).fill("K1A 0A3")

    # A third country, with no region at all.
    mailing = address(
        country="JP",
        line1="1-1 Sample",
        city="Tokyo",
        region="",
        postal_code="100-0001",
    )
    fill_address(page, "mailing_address", mailing)
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
    family = submissions[0]["answers"]["family"]
    assert family["home_address"] == {
        "line1": "24 Sussex Drive",
        "line2": "",
        "city": "Ottawa",
        "region": "ON",
        "postal_code": "K1A 0A3",
        "country": "CA",
    }
    assert family["mailing_address"] == mailing


def test_untouched_address_with_default_country_is_not_provided(page, component_origin):
    """The default United States alone never turns a blank address into one."""
    submissions = []

    def submit(route):
        """Capture the final aggregate request."""
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"accepted": True})

    prepare(page, component_origin, submit=submit)
    group = home_group(page)
    expect(group.get_by_label("Country", exact=True)).to_have_value("US")
    # Typing and then clearing a part leaves the address blank again.
    line1 = group.get_by_label("Address line 1", exact=True)
    line1.fill("Briefly typed")
    line1.fill("")
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
    family = submissions[0]["answers"]["family"]
    assert family["home_address"] == blank_address()
    assert family["mailing_address"] == blank_address()


def test_address_without_a_country_shows_none_until_one_is_chosen(
    page, component_origin
):
    """An address with parts but no country never pretends to be in the US."""
    form = form_payload()
    form["household"]["fields"][0]["value"] = address(country="")
    prepare(page, component_origin, form=form)
    group = home_group(page)
    country = group.get_by_label("Country", exact=True)
    expect(country).to_have_value("")
    assert not review(page)
    expect(group.locator("#family-home_address-country-constraint")).to_have_text(
        "Complete this address field."
    )
    country.select_option("US")
    expect(group.locator("#family-home_address-country-constraint")).to_be_hidden()
    expect(group.get_by_label("State", exact=True)).to_have_value("KY")


def test_server_region_error_follows_the_shown_control(page, component_origin):
    """A server error on the State links to it and leaves when it is hidden."""
    prepare(
        page,
        component_origin,
        submit=lambda route: route.fulfill(
            status=422,
            json={
                "error": "validation",
                "fields": {"family.home_address.region": "Review this state."},
            },
        ),
    )
    fill_address(page, "home_address", address())
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    link = page.locator('#family-flow-message a[href="#family-home_address-region"]')
    expect(link).to_have_text("State: Review this state.")
    link.click()
    group = page.locator("fieldset").filter(
        has=page.locator("#family-home_address-country")
    )
    state = group.get_by_label("State", exact=True)
    expect(state).to_be_focused()
    expect(page.locator("#family-home_address-region-error")).to_be_visible()
    country = group.get_by_label("Country", exact=True)
    country.select_option("CA")
    expect(page.locator("#family-home_address-region-error")).to_have_count(0)
    region = group.get_by_label("State, province or region", exact=True)
    assert region.evaluate(
        "(control) => [...control.labels].map((l) => l.textContent)"
    ) == ["State, province or region"]
    country.select_option("US")
    assert "family-home_address-region-error" not in (
        state.get_attribute("aria-describedby")
    )
    assert state.evaluate(
        "(control) => [...control.labels].map((l) => l.textContent)"
    ) == ["State"]


def test_same_as_home_copies_the_us_state_choice(page, component_origin):
    """The mailing copy shows the home State in its own drop-down."""
    prepare(page, component_origin)
    fill_address(page, "home_address", address(region="NY"))
    show(page, page.get_by_label("Mailing address is the same as home address")).check()
    mailing_state = page.locator("#family-mailing_address-region")
    expect(mailing_state).to_have_js_property("tagName", "SELECT")
    expect(mailing_state).to_have_value("NY")
    expect(mailing_state).to_be_disabled()
    page.locator("#family-home_address-region").select_option("OH")
    expect(mailing_state).to_have_value("OH")


def test_unlisted_us_region_stays_shown_until_a_state_is_chosen(page, component_origin):
    """Free text from records is never dropped from the State drop-down."""
    form = form_payload()
    form["household"]["fields"][0]["value"] = address(region="Kentucky")
    prepare(page, component_origin, form=form)
    group = home_group(page)
    state = group.get_by_label("State", exact=True)
    expect(state).to_have_value("Kentucky")
    # Editing another part keeps it in the answer.
    group.get_by_label("Address line 2", exact=True).fill("Apt 2")
    expect(state).to_have_value("Kentucky")
    assert not review(page)
    expect(group.locator("#family-home_address-region-constraint")).to_have_text(
        "Choose a state from the list."
    )
    # It is kept while another country shows, also across Review and Edit.
    country = group.get_by_label("Country", exact=True)
    country.select_option("CA")
    review(page)
    page.get_by_role("button", name="Back to edit").click()
    group = home_group(page)
    group.get_by_label("Country", exact=True).select_option("US")
    state = group.get_by_label("State", exact=True)
    expect(state).to_have_value("Kentucky")
    state.select_option("KY")
    expect(group.locator("#family-home_address-region-constraint")).to_be_hidden()


def test_redrawing_never_picks_a_state_the_family_cleared(page, component_origin):
    """Carry-over happens only when Country changes, not on a mailing copy."""
    prepare(page, component_origin)
    fill_address(page, "home_address", address(country="CA", region="Texas"))
    group = home_group(page)
    group.get_by_label("Country", exact=True).select_option("US")
    state = group.get_by_label("State", exact=True)
    expect(state).to_have_value("TX")
    state.select_option("")
    show(page, page.get_by_label("Mailing address is the same as home address")).check()
    expect(page.locator("#family-mailing_address-region")).to_have_value("")
    group.get_by_label("Address line 2", exact=True).fill("Unit 3")
    expect(page.locator("#family-mailing_address-region")).to_have_value("")
    expect(state).to_have_value("")


def test_hidden_region_and_blank_country_survive_review_and_edit(
    page, component_origin
):
    """Back to edit rebuilds the form; both regions and the choice stay."""
    prepare(page, component_origin)
    group = home_group(page)
    country = group.get_by_label("Country", exact=True)
    fill_address(page, "home_address", address(country="CA", region="ON"))
    country.select_option("US")
    group.get_by_label("State", exact=True).select_option("KY")
    group.get_by_label("ZIP code", exact=True).fill("40223")
    # A blank mailing address keeps the country chosen for it.
    show(page, page.locator("#family-mailing_address-country")).select_option("IE")
    review(page)
    page.get_by_role("button", name="Back to edit").click()
    group = home_group(page)
    expect(group.get_by_label("State", exact=True)).to_have_value("KY")
    expect(page.locator("#family-mailing_address-country")).to_have_value("IE")
    group.get_by_label("Country", exact=True).select_option("CA")
    expect(group.get_by_label("State, province or region", exact=True)).to_have_value(
        "ON"
    )
