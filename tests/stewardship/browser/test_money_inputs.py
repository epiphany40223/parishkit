"""Money inputs show a "$" before the field and a plain label (#2192).

The pledge editor (Pledges), Financial stewardship's At least and At most,
and the Family form's annual pledge each show the "$" attached before the
field, a label with no unit on screen, and the unit in the field's
accessible name, without a sideways scroll at 320 px (UX conventions,
Numbers and money).
"""

import pytest

from ..test_financial_answers import CHECK
from .pledges_components import PATH
from .test_family_financial import financial_form
from .test_family_ministry import begin
from .test_family_response import expect, review, show
from .test_pledges_to_enter import icon
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The label's text as drawn: its visually hidden unit left out.
SHOWN_LABEL = """(input) => [...input.labels[0].childNodes]
  .filter((node) => !node.classList?.contains("visually-hidden"))
  .map((node) => node.textContent).join("").trim()"""

FITS = "document.documentElement.scrollWidth <= innerWidth"

# The width the label's hidden unit takes on screen (none, when hidden).
HIDDEN_WIDTH = """(input) => input.labels[0].querySelector(".visually-hidden")
  .getBoundingClientRect().width"""

# A border color of the field and of the "$" box before it.
BORDERS = """(input) => [input, input.previousElementSibling]
  .map((node) => getComputedStyle(node).borderTopColor)"""


def assert_money_field(page, field, name):
    """``field`` shows "$" just before it, the label ``name`` on screen,
    and ``name in dollars`` as its accessible name, all within the window."""
    visible(field)
    prefix = field.locator("xpath=preceding-sibling::*[1]")
    expect(prefix).to_have_class("money-prefix")
    expect(prefix).to_have_text("$")
    expect(prefix).to_have_attribute("aria-hidden", "true")
    assert field.evaluate(SHOWN_LABEL) == name
    assert field.evaluate(HIDDEN_WIDTH) <= 1
    expect(field).to_have_accessible_name(f"{name} in dollars")
    before, box = prefix.bounding_box(), field.bounding_box()
    # Attached: the "$" ends where the field starts, on the field's row.
    assert abs(before["x"] + before["width"] - box["x"]) <= 1
    assert abs(before["y"] - box["y"]) <= 1
    assert abs(before["height"] - box["height"]) <= 1
    width = page.viewport_size["width"]
    assert before["x"] >= 0 and box["x"] + box["width"] <= width
    assert page.evaluate(FITS)


@pytest.mark.parametrize("width", [320, 1280])
def test_the_pledge_editor_shows_its_dollar_prefix(page, component_origin, width):
    """The pledge editor's Annual pledge, inside a dialog that still fits."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    icon(page, 3, "Edit").click()
    editor = page.locator("#pledges-edit-dialog-00000000-0000-0000-0000-000000000003")
    field = editor.get_by_label("Annual pledge in dollars")
    assert_money_field(page, field, "Annual pledge")
    dialog, box = editor.bounding_box(), field.bounding_box()
    assert dialog["x"] >= 0 and dialog["x"] + dialog["width"] <= width
    assert box["x"] + box["width"] <= dialog["x"] + dialog["width"]
    # The dialog does not scroll sideways either.
    assert editor.evaluate("(node) => node.scrollWidth <= node.clientWidth")


@pytest.mark.parametrize("width", [320, 1280])
def test_financial_filters_show_their_dollar_prefix(page, component_origin, width):
    """At least and At most, under a plain Annual pledge legend."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/financial-report")
    expect(page.locator("legend", has_text="Annual pledge")).to_have_text(
        "Annual pledge"
    )
    for name in ("At least", "At most"):
        field = page.get_by_label(f"{name} in dollars")
        field.scroll_into_view_if_needed()
        assert_money_field(page, field, name)


@pytest.mark.parametrize("width", [320, 1280])
def test_the_family_annual_pledge_shows_its_dollar_prefix(
    page, component_origin, width
):
    """The Family form's Annual pledge, under its pledge period."""
    page.set_viewport_size({"width": width, "height": 900})
    begin(page, component_origin, financial_form(), None)
    field = show(page, page.get_by_label("Annual pledge in dollars"))
    assert_money_field(page, field, "Annual pledge")


def test_a_refused_family_pledge_links_by_its_shown_name(page, component_origin):
    """A refused annual pledge's error link says "Annual pledge", not its
    hidden unit, and the "$" box takes the refused field's border color."""
    form = financial_form()
    form["financial"]["answers"] = {
        "annual_pledge": "12.00",
        "frequency": "annual",
        "shares": {CHECK: ""},
    }
    begin(
        page,
        component_origin,
        form,
        lambda route: route.fulfill(
            status=422,
            json={
                "error": "validation",
                "fields": {"financial.annual_pledge": "Enter a smaller pledge."},
            },
        ),
    )
    review(page)
    page.get_by_role("button", name="Submit to Sample Parish").click()
    link = page.locator('#family-flow-message a[href="#financial-annual_pledge"]')
    expect(link).to_have_text("Annual pledge: Enter a smaller pledge.")
    field = page.locator("#financial-annual_pledge")
    expect(field).to_have_attribute("aria-invalid", "true")
    page.mouse.move(0, 0)
    own, prefix = field.evaluate(BORDERS)
    assert own == prefix, (own, prefix)
