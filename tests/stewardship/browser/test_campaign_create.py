"""Create the campaign (#142): the first campaign's form and its confirmation.

The pages are the ``/campaign-create`` component fixture (the form alone)
and ``campaign_create_components``' pages, whose Create the campaign opens
the shared review dialog as a one-sentence confirmation (#1356) that
repeats none of the form's values. The server's admission, request and
refusals are tested in ``database/test_campaign_create_postgresql.py``.
"""

import json

import pytest

from .campaign_create_components import APPLIED, ONWARD, PAGE, REFUSED, REVIEW, VALUES
from .stepper_components import PATH as STEPPER
from .test_in_place import MARK, MARKED
from .test_settings_in_place import answer_reviews
from .waits import available, eventually, greyed, has_attribute, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

WCAG = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact}) => ({id, impact}))"""
FOCUSED = "document.activeElement && document.activeElement.id"
SUMMARY = "This creates the campaign as a draft in Testing mode; no email is sent."


def fill(page):
    """Fill in the form's required fields, which makes its button available."""
    page.get_by_label("Campaign name").fill(VALUES["name"])
    page.get_by_label("Campaign start date").fill(VALUES["start_date"])
    page.get_by_label("Campaign end date").fill(VALUES["end_date"])


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def refuse(page, origin, action, refusal):
    """Answer ``PAGE``'s ``action`` POST with ``refusal``, a closed 409."""

    def answer(route):
        """The closed JSON refusal for the chosen POST; the rest falls back
        to an earlier route (a Review's answer) or the fixture server."""
        request = route.request
        if request.method != "POST" or f"action={action}" not in (
            request.post_data or ""
        ):
            route.fallback()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps({"errors": [{"code": "stale"}], "refusal": refusal}),
        )

    page.route(lambda url: url.split("#")[0] == origin + PAGE, answer)


def open_confirmation(page, origin):
    """Fill in the form and choose Create the campaign; return the dialog."""
    page.goto(origin + PAGE)
    fill(page)
    page.get_by_role("button", name="Create the campaign").click()
    dialog = page.get_by_role("dialog", name="Create the campaign?")
    visible(dialog)
    return dialog


def test_new_campaign_form_selects_every_ministry_when_enabled(page, component_origin):
    """A new campaign starts with every Ministry once its module is turned on.

    The form is marked data-new-campaign, so checking Ministry stewardship
    selects all the offered Ministries (Campaign settings, editing an existing
    campaign, selects none), and a hidden module posts nothing.
    """
    page.goto(component_origin + "/campaign-create")
    visible(page.get_by_role("heading", name="Create the campaign"))
    ministry = page.get_by_role("group", name="Ministry selections")
    financial = page.get_by_role("group", name="Financial periods and funds")
    assert not ministry.is_visible() and not financial.is_visible()
    page.get_by_label("Ministry stewardship").check()
    visible(ministry)
    included = page.get_by_label("Included Ministries")
    assert included.evaluate("select => select.selectedOptions.length") == 2
    posted = page.locator("[data-campaign-form]").evaluate(
        "form => Array.from(new FormData(form).keys())"
    )
    assert "ministry_duids" in posted and "fund_duids" not in posted
    visible(page.get_by_role("button", name="Create the campaign"))
    assert not page.get_by_role("button", name="Review changes").count()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_create_pages_have_no_accessibility_violations(
    page, component_origin, axe_source
):
    """The form, its open confirmation and the created campaign's status
    pass the same WCAG checks as every Admin page."""
    page.goto(component_origin + "/campaign-create")
    page.evaluate(axe_source)
    assert page.evaluate(WCAG) == [], "form"
    answer_reviews(page, component_origin, REVIEW, address=PAGE)
    open_confirmation(page, component_origin)
    page.evaluate(axe_source)
    assert page.evaluate(WCAG) == [], "confirmation"
    page.goto(component_origin + APPLIED)
    page.evaluate(axe_source)
    assert page.evaluate(WCAG) == [], "created"


def test_create_opens_a_confirmation_that_repeats_nothing(page, component_origin):
    """Create the campaign opens the dialog as a confirmation: focus on its
    title, its one sentence as the dialog's description, the number of
    default pages and emails the server worked out, Apply and Cancel, and
    none of the values typed on the page behind it. Cancel closes it and
    returns focus to the button, changing nothing."""
    reviews = answer_reviews(page, component_origin, REVIEW, address=PAGE)
    dialog = open_confirmation(page, component_origin)
    assert page.evaluate(FOCUSED) == "campaign-create-review-title"
    assert len(reviews) == 1 and "action=preview" in reviews[0]
    from playwright.sync_api import expect

    expect(dialog).to_have_accessible_description(SUMMARY)
    visible(
        dialog.get_by_text("It starts with 7 pages and emails in the default text.")
    )
    text = dialog.inner_text()
    for value in (VALUES["name"], "2054", "October", "Current", "Proposed"):
        assert value not in text, value
    visible(dialog.get_by_role("button", name="Apply"))
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    eventually(page, "document.activeElement.textContent.trim()", "Create the campaign")
    assert page.get_by_label("Campaign name").input_value() == VALUES["name"]


