"""Update ParishSoft: accessible, Check ParishSoft and Update act in place.

The page (#528; renamed and given its confirmation and run by #1688) is
served by ``publication_components`` at its real address. Check ParishSoft
posts in place and follows the redirect back to the page, so the check's
live status appears below the button with nothing above it moving; when the
poll finds the check finished, the page is re-read once in place and shows
the plan. Update and Run again open the shared review dialog with their
confirmation; Apply closes it and the page is re-read in place with the
run's status. A mark outside every region shows the page never reloaded.
"""

import pytest

from .publication_components import (
    CHECK_FINISHED_STATUS,
    CHECK_STATUS,
    CHECKED,
    CONFIRMATION,
    EXPIRED,
    OFF,
    PUBLICATION,
    REQUESTED,
    RETRIES,
    RUNNING,
    STOPPED,
    UPDATE_FINISHED_STATUS,
    UPDATE_STATUS,
    UPDATED,
    UPDATING,
)
from .test_in_place import MARK, MARKED, VIEWPORT
from .test_live_status_in_place import answer_reads
from .waits import (
    available,
    eventually,
    greyed,
    has_attribute,
    has_text,
    hidden,
    visible,
)

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

STATES = (OFF, PUBLICATION, REQUESTED, CHECKED, RUNNING, EXPIRED, UPDATING)
STATES += (UPDATED, STOPPED)


@pytest.mark.parametrize("width", [320, 1280])
def test_each_state_is_accessible_and_fits(page, component_origin, axe_source, width):
    """Off, ready, checking, checked, held, an hour-old check, updating,
    updated and stopped all pass the accessibility scan and fit the
    window."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in STATES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        ), path


def test_switched_off_offers_no_button(page, component_origin):
    """Off: the page says so, with no Check ParishSoft, no Update and no plan."""
    page.goto(component_origin + OFF)
    visible(page.locator("#publication-off"))
    assert page.get_by_role("button", name="Check ParishSoft").count() == 0
    assert page.get_by_role("button", name="Update 2 changes").count() == 0
    assert page.get_by_role("heading", name="Newest check").count() == 0


def test_a_running_update_holds_both_buttons_with_their_reasons(page, component_origin):
    """Check ParishSoft and Update are unavailable, each named by its own
    visible reason."""
    page.goto(component_origin + RUNNING)
    for name, reason in (
        ("Check ParishSoft", "publication-check-reason"),
        ("Update 2 changes", "update-reason"),
    ):
        button = page.get_by_role("button", name=name)
        greyed(button)
        has_attribute(button, "aria-describedby", reason)
        has_text(
            page.locator(f"#{reason}"),
            "An update is running. Wait for it to finish.",
        )


def test_an_hour_old_check_holds_update_with_its_reason(page, component_origin):
    """An hour-old check greys Update, named by its reason."""
    page.goto(component_origin + EXPIRED)
    button = page.get_by_role("button", name="Update 2 changes")
    greyed(button)
    has_attribute(button, "aria-describedby", "update-reason")
    has_text(
        page.locator("#update-reason"),
        "This check is more than an hour old. Check ParishSoft again.",
    )


def test_check_parishsoft_stays_on_the_page_and_shows_the_plan(page, component_origin):
    """Check ParishSoft posts its key in place; the check's status appears
    below the button, nothing above it moves, and once the check ends the
    page is re-read once, in place, with the plan's rows and counts."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + PUBLICATION)
    page.evaluate(MARK)
    button = page.get_by_role("button", name="Check ParishSoft")
    before = button.bounding_box()
    assert page.get_by_role("heading", name="Current status").count() == 0
    with page.expect_request(lambda request: request.method == "POST") as sent:
        button.click()
    assert "request_key=" in sent.value.post_data
    assert "csrfmiddlewaretoken=" in sent.value.post_data
    has_text(
        page.get_by_role("status").filter(has_text="Check requested."),
        "Check requested.",
    )
    region = page.locator("#publication-check")
    visible(region.get_by_role("heading", name="Current status"))
    visible(region.get_by_role("link", name="View this check's Background task"))
    assert page.evaluate(MARKED) == "kept"
    assert button.bounding_box()["y"] == before["y"]
    polls = answer_reads(
        page,
        component_origin,
        CHECK_STATUS + "?view=publication",
        CHECK_FINISHED_STATUS,
    )
    reads = answer_reads(page, component_origin, REQUESTED, CHECKED)
    visible(region.get_by_text("Finished successfully."))
    plan = page.locator("#publication-plan")
    visible(plan.get_by_text("2 changes for 2 Members will be written."))
    visible(plan.get_by_role("cell", name="Held back"))
    visible(plan.get_by_role("cell", name="Could not be read"))
    visible(plan.get_by_text("Checked by admin@example.org at", exact=False))
    available(plan.get_by_role("button", name="Update 2 changes"))
    assert page.evaluate(MARKED) == "kept"
    assert len(polls) >= 1 and len(reads) == 1, (polls, reads)


