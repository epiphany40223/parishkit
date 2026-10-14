"""Hosted file Upload and setup content's Fill in / Reset act in place
(#519 PR 7c).

The pages are served at their real addresses (upload_defaults_components).
The fixture server answers each POST with its Post/Redirect/Get redirect; a
route answers a refusal with the page the view renders. Each test sets a
mark outside every region, which only a full page load could lose, wheels
the page down so the control is below the fold (the mouse wheel, since
Firefox can undo a scripted scroll, #1088), and checks the mark, that the
page was not sent to its top, focus, the announcement and that one POST was
sent per click.
"""

import pytest

from .test_in_place import MARK, MARKED, count_requests
from .upload_defaults_components import (
    BY_NAME,
    CONTENT,
    CONTENT_STEP_UP,
    ENDED,
    FILLED,
    FULL_BY_SIZE,
    LIBRARY,
    LIMITS,
    REFUSED_SIZE,
    REFUSED_TYPE,
    RESET,
    RESET_DONE,
    STEP_UP,
    UPLOAD,
    UPLOADED,
)
from .waits import eventually, has_text, has_value, hidden, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Short enough that the Upload panel and the content forms start below the
# fold.
VIEWPORT = {"width": 1280, "height": 320}
FOCUSED = "selector => document.activeElement === document.querySelector(selector)"
UPLOAD_BUTTON = "#hosted-file-upload button[type=submit]"
STATUS = "div.visually-hidden[role=status]"
FILES = "input => input.files.length"
PDF = {"name": "Parish Flyer.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.7"}


def wheel_below(page, selector, *, attempts=20):
    """Wheel the page until ``selector`` sits 150 px below the viewport's top,
    or as far as the page scrolls, and holds there; return that scrollY.

    A wheel turn can stop short, so the page is wheeled until it holds across
    two settled checks. Firefox moves a small viewport only a few hundred
    pixels per wheel event whatever the delta, so a far target takes several
    turns (hence the generous ``attempts``).
    """
    where = (
        "Math.min(document.documentElement.scrollHeight"
        " - document.documentElement.clientHeight,"
        f" scrollY + document.querySelector({selector!r}).getBoundingClientRect().top"
        " - 150)"
    )
    there = f"Math.abs(({where}) - scrollY) <= 1"
    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 200)
    for _ in range(attempts):
        page.mouse.wheel(0, page.evaluate(f"({where}) - scrollY"))
        painted(page)
        if not page.evaluate(there):
            continue
        painted(page)
        if page.evaluate(there):
            offset = page.evaluate("scrollY")
            assert offset > 0, "the control must start below the fold for this test"
            return offset
    raise AssertionError(f"the page did not stay below {selector}")


def kept_place(page, offset, selector):
    """Assert the page kept the reader's place: it was not reloaded or sent
    to its top, ``selector`` (what took focus) is in the viewport, and any
    scroll to bring it there was a short step, not a jump."""
    assert page.evaluate(MARKED) == "kept"
    eventually(page, FOCUSED, arg=selector)
    now = page.evaluate("scrollY")
    assert now > 0 and abs(now - offset) < 250, (offset, now)
    box = page.locator(selector).bounding_box()
    # Allow for sub-pixel layout at the viewport's top edge.
    assert -1 <= box["y"] < VIEWPORT["height"], box


def answer(page, origin, path, fixture, status):
    """Answer every POST to ``path`` with the page at ``fixture`` (as the
    view renders it, with ``status``); return the list of POSTs answered."""
    posts = []

    def fulfil(route):
        """Fulfil a POST with the fixture; let any other request through."""
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.url)
        route.fulfill(
            response=route.fetch(url=origin + fixture, method="GET"), status=status
        )

    page.route(lambda url: url.split("#")[0] == origin + path, fulfil)
    return posts


def open_and_choose(page, origin, address=LIBRARY):
    """Open the library at ``address``, mark it, choose a PDF and wheel the
    Upload button into view; return the scroll offset before the click."""
    page.set_viewport_size(VIEWPORT)
    page.goto(origin + address)
    page.evaluate(MARK)
    page.locator("#id_file").set_input_files(PDF)
    return wheel_below(page, UPLOAD_BUTTON)


def test_upload_follows_its_redirect_in_place(page, component_origin):
    """Upload sends its multipart body once (a double click included),
    follows the redirect and swaps the library in: no reload, not sent to
    the top, the new file listed, the panel empty again (so Upload waits for
    a file and focus goes to the file field), the notice's own words read out
    and the address the redirect's."""
    posts = count_requests(page, "POST", UPLOAD)
    offset = open_and_choose(page, component_origin)
    page.locator(UPLOAD_BUTTON).dblclick()
    notice = "Uploaded parish-flyer.pdf as {{ file.parish-flyer }}."
    has_text(page.locator(STATUS), notice)
    visible(page.locator("#hosted-file-uploaded").get_by_text("Uploaded parish-flyer"))
    assert page.locator("table input[value='{{ file.parish-flyer }}']").count() == 1
    kept_place(page, offset, "#id_file")
    assert page.locator("#id_file").evaluate(FILES) == 0
    assert page.locator(UPLOAD_BUTTON).is_disabled()
    assert posts == [component_origin + UPLOAD]
    assert page.url == component_origin + UPLOADED + "#hosted-file-upload"
    # The swapped-in form still suggests a name from the next file chosen
    # (the delegated data-slug-from listener).
    page.locator("#id_file").set_input_files({**PDF, "name": "Next Flyer.pdf"})
    has_value(page.locator("#id_slug"), "next-flyer")


