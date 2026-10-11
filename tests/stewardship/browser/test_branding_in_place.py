"""Parish logos are reviewed, applied and followed in place (#750).

The pages are branding_components', served at Parish logos' real address so
the in-place answers come from the page's own address. Each test sets a
mark outside every region, which only a full page load could lose.
"""

import io

import pytest

from .branding_components import (
    PAGE,
    PENDING,
    REFUSED,
    REFUSED_SIZE,
    REVIEW,
    SIZE_ERROR,
)
from .test_in_place import MARK, MARKED
from .test_settings_in_place import FOCUSED, current_step
from .waits import visible

# The four logo sizes uploaded separately (#1062), by field name.
SIZES = ("large", "menu", "icon", "favicon")
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


def png(size=0):
    """A tiny PNG for the file field (the fixture server never reads it),
    padded to ``size`` bytes when given."""
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(stream, format="PNG")
    data = stream.getvalue()
    data += b"\0" * max(0, size - len(data))
    return {"name": "logo.png", "mimeType": "image/png", "buffer": data}


def answer_with(page, origin, refusal):
    """Answer the upload's POST as the view refuses one, with ``refusal``."""

    def refuse(route):
        """Fetch the refusal page and give it as the POST's 400 answer."""
        if route.request.method != "POST":
            route.continue_()
            return
        fetched = route.fetch(url=origin + refusal, method="GET")
        route.fulfill(response=fetched, status=400)

    page.route(lambda url: url.split("#")[0] == origin + PAGE, refuse)


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

    answer_with(page, component_origin, REFUSED)
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    page.get_by_label("Parish logo").set_input_files(png())
    page.get_by_role("button", name="Upload and review logos").click()
    summary = page.locator("#settings-review [data-error-summary]")
    visible(summary)
    visible(summary.locator('a[href="#id_logo"]'))
    visible(page.locator("#id_logo_error"))
    assert page.url == component_origin + PAGE
    assert page.evaluate(MARKED) == "kept"


def test_each_size_upload_shows_its_fields_and_refusals_in_place(
    page, component_origin
):
    """Choosing "Each size separately" (#1062) swaps the one image field for
    the four sizes, each with its one-line rule; a size refused by the
    server is explained at its field and in the summary, with the mode
    still chosen. Nothing above the fields moves."""
    from playwright.sync_api import expect

    answer_with(page, component_origin, REFUSED_SIZE)
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    one = page.get_by_label("Parish logo")
    expect(one).to_be_visible()
    sizes = [page.locator(f"#id_{name}") for name in SIZES]
    for field in sizes:
        expect(field).to_be_hidden()
    choice = page.get_by_label("Each size separately")
    top = choice.bounding_box()["y"]
    choice.check()
    expect(one).to_be_hidden()
    for field in sizes:
        expect(field).to_be_visible()
    visible(page.get_by_text("PNG, square, 32 to 64 pixels, up to 256 KB."))
    assert choice.bounding_box()["y"] == pytest.approx(top, abs=1)
    page.locator("#id_favicon").set_input_files(png())
    page.get_by_role("button", name="Upload and review logos").click()
    summary = page.locator("#settings-review [data-error-summary]")
    visible(summary)
    visible(summary.locator('a[href="#id_favicon"]'))
    favicon = page.locator("#id_favicon")
    expect(favicon).to_have_attribute("aria-invalid", "true")
    visible(page.locator("#branding-upload .errorlist", has_text=SIZE_ERROR))
    expect(page.get_by_label("Each size separately")).to_be_checked()
    assert page.evaluate(MARKED) == "kept"


def test_an_oversized_file_is_refused_before_it_is_sent(page, component_origin):
    """A file over its size's byte limit is refused as soon as it is chosen,
    in the server's words, where the server's errors for the field go: a
    reserved, announced line that keeps its height once the error clears.
    The form is not sent meanwhile."""
    from playwright.sync_api import expect

    posts = []
    page.on("request", lambda request: posts.append(request.method))
    page.goto(component_origin + PAGE)
    page.get_by_label("Each size separately").check()
    favicon = page.locator("#id_favicon")
    line = page.locator("#id_favicon_error").locator("xpath=..")
    expect(line).to_have_attribute("role", "status")
    expect(line).to_have_class("field-error-line")
    height = line.bounding_box()["height"]
    # Its place in the document: the refusal may scroll the window to it.
    place = "node => node.getBoundingClientRect().top + window.scrollY"
    top = favicon.evaluate(place)
    favicon.set_input_files(png(256 * 1024 + 1))
    message = "Favicon: the file is 257 KB; the limit is 256 KB."
    error = page.locator("#id_favicon_error")
    expect(error).to_have_text(message)
    expect(favicon).to_have_attribute("aria-invalid", "true")
    assert "id_favicon_error" in favicon.get_attribute("aria-describedby")
    page.get_by_role("button", name="Upload and review logos").click()
    assert "POST" not in posts
    favicon.set_input_files(png())
    expect(error).to_be_hidden()
    expect(favicon).to_have_attribute("aria-invalid", "false")
    assert line.bounding_box()["height"] == pytest.approx(height, abs=1)
    assert favicon.evaluate(place) == pytest.approx(top, abs=1)


def test_files_together_over_the_limit_are_refused_before_sending(
    page, component_origin
):
    """Files that each fit but together exceed what one upload can carry
    are refused at the field just chosen, and released once they fit."""
    from playwright.sync_api import expect

    posts = []
    page.on("request", lambda request: posts.append(request.method))
    page.goto(component_origin + PAGE)
    page.get_by_label("Each size separately").check()
    page.locator("#id_large").set_input_files(png(4 * 1024 * 1024 + 512 * 1024))
    menu = page.locator("#id_menu")
    menu.set_input_files(png(1024 * 1024))
    error = page.locator("#id_menu_error")
    expect(error).to_have_text(
        "Together the chosen files are 5.5 MB; one upload can carry at most "
        "5 MB. Upload the larger sizes on their own."
    )
    expect(menu).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#id_large")).not_to_have_attribute("aria-invalid", "true")
    page.get_by_role("button", name="Upload and review logos").click()
    assert "POST" not in posts
    # A smaller large logo releases the menu logo refused for the total.
    page.locator("#id_large").set_input_files(png(1024 * 1024))
    expect(error).to_be_hidden()
    expect(menu).to_have_attribute("aria-invalid", "false")


def test_switching_modes_never_leaves_a_silent_refusal(page, component_origin):
    """A size refusal goes with its field when the other mode is chosen,
    and comes back, shown, with the field: the form is never held invalid
    without saying why, nor sent past a refusal."""
    from playwright.sync_api import expect

    page.goto(component_origin + PAGE)
    page.get_by_label("Each size separately").check()
    favicon = page.locator("#id_favicon")
    favicon.set_input_files(png(256 * 1024 + 1))
    error = page.locator("#id_favicon_error")
    expect(error).to_be_visible()
    page.get_by_label("One image for all sizes").check()
    assert favicon.evaluate("node => node.validationMessage") == ""
    assert favicon.evaluate("node => node.dataset.sizeRule") is None
    page.get_by_label("Each size separately").check()
    expect(error).to_have_text("Favicon: the file is 257 KB; the limit is 256 KB.")
    expect(favicon).to_have_attribute("aria-invalid", "true")
    assert favicon.evaluate("node => node.validationMessage") != ""
