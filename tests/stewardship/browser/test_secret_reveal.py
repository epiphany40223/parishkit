"""The shared Show/Hide control on every masked secret field (#1475).

UX conventions spec, Secret fields: the control switches its field between
masked and plain text with the mouse, Enter or Space, keeps focus, is named
by its visible word and the field ("Hide the Slack bot token"), announces
each change, moves nothing, posts the same value, and masks the field again
on submit and on leaving or returning to the page. The markup and the guard
are in test_secret_input.py.
"""

import pytest

from .keyboard import tab_to_next
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each page with a masked secret: its path, the field's label and the
# secret's name, which follows the visible word in the control's name.
PAGES = [
    ("/setup-credential", "ParishSoft API key", "ParishSoft API key"),
    ("/integration-settings", "New ParishSoft API key", "ParishSoft API key"),
    ("/slack-not-set-up", "Slack bot token", "Slack bot token"),
]


# An element's box in page coordinates, so scrolling it into view to click
# it does not count as a move.
BOX = """e => { const box = e.getBoundingClientRect();
  return [box.left + window.scrollX, box.top + window.scrollY,
          box.width, box.height]; }"""


def boxes(page, *locators):
    """The page boxes of ``locators`` and of the form's first submit button."""
    save = page.locator("main form button[type=submit]").first
    return [locator.evaluate(BOX) for locator in (*locators, save)]


def secret(page, label, name):
    """The field labelled ``label`` and its control, found the way users do.

    The control has no aria-label, so a plain label lookup matches only the
    input (the field name in the button is its content, not a label). The
    control is found by its name while masked, then returned by the field it
    controls, since its name changes with the state.
    """
    from playwright.sync_api import expect

    field = page.get_by_label(label)
    expect(field).to_have_count(1)
    expect(page.get_by_label(name)).to_have_count(1)
    expect(page.get_by_label(name)).to_have_attribute("name", "candidate")
    button = page.get_by_role("button", name=f"Show the {name}", exact=True)
    visible(button)
    controls = button.get_attribute("aria-controls")
    assert controls == field.get_attribute("id")
    return field, page.locator(f'[data-secret-reveal][aria-controls="{controls}"]')


def shown(field, button, name, pressed):
    """Assert the field's type and the control agree with ``pressed``.

    The accessible name starts with the visible word (WCAG 2.5.3, Label in
    Name), so a voice user's "click Hide" reaches the shown control. The
    name says the state, so there is no aria-pressed; data-shown holds it.
    """
    from playwright.sync_api import expect

    word = "Hide" if pressed else "Show"
    expect(field).to_have_attribute("type", "text" if pressed else "password")
    expect(button).to_have_attribute("data-shown", "true" if pressed else "false")
    expect(button).to_have_accessible_name(f"{word} the {name}")
    expect(button.page.get_by_role("button", name=word).and_(button)).to_have_count(1)
    assert button.get_attribute("aria-pressed") is None
    assert button.get_attribute("aria-label") is None
    other = "Hide" if word == "Show" else "Show"
    expect(button.locator(f".secret-reveal-{word.lower()}")).to_be_visible()
    expect(button.locator(f".secret-reveal-{other.lower()}")).to_be_hidden()


def announced(button, name, pressed):
    """Assert the polite status line beside ``button`` says the new state."""
    from playwright.sync_api import expect

    status = button.locator("xpath=..").locator("[role=status]")
    state = "shown" if pressed else "hidden"
    expect(status).to_have_text(f"Your {name} is {state}.")