def test_a_field_error_stays_on_the_form(page, component_origin):
    """A Review refused for the form's values marks the field and shows the
    summary on the page; the dialog stays shut."""
    answer_reviews(page, component_origin, REFUSED, 400, address=PAGE)
    page.goto(component_origin + PAGE)
    failures = watch(page)
    fill(page)
    page.get_by_role("button", name="Create the campaign").click()
    visible(page.locator("[data-error-summary]"))
    has_attribute(page.get_by_label("Campaign name"), "aria-invalid", "true")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_apply_creates_the_campaign_in_place(page, component_origin):
    """Apply follows the change and, once it is applied, closes the dialog
    and redraws the page's region from the page naming the change, with no
    reload: the form gives way to the created campaign's status and the
    offer to load its ParishSoft data, the status line says what was
    applied, and the address becomes that page's, so a reload shows the
    same."""
    answer_reviews(page, component_origin, REVIEW, address=PAGE)
    dialog = open_confirmation(page, component_origin)
    failures = watch(page)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(
        page, "document.getElementById('campaign-create-outcome').textContent !== ''"
    )
    line = page.locator("#campaign-create-outcome").inner_text()
    assert line.startswith("Campaign created. Applied at "), line
    assert page.evaluate(FOCUSED) == "campaign-create-outcome"
    region = page.locator("#campaign-create-region")
    visible(region.get_by_text("The campaign has been created."))
    visible(region.get_by_role("button", name="Load the campaign's ParishSoft data"))
    assert page.locator("#settings-form").count() == 0
    assert page.url == component_origin + APPLIED
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_apply_opens_the_next_campaign_setup_step(page, component_origin):
    """Once applied, the dialog opens the step its confirmation names (#1404).

    That step's reserved status line says the campaign was created and
    when, and takes focus, so the Administrator hears where they are.
    """
    answer_reviews(page, component_origin, ONWARD, address=PAGE)
    dialog = open_confirmation(page, component_origin)
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    dialog.get_by_role("button", name="Apply").click()
    page.wait_for_url(component_origin + STEPPER)
    eventually(
        page, "document.getElementById('campaign-step-outcome').textContent !== ''"
    )
    line = page.locator("#campaign-step-outcome").inner_text()
    assert line.startswith("Campaign created. Applied at "), line
    assert page.evaluate(FOCUSED) == "campaign-step-outcome"
    visible(page.locator("#campaign-stepper"))
    assert not failures


@pytest.mark.parametrize("action", ["preview", "confirm"])
def test_a_refusal_shows_in_the_dialog(page, component_origin, action):
    """A Review refused because the campaign can no longer be created, or an
    Apply refused as out of date, shows the server's explanation inside the
    dialog, with no Apply to choose; the form keeps the reader's values."""
    refusal = {
        "message": "The campaign can't be created right now.",
        "fix": "Change it on Campaign settings.",
    }
    if action == "confirm":
        answer_reviews(page, component_origin, REVIEW, address=PAGE)
        refusal = {
            "message": "This page changed in another tab or session.",
            "fix": "Reload the page to start from the current settings.",
        }
    refuse(page, component_origin, action, refusal)
    dialog = open_confirmation(page, component_origin)
    if action == "confirm":
        dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert refusal["message"] in error.inner_text()
    apply = dialog.get_by_role("button", name="Apply")
    # A refused Review has no preview, so no Apply; a refused Apply would be
    # refused again, so it is unavailable.
    if action == "preview":
        assert not apply.is_visible()
    else:
        assert apply.is_disabled()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.get_by_label("Campaign name").input_value() == VALUES["name"]
    assert page.url == component_origin + PAGE


def test_create_waits_for_the_required_fields(page, component_origin):
    """Create the campaign is unavailable until the new campaign is complete.

    The form starts empty, so the complete gate (data-require-complete), not
    the change gate, holds it: its hint names what is missing, in a line
    after the button that keeps its height (data-complete-hint="reserve"),
    so the status line below never moves as the hint comes and goes.
    """
    page.goto(component_origin + "/campaign-create")
    create = page.get_by_role("button", name="Create the campaign")
    hint = page.locator("#settings-complete-hint")
    visible(hint.get_by_text("Fill in the required fields to create the campaign."))
    assert create.is_disabled()
    assert create.get_attribute("aria-describedby") == "settings-complete-hint"
    line = page.locator("#campaign-create-outcome")
    # Page coordinates: filling a field scrolls it into view.
    where = "node => node.getBoundingClientRect().top + window.scrollY"
    top = line.evaluate(where)
    fill(page)
    available(create)
    assert hint.text_content() == ""
    assert line.evaluate(where) == top
    # Emptying a required field holds it again.
    page.get_by_label("Campaign name").fill("")
    greyed(create)
