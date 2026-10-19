"""The Go live page's Prepare step over HTTP (#1585 slice 3).

A go-live request whose cleanup completed, started by a real Administrator
signed in through the ordinary login. Promotions are real source
promotions; the scheduler's go-live producer runs as the scheduler login
and the link tasks it creates run as the worker, so every stage the page
shows comes from the owners. Each assertion is a plain GET, as a browser
reload is, and every POST answers a redirect or the Stop go-live dialog.
"""

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.go_live_sequencing import (
    attempts,
    refreshes_held,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCancellation,
    ProductionTransitionRequest,
)

from .auth_builders import signed_in
from .leader_builders import grant_role
from .test_go_live_sequencing_postgresql import (
    refreshed,
    run_link_task,
    start,
    step,
)
from .test_scheduler_link_preparation_postgresql import cleaned_up
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

PREPARE = reverse("admin:go_live_prepare")
CHECK = reverse("admin:go_live")
# A browser's own page load (Fetch Metadata), as opposed to a page's fetch.
NAVIGATE = {"HTTP_SEC_FETCH_MODE": "navigate"}


@pytest.fixture
def prepare_page(response_service, google):
    """The harness, a signed-in Administrator's browser, and their request."""
    browser, login = signed_in()
    assert login.status_code == 302
    admin = PortalUser.objects.get().pk
    request = ProductionTransitionRequest.objects.get(
        pk=cleaned_up(response_service, admin)
    )
    return response_service, browser, admin, request


def shown(browser, **headers):
    """GET the Prepare step and return its response and its Prepare state."""
    with web_login():
        page = browser.get(PREPARE, **headers)
    assert page.status_code == 200, page.content
    return page, page.context["stage"]


def boxes(stage):
    """Each stage box's state, left to right."""
    return [box["state"] for box in stage.boxes]


def test_prepare_follows_refresh_links_and_stale_links_by_itself(prepare_page):
    """Refresh and prepare again begins an attempt; the boxes follow the work.

    A request with no attempt offers Refresh and prepare again (sent twice,
    one attempt); then the refresh box runs with no primary, the producer
    prepares the links, Ready to confirm completes the step (a browser's
    own load opens Confirm Production), and a later promotion makes the
    links stale, so they are prepared again by themselves.
    """
    harness, browser, admin, request = prepare_page
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("again", "no_attempt")
    assert b"Refresh and prepare again" in page.content
    assert page.content.count(b"data-go-live-primary") == 1
    assert b"data-go-live-stop" in page.content
    again = {"action": "again", "control": stage.controls["again"]}
    with web_login():
        assert browser.post(PREPARE, again).status_code == 403
        answer = post(browser, PREPARE, again)
        assert answer.status_code == 303 and answer["Location"] == PREPARE
        # Sent again (an answer lost): no second attempt.
        assert post(browser, PREPARE, again).status_code == 303
        forged = {"action": "again", "control": "forged"}
        assert post(browser, PREPARE, forged).status_code == 400
    assert len(attempts(request)) == 1
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("running", "refresh")
    assert boxes(stage) == ["done", "active", "waiting"]
    assert b"Waiting for the ParishSoft refresh to finish" in page.content
    assert b"Nothing to do here; this page updates by itself." in page.content
    assert b"data-go-live-primary" not in page.content
    assert b"data-live-pending" in page.content
    assert b"data-live-interval" not in page.content
    assert b"Step 2 of 4:" in page.content
    assert b"1 of 4 steps completed" in page.content
    # The refresh promotes; the producer prepares the links.
    refreshed(harness)
    (prepared,) = step()
    assert prepared.action == "prepare"
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("running", "preparing")
    assert boxes(stage) == ["done", "done", "active"]
    assert b"Preparing links:" in page.content
    preparation = ProductionTokenPreparation.objects.get()
    assert run_link_task(harness, preparation.task_id)
    page, stage = shown(browser)
    assert stage.state == "ready" and boxes(stage) == ["done", "done", "done"]
    confirmation = reverse(
        "admin:production_confirmation", args=[request.pk, preparation.pk]
    )
    assert stage.confirm_url == confirmation
    assert b"Continue to Confirm Production" in page.content
    assert b"2 of 4 steps completed" in page.content
    assert page.context["wizard"].step("confirm").url == confirmation
    # The step completed, so a browser's own load opens Confirm Production.
    with web_login():
        reload = browser.get(PREPARE, **NAVIGATE)
    assert reload.status_code == 303 and reload["Location"] == confirmation
    # A later promotion makes the links stale: the producer discards them
    # and prepares the attempt's second preparation by itself.
    refreshed(harness)
    (discarded,) = step()
    assert discarded.action == "discard"
    page, stage = shown(browser)
    assert stage.state == "running" and boxes(stage) == ["done", "done", "active"]
    assert b"ParishSoft data changed, so new links are being prepared (2 of 4)" in (
        page.content
    )
    cancellation = ProductionTokenCancellation.objects.get(preparation=preparation)
    assert run_link_task(harness, cancellation.task_id, cleanup=True)
    (again_prepared,) = step()
    assert (again_prepared.action, again_prepared.preparation) == ("prepare", 2)
    assert shown(browser)[1].reason == "repreparing"


