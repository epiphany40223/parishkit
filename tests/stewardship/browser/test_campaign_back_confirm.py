"""Campaign setup's Back asks before it discards (#1942, #1404 PR 7).

ui-conventions "Wizards" rule 6: Back never saves. While the step's form
holds a change, Back, a sub-page's Return to <step> and the stepper's step
links first ask "Discard your changes and go back?" in the shared
confirmation dialog: Discard and go back follows the link, posting nothing;
Stay keeps the typed values and focus on the link. An unchanged page
navigates at once. Each page is drawn from the real templates by the
shared component pages (``stepper_components``).
"""

import pytest
from django.urls import reverse

from .artwork_components import STEP
from .stepper_components import CONTENT, DIRECT, EDITOR_DIRECT, PATH
from .test_artwork_dialog import png, slot
from .test_components import axe_violations
from .waits import focused, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

BANNER = "Banner (top of the Family emails only)"
TITLE = "Discard your changes and go back?"
# Where Back leads from Share options (Load ParishSoft data), and the step
# links' Pages and emails; the component server answers neither, so a test
# answers each with a stand-in page.
LOAD_URL = reverse("admin:source_refresh")
CONTENT_URL = reverse("admin:content_catalog")


def stand_in(page, origin, *paths):
    """Answer GETs of ``paths`` with a stand-in page; return every POST seen.

    The POST list proves Discard and go back sends nothing.
    """
    posts = []
    page.on(
        "request",
        lambda request: request.method == "POST" and posts.append(request.url),
    )
    for path in paths:
        page.route(
            origin + path,
            lambda route: route.fulfill(
                content_type="text/html", body="<title>Next</title><p>Next</p>"
            ),
        )
    return posts


def back_link(page):
    """The action row's Back (or Return to <step>) link."""
    return page.locator("[data-campaign-step-actions] [data-campaign-step-back]")


@pytest.mark.parametrize("path", [PATH, DIRECT])
def test_an_unchanged_step_goes_back_at_once(page, component_origin, path):
    """With nothing changed, Back is an ordinary link: no dialog opens."""
    stand_in(page, component_origin, LOAD_URL)
    page.goto(component_origin + path)
    back_link(page).click()
    page.wait_for_url(component_origin + LOAD_URL)


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path", [PATH, DIRECT])
def test_a_changed_step_asks_and_stay_keeps_the_edit(
    page, component_origin, axe_source, width, path
):
    """Back on a changed step opens the dialog; Stay keeps everything.

    The safe choice (Stay) takes focus; nothing on the page moves while the
    dialog is open; Stay closes it with the typed value kept, focus back on
    Back and the page not left. The open dialog passes the scan.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    posts = stand_in(page, component_origin, LOAD_URL)
    page.goto(component_origin + path)
    field = page.locator("#id_options-0-label")
    field.fill("Renamed option")
    back = back_link(page)
    before = back.evaluate(
        "(node) => { const box = node.getBoundingClientRect();"
        " return [box.x + scrollX, box.y + scrollY, box.width, box.height]; }"
    )
    back.click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    stay = dialog.get_by_role("button", name="Stay")
    focused(stay)
    visible(dialog.get_by_role("button", name="Discard and go back"))
    # The dialog is modal and anchored; the page under it does not move.
    after = back.evaluate(
        "(node) => { const box = node.getBoundingClientRect();"
        " return [box.x + scrollX, box.y + scrollY, box.width, box.height]; }"
    )
    assert all(abs(a - b) < 0.5 for a, b in zip(after, before, strict=True))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    stay.click()
    hidden(dialog)
    focused(back)
    assert field.input_value() == "Renamed option"
    assert page.url == component_origin + path
    assert posts == [] and failures == []


@pytest.mark.parametrize("path", [PATH, DIRECT])
def test_discard_goes_back_without_posting(page, component_origin, path):
    """Discard and go back follows Back's link and sends no form."""
    posts = stand_in(page, component_origin, LOAD_URL)
    page.goto(component_origin + path)
    page.locator("#id_options-0-label").fill("Renamed option")
    back_link(page).click()
    dialog = page.get_by_role("dialog", name=TITLE)
    dialog.get_by_role("button", name="Discard and go back").click()
    page.wait_for_url(component_origin + LOAD_URL)
    assert posts == []