# The page has no horizontal scroll (the phone and desktop widths).
NO_SIDE_SCROLL = (
    "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
)


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path,label,name", PAGES)
def test_the_control_shows_and_masks_the_typed_secret(
    page, component_origin, path, label, name, width
):
    """Click, Enter and Space toggle it; focus, name and layout stay put."""
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    field, button = secret(page, label, name)
    visible(button)
    shown(field, button, name, False)
    field.fill("synthetic-secret-1475")
    before = boxes(page, field, button)

    button.click()
    shown(field, button, name, True)
    announced(button, name, True)
    expect(field).to_have_value("synthetic-secret-1475")
    assert boxes(page, field, button) == before
    button.click()
    shown(field, button, name, False)
    announced(button, name, False)
    assert boxes(page, field, button) == before

    # The keyboard: Tab from the field reaches the control (WebKit on macOS
    # needs Option+Tab for buttons, see keyboard.py), which keeps focus
    # while Enter and Space switch it.
    field.focus()
    tab_to_next(page)
    expect(button).to_be_focused()
    page.keyboard.press("Enter")
    shown(field, button, name, True)
    expect(button).to_be_focused()
    page.keyboard.press("Space")
    shown(field, button, name, False)
    expect(button).to_be_focused()
    assert boxes(page, field, button) == before
    expect(field).to_have_value("synthetic-secret-1475")
    assert page.evaluate(NO_SIDE_SCROLL)
    assert not failures


@pytest.mark.parametrize("event", ["pageshow", "pagehide"])
def test_leaving_or_returning_to_the_page_masks_it_again(page, component_origin, event):
    """A page shown again (the back/forward cache) or left is masked."""
    name = "ParishSoft API key"
    page.goto(component_origin + "/setup-credential")
    field, button = secret(page, name, name)
    field.fill("synthetic-secret-1475")
    button.click()
    shown(field, button, name, True)
    page.evaluate(
        "type => window.dispatchEvent("
        "new PageTransitionEvent(type, {persisted: true}))",
        event,
    )
    shown(field, button, name, False)


def test_the_posted_value_is_unchanged_and_the_field_is_masked_again(
    page, component_origin
):
    """Revealed or not, Save posts the same body; the field is masked before
    the request leaves and after Back; the complete gate still holds Save."""
    from playwright.sync_api import expect

    posted = []

    def capture(route):
        """Keep a posted body and answer it with a plain page; load the rest."""
        if route.request.method != "POST":
            route.continue_()
            return
        posted.append(route.request.post_data)
        route.fulfill(status=200, content_type="text/html", body="<p>Saved</p>")

    name = "Slack bot token"
    page.route("**/slack-not-set-up", capture)
    page.goto(component_origin + "/slack-not-set-up")
    save = page.get_by_role("button", name="Save", exact=True)
    field, button = secret(page, name, name)
    page.get_by_label("Slack channel ID").fill("C0123ABC")
    # The gate holds Save while the required token is empty or only spaces,
    # whether it is shown or not.
    button.click()
    field.fill("   ")
    expect(save).to_be_disabled()
    field.fill("xoxb-synthetic-1475")
    expect(save).to_be_enabled()
    shown(field, button, name, True)
    save.click()
    visible(page.get_by_text("Saved"))
    page.go_back()
    field, button = secret(page, name, name)
    shown(field, button, name, False)

    # The same entry, never revealed, posts byte for byte the same body.
    page.get_by_label("Slack channel ID").fill("C0123ABC")
    field.fill("xoxb-synthetic-1475")
    save.click()
    visible(page.get_by_text("Saved"))
    assert len(posted) == 2 and posted[0] == posted[1]
    assert "candidate=xoxb-synthetic-1475" in posted[0]


def test_submit_masks_the_field_before_the_request_leaves(page, component_origin):
    """The page's submit listeners already see the field masked."""
    name = "Slack bot token"
    page.goto(component_origin + "/slack-not-set-up")
    field, button = secret(page, name, name)
    page.get_by_label("Slack channel ID").fill("C0123ABC")
    field.fill("xoxb-synthetic-1475")
    button.click()
    shown(field, button, name, True)
    page.evaluate(
        """() => document.addEventListener("submit", (event) => {
          window.secretTypeAtSubmit = event.target.querySelector(
            "[name=candidate]").type;
          event.preventDefault();
        })"""
    )
    page.get_by_role("button", name="Save", exact=True).click()
    eventually(page, "window.secretTypeAtSubmit", "password")
    shown(field, button, name, False)