def test_a_failed_preparation_offers_retry_that_changes_no_data(prepare_page):
    """A preparation failed after its retries: Failed box, one primary Retry."""
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_taskrun_postgresql import act

    harness, browser, admin, request = prepare_page
    start(request, admin)
    refreshed(harness)
    step()
    preparation = ProductionTokenPreparation.objects.get()
    act(
        act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim"),
        "permanent_failure",
    )
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("failed", "retry")
    assert boxes(stage) == ["done", "done", "failed"]
    assert b"Preparing the Family links failed after its automatic retries." in (
        page.content
    )
    assert page.content.count(b"data-go-live-primary") == 1
    # The same control on every read, so the live region stays the same.
    assert shown(browser)[1].controls["retry"] == stage.controls["retry"]
    retry = {"action": "retry", "control": stage.controls["retry"]}
    with web_login():
        answer = post(browser, PREPARE, retry)
        assert answer.status_code == 303 and answer["Location"] == PREPARE
        # Sent again: the failure it named is gone, so nothing more.
        assert post(browser, PREPARE, retry).status_code == 303
    assert TaskRun.objects.filter(root_id=preparation.task_id).count() == 2
    assert ProductionTokenPreparation.objects.count() == 1
    assert shown(browser)[1].state == "running"


def test_stop_go_live_discards_the_links_and_opens_check(prepare_page):
    """Stop go-live's dialog: worded for the state, Apply stops and opens Check.

    The prepared links are discarded and the go-live is cancelled through
    the owners, which release the gate and the refresh hold; Prepare then
    leads to Check by itself, and a second Apply does nothing more.
    """
    harness, browser, admin, request = prepare_page
    start(request, admin)
    refreshed(harness)
    step()
    preparation = ProductionTokenPreparation.objects.get()
    assert run_link_task(harness, preparation.task_id)
    assert refreshes_held(database_now())
    with web_login():
        review = post(browser, PREPARE, {"action": "review_stop"})
    assert review.status_code == 200, review.content
    assert b"data-review-answer" in review.content
    assert b"The prepared Family links are discarded." in review.content
    assert b"Testing data already deleted is not restored." in review.content
    stop = {"action": "stop", "control": shown(browser)[1].controls["stop"]}
    with web_login():
        assert browser.post(PREPARE, stop).status_code == 403
        answer = post(browser, PREPARE, stop)
        assert answer.status_code == 303 and answer["Location"] == CHECK
        assert post(browser, PREPARE, stop).status_code == 303
    assert ProductionTokenCancellation.objects.filter(preparation=preparation).exists()
    assert ProductionCleanupCancellation.objects.filter(request=request).count() == 1
    assert ProductionTransitionRequest.objects.get(pk=request.pk).state == "cancelled"
    assert not refreshes_held(database_now())
    page, stage = shown(browser)
    assert stage.state == "stopped" and not stage.stopped_by_other
    assert b"Go-live was stopped." in page.content
    assert b"data-live-follow" in page.content and b"data-live-pending" not in (
        page.content
    )
    # A review after the stop is refused inside the dialog.
    with web_login():
        late = post(browser, PREPARE, {"action": "review_stop"})
    assert late.status_code == 409
    assert b"Go-live was already stopped." in late.content
    with web_login():
        check = browser.get(CHECK)
    assert check.status_code == 200
    assert b"Another Administrator stopped go-live." not in check.content


def test_stop_by_another_administrator_moves_prepare_to_check(prepare_page, google):
    """Another Administrator's stop: Prepare settles toward Check; both say so.

    The page that was watching settles (no data-live-pending) with its one
    primary marked data-live-follow, so it opens Check by itself.
    """
    harness, browser, admin, request = prepare_page
    start(request, admin)
    grant_role(harness.service.store, "other@example.org", "administrator")
    google[0].update(email="other@example.org", sub="another-google-subject")
    other, login = signed_in()
    assert login.status_code == 302
    # A control is bound to its own sign-in.
    mine = shown(browser)[1].controls["stop"]
    with web_login():
        control = other.get(PREPARE).context["stage"].controls["stop"]
        refused = post(other, PREPARE, {"action": "stop", "control": mine})
        assert refused.status_code == 403
        answer = post(other, PREPARE, {"action": "stop", "control": control})
        assert answer.status_code == 303 and answer["Location"] == CHECK
    page, stage = shown(browser)
    assert stage.state == "stopped" and stage.stopped_by_other
    assert b"Another Administrator stopped go-live." in page.content
    assert b"data-live-pending" not in page.content
    assert page.content.count(b"data-live-follow") == 1
    assert b"Open Check" in page.content
    with web_login():
        check = browser.get(CHECK)
    assert check.status_code == 200
    assert b"Another Administrator stopped go-live." in check.content
    with web_login():
        mine_check = other.get(CHECK)
    assert b"Another Administrator stopped go-live." not in mine_check.content


def test_retired_go_live_pages_answer_as_unknown_addresses(prepare_page):
    """Testing cleanup and Prepare Family links are gone, with no redirect."""
    _, browser, _, request = prepare_page
    with web_login():
        for path in (
            f"/admin/campaign/go-live/cleanup/{request.pk}/",
            f"/admin/campaign/go-live/cleanup/{request.pk}/links/",
            f"/admin/campaign/go-live/cleanup/{request.pk}",
            f"/admin/campaign/go-live/cleanup/{request.pk}/links",
        ):
            assert browser.get(path).status_code == 404, path
            assert post(browser, path, {"control": "x"}).status_code == 404, path
