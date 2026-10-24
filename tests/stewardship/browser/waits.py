"""Auto-waiting assertions shared by the browser tests.

Playwright is imported lazily so that the module (and every test module that
imports it) still collects where Playwright is not installed, such as the
non-database suite that skips browser tests.
"""

import re


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


# A time zone's short name as Intl.DateTimeFormat gives it: "EDT", or
# "GMT+2" where the locale has no name for the zone.
ZONE_NAME = r"(?:[A-Z]{2,5}|GMT[+-][0-9:]+)"


def reads(text, after=""):
    """A time field's reading, ``text`` then the zone's short name (#2129).

    The zone name depends on the day the test runs (EDT or EST), so the
    pattern takes any short zone name there; ``after`` is what follows it,
    such as a list's repeats.
    """
    return re.compile(f"^{re.escape(text)} {ZONE_NAME}{re.escape(after)}$")


def has_value(locator, value):
    """Assert that the field ``locator`` comes to hold ``value``, waiting.

    Used for a value a script fills in, such as a suggested slug or a key
    from a server answer; an immediate ``input_value`` would race the script.
    """
    from playwright.sync_api import expect

    expect(locator).to_have_value(value)


def has_count(locator, count):
    """Assert that ``locator`` comes to match ``count`` elements, waiting.

    Used after a redraw swaps rows or headings in place.
    """
    from playwright.sync_api import expect

    expect(locator).to_have_count(count)


def focused(locator):
    """Assert that ``locator`` comes to have focus, waiting.

    Focus can move in a later task than the action that causes it: closing a
    ``<dialog>`` hides it at once, but its ``close`` event, where a script
    may move focus, is queued (#1202). An immediate ``document.activeElement``
    check right after waiting for the dialog to hide races that event.
    """
    from playwright.sync_api import expect

    expect(locator).to_be_focused()


def has_class(locator, name):
    """Assert that ``locator`` comes to carry the CSS class ``name``, waiting."""
    import re

    from playwright.sync_api import expect

    expect(locator).to_have_class(re.compile(rf"(^|\s){re.escape(name)}(\s|$)"))


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


def same_place(found, expected, *, tolerance=0.5):
    """Whether two drawn places match within ``tolerance`` pixels.

    A place is a number, or a list, tuple or dict of places, such as
    ``bounding_box()`` dicts or ``[x, y, width, height]`` rows. Linux Firefox
    can report an unmoved box a ten-thousandth of a pixel or less apart
    (1609.88333 against 1609.88330), so a layout-shift check compares within
    half a pixel, not exactly; a real shift moves at least a whole pixel.
    """
    if isinstance(expected, dict):
        return (
            isinstance(found, dict)
            and found.keys() == expected.keys()
            and all(
                same_place(found[k], expected[k], tolerance=tolerance) for k in expected
            )
        )
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(found, (list, tuple))
            and len(found) == len(expected)
            and all(
                same_place(f, e, tolerance=tolerance)
                for f, e in zip(found, expected, strict=True)
            )
        )
    return abs(found - expected) < tolerance


def repeat_line(dates, day, words):
    """Assert that the repeat date list ``dates`` comes to have ``day``'s
    line saying ``words``, waiting; return the line's text.

    The list's lines are the shared schedule preview, which the server draws
    after the page script writes the heading (#2092), so an immediate
    ``inner_text`` races it.
    """
    from playwright.sync_api import expect

    line = dates.locator("li").filter(has_text=day).first
    expect(line).to_contain_text(words)
    return line.inner_text()


def repeat_lines(dates, count):
    """Wait for the repeat date list ``dates`` to draw ``count`` lines (#2092);
    return their texts."""
    from playwright.sync_api import expect

    expect(dates.locator("li")).to_have_count(count)
    return dates.locator("li").all_inner_texts()
