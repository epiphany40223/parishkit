"""The Admin "About this page" panel starts closed and remembers being opened."""

import pytest

from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_about_panel_remembers_opened_and_closed(page, component_origin):
    """Opening the panel survives a reload; closing clears the stored choice."""
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/content-settings")
    panel = page.locator("details[data-about-page]")
    summary = panel.locator("summary")
    placeholders = panel.get_by_text("Use a placeholder name")
    # Help is hidden by default (#227), and opening the page writes nothing.
    expect(panel).not_to_have_attribute("open", "")
    expect(placeholders).to_be_hidden()
    assert page.evaluate("localStorage.length") == 0

    summary.click()
    visible(placeholders)
    # The choice is saved from the asynchronous "toggle" event.
    eventually(page, "() => localStorage.length", 1)
    page.reload()
    expect(panel).to_have_attribute("open", "")
    visible(placeholders)

    summary.click()
    expect(placeholders).to_be_hidden()
    eventually(page, "() => localStorage.length", 0)
    page.reload()
    expect(placeholders).to_be_hidden()
    assert not failures


def test_about_panel_clears_the_old_closed_marker(page, component_origin):
    """A "closed" choice stored when panels started open is simply forgotten."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/content-settings")
    key = page.locator("details[data-about-page]").get_attribute("data-about-page")
    page.evaluate(f"localStorage.setItem('pk-about-page:{key}', 'closed')")
    page.reload()
    expect(page.locator("details[data-about-page]")).not_to_have_attribute("open", "")
    eventually(page, "() => localStorage.length", 0)


# Converted Admin pages with a data table, rendered with the Admin chrome
# (sidebar, breadcrumbs, the Testing banner and the delivery warning) by the
# component fixtures. The report pages with long filter and export forms join
# this list when their filters are collapsed (#227 follow-up).
LAPTOP_PAGES = ["/background", "/deliveries", "/delivery-refusals", "/presence"]
# A typical laptop browser viewport: a 1366x768 screen less the browser's
# own toolbars. Fonts differ between platforms (Linux CI's are wider), so the
# check asks only that the first row's top edge, with some room, is on screen.
LAPTOP_VIEWPORT = {"width": 1366, "height": 650}


@pytest.mark.parametrize("path", LAPTOP_PAGES)
def test_page_data_starts_on_a_laptop_screen(page, component_origin, path):
    """With help collapsed, a converted page's first table row starts on screen."""
    page.set_viewport_size(LAPTOP_VIEWPORT)
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    box = page.locator("main table tbody tr").first.bounding_box()
    assert box is not None
    assert box["y"] + 40 <= LAPTOP_VIEWPORT["height"], (path, box)


# Pages converted in #227 (campaign email and schedules in help-ux-1; keys,
# refreshes and refusals in help-ux-2; the setup wizard in help-ux-3), each
# with the caution or data line that must stay visible while its help is
# closed.
CONVERTED_PAGES = {
    "/campaign-mail": "It is sent only to",
    "/live-family-tests-later": "never to the Family",
    "/schedule-preview": "Email already sent cannot be recalled.",
    "/clone-settings": "It never copies Family codes",
    "/clone-preview": "Copied content",
    "/presence": "Families with the Family form open",
    "/weekly-manual": "not a retry of a previous email",
    "/credential-status": "Key replacement status",
    "/credential-selection": "This integration is stopped until you finish",
    "/source-refresh": "Refresh now",
    "/backup-key": "Never give this server the private key.",
    "/delivery-refusal": "Clearing removes only this refusal",
    "/setup-parish": "Do not enter API tokens",
    "/setup-confirmation": "only this sign-in can continue or cancel setup",
    "/setup-preview": "This is a read-only preview",
    "/setup-mail-test": "Send a fictional sample only",
    "/setup-shares": "Saving changes only this temporary wizard draft.",
    "/setup-branding": "This logo is private",
    "/setup-source": "Starting the load fixes the parish time zone",
    "/setup-credential": "never shown again",
}


@pytest.mark.parametrize("path", sorted(CONVERTED_PAGES))
def test_converted_page_keeps_cautions_visible_and_help_closed(
    page, component_origin, path
):
    """The page's help starts closed; its caution or data line stays visible."""
    from playwright.sync_api import expect

    page.goto(component_origin + path)
    panel = page.locator("details[data-about-page]")
    expect(panel).not_to_have_attribute("open", "")
    expect(panel.locator(".about-page-body")).to_be_hidden()
    visible(page.locator("main").get_by_text(CONVERTED_PAGES[path]).first)


