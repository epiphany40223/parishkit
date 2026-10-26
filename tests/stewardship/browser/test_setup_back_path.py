"""Setup's Check parish data page and its way back around a changed key (#2201).

A restored Check parish data page re-reads its panel, so a check of an
earlier connection never shows as current, in place and without a reload.
"""

import pytest

from .setup_back_path_components import (
    CREDENTIAL_CHECKED,
    SOURCE_CHECKED,
    SOURCE_STALE,
)
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads
from .test_setup_live_status import answer
from .test_setup_mail import place
from .waits import has_attribute, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

SHOWN = "document.dispatchEvent(new Event('visibilitychange'))"
RESTORED = (
    "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
)
NOT_RESTORED = "window.dispatchEvent(new PageTransitionEvent('pageshow'))"


# Finish setup, the step after the check (#1578), opens only while the check
# is current.
FINISH = '.setup-track a[data-tip*="Finish setup"]'


def test_a_settled_region_elsewhere_is_not_read_again(page, component_origin):
    """Only a region marked data-live-recheck-shown is re-checked: a settled
    Test email page reads nothing when its tab is shown again."""
    from .conftest import SETUP_MAIL_STATUS
    from .setup_live_components import SETTLED

    address = SETTLED[("mail", "accepted")]
    polls = answer(page, SETUP_MAIL_STATUS, [""])
    reads = answer_reads(page, component_origin, address, address)
    page.goto(component_origin + address)
    page.evaluate(MARK)
    page.evaluate(SHOWN)
    page.evaluate(NOT_RESTORED)
    page.wait_for_timeout(2500)
    assert polls == [] and reads == []
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("event", [SHOWN, RESTORED])
def test_a_check_page_shown_again_is_read_again(page, component_origin, event):
    """Check parish data shown again (its tab, or the back/forward cache)
    re-reads its panel and step bar, so a check whose connection changed
    elsewhere gives way to Check parish data in place (#2201). A first
    load's pageshow reads nothing."""
    reads = answer_reads(page, component_origin, SOURCE_CHECKED, SOURCE_STALE)
    page.goto(component_origin + SOURCE_CHECKED)
    page.evaluate(MARK)
    checked = page.locator("[data-source-checked]")
    visible(checked)
    assert "ParishSoft answered for St. Example Parish (4321)" in checked.inner_text()
    assert page.locator(FINISH).get_attribute("href")
    page.evaluate(NOT_RESTORED)
    page.wait_for_timeout(300)
    assert reads == []
    page.evaluate(event)
    visible(page.get_by_role("button", name="Check parish data"))
    visible(page.locator("[data-source-stale]"))
    hidden(checked)
    has_attribute(page.locator(FINISH), "aria-disabled", "true")
    assert len(reads) == 1
    assert page.evaluate(MARKED) == "kept"


def test_the_connection_page_says_a_changed_key_needs_a_new_check(
    page, component_origin, axe_source
):
    """While a current parish data check exists, the saved-key notice says a
    different key or organization means checking again (#2201); typing a key
    moves nothing."""
    from .test_components import axe_violations

    page.goto(component_origin + CREDENTIAL_CHECKED)
    note = page.locator("[data-source-checked-note]")
    visible(note)
    assert "means checking it again" in note.inner_text()
    save = page.get_by_role("button", name="Save and continue")
    before = place(page, ".setup-actions button")
    page.get_by_label("ParishSoft API key").fill("another-synthetic-key")
    visible(save)
    assert same_place(place(page, ".setup-actions button"), before)
    visible(note)
    assert axe_violations(page, axe_source) == []
