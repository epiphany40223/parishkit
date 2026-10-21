"""Every Admin export downloads in place (#1778).

ui-conventions "Downloads happen in place": an export button posts its format
with fetch and the page never leaves. A queued export's answer is a redirect
to its status address, which the page reads passively until it says ready,
then posts its Download and saves the file; an in-memory export's answer is
the file itself. The clicked button is busy until then, and a refusal is said
in the row's reserved status line, which keeps its height. The server is
stubbed here (page.route). A queued export's POST is passed on to the
fixture server's EXPORT_QUEUED, a real redirect (Playwright cannot fulfil
one from a route on every engine) to the shared status page's pending
fixture (``live_status_components``); each read of that address the route
sees is answered in the state the test names, such as ready.
"""

import re
from unicodedata import normalize

import pytest
from django.urls import reverse

from .comparison_components import PAGE as COMPARISON
from .followup_components import REQUESTS
from .live_status_components import (
    EXPORT,
    EXPORT_PENDING,
    EXPORT_QUEUED,
    EXPORT_WAITING,
)
from .past_campaign_components import PAGE as PAST_CAMPAIGNS
from .pledges_components import PATH as PLEDGES
from .test_charts import assert_clean, watch
from .timeline_components import ADMIN as TIMELINE
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Each export on its page: (page, the button's group or form, the button,
# whether the server queues it). The group is the export row's label; the
# campaign archive's single button sits in its own form.
EXPORTS = [
    ("/response-dashboard", "Export", "Export XLSX", True),
    ("/campaign-comparison", "Download", "Export PDF", True),
    ("/financial-report", "Export complete results", "Export CSV", True),
    ("/information", "Export complete results", "Export XLSX", True),
    (REQUESTS, "Export the follow-up packet", "Export PDF", True),
    ("/family-directory", "Export complete results", "Export XLSX", True),
    (TIMELINE, "Export this timeline", "Export XLSX", True),
    (PAST_CAMPAIGNS, None, "Download campaign archive", True),
    ("/limitations-report", "Export both tables", "Export XLSX", False),
    ("/census-changes", "Export these changes", "Export CSV", False),
    ("/followup-queue", "Export these requests", "Export XLSX", False),
    (PLEDGES, "Export this list", "Export XLSX", False),
    ("/logs", "Export these log entries", "Export CSV", False),
]
# The status address a queued export's answer redirects to: pending when
# first read, then as _serve_status says. The shared status page's Download
# posts to its own address.
STATUS = EXPORT_PENDING
DOWNLOAD = reverse("admin:report_export_download", args=[EXPORT.split("/")[-2]])
FILE = b"PK fake export"
NAME = "stewardship-export-été.xlsx"
DISPOSITION = (
    'attachment; filename="stewardship-export-_t_.xlsx"; '
    "filename*=UTF-8''stewardship-export-%C3%A9t%C3%A9.xlsx"
)


def _button(page, group, name):
    """The export's button: in its row's group, or the page's own button."""
    scope = page.get_by_role("group", name=group, exact=True) if group else page
    return scope.get_by_role("button", name=name, exact=True)


def _serve_status(page, component_origin, states, busy_downloads=0):
    """Serve STATUS as the status page in each of ``states`` in turn (the
    last one repeats; "unavailable" is a 503 and "gone" a 410), and
    Download as the file after ``busy_downloads`` busy (503) answers.
    Returns the requests seen."""
    seen = []
    pages = {
        "pending": component_origin + EXPORT_PENDING,
        "ready": component_origin + "/live-export-ready",
        "failed": component_origin + EXPORT,
        "waiting": component_origin + EXPORT_WAITING,
    }

    def status(route):
        """One passive status read: the next state's page."""
        seen.append(("status", route.request.method))
        reads = sum(1 for kind, _ in seen if kind == "status")
        state = states[min(reads, len(states)) - 1]
        if state in ("gone", "unavailable"):
            code = 410 if state == "gone" else 503
            route.fulfill(status=code, content_type="text/html", body="<p>No</p>")
            return
        route.fulfill(response=route.fetch(url=pages[state], method="GET"))

    def download(route):
        """The ready file's Download, answered with the file."""
        seen.append(("download", route.request.method))
        if sum(1 for kind, _ in seen if kind == "download") <= busy_downloads:
            route.fulfill(status=503, content_type="text/html", body="<p>Busy</p>")
            return
        route.fulfill(
            status=200,
            body=FILE,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": DISPOSITION,
            },
        )

    page.route("**" + STATUS, status)
    page.route("**" + DOWNLOAD, download)
    return seen


