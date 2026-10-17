"""The directory's Exact Family code refused at its field (#1332).

A malformed code that reaches the server (a request built by hand, or a
browser whose gate did not run) draws the directory again, answered 400,
with the filters sent in the form, the code marked, the message in the
gate's reserved hint row (over the hint, which says the same words and is
hidden while it shows) and in the summary. The fixtures are in
directory_components.
"""

import pytest

from .test_charts import assert_clean, watch

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

MESSAGE = "Type the whole eight-letter Family code, or clear it."
# The places of Apply, the hint row and the content after the filters
# (the matching count) from the filter form's top, and the hint row's and
# hint's heights. The error summary above comes and goes with the mark, so
# places are measured within the form.
PLACES = """() => {
  const form = document.getElementById("table-filters");
  const top = (node) =>
    node.getBoundingClientRect().top - form.getBoundingClientRect().top;
  const row = document.getElementById("directory-filter-hint").parentElement;
  return [
    top(form.querySelector("[type=submit]")),
    top(row),
    top(document.getElementById("directory-summary")),
    row.getBoundingClientRect().height,
    document.getElementById("directory-filter-hint").getBoundingClientRect().height,
  ];
}"""
# Apply's top in page coordinates.
APPLY_BOX = (
    "document.querySelector('#table-filters [type=submit]')"
    ".getBoundingClientRect().y + scrollY"
)


def hint_visibility(page):
    """The gate's hint's computed visibility."""
    return page.locator("#directory-filter-hint").evaluate(
        "node => getComputedStyle(node).visibility"
    )


def assert_refused_at_the_code(page):
    """The code is marked and described by the hint row's one message line.

    The message is said once: the gate's hint, in the same words, is held
    hidden in the same row, at the same place.
    """
    from playwright.sync_api import expect

    code = page.locator("#exact-code")
    expect(code).to_have_attribute("aria-invalid", "true")
    expect(code).to_have_attribute("aria-describedby", "directory-filters_error")
    line = page.locator("#directory-filters_error")
    expect(line).to_have_text(MESSAGE)
    expect(line).to_be_visible()
    assert hint_visibility(page) == "hidden"
    hint = page.locator("#directory-filter-hint")
    assert abs(line.bounding_box()["y"] - hint.bounding_box()["y"]) <= 1
    assert page.locator("#table-filters").inner_text().count(MESSAGE) == 1
    summary = page.locator("[data-error-summary]")
    expect(summary).to_have_count(1)
    link = summary.get_by_role("link", name=MESSAGE)
    expect(link).to_have_attribute("href", "#exact-code")


def test_refused_code_is_shown_at_its_field(page, component_origin):
    """The page refused for its code shows what was sent, marked at the code.

    The summary takes focus and its link moves focus to the code. An edit
    to a whole code clears the mark, the message and the summary; an edit
    that leaves it malformed lets the gate's hint say it, still once. The
    hint row keeps its height throughout, so nothing moves.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/directory-refused-code")
    assert_refused_at_the_code(page)
    expect(page.locator("[data-error-summary]")).to_be_focused()
    expect(page.locator("#exact-code")).to_have_value("ABCD-EFG")
    expect(page.locator("#directory-search")).to_have_value("Example")
    expect(page.locator("#directory-phone")).to_have_value("yes")
    assert page.locator("[data-filter-panel]").evaluate("node => node.open")
    page.locator("[data-error-summary] a").click()
    expect(page.locator("#exact-code")).to_be_focused()
    before = page.evaluate(PLACES)
    page.locator("#exact-code").fill("ABCD-EFGH")
    expect(page.locator("#exact-code")).not_to_have_attribute("aria-invalid", "true")
    assert page.locator("#exact-code").get_attribute("aria-describedby") is None
    expect(page.locator("#directory-filters_error")).to_be_hidden()
    expect(page.locator("[data-error-summary]")).to_have_count(0)
    assert hint_visibility(page) == "visible"
    assert page.evaluate(PLACES) == pytest.approx(before, abs=1)
    page.locator("#exact-code").fill("ABC")
    page.locator("#exact-code").blur()
    expect(page.locator("#directory-filter-hint")).to_have_text(MESSAGE)
    assert page.locator("#table-filters").inner_text().count(MESSAGE) == 1
    assert page.get_by_role("button", name="Apply filters").is_disabled()
    assert page.evaluate(PLACES) == pytest.approx(before, abs=1)
    assert_clean(page, errors)


def test_hint_row_lays_out_as_the_hint_alone(page, component_origin):
    """An ordinary page's hint row is the hint's own line, with no margin,
    and a refused page is laid out the same: it is no taller for a refusal
    only a request built by hand gets."""
    errors = watch(page)
    page.goto(component_origin + "/family-directory")
    row = page.locator(".hint-row-messages")
    margins = row.evaluate(
        "node => { const s = getComputedStyle(node);"
        " return [s.marginTop, s.marginBottom]; }"
    )
    assert margins == ["0px", "0px"]
    places = page.evaluate(PLACES)
    assert places[3] == pytest.approx(places[4], abs=1) and places[4] > 0
    assert hint_visibility(page) == "visible"
    page.goto(component_origin + "/directory-refused-code")
    assert page.evaluate(PLACES) == pytest.approx(places, abs=1)
    assert_clean(page, errors)


def test_in_place_apply_refused_marks_the_kept_filter_form(page, component_origin):
    """A refused in-place Apply marks the code on the form it kept.

    No reload and no shift: the form keeps what was typed and takes the
    mark and message from the 400 answer into the hint row, so Apply stays
    under the pointer, and the summary takes focus.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + "/directory-refused-code").text()
    page.goto(component_origin + "/family-directory")
    page.evaluate("window.__directoryMarker = 1")
    page.route(
        "**/families/",
        lambda route: route.fulfill(status=400, body=answer, content_type="text/html"),
    )
    page.locator("#exact-code").fill("ABCD-EFGH")
    before = page.evaluate(APPLY_BOX)
    page.get_by_role("button", name="Apply filters").click()
    assert_refused_at_the_code(page)
    expect(page.locator("[data-error-summary]")).to_be_focused()
    assert abs(page.evaluate(APPLY_BOX) - before) <= 1
    assert page.evaluate("window.__directoryMarker") == 1
    # What was typed stays; the answer's own values are not written over it.
    expect(page.locator("#exact-code")).to_have_value("ABCD-EFGH")
    page.locator("#exact-code").fill("ABCD-EFGI")
    expect(page.locator("#directory-filters_error")).to_be_hidden()
    assert hint_visibility(page) == "visible"
    # The browser reports the answer's 400 itself; nothing else may appear.
    assert_clean(page, [error for error in errors if "status of 400" not in error])
