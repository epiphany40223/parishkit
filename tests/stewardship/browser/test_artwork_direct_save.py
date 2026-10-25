"""Campaign images' Save and continue in the campaign wizard (#1404 PR 4c).

Campaign images as the wizard draws it (``artwork_components.STEP``): no
Upload and review; a chosen image turns the action row's Continue into Save
and continue, in the same place. Save and continue stages each chosen image
at its slot's upload address, then posts one save naming the staged images,
follows the change and opens the next step not done yet. A refused image is
said in its slot's own line, with nothing saved, and the images already
staged are not uploaded again on the retry.
"""

import json
import re
from urllib.parse import parse_qs

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.artwork_views import NOT_STATIC_IMAGE

from .artwork_components import BUNDLE, PAGE, REFUSED_IMAGE, REMOVAL, STEP, UPLOAD
from .review_helpers import answer_reviews
from .test_artwork_dialog import png, slot
from .waits import available, has_attribute, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

BANNER = "Banner (top of the Family emails only)"
WELCOME = "Welcome page icon"
# Where Save and continue goes once applied: the next step not done yet.
GO_LIVE = reverse("admin:go_live")
# Delay every fetch the page makes by a second.
SLOW_FETCH = """() => {
  const real = window.fetch;
  window.fetch = (url, init) =>
    new Promise((done) => setTimeout(done, 1000)).then(() => real(url, init));
}"""


def primary(page):
    """The action row's Continue, its Save and continue, and their cell."""
    row = page.locator("[data-campaign-step-actions]")
    return (
        row.locator("[data-campaign-continue] button"),
        row.locator("[data-campaign-save]"),
        row.locator("[data-campaign-primary]"),
    )


def placed(button, cell):
    """The cell's and the visible ``button``'s boxes within the row, read in
    one script so they come from one layout."""
    return button.evaluate(
        """(button, cell) => {
          const row = cell
            .closest("[data-campaign-step-actions]")
            .getBoundingClientRect();
          return [cell, button].flatMap((node) => {
            const box = node.getBoundingClientRect();
            return [box.x - row.x, box.y - row.y, box.width, box.height];
          });
        }""",
        cell.element_handle(),
    )


def watch_posts(page, origin):
    """Note every POST the page sends (address, body); let each through."""
    sent = []

    def handle(route):
        """Note the POST and pass it on to the fixture server."""
        if route.request.method == "POST":
            sent.append((route.request.url.removeprefix(origin), route.request))
        route.continue_()

    page.route(lambda url: url.startswith(origin + PAGE), handle)
    return sent


def test_a_chosen_image_turns_continue_into_save_and_continue(page, component_origin):
    """Choosing an image is the step's change; clearing it gives Continue back."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + STEP)
    proceed, save, cell = primary(page)
    visible(proceed)
    hidden(save)
    assert proceed.inner_text() == "Continue to Go live"
    # The slots draw no Upload and review and open no review dialog.
    assert page.get_by_role("button", name="Upload and review").count() == 0
    assert page.locator("form[data-review-open][data-wizard-upload]").count() == 0
    before = placed(proceed, cell)
    field = slot(page, BANNER).get_by_label("Image")
    field.set_input_files(png())
    available(save)
    hidden(proceed)
    assert save.inner_text() == "Save and continue"
    assert same_place(placed(save, cell), before)
    field.set_input_files([])
    available(proceed)
    hidden(save)
    assert same_place(placed(proceed, cell), before)
    assert failures == []


def test_save_and_continue_stages_each_image_then_saves(page, component_origin):
    """The image is staged at its slot's address, then one save names it.

    While it is staged and saved the slots' region is held (inert and busy),
    so no file can change and no image be removed under the save.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = watch_posts(page, component_origin)
    page.route(
        component_origin + GO_LIVE,
        lambda route: route.fulfill(content_type="text/html", body="<p>Go live</p>"),
    )
    page.goto(component_origin + STEP)
    _, save, _ = primary(page)
    slot(page, BANNER).get_by_label("Image").set_input_files(png())
    # Each request answers a moment late, so the held region can be seen.
    page.evaluate(SLOW_FETCH)
    save.click()
    has_attribute(page.locator("#artwork-slots"), "aria-busy", "true")
    assert page.locator("#artwork-slots").evaluate("(region) => region.inert")
    page.wait_for_url(component_origin + GO_LIVE)
    assert [address for address, _ in sent] == [UPLOAD, PAGE]
    upload, saved = (request for _, request in sent)
    assert "multipart/form-data" in upload.headers["content-type"]
    body = parse_qs(saved.post_data)
    assert body["action"] == ["save"] and body["bundle_banner"] == [str(BUNDLE)]
    assert set(body) == {
        "csrfmiddlewaretoken",
        "action",
        "request_key",
        "base_digest",
        "bundle_banner",
    }
    # The next step's status line says what was saved (kept for that page).
    kept = json.loads(page.evaluate("sessionStorage.getItem('pk-review-outcome')"))
    assert kept["path"] == GO_LIVE
    assert kept["text"].startswith("Campaign images saved. Applied at ")
    assert failures == []