def _answer_export(
    page, origin, action, *, queued, status=None, body="", kind="text/html"
):
    """Answer the export form's POST at ``action``: a redirect to STATUS for
    a queued export, the file for an in-memory one, or ``status``/``body``
    (of media type ``kind``) for a refusal. Returns the posted bodies."""
    posted = []

    def answer(route):
        """The export's own answer."""
        posted.append(route.request.post_data or "")
        if status:
            route.fulfill(status=status, content_type=kind, body=body)
        elif queued:
            route.continue_(url=origin + EXPORT_QUEUED)
        else:
            route.fulfill(
                status=200,
                body=FILE,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": DISPOSITION,
                },
            )

    page.route("**" + action, answer)
    return posted


def _action(button):
    """The address the button's form posts to."""
    return button.evaluate(
        "b => new URL(b.form.getAttribute('action'), location.href).pathname"
    )


@pytest.mark.parametrize(("path", "group", "name", "queued"), EXPORTS)
def test_each_export_downloads_without_leaving_the_page(
    page, component_origin, path, group, name, queued
):
    """Click the export: the file arrives and the page is the same page."""
    errors = watch(page)
    page.goto(component_origin + path)
    button = _button(page, group, name)
    visible(button)
    seen = _serve_status(page, component_origin, ["pending", "ready"])
    posted = _answer_export(page, component_origin, _action(button), queued=queued)
    page.evaluate("window.__stayed = true")
    where = page.url
    with page.expect_download(timeout=20_000) as download:
        button.click()
    # macOS WebKit hands the name back decomposed (NFD).
    assert normalize("NFC", download.value.suggested_filename) == NAME
    assert download.value.path().read_bytes() == FILE
    assert len(posted) == 1
    if group:
        value = button.get_attribute("value")
        assert f"format={value}" in posted[0]
    if queued:
        # Read passively (the redirect's own read too, where an engine
        # routes it), pending and then ready, then the one Download post.
        assert seen[-1] == ("download", "POST")
        assert set(seen[:-1]) == {("status", "GET")} and len(seen) >= 3
    else:
        assert seen == []
    assert page.url == where
    assert page.evaluate("window.__stayed === true")
    # Released, with nothing said: the line keeps its place, empty.
    assert button.get_attribute("aria-disabled") is None
    assert "is-busy" not in (button.get_attribute("class") or "")
    line = button.locator("xpath=ancestor::form[1]//*[@data-export-status]")
    assert line.inner_text() == ""
    assert_clean(page, errors)


def test_a_queued_export_is_busy_and_says_its_status(page, component_origin):
    """While the file is prepared the button shows its spinner, the row's
    other buttons wait, and after five seconds the reserved line says the
    server's status, without moving what follows it."""
    page.goto(component_origin + "/financial-report")
    button = _button(page, "Export complete results", "Export CSV")
    visible(button)
    _serve_status(page, component_origin, ["pending"])
    _answer_export(page, component_origin, _action(button), queued=True)
    line = page.locator("#financial-export-status")
    after = line.evaluate("node => node.getBoundingClientRect().height")
    button.click()
    page.wait_for_timeout(6000)
    assert "is-busy" in button.get_attribute("class")
    assert button.get_attribute("aria-disabled") == "true"
    for other in ("Export XLSX", "Export PDF"):
        assert (
            _button(page, "Export complete results", other).get_attribute(
                "aria-disabled"
            )
            == "true"
        )
    assert "Preparing your file" in line.inner_text()
    assert line.evaluate("node => node.getBoundingClientRect().height") == after


@pytest.mark.parametrize(
    ("states", "said"),
    [
        (["pending", "failed"], "The file could not be created."),
        (["pending", "gone"], "This export ended before it was downloaded."),
    ],
)
def test_a_queued_export_that_cannot_finish_says_so_in_place(
    page, component_origin, states, said
):
    """A failed or ended export is said in the row's line, and the buttons
    are released for another try, on the same page."""
    page.goto(component_origin + "/family-directory")
    button = _button(page, "Export complete results", "Export XLSX")
    visible(button)
    seen = _serve_status(page, component_origin, states)
    _answer_export(page, component_origin, _action(button), queued=True)
    where = page.url
    button.click()
    line = page.locator("#directory-export-status")
    from playwright.sync_api import expect

    expect(line).to_contain_text(said, timeout=15_000)
    assert ("download", "POST") not in seen
    assert button.get_attribute("aria-disabled") is None
    assert page.url == where


