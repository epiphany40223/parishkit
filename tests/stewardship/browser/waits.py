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


def greyed(locator):
    """Assert that ``locator`` stays visible and becomes disabled, waiting.

    A field that does not apply stays in place, greyed and not sent
    (``data-enabled-when``), rather than hiding, so nothing moves (#1014).
    """
    from playwright.sync_api import expect

    expect(locator).to_be_visible()
    expect(locator).to_be_disabled()


def available(locator):
    """Assert that ``locator`` is visible and becomes enabled, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_be_visible()
    expect(locator).to_be_enabled()


def has_attribute(locator, name, value):
    """Assert that ``locator`` gets attribute ``name`` equal to ``value``, waiting.

    Used for state a script sets after a fetch, such as a swapped-in table's
    ``aria-sort``; an immediate ``get_attribute`` would race the swap.
    """
    from playwright.sync_api import expect

    expect(locator).to_have_attribute(name, value)


def has_text(locator, text):
    """Assert that ``locator`` comes to have exactly ``text``, waiting.

    A live region is written a moment after the event it announces, so an
    immediate ``inner_text`` races it.
    """
    from playwright.sync_api import expect

    expect(locator).to_have_text(text)


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


# True once the scroll position is the same at two consecutive frames.
SCROLL_STEADY = (
    "new Promise((done) => requestAnimationFrame(() => {"
    " const x = window.scrollX, y = window.scrollY;"
    " requestAnimationFrame(() =>"
    " done(window.scrollX === x && window.scrollY === y));"
    " }))"
)


def painted(page, *, timeout=5000):
    """Wait until the page has drawn two more frames and stopped scrolling.

    After typing, Firefox scrolls the focused field's caret into view at its
    next refresh, not at once: a test that scrolls, or measures where things
    sit, right after ``fill`` races that deferred scroll and sees the page
    jump by up to the whole viewport. Two frames usually let it land; a
    slow or smooth scroll can take longer, so the wait then continues until
    the scroll position holds across two consecutive frames. A user cannot
    type and scroll in the same frame, so this changes no behaviour under
    test. If the page is still scrolling after ``timeout`` ms, the wait logs
    what it waited for, the limit and the time spent, and fails.
    """
    import logging
    import time

    started = time.monotonic()
    page.evaluate(
        "new Promise((done) =>"
        " requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    try:
        eventually(page, SCROLL_STEADY, timeout=timeout)
    except AssertionError:
        logging.getLogger(__name__).error(
            "painted: scroll position still changing between frames after "
            "%.0f ms (limit %d ms)",
            (time.monotonic() - started) * 1000,
            timeout,
        )
        raise