def test_upload_sends_the_chosen_file_as_multipart(page, component_origin):
    """The in-place POST is the multipart submission a native one would be
    (the engines do not expose a FormData body to the test, so its type is
    what is checked here; the fixture server's answer is the redirect)."""
    seen = []
    page.on(
        "request",
        lambda request: (
            seen.append(request)
            if request.method == "POST" and UPLOAD in request.url
            else None
        ),
    )
    open_and_choose(page, component_origin)
    page.locator(UPLOAD_BUTTON).click()
    has_text(
        page.locator(STATUS), "Uploaded parish-flyer.pdf as {{ file.parish-flyer }}."
    )
    assert len(seen) == 1
    assert seen[0].headers["content-type"].startswith("multipart/form-data")


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        (REFUSED_TYPE, "This file type isn't accepted."),
        (REFUSED_SIZE, "Choose a file no larger than 10 MB."),
    ],
    ids=["wrong-type", "too-large"],
)
def test_a_refused_upload_is_shown_in_place(page, component_origin, fixture, message):
    """A file the server refuses (400: a wrong type, or larger than it
    takes) is swapped in like the page: no reload, the error beside the
    field and in the summary, which takes focus and is read out, the
    reader's place kept, the address unchanged and one POST sent."""
    from playwright.sync_api import expect

    posts = answer(page, component_origin, UPLOAD, fixture, 400)
    open_and_choose(page, component_origin)
    page.locator(UPLOAD_BUTTON).click()
    summary = "#hosted-file-upload [data-error-summary]"
    visible(page.locator(summary))
    eventually(page, FOCUSED, arg=summary)
    expect(page.locator("#hosted-file-upload .errorlist")).to_contain_text(message)
    expect(page.locator(STATUS)).to_contain_text(message)
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("scrollY") > 0
    assert page.url == component_origin + LIBRARY
    assert len(posts) == 1


def test_a_file_too_large_for_the_room_sends_nothing(page, component_origin):
    """A file over the library's room is refused at the field, in the
    server's words, and Upload sends nothing (data-max-bytes, #563)."""
    posts = count_requests(page, "POST", UPLOAD)
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + LIMITS)
    page.evaluate(MARK)
    page.locator("#id_file").set_input_files(
        {"name": "big.pdf", "mimeType": "application/pdf", "buffer": b"%" * 3000}
    )
    wheel_below(page, UPLOAD_BUTTON)
    page.locator(UPLOAD_BUTTON).click(force=True)
    visible(page.get_by_text("Choose a file no larger than 2 KB.").first)
    painted(page)
    assert page.evaluate(MARKED) == "kept"
    assert posts == []


def test_a_deletion_gives_the_upload_field_its_room_back(page, component_origin):
    """In a library full by size the field refuses any file (its limit is
    0) and Upload is unavailable. Deleting a file in place redraws the
    field's limit from the room given back (#1252): the file already chosen
    is released, and Upload sends it, all without a reload."""
    posts = count_requests(page, "POST", UPLOAD)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + FULL_BY_SIZE)
    page.evaluate(MARK)
    field = page.locator("#id_file")
    assert field.get_attribute("data-max-bytes") == "0"
    assert page.locator(UPLOAD_BUTTON).is_disabled()
    field.set_input_files(PDF)
    eventually(page, "() => !!document.querySelector('#id_file').dataset.clientError")
    page.get_by_role("button", name="Delete parish-archive").click()
    dialog = page.locator("dialog#hosted-file-delete")
    dialog.get_by_role("button", name="Delete", exact=True).click()
    hidden(dialog)
    eventually(
        page,
        "() => document.querySelector('#id_file').dataset.maxBytes === '10485760'",
    )
    eventually(page, "() => !document.querySelector('#id_file').dataset.clientError")
    assert field.get_attribute("aria-invalid") == "false"
    assert field.evaluate(FILES) == 1
    page.locator(UPLOAD_BUTTON).click()
    visible(page.locator("#hosted-file-uploaded").get_by_text("Uploaded parish-flyer"))
    assert page.evaluate(MARKED) == "kept"
    assert posts == [component_origin + UPLOAD]


