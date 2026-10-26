"""Setup > Parish logos previews the chosen logo before Save (#2270).

The preview box has a fixed size, draws a chosen image at once from a blob:
object URL (revoked when replaced), shows the saved logo otherwise, and keeps
nothing for a file the page refuses or cannot draw. Nothing below it moves.
"""

import io
import re

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

LIMIT = 5 * 1024 * 1024
BOX = "#logo-choice-preview .file-preview-box"
IMAGE = "#logo-choice-preview img"


def expect(locator):
    """Playwright's retrying assertions for ``locator``."""
    from playwright.sync_api import expect as playwright_expect

    return playwright_expect(locator)


def real_png(color="red"):
    """A small drawable PNG for the logo field."""
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (64, 32), color).save(stream, format="PNG")
    return {"name": "logo.png", "mimeType": "image/png", "buffer": stream.getvalue()}


def open_page(page, origin, path, width=1024):
    """Open a fixture page at ``width``, recording script errors and revokes."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    # Spy on revokeObjectURL so the test can see each object URL released.
    page.add_init_script(
        "window.__revoked = [];"
        "const r = URL.revokeObjectURL.bind(URL);"
        "URL.revokeObjectURL = (u) => { window.__revoked.push(u); r(u); };"
    )
    page.goto(origin + path)
    return failures


def layout(page):
    """The box's size and the Save button's position: what must not move."""
    return page.evaluate(
        """(sel) => {
          const box = document.querySelector(sel).getBoundingClientRect();
          const save = [...document.querySelectorAll('button')]
            .find((b) => b.textContent.includes('Save and continue'))
            .getBoundingClientRect();
          return [box.width, box.height, box.top, save.top, save.left];
        }""",
        BOX,
    )


def shown(page):
    """The preview image's src when it is shown, else None."""
    return page.evaluate(
        "(sel) => { const i = document.querySelector(sel);"
        " return i.hidden ? null : i.getAttribute('src'); }",
        IMAGE,
    )


@pytest.mark.parametrize("width", [320, 1024])
def test_chosen_logo_is_previewed_without_moving_anything(
    page, component_origin, width
):
    """Choosing a file draws it in the box at once; replacing or clearing it
    revokes the earlier object URL; the box and the buttons never move."""
    failures = open_page(page, component_origin, "/setup-branding", width)
    before = layout(page)
    assert shown(page) is None
    assert "No logo chosen yet" in page.locator(BOX).inner_text()
    field = page.locator("#id_logo")
    field.set_input_files(real_png("red"))
    page.locator(IMAGE).wait_for(state="visible")
    first = shown(page)
    assert first.startswith("blob:")
    assert page.locator(IMAGE).evaluate("i => i.complete && i.naturalWidth") == 64
    assert layout(page) == before
    field.set_input_files(real_png("green"))
    assert first in page.evaluate("() => window.__revoked")
    assert shown(page).startswith("blob:") and shown(page) != first
    assert layout(page) == before
    field.set_input_files([])
    assert shown(page) is None
    assert layout(page) == before
    assert not failures


def test_refused_files_keep_no_preview(page, component_origin):
    """A file over 5 MB shows the refusal in the field's reserved line, and a
    file that is not an image is not drawn; neither leaves a preview."""
    failures = open_page(page, component_origin, "/setup-branding")
    before = layout(page)
    field = page.locator("#id_logo")
    field.set_input_files(real_png())
    page.locator(IMAGE).wait_for(state="visible")
    big = {"name": "logo.png", "mimeType": "image/png", "buffer": b"\0" * (LIMIT + 1)}
    field.set_input_files(big)
    error = page.locator("#id_logo_error")
    error.wait_for(state="visible")
    assert error.inner_text().endswith("the limit is 5 MB.")
    assert shown(page) is None
    assert layout(page) == before
    text = {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"}
    field.set_input_files(text)
    assert shown(page) is None
    # Bytes that are not a picture, though named one: the browser cannot draw
    # them, so the preview is dropped rather than left broken.
    field.set_input_files(
        {"name": "logo.png", "mimeType": "image/png", "buffer": b"\0" * 1024}
    )
    page.locator(IMAGE).wait_for(state="hidden")
    assert shown(page) is None
    assert layout(page) == before
    assert not failures


def test_saved_logo_is_previewed_on_a_revisit(page, component_origin):
    """With a logo saved the box shows it before any choice; a chosen file
    replaces it, and a refused one puts the saved logo back."""
    failures = open_page(page, component_origin, "/setup-branding-saved", 320)
    before = layout(page)
    saved = shown(page)
    assert saved.startswith("/admin/setup/branding/assets/")
    assert page.locator(IMAGE).evaluate("i => i.complete && i.naturalWidth") > 0
    assert page.locator(IMAGE).get_attribute("alt") == "The saved logo"
    field = page.locator("#id_logo")
    field.set_input_files(real_png())
    expect(page.locator(IMAGE)).to_have_attribute("src", re.compile("^blob:"))
    assert page.locator(IMAGE).get_attribute("alt") == "The logo you chose"
    assert layout(page) == before
    big = {"name": "logo.png", "mimeType": "image/png", "buffer": b"\0" * (LIMIT + 1)}
    field.set_input_files(big)
    expect(page.locator(IMAGE)).to_have_attribute("src", saved)
    assert shown(page) == saved
    assert layout(page) == before
    assert not failures
