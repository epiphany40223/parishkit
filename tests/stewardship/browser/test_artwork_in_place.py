"""Campaign images are reviewed, applied and followed in place (#750).

The pages are artwork_components', served at Campaign images' real address
so the in-place answers come from the page's own address. Each test sets a
mark outside every region, which only a full page load could lose.
"""

import io

import pytest

from .artwork_components import PAGE, PENDING, REMOVE, REVIEW
from .test_in_place import MARK, MARKED
from .test_settings_in_place import FOCUSED, current_step
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The first slot's top edge in the document: focus may scroll the window to
# the review under the slots, but nothing may move a slot on the page.
SLOT_PLACE = (
    "document.querySelector('.artwork-slot').getBoundingClientRect().top"
    " + window.scrollY"
)
# The review region's top edge in the document: the slots above it keep
# their heights, so a slot gaining or losing its image never moves it.
REVIEW_PLACE = (
    "document.getElementById('settings-review').getBoundingClientRect().top"
    " + window.scrollY"
)


def png():
    """A tiny PNG for the file field (the fixture server never reads it)."""
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "green").save(stream, format="PNG")
    return {"name": "banner.png", "mimeType": "image/png", "buffer": stream.getvalue()}


def slot(page, label):
    """The slot panel headed ``label``."""
    return page.locator(".artwork-slot", has=page.get_by_role("heading", name=label))


def test_upload_review_apply_and_status_happen_in_place(page, component_origin):
    """An upload answers with its review under the slots, which takes focus;
    Apply shows the change's status there, and once applied the page
    refreshes itself quietly with the new current image. Nothing reloads,
    no slot moves, and the indicator follows each step."""
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    top = page.evaluate(SLOT_PLACE)
    banner = slot(page, "Banner (top of the Family emails only)")
    banner.get_by_label("Image").set_input_files(png())
    banner.get_by_role("button", name="Upload and review").click()
    region = page.locator("#settings-review")
    visible(region.get_by_role("heading", name="Review your changes"))
    assert page.evaluate(FOCUSED, "#settings-review-title")
    assert "not yet in use" in region.inner_text()
    assert current_step(page) == "Review"
    assert page.url == f"{component_origin}{REVIEW}#settings-review"
    assert page.evaluate(SLOT_PLACE) == pytest.approx(top, abs=1)
    below = page.evaluate(REVIEW_PLACE)
    region.get_by_role("button", name="Apply changes").click()
    visible(region.get_by_role("heading", name="Change status"))
    assert current_step(page) == "Apply"
    visible(region.get_by_role("heading", name="Applied: your change is saved"))
    # The quiet refresh shows the banner as the slot's current image; the
    # slot keeps its height, so the status below did not move (#736).
    visible(banner.get_by_text("Current image"))
    assert page.evaluate(REVIEW_PLACE) == pytest.approx(below, abs=1)
    assert PENDING.split("?")[1] in page.url
    assert page.evaluate(SLOT_PLACE) == pytest.approx(top, abs=1)
    assert page.evaluate(MARKED) == "kept"


def test_remove_this_image_is_reviewed_in_place(page, component_origin):
    """Remove this image shows its review under the slots, without a reload."""
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    top = page.evaluate(SLOT_PLACE)
    slot(page, "Welcome page icon").get_by_role(
        "link", name="Remove this image"
    ).click()
    region = page.locator("#settings-review")
    visible(region.get_by_role("heading", name="Review your changes"))
    assert "Stop showing an image for: Welcome page icon" in region.inner_text()
    assert page.url == f"{component_origin}{REMOVE}#settings-review"
    visible(region.get_by_role("button", name="Apply changes"))
    assert page.evaluate(SLOT_PLACE) == pytest.approx(top, abs=1)
    assert page.evaluate(MARKED) == "kept"


def test_a_refused_upload_is_explained_in_place(page, component_origin):
    """A refused upload redraws the slots with the slot's field error, and
    its summary in the review region, linked to the field, takes focus."""
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    member = slot(page, "Member pages icon")
    member.get_by_label("Image").set_input_files(png())
    member.get_by_role("button", name="Upload and review").click()
    summary = page.locator("#settings-review [data-error-summary]")
    visible(summary)
    visible(summary.locator('a[href="#id_member_image"]'))
    visible(slot(page, "Member pages icon").locator(".errorlist"))
    assert page.url == component_origin + PAGE
    assert page.evaluate(MARKED) == "kept"
