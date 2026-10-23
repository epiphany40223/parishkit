"""A report email's Send a test to me in the browser (#2036).

A row's envelope and the editor's Send a test to me post in place, from the
page script, with no dialog and no page load; the server's answer (routed
here) is written to a status line that is already there, so nothing moves:
the row's to the page's status line above the table ("Test of Weekly report
sent to you at …", in this browser's time), the editor's to the reserved
lines under its buttons. A refusal says why, in the error colour. The page
fits a 320 px phone without a page scroll, and axe passes.
"""

import json

import pytest

from .report_email_editor_components import EDIT
from .report_emails_components import PAGE, TEST
from .test_components import axe_violations
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

QUEUED = json.dumps({"queued": True, "updating": False})
PENDING = json.dumps(
    {
        "errors": [{"code": "stale", "field_id": None, "message": "Stale."}],
        "refusal": {
            "message": "A test of this report is already on its way to you.",
            "fix": (
                "Wait for it to arrive, then send another. If Sent mail shows "
                "its delivery as unknown, record what happened there first."
            ),
            "link": None,
        },
    }
)


def route(page, url, answers, posted):
    """Answer each post to ``url`` with the next of ``answers`` (status, body)."""

    def answer(request_route):
        """Keep what was posted, then answer it."""
        posted.append(request_route.request.post_data or "")
        status, body = answers.pop(0)
        request_route.fulfill(status=status, body=body, content_type="application/json")

    page.route(url, answer)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 1280])
def test_a_rows_test_says_it_was_sent_in_place(
    page, component_origin, axe_source, width, scheme
):
    """The row's envelope posts the report, keeps its place and focus, and the
    page's status line says the test was sent, with the time; a refusal (a
    test already pending) says why in the error colour, readable in either
    scheme. No dialog opens."""
    from playwright.sync_api import expect

    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    posted = []
    route(
        page,
        component_origin + TEST,
        [(200, QUEUED), (409, PENDING)],
        posted,
    )
    page.goto(component_origin + PAGE)
    button = page.get_by_role("button", name="Send a test of Weekly report to me")
    visible(button)
    # By keyboard: WebKit's click gives a button no focus. Focus scrolls the
    # button into view, so its place is measured after it.
    button.focus()
    box = button.bounding_box()
    table = page.locator("#report-emails-table").bounding_box()
    page.keyboard.press("Enter")
    outcome = page.locator("#report-emails-outcome")
    expect(outcome).to_contain_text("Test of Weekly report sent to you at")
    assert "report=" in posted[0]
    assert button.bounding_box() == box
    assert page.locator("#report-emails-table").bounding_box() == table
    assert button.evaluate("node => node === document.activeElement")
    assert not page.locator("dialog[open]").count()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    page.keyboard.press("Enter")
    expect(outcome).to_contain_text("already on its way to you")
    expect(outcome).to_have_class("review-outcome is-error")
    assert len(posted) == 2
    assert axe_violations(page, axe_source) == []


def test_every_row_offers_a_test(page, component_origin):
    """A test never counts as a send, so even a busy row offers it."""
    page.goto(component_origin + PAGE)
    for name in ("Weekly report", "Finance council report", "Daily report"):
        button = page.get_by_role("button", name=f"Send a test of {name} to me")
        visible(button)
        assert button.get_attribute("aria-disabled") is None


@pytest.mark.parametrize("width", [320, 1280])
def test_the_editor_tests_the_form_as_it_is(page, component_origin, axe_source, width):
    """Edit's Send a test to me needs no change, posts the form with test=yes
    in place (no dialog), and the reserved lines under the buttons say it
    was sent, or name each refused field; the buttons never move."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    posted = []
    refused = json.dumps(
        {"errors": [{"field": "Report name", "message": "Enter a name."}]}
    )
    route(
        page,
        component_origin + "/admin/mail/report-emails/*/",
        [(200, QUEUED), (400, refused)],
        posted,
    )
    page.goto(component_origin + EDIT)
    button = page.get_by_role("button", name="Send a test to me")
    visible(button)
    # Focus scrolls the button into view, so places are measured after it.
    button.focus()
    box = button.bounding_box()
    preview = page.get_by_role("button", name="Preview", exact=True).bounding_box()
    page.keyboard.press("Enter")
    status = page.locator("#report-test-status")
    expect(status).to_contain_text("Test sent to you at")
    assert posted and "test=yes" in posted[0] and "action=preview" in posted[0]
    assert "render=yes" not in posted[0]
    assert not page.locator("dialog[open]").count()
    assert button.bounding_box() == box
    assert page.get_by_role("button", name="Preview", exact=True).bounding_box() == (
        preview
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    page.keyboard.press("Enter")
    expect(status).to_contain_text("Report name: Enter a name.")
    expect(status).to_have_class("reserve-lines-wrap report-test-status is-error")
    assert button.bounding_box() == box
    assert axe_violations(page, axe_source) == []