def test_a_refused_export_is_said_in_place_and_a_new_key_is_sent(
    page, component_origin
):
    """The server's refusal page (other work holds exports) is said in the
    line, not shown as the page, and the next click sends a fresh one-time
    request key."""
    page.goto(component_origin + "/financial-report")
    button = _button(page, "Export complete results", "Export XLSX")
    visible(button)
    refusal = (
        "<main><h1>Export action unavailable</h1><p role=status>Export "
        "processing is temporarily unavailable. Your access has not been "
        "denied. Try again in a few seconds.</p></main>"
    )
    posted = _answer_export(
        page, component_origin, _action(button), queued=True, status=503, body=refusal
    )
    where = page.url
    button.click()
    line = page.locator("#financial-export-status")
    from playwright.sync_api import expect

    expect(line).to_contain_text("Export processing is temporarily unavailable.")
    assert page.url == where
    button.click()
    expect(line).to_contain_text("Export processing is temporarily unavailable.")
    assert len(posted) == 2
    keys = [
        dict(part.split("=", 1) for part in body.split("&"))["request_key"]
        for body in posted
    ]
    assert keys[0] != keys[1]


@pytest.mark.parametrize(
    ("states", "busy"), [(["unavailable", "unavailable", "ready"], 0), (["ready"], 2)]
)
def test_a_passing_failure_is_tried_again(page, component_origin, states, busy):
    """A status read answered 503 is read again, and a busy Download is
    posted again, rather than ending the export: the file still arrives."""
    page.goto(component_origin + "/financial-report")
    button = _button(page, "Export complete results", "Export XLSX")
    visible(button)
    seen = _serve_status(page, component_origin, states, busy_downloads=busy)
    posted = _answer_export(page, component_origin, _action(button), queued=True)
    with page.expect_download(timeout=30_000) as download:
        button.click()
    assert download.value.path().read_bytes() == FILE
    assert len(posted) == 1
    assert seen.count(("download", "POST")) == busy + 1


def test_a_typed_refusal_is_said_in_place(page, component_origin):
    """A script's typed JSON refusal (the follow-up packet's limit, a
    denial) is said with its message and fix, in the row's line."""
    page.goto(component_origin + REQUESTS)
    button = _button(page, "Export the follow-up packet", "Export PDF")
    visible(button)
    refusal = (
        '{"errors": [{"code": "invalid", "field_id": "ministries"}], '
        '"refusal": {"message": "Too many Ministries for one packet.", '
        '"fix": "Choose fewer Ministries."}}'
    )
    _answer_export(
        page,
        component_origin,
        _action(button),
        queued=True,
        status=400,
        body=refusal,
        kind="application/json",
    )
    button.click()
    line = page.locator("#ministry-packet-export-status")
    from playwright.sync_api import expect

    expect(line).to_have_text(
        "Too many Ministries for one packet. Choose fewer Ministries."
    )
    assert button.get_attribute("aria-disabled") is None


def test_a_double_click_sends_one_export(page, component_origin):
    """A second click while the export runs is ignored: one POST."""
    page.goto(component_origin + "/limitations-report")
    button = _button(page, "Export both tables", "Export XLSX")
    visible(button)
    held = []
    page.route("**" + _action(button), lambda route: held.append(route))
    button.evaluate("b => { b.click(); b.click(); }")
    page.wait_for_timeout(1000)
    button.evaluate("b => b.click()")
    page.wait_for_timeout(500)
    assert len(held) == 1
    held[0].fulfill(status=204)


