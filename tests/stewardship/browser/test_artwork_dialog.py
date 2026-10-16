"""Campaign images reviewed and applied in the shared review dialog (#750).

The pages are artwork_components', served at Campaign images' real address.
An upload posts its image (multipart) without a page load and opens the
dialog with the image as it will appear; Remove this image opens it with a
confirmation of the removal (#1356). Once Apply is applied, the dialog
closes, the slots are redrawn in place with the new current image and the
status line under them says what was applied, with no reload and no slot
moving. A refused upload stays on
the slot's field; a refused Apply shows in the dialog. Each test sets a mark
outside every region, which only a full page load could lose.
"""

import io
import json
import re

import pytest

from .artwork_components import PAGE, REMOVAL
from .test_in_place import MARK, MARKED
from .test_settings_in_place import answer_reviews
from .waits import eventually, has_attribute, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
# The first slot's and the last slot's status line's places in the document:
# nothing may move a slot, and a slot gaining an image never moves what is
# below it.
PLACES = """() => [document.querySelector('.artwork-slot'),
    document.getElementById('artwork-outcome-closing')].map((node) => {
        const box = node.getBoundingClientRect();
        return [box.left + window.scrollX, box.top + window.scrollY];
    })"""


def applied_line(start):
    """The status line once a change is applied, starting with ``start``."""
    return re.compile(rf"^{re.escape(start)} Applied at \d{{1,2}}:\d{{2}}\s?[AP]M\.$")


def said(slot):
    """A script that is true once ``slot``'s own status line says something."""
    return f"document.getElementById('artwork-outcome-{slot}').textContent !== ''"


def png():
    """A tiny PNG for the file field (the fixture server never reads it)."""
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "green").save(stream, format="PNG")
    return {"name": "banner.png", "mimeType": "image/png", "buffer": stream.getvalue()}


def slot(page, label):
    """The slot panel headed ``label``."""
    return page.locator(".artwork-slot", has=page.get_by_role("heading", name=label))


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def dialog_of(page, name="Review campaign image"):
    """The review dialog, once it is open, named ``name``."""
    dialog = page.get_by_role("dialog", name=name)
    visible(dialog)
    return dialog


def upload_banner(page):
    """Choose an image for the banner and review it; returns the slot."""
    banner = slot(page, "Banner (top of the Family emails only)")
    banner.get_by_label("Image").set_input_files(png())
    banner.get_by_role("button", name="Upload and review").click()
    return banner


def test_an_upload_is_reviewed_and_applied_in_the_dialog(page, component_origin):
    """Upload and review sends the image multipart, as the form would, and
    opens the dialog on its title with the image; Apply closes it, redraws
    the slots with the banner as the slot's current image, and the status
    line says what was applied and takes focus. Nothing reloads and neither
    a slot nor the status line moves."""
    page.goto(component_origin + PAGE)
    failures = watch(page)
    places = page.evaluate(PLACES)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        banner = upload_banner(page)
    assert sent.value.headers["content-type"].startswith("multipart/form-data")
    dialog = dialog_of(page)
    assert page.evaluate(FOCUSED) == "artwork-review-title"
    assert "not yet in use" in dialog.inner_text()
    # An upload keeps its full review (class C, #1356): no confirmation.
    assert dialog.get_attribute("aria-describedby") is None
    # The staged image as it will appear (the fixture server has no file).
    assert dialog.locator("img.branding-preview.artwork-banner").count() == 1
    assert banner.get_by_text("Current image").count() == 0
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, said("banner"))
    line = page.locator("#artwork-outcome-banner")
    assert applied_line("Campaign image changed.").search(line.inner_text())
    assert page.evaluate(FOCUSED) == "artwork-outcome-banner"
    visible(banner.get_by_text("Current image"))
    visible(banner.get_by_role("button", name="Remove this image"))
    assert page.locator("[data-edits-lost]").count() == 0
    assert same_place(page.evaluate(PLACES), places)
    assert page.url == component_origin + PAGE
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_remove_this_image_is_confirmed_in_the_dialog(page, component_origin):
    """Remove this image, offered only where a slot has an image, opens the
    dialog as a confirmation (#1356): "Remove this image?", described by its
    one sentence. Apply closes it, and the status line says the image was
    removed and takes focus. Nothing reloads, and neither a slot nor the
    status line moves when the slots are redrawn."""
    reviews = answer_reviews(page, component_origin, REMOVAL, address=PAGE)
    page.goto(component_origin + PAGE)
    failures = watch(page)
    places = page.evaluate(PLACES)
    assert page.get_by_role("button", name="Remove this image").count() == 1
    slot(page, "Welcome page icon").get_by_role(
        "button", name="Remove this image"
    ).click()
    dialog = dialog_of(page, "Remove this image?")
    assert page.evaluate(FOCUSED) == "artwork-review-title"
    assert dialog.get_attribute("aria-describedby") == "artwork-review-summary"
    summary = dialog.locator("#artwork-review-summary").inner_text()
    assert summary == (
        "Welcome page icon: Families will see no image there until you upload"
        " a new one."
    )
    assert "Nothing changes until" not in dialog.inner_text()
    assert len(reviews) == 1 and "slot=welcome" in reviews[0]
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, said("welcome"))
    line = page.locator("#artwork-outcome-welcome").inner_text()
    assert applied_line("Campaign image removed.").search(line), line
    assert page.evaluate(FOCUSED) == "artwork-outcome-welcome"
    assert same_place(page.evaluate(PLACES), places)
    assert page.evaluate(MARKED) == "kept"
    assert not failures


