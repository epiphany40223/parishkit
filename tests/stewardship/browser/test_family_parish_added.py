"""A census change the parish added, in its field on the reviewed form (#2008).

Staff may Add a census change no Family answer asked for (#1710). The
reviewed form draws the added value in its field, editable, with an "Added
by the parish" badge directly under the field (under the address fields for
a Family address; review pipeline spec, "What the reviewed form shows").
These tests give the browser such a payload (``added`` true), at a phone and
a desktop width, and check the badge's place, its description link, that
it stays put while the Family types, that the page does not scroll sideways
and passes axe, and that an unchanged submit sends the added values the
fields showed; without JavaScript the Family page only explains that it
needs it, as before.
"""

import pytest

from .test_components import axe_violations
from .test_family_ministry import begin, ministry_form
from .test_family_response import expect, show
from .test_family_reviewed import capture, field, send_unchanged

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

ADDED_NICKNAME = "Dave"
ADDED_MAILING = {
    "line1": "5 New St",
    "line2": "",
    "city": "Louisville",
    "region": "KY",
    "postal_code": "40202",
    "country": "US",
}


def parish_added_form():
    """A census form whose nickname and mailing address are parish-added."""
    form = ministry_form()
    form["ministries"] = None
    form["modules"] = ["census"]
    nickname = field(form, "nickname")
    nickname.update(value=ADDED_NICKNAME, changed=True, added=True)
    for entry in form["household"]["fields"]:
        entry["added"] = entry["name"] == "mailing_address"
        if entry["added"]:
            entry.update(value=dict(ADDED_MAILING), changed=True)
    return form


def box(locator):
    """A locator's page position and size, rounded, for a no-shift check."""
    found = locator.bounding_box()
    return tuple(round(found[key]) for key in ("x", "y", "width", "height"))


@pytest.mark.parametrize("width", [320, 1280])
def test_a_parish_added_value_is_in_its_field_with_a_badge(
    page, component_origin, axe_source, width
):
    """The field holds the added value; the badge is directly under it,
    describes it, and neither moves nor goes while the Family types; an
    unchanged submit sends the added values."""
    page.set_viewport_size({"width": width, "height": 900})
    submissions, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    begin(page, component_origin, parish_added_form(), capture(submissions))
    nickname = show(page, page.get_by_label("Nickname (optional)"))
    expect(nickname).to_have_value(ADDED_NICKNAME)
    badge = page.locator("#" + nickname.get_attribute("id") + "-added")
    expect(badge).to_have_text("Added by the parish")
    expect(badge).to_be_visible()
    assert (
        badge.get_attribute("id") in nickname.get_attribute("aria-describedby").split()
    )
    # Directly under the field, with only its status line between them.
    assert box(badge)[1] >= box(nickname)[1] + box(nickname)[3]
    assert (
        badge.evaluate("node => node.previousElementSibling.id")
        == nickname.get_attribute("id") + "-status"
    )
    # Only fields drawn with an added value carry a badge.
    assert page.locator(".parish-added").count() == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []

    # Typing changes nothing about the badge: same place under the field
    # (measured from the field, as typing may scroll the page), same words.
    def offset():
        """The badge's place and size relative to the field."""
        field_box, badge_box = box(nickname), box(badge)
        return (
            badge_box[0] - field_box[0],
            badge_box[1] - field_box[1],
            *badge_box[2:],
        )

    before = offset()
    nickname.fill("Davey")
    expect(badge).to_have_text("Added by the parish")
    assert offset() == before
    nickname.fill(ADDED_NICKNAME)
    assert offset() == before
    mailing = page.locator("#family-mailing_address-added")
    show(page, mailing)
    expect(mailing).to_have_text("Added by the parish")
    expect(page.locator("#family-mailing_address-line1")).to_have_value("5 New St")
    group = mailing.locator("xpath=ancestor::fieldset[1]")
    assert group.get_attribute("aria-describedby") == "family-mailing_address-added"
    line1 = page.locator("#family-mailing_address-line1")
    assert (
        "family-mailing_address-added"
        in line1.get_attribute("aria-describedby").split()
    )
    # Under the address fields: after the last control of the fieldset.
    assert mailing.evaluate(
        "node => !node.nextElementSibling ||"
        " !node.nextElementSibling.matches('input, select, label')"
    )
    country = page.locator("#family-mailing_address-country")
    assert box(mailing)[1] >= box(country)[1] + box(country)[3]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    send_unchanged(page)
    (sent,) = submissions
    assert sent["members"]["3"]["nickname"] == ADDED_NICKNAME
    assert sent["family"]["mailing_address"]["line1"] == "5 New St"
    assert not errors


def test_without_javascript_the_page_only_says_it_needs_it(
    browser_engine, component_origin
):
    """The Family form needs JavaScript (its noscript notice); with it off
    the page explains so and shows no form, and so no badge."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.route(
            "**/family/form",
            lambda route: route.fulfill(json={"form": parish_added_form()}),
        )
        page.goto(component_origin + "/family")
        assert "enable JavaScript" in page.locator("noscript").inner_text()
        assert "Added by the parish" not in page.locator("body").inner_text()
    finally:
        context.close()
