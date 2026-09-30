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


def hidden(locator):
    """Assert that ``locator`` becomes hidden, waiting like Playwright's expect.

    An immediate ``assert locator.is_hidden()`` (or ``not is_visible()``) right
    after an action races the script that hides the element, and fails
    intermittently on slower engines. ``expect(...).to_be_hidden()`` retries
    until its timeout.
    """
    from playwright.sync_api import expect

    expect(locator).to_be_hidden()


def recorded(page, requests, count, *, timeout=10000):
    """Wait until a route handler has appended ``count`` requests to ``requests``.

    A request the page sends from a timer fired by ``page.clock`` reaches the
    Python route handler only while Playwright is processing events, so an
    immediate ``assert len(requests) == count`` after ``fast_forward`` races
    it, and loses on slower engines. Waiting in short steps lets Playwright
    dispatch the route. More than ``count`` requests fails at once.
    """
    import time

    deadline = time.monotonic() + timeout / 1000
    while len(requests) < count and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert len(requests) == count, f"{len(requests)} requests, not {count}"


def eventually(page, expression, expected=True, *, arg=None, timeout=30000):
    """Poll ``page.evaluate(expression, arg)`` until it returns ``expected``.

    Use this only for page state no locator assertion can express, such as
    ``localStorage`` or a window variable; prefer ``expect(locator)`` otherwise.
    ``page.wait_for_function`` is not a substitute: when its condition is not
    already true, Playwright re-evaluates the predicate inside the page, which
    the stewardship CSP (``script-src 'self'``, no ``'unsafe-eval'``) blocks.
    ``page.evaluate`` runs through the browser's debugging protocol instead, so
    the policy does not apply and each poll is an ordinary round trip. The
    default timeout matches the 30 s ``wait_for_function`` default it replaces.
    """
    import time

    deadline = time.monotonic() + timeout / 1000
    while (value := page.evaluate(expression, arg)) != expected:
        if time.monotonic() > deadline:
            raise AssertionError(f"{expression!r} returned {value!r}, not {expected!r}")
        page.wait_for_timeout(50)