def test_a_save_redrawn_in_place_says_no_image_was_lost(page, component_origin):
    """A save redrawn in place draws the slots anew with no "not kept" line.

    When the page read again once the save is applied names no later step
    (the wizard ended meanwhile), the page is redrawn in place, drawing the
    slot's form again. The chosen image went with the save, so nothing says
    it was lost (#2185).
    """
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    loads = []

    def no_next(route):
        """Load the step first; every later load names no next step."""
        loads.append(route.request.url)
        if len(loads) == 1:
            route.continue_()
            return
        fetched = route.fetch()
        body = re.sub(r' data-next-url="[^"]*"', "", fetched.text())
        route.fulfill(response=fetched, body=body)

    page.route(component_origin + STEP, no_next)
    page.goto(component_origin + STEP)
    form = page.locator("#artwork-upload-banner")
    form.evaluate("(form) => { form.dataset.drawn = 'first'; }")
    _, save, _ = primary(page)
    slot(page, BANNER).get_by_label("Image").set_input_files(png())
    save.click()
    # The slot's form drawn anew carries no mark of the first drawing.
    expect(form).not_to_have_attribute("data-drawn", "first")
    expect(form).to_be_attached()
    # The "not kept" line would be added in a microtask after the swap.
    page.evaluate("() => new Promise((done) => setTimeout(done, 50))")
    assert page.locator("[data-edits-lost]").count() == 0
    assert page.url == component_origin + STEP
    assert len(loads) == 2
    assert failures == []


def test_a_refused_image_is_said_in_its_slot_and_saves_nothing(page, component_origin):
    """A refused image is marked at its field and said in its slot's line.

    The banner, staged before the refusal, is not uploaded again when Save
    and continue is pressed again, but is once its file is changed; nothing
    is saved any time.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = watch_posts(page, component_origin)
    page.goto(component_origin + STEP)
    _, save, _ = primary(page)
    slot(page, BANNER).get_by_label("Image").set_input_files(png())
    welcome = slot(page, WELCOME).get_by_label("Image")
    welcome.set_input_files(png())
    save.click()
    line = page.locator("#artwork-outcome-welcome")
    has_text(line, f"Image: {NOT_STATIC_IMAGE['section']}")
    assert welcome.get_attribute("aria-invalid") == "true"
    assert page.url == component_origin + STEP
    available(save)
    save.click()
    has_text(line, f"Image: {NOT_STATIC_IMAGE['section']}")
    available(save)
    # A changed file drops its staged image, so it is staged again.
    slot(page, BANNER).get_by_label("Image").set_input_files(png())
    save.click()
    has_text(line, f"Image: {NOT_STATIC_IMAGE['section']}")
    available(save)
    assert [address for address, _ in sent] == [
        UPLOAD,
        REFUSED_IMAGE,
        REFUSED_IMAGE,
        UPLOAD,
        REFUSED_IMAGE,
    ]
    assert failures == []


def test_a_removal_keeps_its_confirmation_and_stays_on_the_step(page, component_origin):
    """Remove this image is confirmed in the dialog and applied in place.

    The slots are redrawn from the wizard's page: the chosen image is gone
    with the redraw, so the row offers Continue again, and the page stays
    on its step with no reload.
    """
    from .test_in_place import MARK, MARKED

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    answer_reviews(page, component_origin, REMOVAL, address=PAGE)
    page.goto(component_origin + STEP)
    page.evaluate(MARK)
    proceed, save, _ = primary(page)
    slot(page, BANNER).get_by_label("Image").set_input_files(png())
    available(save)
    slot(page, WELCOME).get_by_role("button", name="Remove this image").click()
    dialog = page.get_by_role("dialog", name="Remove this image?")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    has_text(
        page.locator("#artwork-outcome-welcome"),
        re.compile(r"^Campaign image removed\. Applied at "),
    )
    assert (
        slot(page, WELCOME).get_by_role("button", name="Remove this image").count() == 0
    )
    available(proceed)
    hidden(save)
    assert page.url == component_origin + STEP
    assert page.evaluate(MARKED) == "kept"
    assert failures == []