def test_undoing_the_change_lets_back_go_at_once(page, component_origin):
    """A change typed and then undone is no change: Back asks nothing."""
    stand_in(page, component_origin, LOAD_URL)
    page.goto(component_origin + PATH)
    field = page.locator("#id_options-0-label")
    saved = field.input_value()
    field.fill("Renamed option")
    field.fill(saved)
    back_link(page).click()
    page.wait_for_url(component_origin + LOAD_URL)


@pytest.mark.parametrize("width", [320, 1280])
def test_a_step_link_asks_the_same_and_escape_stays(page, component_origin, width):
    """Following a step in the bar or the list asks the same as Back.

    Escape is Stay: the dialog closes, focus returns to the step link and
    the page is not left. A step link on an unchanged page just opens.
    """
    stand_in(page, component_origin, CONTENT_URL)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    page.locator("#id_options-0-label").fill("Renamed option")
    stepper = page.locator("#campaign-stepper")
    # The step bar on a phone; the open list on a wide screen.
    if width < 1024:
        step = stepper.locator(f'.setup-track a[href="{CONTENT_URL}"]')
    else:
        step = stepper.locator(f'.setup-stepper-wide a[href="{CONTENT_URL}"]')
    step.click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    page.keyboard.press("Escape")
    hidden(dialog)
    focused(step)
    assert page.url == component_origin + PATH
    # Discard follows the step link instead.
    step.click()
    dialog.get_by_role("button", name="Discard and go back").click()
    page.wait_for_url(component_origin + CONTENT_URL)
    # Leave campaign setup is not a step: it never asks.
    page.goto(component_origin + PATH)
    page.locator("#id_options-0-label").fill("Renamed option")
    stand_in(page, component_origin, reverse("admin:index"))
    stepper.locator("[data-campaign-leave]").click()
    page.wait_for_url(component_origin + reverse("admin:index"))


def test_a_sub_pages_return_to_asks_once_its_form_changed(page, component_origin):
    """Return to <step> on a sub-page that saves asks the same (rule 10).

    The fixture editor's Return to links the fixture Pages and emails
    (``CONTENT``), which the component server draws.
    """
    posts = stand_in(page, component_origin)
    page.goto(component_origin + EDITOR_DIRECT)
    page.locator("[data-content-editor]").click()
    page.keyboard.press("End")
    page.keyboard.type(" and thanks")
    back = back_link(page)
    back.click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    dialog.get_by_role("button", name="Stay").click()
    hidden(dialog)
    focused(back)
    back.click()
    dialog.get_by_role("button", name="Discard and go back").click()
    page.wait_for_url(component_origin + CONTENT)
    assert posts == []


def test_a_chosen_image_on_campaign_images_asks_before_back(page, component_origin):
    """A chosen image is Campaign images' change: Back asks, Stay keeps it.

    Campaign images saves directly, so its change is the image chosen in a
    slot's file field, not a typed value; Discard and go back sends nothing.
    """
    page.goto(component_origin + STEP)
    back = back_link(page)
    target = back.get_attribute("href")
    posts = stand_in(page, component_origin, target)
    slot(page, BANNER).get_by_label("Image").set_input_files(png())
    back.click()
    dialog = page.get_by_role("dialog", name=TITLE)
    visible(dialog)
    dialog.get_by_role("button", name="Stay").click()
    hidden(dialog)
    focused(back)
    assert page.url == component_origin + STEP
    back.click()
    dialog.get_by_role("button", name="Discard and go back").click()
    page.wait_for_url(component_origin + target)
    assert posts == []


def test_a_modified_click_on_back_is_not_intercepted(page, component_origin):
    """Ctrl or Meta plus click (open in a new tab) never opens the dialog.

    A listener on the window, which runs after the page's own, records
    whether the click was prevented: the page must leave it alone.
    """
    page.goto(component_origin + PATH)
    page.locator("#id_options-0-label").fill("Renamed option")
    page.evaluate(
        "window.addEventListener('click', (event) =>"
        " { window.lastClickPrevented = event.defaultPrevented; })"
    )
    back_link(page).click(modifiers=["ControlOrMeta"])
    assert page.evaluate("window.lastClickPrevented") is False
    assert page.get_by_role("dialog", name=TITLE).count() == 0
    assert page.url == component_origin + PATH
