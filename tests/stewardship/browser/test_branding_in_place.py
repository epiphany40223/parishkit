"""Parish logos are reviewed, applied and followed in place (#750).

The pages are branding_components', served at Parish logos' real address so
the in-place answers come from the page's own address. Each test sets a
mark outside every region, which only a full page load could lose.
"""

import io

import pytest

from .branding_components import (
    ADVANCED_PAGE,
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


NO_BASIC = "Your custom logos have no basic equivalent."
UPLOAD = "Upload and review logos"
BASIC_HINT = "Choose an image to upload and review it."
ADVANCED_HINT = "Choose an image for at least one size to upload and review it."


# The upload form's fields as it would send them (a file by its name).
SENT = """() => Object.fromEntries([...new FormData(
    document.getElementById('branding-upload-form'))]
  .filter(([name]) => !['csrfmiddlewaretoken', 'base_digest'].includes(name))
  .map(([name, value]) => [name, value instanceof File ? value.name : value]))"""


def mode(page, name):
    """The shared Basic / Advanced toggle's radio for ``name`` (#1121)."""
    return page.get_by_role("group", name="Mode", exact=True).get_by_role(
        "radio", name=name
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
    """Choosing Advanced (#1062) swaps the one image field for the four
    sizes, each with its one-line rule; a size refused by the server is
    explained at its field and in the summary, drawn in Advanced, the mode
    it was sent from. Nothing above the fields moves."""
    from playwright.sync_api import expect

    answer_with(page, component_origin, REFUSED_SIZE)
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    one = page.get_by_label("Parish logo")
    expect(one).to_be_visible()
    sizes = [page.locator(f"#id_{name}") for name in SIZES]
    for field in sizes:
        expect(field).to_be_hidden()
    choice = mode(page, "Advanced")
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
    expect(mode(page, "Advanced")).to_be_checked()
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
    mode(page, "Advanced").check()
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
    # Upload and review is held while a chosen file is refused.
    expect(page.get_by_role("button", name=UPLOAD)).to_be_disabled()
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
    mode(page, "Advanced").check()
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
    expect(page.get_by_role("button", name=UPLOAD)).to_be_disabled()
    assert "POST" not in posts
    # A smaller large logo releases the menu logo refused for the total.
    page.locator("#id_large").set_input_files(png(1024 * 1024))
    expect(error).to_be_hidden()
    expect(menu).to_have_attribute("aria-invalid", "false")


def test_switching_modes_never_leaves_a_silent_refusal(page, component_origin):
    """A refused size stays with its field while Advanced is left for Basic
    (after the dialog, since a size is chosen) and Upload and review says
    why it is unavailable; choosing a Basic image empties the sizes (it
    replaces all four), so the hidden refusal goes and nothing unseen is
    sent."""
    from playwright.sync_api import expect

    page.goto(component_origin + PAGE)
    mode(page, "Advanced").check()
    favicon = page.locator("#id_favicon")
    favicon.set_input_files(png(256 * 1024 + 1))
    error = page.locator("#id_favicon_error")
    expect(error).to_be_visible()
    mode(page, "Basic").click()
    page.get_by_role("dialog", name=NO_BASIC).get_by_role(
        "button", name="Switch to Basic"
    ).click()
    expect(mode(page, "Basic")).to_be_checked()
    upload = page.get_by_role("button", name=UPLOAD)
    expect(upload).to_be_disabled()
    expect(page.locator("#branding-upload-hint")).to_have_text(BASIC_HINT)
    page.get_by_label("Parish logo").set_input_files(png())
    expect(upload).to_be_enabled()
    assert favicon.evaluate("node => node.files.length") == 0
    assert favicon.evaluate("node => node.validationMessage") == ""
    mode(page, "Advanced").check()
    expect(error).to_be_hidden()
    expect(favicon).not_to_have_attribute("aria-invalid", "true")


def test_basic_is_one_required_image_and_advanced_needs_one_size(
    page, component_origin
):
    """Upload and review is unavailable until a real change, with the reason
    in a reserved line under it: a Basic image, or one Advanced size. The
    toggle and the line never move, and the mode is sent with the upload."""
    from playwright.sync_api import expect

    page.goto(component_origin + PAGE)
    expect(mode(page, "Basic")).to_be_checked()
    upload = page.get_by_role("button", name=UPLOAD)
    hint = page.locator("#branding-upload-hint")
    expect(upload).to_be_disabled()
    expect(hint).to_have_text(BASIC_HINT)
    line = hint.bounding_box()["height"]
    toggle = mode(page, "Advanced").bounding_box()["y"]
    mode(page, "Advanced").check()
    expect(page.get_by_label("Parish logo")).to_be_hidden()
    expect(upload).to_be_disabled()
    expect(hint).to_have_text(ADVANCED_HINT)
    assert mode(page, "Advanced").bounding_box()["y"] == pytest.approx(toggle, abs=1)
    page.locator("#id_menu").set_input_files(png())
    expect(upload).to_be_enabled()
    expect(hint).to_have_text("")
    assert hint.bounding_box()["height"] == pytest.approx(line, abs=1)
    page.locator("#id_menu").set_input_files([])
    expect(upload).to_be_disabled()
    # Back to Basic with no size chosen and logos made from one image: no
    # dialog, and the image field asks again.
    mode(page, "Basic").check()
    expect(page.get_by_role("dialog", name=NO_BASIC)).to_be_hidden()
    expect(hint).to_have_text(BASIC_HINT)
    page.get_by_label("Parish logo").set_input_files(png())
    expect(upload).to_be_enabled()
    # A Basic image stays chosen across a visit to Advanced.
    mode(page, "Advanced").check()
    mode(page, "Basic").check()
    expect(page.get_by_role("dialog", name=NO_BASIC)).to_be_hidden()
    assert page.get_by_label("Parish logo").evaluate("node => node.files.length") == 1
    # What the upload sends: the mode, the one image and no size's file.
    assert page.evaluate(SENT) == {
        "editor_mode": "basic",
        "logo": "logo.png",
        "large": "",
        "menu": "",
        "icon": "",
        "favicon": "",
    }
    upload.click()
    region = page.locator("#settings-review")
    visible(region.get_by_role("heading", name="Review your changes"))


@pytest.mark.parametrize("dismiss", ["Keep Advanced", "Escape"])
def test_logos_saved_from_advanced_open_advanced_and_ask_before_basic(
    page, component_origin, dismiss
):
    """Logos saved from Advanced open in Advanced; Basic has no equivalent,
    so switching asks first, with Keep Advanced focused, and keeping (or
    Escape) changes nothing. Switch to Basic leaves no image chosen."""
    from playwright.sync_api import expect

    page.goto(component_origin + ADVANCED_PAGE)
    expect(mode(page, "Advanced")).to_be_checked()
    for name in SIZES:
        expect(page.locator(f"#id_{name}")).to_be_visible()
    mode(page, "Basic").click()
    dialog = page.get_by_role("dialog", name=NO_BASIC)
    visible(dialog)
    keep = dialog.get_by_role("button", name="Keep Advanced")
    expect(keep).to_be_focused()
    if dismiss == "Escape":
        page.keyboard.press("Escape")
    else:
        keep.click()
    expect(dialog).to_be_hidden()
    expect(mode(page, "Advanced")).to_be_checked()
    expect(page.locator("#id_large")).to_be_visible()
    mode(page, "Basic").click()
    dialog.get_by_role("button", name="Switch to Basic").click()
    expect(mode(page, "Basic")).to_be_checked()
    expect(page.get_by_label("Parish logo")).to_be_visible()
    expect(page.get_by_role("button", name=UPLOAD)).to_be_disabled()


def test_a_size_field_without_its_own_message_still_refuses(page, component_origin):
    """A data-max-bytes field marked before #1062 has no
    data-max-bytes-message (Past campaigns' archive words its refusal in
    data-too-large, #1018): an oversized file is still refused, in that
    text, rather than the check failing on the missing message."""
    from playwright.sync_api import expect

    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    page.goto(component_origin + PAGE)
    mode(page, "Advanced").check()
    favicon = page.locator("#id_favicon")
    favicon.evaluate(
        "node => { node.removeAttribute('data-max-bytes-message');"
        " node.dataset.tooLarge = 'Choose a smaller file.'; }"
    )
    favicon.set_input_files(png(256 * 1024 + 1))
    refusal = "Choose a smaller file."
    expect(page.locator("#id_favicon_error")).to_have_text(refusal)
    assert favicon.evaluate("node => node.validationMessage") == refusal
    assert errors == []