def test_locked_campaign_dates_are_one_line_with_a_tip(page, component_origin):
    """Locked dates show one line; why they lock opens from its "i" tip (#227)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + "/schedule-settings-locked")
    line = page.locator("#window-locked")
    visible(line)
    assert line.bounding_box()["height"] < 40
    bubble = page.locator("#window-locked-tip")
    expect(bubble).to_be_hidden()
    page.locator('button[aria-controls="window-locked-tip"]').click()
    visible(bubble)
    expect(bubble).to_contain_text("lock once the campaign goes live")


def test_every_about_control_sits_beside_its_heading(page, component_origin):
    """On every fixture page with an About panel, the closed control is in the
    heading row: the panel directly follows the page's h1, and its summary is
    drawn on the same line as the heading, to the right of it (#227)."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + "/about-page-index")
    paths = page.inner_text("body").split()
    assert len(paths) >= 20, paths
    misplaced = []
    for path in paths:
        open_page(page, component_origin + path)
        placement = placement_on(page)
        if placement:
            misplaced.append(f"{path}: {placement}")
    assert not misplaced, misplaced


def open_page(page, url):
    """Go to ``url`` and wait until the page has settled on its final address.

    A report page without a timezone in its URL replaces itself with one
    (report-v1.js). That script is deferred, so it has already started the
    replacement when ``goto`` returns, and checking the page then, or going
    to the next one, races it. The script's own condition is evaluated
    here: when it holds, wait for the replaced URL and its load.
    """
    from playwright.sync_api import Error

    page.goto(url)
    try:
        replacing = page.evaluate(REPLACES_ITSELF)
    except Error as error:
        if "context was destroyed" not in str(error):
            raise
        replacing = True
    if replacing:
        page.wait_for_url(lambda address: "timezone=" in address)
    page.wait_for_load_state()


# report-v1.js's condition for replacing the page with a timezone in its URL.
REPLACES_ITSELF = """() => {
  const select = document.querySelector("[data-report-options] select[name=timezone]");
  if (!select || new URL(location.href).searchParams.has("timezone")) return false;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return zone !== select.value
    && Array.from(select.options).some(option => option.value === zone);
}"""


def placement_on(page):
    """Why the page's About control is not beside its heading, or "".

    A live-status page may reload itself just after loading; the check then
    runs again on the reloaded page.
    """
    from playwright.sync_api import Error

    for _ in range(3):
        try:
            return page.evaluate(PLACEMENT)
        except Error as error:
            if "context was destroyed" not in str(error):
                raise
            page.wait_for_load_state()
    return page.evaluate(PLACEMENT)


# The placement rule in the page: the panel directly follows the h1, and the
# closed summary's middle lies within the heading's line box, to its right.
PLACEMENT = """() => {
  const panel = document.querySelector("details[data-about-page]");
  if (!panel) return "no About panel after loading";
  const heading = panel.previousElementSibling;
  if (!heading || heading.tagName !== "H1") return "not after the h1";
  const h = heading.getBoundingClientRect();
  const s = panel.querySelector("summary").getBoundingClientRect();
  const middle = (s.top + s.bottom) / 2;
  if (middle < h.top || middle > h.bottom) return "not on the heading line";
  if (s.left < h.right) return "not beside the heading";
  return "";
}"""


# Representative pages for the control's steadiness (#602): a setup page
# without Admin chrome, an Admin table page, and a page with a long heading
# that wraps at phone width.
STEADY_PAGES = ["/setup-credential", "/background", "/information", "/daily-digest"]
# A wide laptop window and a phone.
STEADY_VIEWPORTS = [{"width": 1366, "height": 768}, {"width": 390, "height": 844}]


@pytest.mark.parametrize("path", STEADY_PAGES)
@pytest.mark.parametrize("viewport", STEADY_VIEWPORTS, ids=["wide", "phone"])
def test_about_control_stays_put_when_toggled(page, component_origin, path, viewport):
    """Opening and closing the panel never moves its control (#602).

    The control's box is measured closed, open and closed again, both by
    mouse and by keyboard (Enter and Space); keyboard toggling leaves focus
    on the control.
    """
    from playwright.sync_api import expect

    page.set_viewport_size(viewport)
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    panel = page.locator("details[data-about-page]")
    summary = panel.locator("summary")
    body = panel.locator(".about-page-body")
    closed = summary.bounding_box()
    for key in (None, "Enter", " "):
        if key:
            # Focus once: a locator's press would re-focus the control first,
            # hiding a toggle that lost focus.
            summary.focus()
        for state in ("open", "closed again"):
            if key:
                page.keyboard.press(key)
            else:
                summary.click()
            if state == "open":
                expect(panel).to_have_attribute("open", "")
                visible(body)
                # The help opens below the heading row, not beside the control.
                assert body.bounding_box()["y"] >= closed["y"] + closed["height"] - 1
            else:
                expect(panel).not_to_have_attribute("open", "")
            assert summary.bounding_box() == closed, (path, key, state)
            if key:
                assert page.evaluate("document.activeElement.tagName") == "SUMMARY"


@pytest.mark.parametrize("path", ["/setup-credential", "/background", "/family-login"])
def test_theme_reserves_the_scrollbar_gutter(page, component_origin, path):
    """Both portals reserve the scrollbar's space on every page (#607)."""
    page.goto(component_origin + path)
    gutter = page.evaluate("getComputedStyle(document.documentElement).scrollbarGutter")
    assert gutter == "stable", (path, gutter)


def test_opening_help_that_makes_the_page_scroll_moves_nothing(page, component_origin):
    """A page that fits the window keeps its width and control when help scrolls it.

    The window is sized to the closed page's height, so opening the help makes
    the page need a vertical scrollbar. With classic (always visible)
    scrollbars, an unreserved gutter would narrow the layout and move the
    centred page and its control sideways (#607). Headless test browsers do
    not draw classic scrollbars, so this cannot show the shift itself; the
    gutter test above is the one that fails without the rule. This one keeps
    the layout width and the control's box exact as the page starts to scroll.
    """
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + "/setup-credential")
    page.evaluate("document.fonts.ready.then(() => true)")
    height = page.evaluate("document.documentElement.scrollHeight")
    page.set_viewport_size({"width": 1366, "height": height})
    root = "document.documentElement"
    assert page.evaluate(f"{root}.scrollHeight <= {root}.clientHeight")
    width = page.evaluate(f"{root}.clientWidth")
    panel = page.locator("details[data-about-page]")
    summary = panel.locator("summary")
    closed = summary.bounding_box()
    summary.click()
    expect(panel).to_have_attribute("open", "")
    visible(panel.locator(".about-page-body"))
    # The open help made the page scroll, and nothing moved.
    assert page.evaluate(f"{root}.scrollHeight > {root}.clientHeight")
    assert page.evaluate(f"{root}.clientWidth") == width
    assert summary.bounding_box() == closed
