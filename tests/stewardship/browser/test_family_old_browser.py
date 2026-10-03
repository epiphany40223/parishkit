"""The Family form's old-browser notice (#384 L1).

family-support-v1.js reveals a hidden notice when the browser lacks an API
family-v1.js needs. These tests remove one such API before any page script
runs, standing in for an older browser, and check that the notice appears
while the form itself is left untouched.
"""

import pytest

from .test_family_response import expect, prepare

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NOTICE = "This browser is too old to use this form."


def test_capable_browser_never_sees_the_notice(page, component_origin):
    """A current browser keeps the notice hidden and the form starts normally."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    prepare(page, component_origin)
    expect(page.locator(".family-nav")).to_be_visible()
    expect(page.locator("#browser-unsupported")).to_be_hidden()
    # The form loaded on its own (#466), so the retry fallback never showed.
    expect(page.locator("#family-entry")).to_be_hidden()
    assert errors == []


@pytest.mark.parametrize(
    "removal",
    [
        "delete window.structuredClone",
        "delete Object.hasOwn",
        "delete Array.prototype.at",
        "delete Array.prototype.findLastIndex",
        "delete Crypto.prototype.randomUUID",
        "delete Object.fromEntries",
        "delete String.prototype.replaceAll",
        "delete Element.prototype.replaceChildren",
        "delete window.fetch",
        "delete window.URL",
    ],
    ids=[
        "structuredClone",
        "hasOwn",
        "at",
        "findLastIndex",
        "randomUUID",
        "fromEntries",
        "replaceAll",
        "replaceChildren",
        "fetch",
        "URL",
    ],
)
def test_missing_api_reveals_the_notice(page, component_origin, removal):
    """Without a required API the notice shows and the form is not altered."""
    page.add_init_script(removal + ";")
    prepare(page, component_origin)
    notice = page.locator("#browser-unsupported")
    expect(notice).to_be_visible()
    expect(notice).to_have_text(
        NOTICE
        + " Please update your device’s software, or use another device or browser."
    )
    expect(notice).to_have_attribute("role", "alert")
    # The check only reveals the notice and never touches the form area, whose
    # own script may or may not get as far as loading the form (#466).
    expect(page.locator("#family-flow")).to_be_attached()
