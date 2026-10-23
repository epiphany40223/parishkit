"""A report email's Preview in the browser (#1786, #2035).

A row's Preview and the editor's Preview open the review dialog "Preview —
not sent" with the server's answer (routed here): it has no Apply, a Close
button, the subject and recipients, and the email under a binary choice
Email | Plain text that switches in place, focus kept on the segment. The
dialog fits a 320 px phone without a page scroll, and neither it nor the
page has an accessibility violation, in light and dark, with a real
Daily-shaped and Weekly-shaped email: the email's panel keeps the light
colours in dark, so its headings, links and table headers stay readable.
"""

from io import BytesIO

import pytest

from .report_email_editor_components import EDIT
from .report_emails_components import PAGE, PREVIEW_IMAGE, RENDER, preview_answer
from .test_components import axe_violations
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def _png():
    """A small chart stand-in, served for the preview's image address."""
    from PIL import Image

    data = BytesIO()
    Image.new("RGB", (600, 200), "white").save(data, format="PNG")
    return data.getvalue()


def serve(page, component_origin, path, answer):
    """Route a Preview post at ``path`` to ``answer``, and its image."""
    image = _png()
    page.route(
        component_origin + path,
        lambda route: route.fulfill(status=200, body=answer, content_type="text/html"),
    )
    page.route(
        component_origin + PREVIEW_IMAGE,
        lambda route: route.fulfill(status=200, body=image, content_type="image/png"),
    )


# What each shape's plain text says, which the Plain text view shows.
TEXT = {"weekly": "Please call us.", "daily": "Response funnel (Families)"}
# The light scheme's ink and link colours (ui-v1.css :root), which the
# email's panel keeps in either scheme.
LIGHT = {"ink": "rgb(27, 42, 49)", "link": "rgb(21, 90, 138)"}


def check_light_email(email):
    """The email's headings and links take the light tokens, in any scheme."""
    heading = email.locator("h2").first
    assert heading.evaluate("node => getComputedStyle(node).color") == LIGHT["ink"]
    # Not the closing button, which keeps the portal button's colours.
    link = email.locator("a:not(td[bgcolor] > a)").first
    assert link.evaluate("node => getComputedStyle(node).color") == LIGHT["link"]
    assert email.evaluate("node => getComputedStyle(node).backgroundColor") == (
        "rgb(255, 255, 255)"
    )


def check_dialog(page, dialog, shape="weekly"):
    """The dialog's look and its Email | Plain text pair, in place."""
    from playwright.sync_api import expect

    visible(dialog.get_by_role("heading", name="Preview — not sent"))
    # No Apply: a preview changes nothing.
    expect(dialog.locator("[data-confirm-accept]")).to_be_hidden()
    expect(dialog.get_by_role("button", name="Close")).to_be_visible()
    name = "Daily report" if shape == "daily" else "Weekly report"
    expect(dialog.locator("[data-preview-subject]")).to_contain_text(name)
    recipients = dialog.locator("[data-preview-recipients]")
    expect(recipients).to_contain_text("All Administrators (2 people)")
    expect(recipients).to_contain_text("council@example.org")
    email = dialog.locator("[data-preview-email]")
    text = dialog.locator("[data-preview-text]")
    expect(email).to_be_visible()
    expect(text).to_be_hidden()
    check_light_email(email)
    plain = dialog.get_by_role("button", name="Plain text")
    box = plain.bounding_box()
    # By keyboard: WebKit's click gives a button no focus.
    plain.focus()
    page.keyboard.press("Enter")
    expect(text).to_be_visible()
    expect(email).to_be_hidden()
    assert plain.get_attribute("aria-pressed") == "true"
    assert dialog.get_by_role("button", name="Email").get_attribute("aria-pressed") == (
        "false"
    )
    # The pair stays put and keeps the focus (no layout shift).
    assert plain.bounding_box() == box
    assert plain.evaluate("node => node === document.activeElement")
    expect(text).to_contain_text(TEXT[shape])
    dialog.get_by_role("button", name="Email").click()
    expect(email).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("shape", ["weekly", "daily"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 1280])
def test_a_rows_preview_opens_the_dialog(
    page, component_origin, axe_source, width, scheme, shape
):
    """The row's Preview opens "Preview — not sent", which closes back to it;
    axe passes on both views, the email and its plain text."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    serve(page, component_origin, RENDER, preview_answer(shape=shape))
    page.goto(component_origin + PAGE)
    opener = page.get_by_role("button", name="Preview Weekly report")
    visible(opener)
    opener.click()
    dialog = page.locator("#report-preview-dialog")
    check_dialog(page, dialog, shape)
    assert axe_violations(page, axe_source) == []
    dialog.get_by_role("button", name="Plain text").click()
    visible(dialog.locator("[data-preview-text]"))
    assert axe_violations(page, axe_source) == []
    dialog.get_by_role("button", name="Close").click()
    assert not dialog.evaluate("dialog => dialog.open")
    assert opener.evaluate("node => node === document.activeElement")


def test_preview_is_offered_while_a_send_is_in_progress(page, component_origin):
    """Preview reads only, so a busy row still offers it."""
    page.goto(component_origin + PAGE)
    busy = page.get_by_role("button", name="Preview Finance council report")
    visible(busy)
    assert busy.get_attribute("aria-disabled") is None


@pytest.mark.parametrize("width", [320, 1280])
def test_the_editor_previews_the_form_as_it_is(
    page, component_origin, axe_source, width
):
    """Edit's Preview needs no change, posts render=yes and opens its own
    dialog, never the review's."""
    page.set_viewport_size({"width": width, "height": 900})
    posted = []

    def answer(route):
        """Answer the Preview post, keeping what it sent."""
        posted.append(route.request.post_data or "")
        route.fulfill(status=200, body=preview_answer(), content_type="text/html")

    page.route(component_origin + "/admin/mail/report-emails/" + "*/", answer)
    page.route(
        component_origin + PREVIEW_IMAGE,
        lambda route: route.fulfill(status=200, body=_png(), content_type="image/png"),
    )
    page.goto(component_origin + EDIT)
    preview = page.get_by_role("button", name="Preview", exact=True)
    visible(preview)
    preview.click()
    dialog = page.locator("#report-preview-dialog")
    check_dialog(page, dialog)
    assert not page.locator("#report-review").evaluate("dialog => dialog.open")
    assert posted and "render=yes" in posted[0] and "action=preview" in posted[0]
    assert axe_violations(page, axe_source) == []


def test_the_updating_and_testing_notices(page, component_origin):
    """The answer says when its figures are Updating and when Testing would
    send every message to the Testing recipient."""
    serve(page, component_origin, RENDER, preview_answer(updating=True, testing=True))
    page.goto(component_origin + PAGE)
    page.get_by_role("button", name="Preview Weekly report").click()
    dialog = page.locator("#report-preview-dialog")
    visible(dialog.get_by_text("Updating —", exact=False))
    visible(dialog.get_by_text("Testing mode:", exact=False))