def test_update_confirms_in_the_dialog_and_shows_the_run_in_place(
    page, component_origin
):
    """Update opens the review dialog with the plan's counts as its
    question, focus on its title; Apply posts the plan, counts and key,
    the dialog closes and the page is re-read in place showing the run's
    progress, then each change's result once the run ends; the page never
    reloads and nothing above the plan moves."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + CHECKED)
    page.evaluate(MARK)
    # Where Check ParishSoft sits in the document, so a scroll that brings
    # the status line into view is not taken for a control moving.
    place = "(n) => n.getBoundingClientRect().top + window.scrollY"
    check = page.get_by_role("button", name="Check ParishSoft")
    before = check.evaluate(place)
    with page.expect_request(
        lambda request: request.method == "POST" and CONFIRMATION in request.url
    ) as reviewed:
        page.get_by_role("button", name="Update 2 changes").click()
    assert "action=preview" in reviewed.value.post_data
    assert "plan=" in reviewed.value.post_data
    dialog = page.locator("#update-review")
    visible(dialog)
    title = dialog.get_by_role("heading", name="Write 2 changes for 2 Members to")
    visible(title)
    eventually(page, "document.activeElement?.hasAttribute('data-review-title')")
    has_text(
        dialog.locator("#update-review-summary"),
        "Each Member's contact record in ParishSoft is read again, and only "
        "these changes are written; nothing else changes. 1 Member is held "
        "back. This cannot be undone from here.",
    )
    # The page's re-reads (after Apply, then once the run ends) bring the
    # run under way, then the run done.
    reads = answer_reads(page, component_origin, CHECKED, UPDATING, UPDATED)
    polls = answer_reads(
        page, component_origin, UPDATE_STATUS + "?view=update", UPDATE_FINISHED_STATUS
    )
    with page.expect_request(
        lambda request: request.method == "POST" and CONFIRMATION in request.url
    ) as applied:
        dialog.get_by_role("button", name="Apply").click()
    body = applied.value.post_data
    for field in ("action=confirm", "plan=", "write_entities=2", "write_fields=2"):
        assert field in body, body
    assert "blocked_entities=1" in body and "request_key=" in body
    hidden(dialog)
    plan = page.locator("#publication-plan")
    visible(plan.get_by_text("Finished successfully."))
    # Updated in ParishSoft, and Confirmed once the refresh showed it.
    visible(plan.get_by_role("cell", name="Confirmed"))
    visible(plan.get_by_role("cell", name="Failed: ParishSoft refused the change"))
    visible(
        plan.get_by_text(
            "ParishSoft is being refreshed so Census changes shows the new values."
        )
    )
    assert plan.get_by_role("button", name="Run again").count() == 0
    assert page.evaluate(MARKED) == "kept"
    assert check.evaluate(place) == before
    assert len(reads) == 2 and len(polls) >= 1, (reads, polls)


def test_updating_counts_the_members_done(page, component_origin):
    """While the run is under way its status says how many Members are done,
    with its Background task link, and neither Update nor Run again shows."""
    page.goto(component_origin + UPDATING)
    plan = page.locator("#publication-plan")
    visible(plan.get_by_text("Updating ParishSoft: 1 of 2 Members done"))
    visible(plan.get_by_role("link", name="View this update's Background task"))
    assert plan.get_by_role("button", name="Update 2 changes").count() == 0
    assert plan.get_by_role("button", name="Run again").count() == 0


def test_a_stopped_run_offers_run_again_alone(page, component_origin):
    """A run that stopped before it finished offers Run again in Update's
    place, which opens its own confirmation naming the Members left."""
    page.goto(component_origin + STOPPED)
    plan = page.locator("#publication-plan")
    visible(plan.get_by_text("This update stopped before it finished.", exact=False))
    visible(plan.get_by_role("cell", name="Not finished"))
    assert plan.get_by_role("button", name="Update 2 changes").count() == 0
    with page.expect_request(
        lambda request: request.method == "POST" and RETRIES in request.url
    ):
        plan.get_by_role("button", name="Run again").click()
    dialog = page.locator("#rerun-review")
    visible(dialog.get_by_role("heading", name="Try the 1 Member this run did not"))
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
