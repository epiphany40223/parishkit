"""ParishSoft connection's organization lookup in the browser (#1554).

While ParishSoft is asked which organizations the key reaches, the page
re-reads itself quietly (ui-conventions "Pages that update themselves") and
swaps the answer into its one #parishsoft-connection region: the list, the
one organization to confirm with Yes or No, or the failure at the key
field. Each wait test installs a fake clock, routes the page's re-read to
the answer's fixture page, and marks the heading outside the region, which
only a full page load could lose.
"""

import pytest

from .conftest import LOOKUP_ID, NOW
from .test_in_place import MARK, MARKED, VIEWPORT
from .test_live_status_in_place import answer_reads
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

WAIT = "/setup-organization-wait"
ONE = "/setup-organization-one"
FAILED = "/setup-organization-failed"
# A little more than the wait's 5-second re-read pace.
TICK = 6 * 1000
REGION = "#parishsoft-connection"


def contains(locator, text):
    """Assert that ``locator`` comes to contain ``text``, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_contain_text(text)


def waiting(page, origin, answer):
    """Open the wait under a fake clock, mark it, and answer its re-read
    with the ``answer`` page; return the reads answered."""
    page.set_viewport_size(VIEWPORT)
    page.clock.install(time=NOW)
    page.goto(origin + WAIT)
    contains(page.locator(REGION), "Asking ParishSoft which organizations")
    page.evaluate(MARK)
    return answer_reads(page, origin, WAIT, answer)


def test_the_wait_swaps_in_the_organization_in_place(page, component_origin):
    """The spinner gives way to the one organization to confirm, in place:
    no reload, the address unchanged, and no more re-reads."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    reads = waiting(page, component_origin, ONE)
    page.clock.fast_forward(TICK)
    contains(
        page.locator(REGION),
        "This key reaches one ParishSoft organization: St. Example Parish (7).",
    )
    assert page.locator(f"{REGION} [role=status]").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + WAIT
    visible(page.get_by_role("button", name="Yes, use this organization"))
    page.clock.fast_forward(3 * TICK)
    assert len(reads) == 1
    assert failures == []


def test_a_failed_lookup_shows_at_the_key_field_in_place(page, component_origin):
    """A refused key's message is at the key field, swapped in without a
    reload, with the key field ready for a different key."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    waiting(page, component_origin, FAILED)
    page.clock.fast_forward(TICK)
    key = page.get_by_label("ParishSoft API key")
    visible(key)
    contains(
        page.locator(REGION),
        "ParishSoft did not accept this key. Check that you copied the whole key",
    )
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + WAIT
    # The message describes the key field, as every field error does.
    described = key.get_attribute("aria-describedby") or ""
    assert any(
        "did not accept this key" in (page.locator(f"#{name}").text_content() or "")
        for name in described.split()
        if page.locator(f"#{name}").count()
    )
    assert failures == []


@pytest.mark.parametrize(
    "button,action",
    [
        ("Yes, use this organization", "choose"),
        ("No, enter a different key", "discard"),
    ],
)
def test_one_organization_is_confirmed_with_yes_or_no(
    page, component_origin, button, action
):
    """Yes posts the choice of the one organization for this lookup; No
    posts the discard, which needs no choice."""
    posted = []

    def answer(route):
        """Record the page's own POST and answer it with a plain page."""
        if route.request.method != "POST":
            route.continue_()
            return
        posted.append(route.request.post_data)
        route.fulfill(content_type="text/html", body="<p>Answered</p>")

    page.goto(component_origin + ONE)
    page.route(lambda url: url.split("#")[0] == component_origin + ONE, answer)
    page.get_by_role("button", name=button).click()
    contains(page.locator("p"), "Answered")
    assert len(posted) == 1
    fields = dict(pair.split("=", 1) for pair in posted[0].split("&"))
    assert fields["action"] == action
    assert fields["lookup"] == str(LOOKUP_ID)
    assert fields["organization"] == "7"
