"""The reviewed Family form (#1705) in the real Family flow on every engine.

The server draws a returning Family's form from the reviewed result
(review pipeline spec, "Reviewed Family form"): a reviewer's value in the
field with the usual change marker, an Ignored change as ParishSoft has it,
accepted Ministry choices already chosen and an edited pledge's amount. The
form payload keeps its shape, so these tests give the browser such a
payload and check that the Family sees those values with today's controls
and markers, that nothing names the review, and that sending the form
unchanged sends exactly the values it showed.
"""

from types import SimpleNamespace

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
from .test_family_financial import financial_form
from .test_family_ministry import begin, ministry_form
from .test_family_response import expect, review, show

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Words of the review pipeline that a Family page must never show.
PIPELINE_WORDS = (
    "Accepted",
    "Ignored",
    "To review",
    "Reviewer",
    "Added by the parish",
    "ParishSoft",
    "reviewed",
)


def field(form, name):
    """Member 3's payload field ``name``."""
    (found,) = [item for item in form["members"][0]["fields"] if item["name"] == name]
    return found


def capture(submissions):
    """A submit route that records each final aggregate and accepts it."""

    def submit(route):
        """Only the definitive Submit reaches the server."""
        submissions.append(route.request.post_data_json["answers"])
        route.fulfill(json={"accepted": True})

    return submit


def send_unchanged(page):
    """Review and submit the form exactly as it was drawn."""
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    expect(
        show(page, page.get_by_role("heading", name="Thank you!", include_hidden=True))
    ).to_be_visible()


def assert_no_pipeline_words(page):
    """The Family's page names no stage, decision, reviewer or ParishSoft."""
    text = page.locator("body").inner_text()
    for word in PIPELINE_WORDS:
        assert word not in text, word


def reviewed_census_form():
    """A census form drawn as reviewed: an edited first name (the reviewer's
    value, marked changed); every other field is as the parish records have
    it, unmarked."""
    form = ministry_form()
    form["ministries"] = None
    form["modules"] = ["census"]
    edited = field(form, "first_name")
    edited.update(value="Alexander", changed=True)
    return form


def test_a_reviewed_census_value_is_shown_marked_and_sent_back(page, component_origin):
    """The reviewer's value fills the field with today's change marker; an
    unchanged submit sends it, and the page names nothing of the review."""
    page.set_viewport_size({"width": 320, "height": 900})
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, reviewed_census_form(), capture(submissions))
    name = show(page, page.get_by_label("First name (required)"))
    expect(name).to_have_value("Alexander")
    expect(page.locator("#" + name.get_attribute("id") + "-status")).to_have_text(
        "Changed from parish records."
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_no_pipeline_words(page)
    send_unchanged(page)
    assert submissions[0]["members"]["3"]["first_name"] == "Alexander"
    assert not errors


def test_reviewed_ministry_choices_are_chosen_and_sent_back(page, component_origin):
    """An accepted join is already chosen and listed under Joining, an
    accepted leave is Stop participating; an unchanged submit sends both."""
    form = ministry_form()
    form["ministries"]["members"]["3"] = {"current": [4], "join": [9], "leave": [4]}
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, form, capture(submissions))
    joining = page.locator(".ministry-joining li", has_text="Food pantry").first
    expect(show(page, joining)).to_be_visible()
    choir = page.get_by_role("group", name="Choir", include_hidden=True)
    expect(choir.get_by_label("Stop participating in this ministry")).to_be_checked()
    # Opening the join list shows the accepted join chosen, as the Family's
    # own choice would be.
    show(
        page, page.get_by_text("Click here to join more ministries", exact=True)
    ).click()
    pantry = page.get_by_role(
        "checkbox", name="Food pantry", exact=True, include_hidden=True
    )
    expect(show(page, pantry)).to_be_checked()
    assert_no_pipeline_words(page)
    send_unchanged(page)
    assert submissions[0]["ministries"]["members"]["3"] == {"join": [9], "leave": [4]}
    assert not errors


def reviewed_financial_form():
    """A financial form drawn by the real presenter as a reviewed form shows
    an edited pledge: the reviewer's $1,300.00 quarterly over the Family's
    $1,200.00 monthly, with the Family's own share method."""
    form = financial_form()
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
        giving_observation(cursor(definition), definition),
        family_duid=1,
        pledges=[record("1200.00")],
        contributions=[record("500.00")],
    )
    own = {
        "annual_pledge": "1200.00",
        "frequency": "monthly",
        "shares": {CHECK: ""},
        "cannot_give": False,
    }
    form["financial"] = financial_presentation(
        inputs,
        SimpleNamespace(answers={"financial": own}),
        parish_name="Sample Parish",
        shown=own | {"annual_pledge": "1300.00", "frequency": "quarterly"},
    )
    return form


def test_an_edited_pledge_is_shown_and_sent_back(page, component_origin):
    """The reviewer's amount and frequency fill the pledge fields with the
    Family's own share method; an unchanged submit sends them."""
    form = reviewed_financial_form()
    assert form["financial"]["answers"]["annual_pledge"] == "1300.00"
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, form, capture(submissions))
    amount = show(page, page.get_by_label("Annual pledge (USD)"))
    assert amount.input_value().replace(",", "") == "1300.00"
    expect(page.get_by_label("Pledge frequency")).to_have_value("quarterly")
    expect(page.locator(f"#financial-option-{CHECK}")).to_be_checked()
    assert_no_pipeline_words(page)
    send_unchanged(page)
    sent = submissions[0]["financial"]
    assert (sent["annual_pledge"].replace(",", ""), sent["frequency"]) == (
        "1300.00",
        "quarterly",
    )
    assert sent["shares"] == {CHECK: ""}
    assert not errors
