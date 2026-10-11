"""Parish logos are reviewed, applied and followed in place (#750).

The pages are branding_components', served at Parish logos' real address so
the in-place answers come from the page's own address. Each test sets a
mark outside every region, which only a full page load could lose.
"""

import io

import pytest

from .branding_components import PAGE, PENDING, REFUSED, REVIEW
from .test_in_place import MARK, MARKED
from .test_settings_in_place import FOCUSED, current_step
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The upload form's top edge in the document: focus may scroll the window to
# the review under it, but nothing may move the form on the page.
FORM_PLACE = (
    "document.getElementById('branding-upload').getBoundingClientRect().top"
    " + window.scrollY"
)
FOLLOWS = (
    "([first, second]) => Boolean(document.querySelector(first)"
    ".compareDocumentPosition(document.querySelector(second))"
    " & Node.DOCUMENT_POSITION_FOLLOWING)"
)


def png():
    """A tiny PNG for the file field (the fixture server never reads it)."""
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(stream, format="PNG")
    return {"name": "logo.png", "mimeType": "image/png", "buffer": stream.getvalue()}


def test_upload_review_apply_and_status_happen_in_place(page, component_origin):
    """An upload answers with every size under the form, which takes focus;
    Apply shows the change's status there, and once applied the page
    refreshes itself quietly with the new current logos below. Nothing
    reloads, the form never moves, and the indicator follows each step."""
    from playwright.sync_api import expect

    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    # The current logos sit below the review region, so logos appearing
    # there can never move the form or the review (#736).
    assert page.evaluate(FOLLOWS, ["#branding-upload", "#settings-review"])
    assert page.evaluate(FOLLOWS, ["#settings-review", "#branding-current"])
    top = page.evaluate(FORM_PLACE)
    page.get_by_label("Parish logo").set_input_files(png())
    page.get_by_role("button", name="Upload and review logos").click()
    region = page.locator("#settings-review")
    visible(region.get_by_role("heading", name="Review your changes"))
    assert page.evaluate(FOCUSED, "#settings-review-title")
    expect(region.locator("figure")).to_have_count(4)
    assert current_step(page) == "Review"
    assert page.url == f"{component_origin}{REVIEW}#settings-review"
    assert page.evaluate(FORM_PLACE) == pytest.approx(top, abs=1)
    region.get_by_role("button", name="Apply changes").click()
    visible(region.get_by_role("heading", name="Change status"))
    assert current_step(page) == "Apply"
    visible(region.get_by_role("heading", name="Applied: your change is saved"))
    # The quiet refresh shows the new current logos.
    expect(page.locator("#branding-current figure")).to_have_count(4)
    assert PENDING.split("?")[1] in page.url
    assert page.evaluate(FORM_PLACE) == pytest.approx(top, abs=1)
    assert page.evaluate(MARKED) == "kept"


def test_a_refused_upload_is_explained_in_place(page, component_origin):
    """A refused upload redraws the form with its field error, and its
    summary in the review region, linked to the field, takes focus."""

    def refuse(route):
        """Answer the upload as the view refuses one."""
        if route.request.method != "POST":
            route.continue_()
            return
        fetched = route.fetch(url=component_origin + REFUSED, method="GET")
        route.fulfill(response=fetched, status=400)

    page.route(lambda url: url.split("#")[0] == component_origin + PAGE, refuse)
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    page.get_by_label("Parish logo").set_input_files(png())
    page.get_by_role("button", name="Upload and review logos").click()
    summary = page.locator("#settings-review [data-error-summary]")
    visible(summary)
    visible(summary.locator('a[href="#id_logo"]'))
    visible(page.locator("#branding-upload .errorlist"))
    assert page.url == component_origin + PAGE
    assert page.evaluate(MARKED) == "kept"
