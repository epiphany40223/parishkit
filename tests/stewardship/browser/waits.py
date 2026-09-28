"""Auto-waiting assertions shared by the browser tests.

Playwright is imported lazily so that the module (and every test module that
imports it) still collects where Playwright is not installed, such as the
non-database suite that skips browser tests.
"""


def visible(locator):
    """Assert that ``locator`` becomes visible, waiting like Playwright's expect.

    An immediate ``assert locator.is_visible()`` races a navigation, a
    disclosure expanding or a script rendering, and fails intermittently on
    slower engines. ``expect(...).to_be_visible()`` retries until its timeout.
    """
    from playwright.sync_api import expect

    expect(locator).to_be_visible()