# A window short enough that the last slot is well below the fold.
SHORT_WINDOW = {"width": 1280, "height": 600}


@pytest.mark.parametrize("change", ["upload", "remove"])
def test_the_status_line_is_in_the_slot_and_the_page_never_jumps(
    page, component_origin, change
):
    """Once a change is applied, the status line saying so is the one in
    the slot's own pane, beside the control that made the change, and the
    page does not scroll: no jump to a line at the bottom of the page
    (UX conventions, "Review dialog"). Every other slot's line stays
    empty."""
    page.set_viewport_size(SHORT_WINDOW)
    if change == "remove":
        answer_reviews(page, component_origin, REMOVAL, address=PAGE)
    page.goto(component_origin + PAGE)
    failures = watch(page)
    if change == "upload":
        changed, start, name = "banner", "Campaign image changed.", "Banner"
        upload_banner(page)
        title = "Review campaign image"
    else:
        changed, start, name = "welcome", "Campaign image removed.", "Welcome"
        slot(page, "Welcome page icon").get_by_role(
            "button", name="Remove this image"
        ).click()
        # Remove this image is a confirmation (#1356).
        title = "Remove this image?"
    dialog = dialog_of(page, title)
    scrolled = page.evaluate("window.scrollY")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, said(changed))
    pane = page.locator(
        ".artwork-slot", has=page.locator(f"#artwork-outcome-{changed}")
    )
    assert pane.get_by_role("heading").inner_text().startswith(name)
    line = pane.get_by_role("status")
    assert applied_line(start).search(line.inner_text())
    assert page.evaluate(FOCUSED) == f"artwork-outcome-{changed}"
    assert page.evaluate("window.scrollY") == pytest.approx(scrolled, abs=1)
    # The line is in view where the reader already was.
    box = line.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= SHORT_WINDOW["height"]
    others = page.evaluate(
        "[...document.querySelectorAll('[data-review-outcome]')]"
        ".filter((node) => node.tagName === 'P' && node.textContent).map((n) => n.id)"
    )
    assert others == [f"artwork-outcome-{changed}"]
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_refused_upload_stays_on_the_field(page, component_origin):
    """A refused upload marks the slot's field with its error and shows the
    summary, linked to the field; no dialog opens and nothing reloads."""
    page.goto(component_origin + PAGE)
    failures = watch(page)
    member = slot(page, "Member pages icon")
    member.get_by_label("Image").set_input_files(png())
    member.get_by_role("button", name="Upload and review").click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    visible(summary.locator('a[href="#id_member_image"]'))
    has_attribute(page.locator("#id_member_image"), "aria-invalid", "true")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    assert page.url == component_origin + PAGE
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_refused_apply_shows_in_the_dialog(page, component_origin):
    """An Apply refused because the upload expired, or the settings changed
    since, says why in the dialog, below its buttons, and stays until
    closed; the slots are not redrawn and the status line stays empty."""

    def refuse(route):
        """Refuse Apply as the server does."""
        if "action=confirm" not in (route.request.post_data or ""):
            route.continue_()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "errors": [{"code": "stale"}],
                    "refusal": {
                        "message": "This upload expired, or the settings "
                        "changed after it was uploaded, so it was not used. "
                        "Reload the page, then upload the image again."
                    },
                }
            ),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + PAGE, refuse)
    page.goto(component_origin + PAGE)
    failures = watch(page)
    banner = upload_banner(page)
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "Reload the page, then upload the image again." in error.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#artwork-outcome-banner").inner_text() == ""
    assert banner.get_by_text("Current image").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert not failures


# The bottom edge of the slot headed by the argument, in the document.
SLOT_BOTTOM = (
    "(label) => [...document.querySelectorAll('.artwork-slot')]"
    ".find((s) => s.querySelector('h2').textContent === label)"
    ".getBoundingClientRect().bottom + window.scrollY"
)


def test_upload_waits_for_an_image_and_an_unshown_slot_says_why(page, component_origin):
    """Upload and review waits for a chosen image, saying so in a line kept
    after the button, so choosing one moves nothing. The Financial page icon
    of a campaign without Financial stewardship is greyed in place, its
    Upload named by why (#563)."""
    page.goto(component_origin + PAGE)
    closing = slot(page, "Closing page icon")
    button = closing.get_by_role("button", name="Upload and review")
    hint = closing.locator("#artwork-hint-closing")
    assert button.is_disabled()
    assert hint.inner_text() == "Choose an image to upload."
    assert button.get_attribute("aria-describedby") == "artwork-hint-closing"
    bottom = page.evaluate(SLOT_BOTTOM, "Closing page icon")
    closing.get_by_label("Image").set_input_files(png())
    assert button.is_enabled()
    assert hint.inner_text() == ""
    assert page.evaluate(SLOT_BOTTOM, "Closing page icon") == pytest.approx(
        bottom, abs=1
    )
    financial = slot(page, "Financial page icon")
    unavailable = financial.get_by_role("button", name="Upload and review")
    assert unavailable.is_disabled()
    field = financial.get_by_label("Image")
    assert field.is_disabled()
    assert field.get_attribute("aria-describedby") == (
        "id_financial_image_helptext artwork-hint-financial"
    )
    reason = financial.locator("#artwork-hint-financial")
    assert "Financial stewardship is off" in reason.inner_text()
    assert unavailable.get_attribute("aria-describedby") == "artwork-hint-financial"