def test_a_sort_keeps_the_chosen_file_and_typed_name(page, component_origin):
    """A re-sort redraws the table, but not the Upload panel, which only its
    own answer replaces (data-in-place-own): the chosen file and the typed
    placeholder name are kept."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIBRARY)
    page.evaluate(MARK)
    page.locator("#id_file").set_input_files(PDF)
    page.locator("#id_slug").fill("my-flyer")
    page.locator("th[data-sort-column='name'] .sort-link").click()
    # The table's own address, with its region's fragment.
    page.wait_for_url(lambda url: url.split("#")[0] == component_origin + BY_NAME)
    has_text(page.locator(STATUS), "Sorted by Name, ascending")
    assert page.evaluate(MARKED) == "kept"
    assert page.locator("#id_slug").input_value() == "my-flyer"
    files = page.locator("#id_file").evaluate("input => input.files.length")
    assert files == 1


def test_an_upload_answered_by_the_step_up_page_shows_it_whole(page, component_origin):
    """Upload needs no fresh sign-in, but an answer that is not this page
    (the step-up page, 403) is shown whole, as a native submission would
    show it, and was sent once."""
    posts = answer(page, component_origin, UPLOAD, STEP_UP, 403)
    open_and_choose(page, component_origin)
    page.locator(UPLOAD_BUTTON).click()
    visible(page.get_by_role("button", name="Confirm with Google"))
    hidden(page.locator("#hosted-file-upload"))
    assert len(posts) == 1


def test_an_upload_after_the_session_ended_loads_the_sign_in(page, component_origin):
    """An upload whose session ended is redirected to the sign-in page,
    which is loaded as the page it is, never swapped in."""
    open_and_choose(page, component_origin, ENDED)
    page.locator(UPLOAD_BUTTON).click()
    page.wait_for_url(component_origin + "/login")


def open_content(page, origin, address, selector):
    """Open the content list at ``address``, mark it and wheel ``selector``
    into view; return the scroll offset before the click."""
    page.set_viewport_size(VIEWPORT)
    page.goto(origin + address)
    page.evaluate(MARK)
    return wheel_below(page, selector)


def test_fill_in_follows_its_redirect_in_place(page, component_origin):
    """Fill in posts once (a double click included), follows the redirect
    and swaps the page's regions in: no reload, not sent to the top, each
    entry's state and the stepper's count updated, the notice that took
    Fill in's place focused and read out, and the address the redirect's."""
    posts = count_requests(page, "POST", CONTENT)
    fill = "form[data-in-place='setup-content-fill'] button"
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + CONTENT)
    page.evaluate(MARK)
    # The reader opened the step list, which must stay open.
    page.locator("#setup-stepper-list summary").click()
    offset = wheel_below(page, fill)
    page.locator(fill).dblclick()
    notice = (
        "Filled in the default text for 2 page(s) and 1 email(s)."
        " Review each one and edit it as needed."
    )
    has_text(page.locator(STATUS), notice)
    kept_place(page, offset, "#setup-content-filled")
    hidden(page.locator(fill))
    entries = page.locator("#setup-content-entries")
    visible(entries.get_by_text("Default text").first)
    assert entries.get_by_text("Empty").count() == 0
    visible(page.locator("#setup-stepper").get_by_text("1 of", exact=False).first)
    # The step list the reader opened stays open (data-keep-open).
    assert page.locator("#setup-stepper-list").evaluate("node => node.open")
    assert posts == [component_origin + CONTENT]
    assert page.url == component_origin + FILLED + "#setup-content-fill"


def test_reset_follows_its_redirect_in_place(page, component_origin):
    """Reset, once its box is ticked, posts once and swaps the list in: no
    reload, not sent to the top, the box unticked again (holding Reset), so
    focus goes to Reset's heading beside it, and the notice read out."""
    posts = count_requests(page, "POST", RESET)
    reset = "form[data-in-place='setup-content-reset'] button"
    offset = open_content(page, component_origin, RESET, reset)
    box = page.get_by_label("Yes, replace every page and email with the default text")
    box.check()
    page.locator(reset).click()
    has_text(
        page.locator(STATUS), "Reset 3 page(s) and 4 email(s) to the default text."
    )
    kept_place(page, offset, "#setup-content-reset h2")
    assert not box.is_checked()
    visible(page.locator("#setup-content-filled"))
    assert posts == [component_origin + RESET]
    assert page.url == component_origin + RESET_DONE + "#setup-content-reset"


def test_a_refused_fill_in_shows_its_page_whole(page, component_origin):
    """A Fill in the server refuses with a page of no region (a stale
    version's error page, or a step-up) is shown whole, as a native
    submission would show it, and was sent once."""
    posts = answer(page, component_origin, CONTENT, CONTENT_STEP_UP, 403)
    fill = "form[data-in-place='setup-content-fill'] button"
    open_content(page, component_origin, CONTENT, fill)
    page.locator(fill).click()
    visible(page.get_by_role("button", name="Confirm with Google"))
    hidden(page.locator("#setup-content-fill"))
    assert len(posts) == 1
