"""Hosted files (#346): copy buttons, the slug suggestion and inline images."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_library_copy_buttons_and_slug_suggestion(page, component_origin, tmp_path):
    """Copy buttons appear with script; choosing a file suggests its slug."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/hosted-files")
    table = page.locator("table.hosted-files")
    visible(table.get_by_text("Sample campaign › Family welcome (page)"))
    field = page.locator("#id_slug")
    picked = tmp_path / "Parish Picnic Flyer (2027).pdf"
    picked.write_bytes(b"%PDF-1.7\n%%EOF\n")
    page.locator("#id_file").set_input_files(str(picked))
    page.wait_for_function(
        "document.querySelector('#id_slug').value === 'parish-picnic-flyer-2027'"
    )
    # A name the Admin typed is never replaced.
    field.fill("my-flyer")
    page.locator("#id_file").set_input_files(str(picked))
    assert field.input_value() == "my-flyer"
    if page.evaluate("Boolean(navigator.clipboard && navigator.clipboard.writeText)"):
        copy = page.locator("button[data-copy]").first
        visible(copy)
    # Without script the fields stay selectable read-only text.
    assert page.locator("input.hosted-file-copy").first.get_attribute("readonly") == ""
    assert not failures


def test_hosted_images_load_inline_under_the_page_policy(page, component_origin):
    """A same-origin hosted image loads under the Family pages' CSP and fits."""
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(component_origin + "/hosted-image-page")
    image = page.locator(".content-block img")
    visible(image)
    assert image.evaluate("node => node.complete && node.naturalWidth > 0")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
