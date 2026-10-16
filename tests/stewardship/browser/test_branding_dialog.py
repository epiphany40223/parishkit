"""Parish logos reviewed and applied in the shared review dialog (#750).

The pages are branding_components', served at Parish logos' real address.
An upload posts its files (multipart) without a page load and opens the
dialog with every generated size; once Apply is applied, the dialog closes,
the form and the current logos are redrawn in place and the status line
under the form says what was applied, with no reload and nothing moving. A
refused upload stays on its field; a refused Apply shows in the dialog. The
Basic and Advanced modes (#1062, #1121) are checked here too. Each test
sets a mark outside every region, which only a full page load could lose.
"""

import io
import json
import re

import pytest

from .branding_components import (
    ADVANCED_PAGE,
    BASIC_TO_ADVANCED,
    PAGE,
    REFUSED,
    REFUSED_SIZE,
    REVIEW,
    SIZE_ERROR,
)
from .test_in_place import MARK, MARKED
from .waits import eventually, hidden, same_place, visible

# The four logo sizes uploaded separately (#1062), by field name.
SIZES = ("large", "menu", "icon", "favicon")
pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
# The form's, the status line's and the current logos' places in the
# document: nothing may move them, and logos appearing below never move
# what is above.
PLACES = """() => ['branding-upload', 'branding-outcome', 'branding-current']
    .map((id) => {
        const box = document.getElementById(id).getBoundingClientRect();
        return [box.left + window.scrollX, box.top + window.scrollY];
    })"""