@pytest.mark.parametrize("width", [390, 1280])
def test_a_long_refusal_never_moves_the_page(page, component_origin, width):
    """The line keeps room for two sentences (three lines on a phone); an
    error page's long notice is cut to its first two sentences."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/financial-report")
    button = _button(page, "Export complete results", "Export XLSX")
    visible(button)
    # The export error page's own notice (report-export-error.html), with
    # more after it than the line shows.
    long = (
        "<main><p role=alert>Export processing is temporarily unavailable. "
        "Your access has not been denied. Try again in a few seconds. A "
        "fourth sentence that is never shown in the row's line.</p></main>"
    )
    _answer_export(
        page, component_origin, _action(button), queued=True, status=409, body=long
    )
    line = page.locator("#financial-export-status")
    before = line.evaluate("node => node.getBoundingClientRect().height")
    tall = "document.documentElement.scrollHeight"
    height = page.evaluate(tall)
    button.click()
    from playwright.sync_api import expect

    expect(line).to_have_text(
        "Export processing is temporarily unavailable. Your access has not been denied."
    )
    assert line.evaluate("node => node.getBoundingClientRect().height") == before
    assert page.evaluate(tall) == height


def test_a_waiting_export_says_why_in_browser_time(page, component_origin):
    """An export queued behind other work says why in the line (#340), with
    the time in the browser's zone and without the ticking "queued" time,
    which would make every read a new announcement."""
    page.goto(component_origin + "/financial-report")
    button = _button(page, "Export complete results", "Export CSV")
    visible(button)
    _serve_status(page, component_origin, ["waiting"])
    _answer_export(page, component_origin, _action(button), queued=True)
    button.click()
    line = page.locator("#financial-export-status")
    from playwright.sync_api import expect

    expect(line).to_contain_text(
        "Waiting for a ParishSoft refresh to finish", timeout=15_000
    )
    text = line.inner_text()
    assert "started" in text and "queued" not in text
    # A clock time, never the server's ISO text.
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", text)


def _hold_export(page, action):
    """Hold the export's POST at ``action`` unanswered; returns the held
    routes, answered later with ``_answer_file``."""
    held = []
    page.route("**" + action, lambda route: held.append(route))
    return held


def _answer_file(route):
    """Answer a held export with the file."""
    route.fulfill(
        status=200,
        body=FILE,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": DISPOSITION,
        },
    )


def _assert_busy_after_redraw(page, group, clicked, other, held):
    """The redrawn row shows the running export: the clicked button busy,
    every button waiting, and a click on another sends no second export."""
    from playwright.sync_api import expect

    fresh = _button(page, group, clicked)
    expect(fresh).to_have_class(re.compile(r"\bis-busy\b"))
    expect(fresh).to_have_attribute("aria-disabled", "true")
    second = _button(page, group, other)
    expect(second).to_have_attribute("aria-disabled", "true")
    assert "is-busy" not in (second.get_attribute("class") or "")
    second.evaluate("b => b.click()")
    page.wait_for_timeout(500)
    assert len(held) == 1


def _assert_released_with_file(page, group, clicked, held):
    """Once the file arrives the redrawn row is released."""
    from playwright.sync_api import expect

    with page.expect_download() as download:
        _answer_file(held[0])
    assert download.value.path().read_bytes() == FILE
    button = _button(page, group, clicked)
    expect(button).not_to_have_attribute("aria-disabled", "true")
    assert "is-busy" not in (button.get_attribute("class") or "")


def test_a_redrawn_comparison_keeps_its_running_export(page, component_origin):
    """Compare on day redraws the report, export form and all, while an
    export runs: the fresh form shows it busy, sends no second export, and
    is released when the file arrives."""
    # Its own address, which the in-place refresh keeps.
    page.goto(component_origin + COMPARISON)
    button = _button(page, "Download", "Export XLSX")
    visible(button)
    held = _hold_export(page, _action(button))
    button.click()
    page.wait_for_timeout(300)
    assert len(held) == 1
    page.get_by_label("Compare on day").fill("5")
    page.get_by_role("button", name="Compare", exact=True).click()
    from playwright.sync_api import expect

    expect(page.locator("#comparison-report p").first).to_contain_text("Day 5 of")
    _assert_busy_after_redraw(page, "Download", "Export XLSX", "Export CSV", held)
    _assert_released_with_file(page, "Download", "Export XLSX", held)


def test_a_synced_row_keeps_its_running_export(page, component_origin):
    """An in-place Apply redraws Ministry requests' synced export buttons
    while an export runs: they show it busy, send no second export, and are
    released when the file arrives."""
    from .test_table_sorting import fulfil_post_with
    from .waits import eventually

    group = "Export these requests"
    page.goto(component_origin + "/followup-queue")
    button = _button(page, group, "Export XLSX")
    visible(button)
    held = _hold_export(page, _action(button))
    button.click()
    page.wait_for_timeout(300)
    assert len(held) == 1
    fulfil_post_with(
        page,
        "**" + reverse("admin:ministry_report"),
        component_origin,
        lambda request: "/followup-all",
    )
    page.get_by_label("Search Member or Ministry name").fill("")
    page.get_by_role("button", name="Apply filters").click()
    eventually(
        page, "document.querySelector('#table-export input[name=search]').value", ""
    )
    _assert_busy_after_redraw(page, group, "Export XLSX", "Export CSV", held)
    _assert_released_with_file(page, group, "Export XLSX", held)