FOLLOWS = (
    "([first, second]) => Boolean(document.querySelector(first)"
    ".compareDocumentPosition(document.querySelector(second))"
    " & Node.DOCUMENT_POSITION_FOLLOWING)"
)
REFUSAL = (
    "These logos expired, or the settings changed after they were uploaded, "
    "so they were not used. Reload the page, then upload the logo again."
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


def applied_line(start):
    """The status line once a change is applied, starting with ``start``."""
    return re.compile(rf"^{re.escape(start)} Applied at \d{{1,2}}:\d{{2}}\s?[AP]M\.$")


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def answer_uploads(page, origin, fixture=REVIEW, status=200):
    """Answer the upload's POST (multipart) with ``fixture`` as the view
    does: the dialog's fragment, or a refused upload's page (status 400).
    Apply's POST goes through to the fixture server. Returns the uploads'
    content types."""
    uploads = []

    def answer(route):
        """Serve ``fixture`` for an upload; let loads and Apply through."""
        kind = route.request.headers.get("content-type", "")
        if route.request.method != "POST" or not kind.startswith("multipart/"):
            route.fallback()
            return
        uploads.append(kind)
        fetched = route.fetch(url=origin + fixture, method="GET")
        route.fulfill(response=fetched, status=status)

    page.route(lambda url: url.split("#")[0] == origin + PAGE, answer)
    return uploads


def dialog_of(page):
    """The review dialog, once it is open."""
    dialog = page.get_by_role("dialog", name="Review parish logos")
    visible(dialog)
    return dialog


def test_an_upload_is_reviewed_and_applied_in_the_dialog(page, component_origin):
    """Upload and review sends the image multipart and opens the dialog on
    its title with every generated size; Apply closes it, redraws the form
    (cleared) and the current logos, and the status line says what was
    applied and takes focus. Nothing reloads and nothing moves."""
    from playwright.sync_api import expect

    uploads = answer_uploads(page, component_origin)
    page.goto(component_origin + PAGE)
    failures = watch(page)
    # The current logos sit below the form and the status line, so logos
    # appearing there can never move either (#736).
    assert page.evaluate(FOLLOWS, ["#branding-upload", "#branding-outcome"])
    assert page.evaluate(FOLLOWS, ["#branding-outcome", "#branding-current"])
    assert page.locator("#settings-review").count() == 0
    places = page.evaluate(PLACES)
    page.locator("#id_logo").set_input_files(png())
    page.get_by_role("button", name=UPLOAD).click()
    dialog = dialog_of(page)
    assert len(uploads) == 1
    assert page.evaluate(FOCUSED) == "branding-review-title"
    assert "not yet in use" in dialog.inner_text()
    expect(dialog.locator("figure")).to_have_count(4)
    expect(page.locator("#branding-current figure")).to_have_count(0)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('branding-outcome').textContent !== ''")
    line = page.locator("#branding-outcome").inner_text()
    assert applied_line("Parish logos changed.").search(line), line
    assert page.evaluate(FOCUSED) == "branding-outcome"
    expect(page.locator("#branding-current figure")).to_have_count(4)
    assert page.locator("#id_logo").evaluate("node => node.files.length") == 0
    assert same_place(page.evaluate(PLACES), places)
    assert page.url == component_origin + PAGE
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_an_advanced_upload_is_applied_and_reopens_in_advanced(page, component_origin):
    """A page opened in Basic, switched to Advanced and one size uploaded:
    the dialog marks that size Changed; once applied the form is redrawn
    from the server in Advanced, the mode that saved the new logos, which
    Basic can no longer show (data-stored-basic), with no reload."""
    from playwright.sync_api import expect

    uploads = answer_uploads(page, component_origin)
    page.goto(component_origin + BASIC_TO_ADVANCED)
    failures = watch(page)
    expect(mode(page, "Basic")).to_be_checked()
    editor = page.locator("[data-editor-modes]")
    expect(editor).to_have_attribute("data-stored-basic", "true")
    mode(page, "Advanced").check()
    page.locator("#id_favicon").set_input_files(png())
    page.get_by_role("button", name=UPLOAD).click()
    dialog = dialog_of(page)
    assert len(uploads) == 1
    visible(dialog.locator("figure.changed"))
    assert dialog.locator("figure.changed").count() == 1
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('branding-outcome').textContent !== ''")
    # Only the redraw from the server's page can say this.
    expect(editor).to_have_attribute("data-stored-basic", "false")
    expect(mode(page, "Advanced")).to_be_checked()
    expect(page.locator("#id_favicon")).to_be_visible()
    assert page.locator("#id_favicon").evaluate("node => node.files.length") == 0
    expect(page.locator("#branding-current figure")).to_have_count(4)
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_refused_upload_stays_on_the_field(page, component_origin):
    """A refused upload marks the field with its error and shows the
    summary above the heading, linked to the field; no dialog opens and
    nothing reloads."""
    answer_uploads(page, component_origin, REFUSED, status=400)
    page.goto(component_origin + PAGE)
    failures = watch(page)
    page.locator("#id_logo").set_input_files(png())
    page.get_by_role("button", name=UPLOAD).click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    visible(summary.locator('a[href="#id_logo"]'))
    visible(page.locator("#id_logo_error"))
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    assert page.url == component_origin + PAGE
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_refused_apply_shows_in_the_dialog(page, component_origin):
    """An Apply refused because the logos expired, or the settings changed
    since, says why in the dialog (reload first), below its buttons, and
    stays until closed; nothing is redrawn and the status line stays
    empty."""
    answer_uploads(page, component_origin)

    def refuse(route):
        """Refuse Apply as the server does; an upload (binary multipart,
        whose body is not text) goes on to the upload's answer."""
        body = route.request.post_data_buffer or b""
        if b"action=confirm" not in body:
            route.fallback()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {"errors": [{"code": "stale"}], "refusal": {"message": REFUSAL}}
            ),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + PAGE, refuse)
    page.goto(component_origin + PAGE)
    failures = watch(page)
    page.locator("#id_logo").set_input_files(png())
    page.get_by_role("button", name=UPLOAD).click()
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "Reload the page, then upload the logo again." in error.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#branding-outcome").inner_text() == ""
    assert page.locator("#branding-current figure").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_each_size_upload_shows_its_fields_and_refusals_on_them(page, component_origin):
    """Choosing Advanced (#1062) swaps the one image field for the four
    sizes, each with its one-line rule; a size refused by the server is
    explained at its field and in the summary, drawn in Advanced, the mode
    it was sent from. Nothing above the fields moves."""
    from playwright.sync_api import expect

    answer_uploads(page, component_origin, REFUSED_SIZE, status=400)
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    one = page.locator("#id_logo")
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
    summary = page.locator("[data-error-summary]")
    visible(summary)
    visible(summary.locator('a[href="#id_favicon"]'))
    assert not page.locator("dialog[data-review-dialog]").is_visible()
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
    page.locator("#id_logo").set_input_files(png())
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
    expect(page.locator("#id_logo")).to_be_hidden()
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
    page.locator("#id_logo").set_input_files(png())
    expect(upload).to_be_enabled()
    # A Basic image stays chosen across a visit to Advanced.
    mode(page, "Advanced").check()
    mode(page, "Basic").check()
    expect(page.get_by_role("dialog", name=NO_BASIC)).to_be_hidden()
    assert page.locator("#id_logo").evaluate("node => node.files.length") == 1
    # What the upload sends: the mode, the one image and no size's file.
    assert page.evaluate(SENT) == {
        "editor_mode": "basic",
        "logo": "logo.png",
        "large": "",
        "menu": "",
        "icon": "",
        "favicon": "",
    }
    answer_uploads(page, component_origin)
    upload.click()
    expect(dialog_of(page).locator("figure")).to_have_count(4)


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
    expect(page.locator("#id_logo")).to_be_visible()
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
