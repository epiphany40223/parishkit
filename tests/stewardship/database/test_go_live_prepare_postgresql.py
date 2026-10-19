"""The Go live page's Prepare step over HTTP (#1585 slice 3).

A go-live request whose cleanup completed, started by a real Administrator
signed in through the ordinary login. Promotions are real source
promotions; the scheduler's go-live producer runs as the scheduler login
and the link tasks it creates run as the worker, so every stage the page
shows comes from the owners. Each assertion is a plain GET, as a browser
reload is, and every POST answers a redirect or the Stop go-live dialog.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection
from django.urls import reverse
from django.utils import timezone

from parishkit.stewardship.accounts import go_live_prepare, go_live_prepare_views
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns import cleanup_tasks
from parishkit.stewardship.campaigns import go_live_sequencing as sequencing
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.cleanup_requests import begin_cleanup
from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessTokenGeneration,
)
from parishkit.stewardship.campaigns.go_live_sequencing import (
    attempts,
    live_preparations,
    refresh_command_id,
    refreshes_held,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCancellation,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.refresh_models import SourceRefreshCommand
from parishkit.stewardship.storage import StaleRecordError

from .auth_builders import signed_in
from .leader_builders import grant_role
from .test_background_grants_postgresql import task_login
from .test_cleanup_inventory_postgresql import settle_test_receipts
from .test_cleanup_tasks_postgresql import run
from .test_go_live_sequencing_postgresql import (
    current,
    refreshed,
    run_link_task,
    start,
    step,
)
from .test_scheduler_link_preparation_postgresql import cleaned_up
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_taskrun_postgresql import act

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


def fail(task_id):
    """Fail the newest run of a task chain after its retries, as its worker would."""
    task = TaskRun.objects.filter(root_id=task_id).order_by("-retry_sequence").first()
    act(act(_status(task), "claim"), "permanent_failure")


def stop(browser):
    """Apply Stop go-live with the control the step shows now; return the answer."""
    control = shown(browser)[1].controls["stop"]
    with web_login():
        return post(browser, PREPARE, {"action": "stop", "control": control})


def ready(harness, request, admin):
    """Begin attempt 1, promote its refresh and prepare its links by the producer."""
    start(request, admin)
    refreshed(harness)
    step()
    preparation = ProductionTokenPreparation.objects.get()
    assert run_link_task(harness, preparation.task_id)
    return preparation


@pytest.fixture
def cleanup_page(response_service, google):
    """The harness, a signed-in Administrator's browser, them, and a go-live
    request whose Testing cleanup is queued, not yet run, with attempt 1."""
    browser, login = signed_in()
    assert login.status_code == 302
    admin = PortalUser.objects.get().pk
    with work_transaction():
        settle_test_receipts(response_service)
    now = timezone.now()
    status = begin_cleanup(
        campaign_id=response_service.campaign.pk,
        request_key=uuid4(),
        actor_id=admin,
        correlation_id=uuid4(),
        readiness_digest="b" * 64,
        acknowledged_at=now,
        reauthenticated_at=now - timedelta(seconds=1),
        admit=lambda *args: True,
    )
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    start(request, admin)
    return response_service, browser, admin, request, status


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


def test_a_discard_that_fails_after_stop_stays_retryable_on_check(prepare_page):
    """Stop go-live's discard fails after its retries once go-live is stopped.

    Check shows Stopping while it runs, then Stop failed with its one
    primary, Retry stopping go-live; Start is refused until the discard is
    done. The retry (sent twice) re-requests the same disposal once, and
    once it runs the links are disposed and Check says when go-live stopped.
    """
    harness, browser, admin, request = prepare_page
    preparation = ready(harness, request, admin)
    answer = stop(browser)
    assert answer.status_code == 303 and answer["Location"] == CHECK
    cancellation = ProductionTokenCancellation.objects.get(preparation=preparation)
    with web_login():
        check = browser.get(CHECK)
    assert check.context["state"] == "stopping"
    assert check.context["stage"].stop_kind == "links"
    fail(cancellation.task_id)
    # Prepare is no longer the step in progress; it leads to Check.
    assert shown(browser)[1].state == "stopped"
    with web_login():
        check = browser.get(CHECK)
        assert check.context["state"] == "stop_failed"
        assert b"Retry stopping go-live" in check.content
        assert check.content.count(b"data-go-live-primary") == 1
        # Start go-live stays unavailable until the disposal finishes.
        review = post(browser, CHECK, {"action": "review"})
        assert review.status_code == 409
        assert b"Stopping go-live did not finish." in review.content
        retry = {"action": "retry_stop", "control": check.context["stage"].retry}
        assert post(browser, CHECK, retry).status_code == 303
        assert post(browser, CHECK, retry).status_code == 303
    assert TaskRun.objects.filter(root_id=cancellation.task_id).count() == 2
    child = TaskRun.objects.filter(root_id=cancellation.task_id).latest(
        "retry_sequence"
    )
    assert run_link_task(harness, child.pk, cleanup=True)
    assert not live_preparations(current(request))
    with web_login():
        check = browser.get(CHECK)
    assert check.context["state"] not in {"stopping", "stop_failed"}
    assert check.context["stage"].stopped_at is not None


def test_a_discard_that_failed_before_stop_is_retried_from_check(prepare_page):
    """Stale links whose discard already failed, then Stop go-live.

    Stop requests nothing more for those links (their discard exists), and
    Check offers Retry stopping go-live for that same discard, so no
    staged token is ever left without a retry.
    """
    harness, browser, admin, request = prepare_page
    preparation = ready(harness, request, admin)
    refreshed(harness)
    (discarded,) = step()
    assert discarded.action == "discard"
    cancellation = ProductionTokenCancellation.objects.get(preparation=preparation)
    fail(cancellation.task_id)
    assert stop(browser)["Location"] == CHECK
    assert ProductionTokenCancellation.objects.count() == 1
    with web_login():
        check = browser.get(CHECK)
        assert check.context["state"] == "stop_failed"
        retry = {"action": "retry_stop", "control": check.context["stage"].retry}
        assert post(browser, CHECK, retry).status_code == 303
    child = TaskRun.objects.filter(root_id=cancellation.task_id).latest(
        "retry_sequence"
    )
    assert run_link_task(harness, child.pk, cleanup=True)
    assert not live_preparations(current(request))


def test_stop_during_link_preparation_prepares_nothing_more(prepare_page):
    """Stop while the producer's preparation task is queued, not yet run.

    The discard is requested at once and runs behind the preparation; the
    producer prepares nothing after the stop, even after a new promotion,
    and once both tasks ran no generation of those links is left active or
    undisposed.
    """
    harness, browser, admin, request = prepare_page
    start(request, admin)
    refreshed(harness)
    (prepared,) = step()
    assert prepared.action == "prepare"
    preparation = ProductionTokenPreparation.objects.get()
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("running", "preparing")
    assert stop(browser)["Location"] == CHECK
    cancellation = ProductionTokenCancellation.objects.get(preparation=preparation)
    assert current(request).state == "cancelled"
    assert step() == ()
    refreshed(harness)
    assert step() == ()
    run_link_task(harness, preparation.task_id)
    assert run_link_task(harness, cancellation.task_id, cleanup=True)
    assert ProductionTokenPreparation.objects.count() == 1
    assert not FamilyAccessTokenGeneration.objects.filter(
        operation_id=preparation.pk, state="active"
    ).exists()
    assert not live_preparations(current(request))
    assert not refreshes_held(database_now())


def test_stop_during_a_cleanup_batch_stops_cleanup_and_prepares_nothing(
    cleanup_page,
):
    """Stop while a Testing cleanup batch runs: it stops at its safe point.

    Prepare offers Stop while cleanup runs; the stop records intent, the
    running batch then ends cancelled instead of complete, and the
    producer never prepares links for the stopped request.
    """
    from parishkit.stewardship.campaigns.cleanup_tasks import TASK_TYPE as CLEANUP
    from parishkit.stewardship.campaigns.cleanup_tasks import cleanup_handler
    from parishkit.stewardship.jobs.dispatch import claim_hint
    from parishkit.stewardship.jobs.lifetime import maintain_execution
    from parishkit.stewardship.jobs.queues import WorkQueue

    harness, browser, admin, request, status = cleanup_page
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            status.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={CLEANUP: cleanup_handler()},
        )
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("running", "cleanup")
    assert stage.stop_kind == "cleanup"
    assert stop(browser)["Location"] == CHECK
    assert ProductionCleanupCancellation.objects.filter(request=request).exists()
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        maintain_execution(execution),
    ):
        execution.handler.execute(execution)
    assert current(request).state == "cancelled"
    refreshed(harness)
    assert step() == ()
    assert not ProductionTokenPreparation.objects.exists()
    assert not refreshes_held(database_now())


def test_a_failed_cleanup_offers_retry_through_its_owner(cleanup_page, monkeypatch):
    """Cleanup failed after its retries: Failed box, the time, one Retry.

    Retry retries the same cleanup task through go_live_progress (sent
    twice, one retry), and the step runs again.
    """
    harness, browser, admin, request, status = cleanup_page
    task = _status(TaskRun.objects.get(pk=status.task_id))
    for _ in range(4):
        task = act(task, "claim", lease_seconds=60)
        task = act(task, "retryable_failure")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")

    def failure(*args, **kwargs):
        """Inject one batch failure, keeping every real worker fence."""
        raise DatabaseError("synthetic private database failure")

    with monkeypatch.context() as patch:
        patch.setattr(cleanup_tasks, "apply_checkpoint", failure)
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert run(status)
    assert current(request).state == "cleanup_failed"
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("failed", "cleanup")
    assert boxes(stage)[0] == "failed"
    assert b"Deleting the Testing data failed after its automatic retries." in (
        page.content
    )
    assert b"Its last attempt failed at" in page.content
    assert page.content.count(b"data-go-live-primary") == 1
    retry = {"action": "retry", "control": stage.controls["retry"]}
    with web_login():
        assert post(browser, PREPARE, retry)["Location"] == PREPARE
        assert post(browser, PREPARE, retry).status_code == 303
    assert TaskRun.objects.filter(root_id=status.task_id).count() == 2
    assert current(request).state == "cleanup_queued"
    assert shown(browser)[1].state == "running"


def test_a_failed_refresh_offers_refresh_and_prepare_again(prepare_page):
    """The attempt's refresh failed: its box is Failed with the time, and
    Refresh and prepare again begins attempt 2."""
    harness, browser, admin, request = prepare_page
    start(request, admin)
    command = SourceRefreshCommand.objects.select_related("request").get(
        pk=refresh_command_id(request.pk, 1)
    )
    fail(command.request.task_root_id)
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("again", "refresh_failed")
    assert boxes(stage) == ["done", "failed", "waiting"]
    assert b"The ParishSoft refresh failed" in page.content
    assert b"Its last attempt failed at" in page.content
    again = {"action": "again", "control": stage.controls["again"]}
    with web_login():
        assert post(browser, PREPARE, again)["Location"] == PREPARE
    assert len(attempts(request)) == 2
    assert shown(browser)[1].reason == "refresh"


def test_hold_ended_and_data_keeps_changing_show_nothing_in_progress(
    prepare_page, monkeypatch
):
    """Hold ended, then ParishSoft data keeps changing: Refresh and prepare
    again is the primary, and the links box is Not started, then Failed,
    never In progress while nothing runs."""
    harness, browser, admin, request = prepare_page
    start(request, admin)
    refreshed(harness)
    with monkeypatch.context() as patch:
        patch.setattr(sequencing, "REFRESH_HOLD", timedelta(0))
        assert step() == ()
        page, stage = shown(browser)
        assert (stage.state, stage.reason) == ("again", "hold_ended")
        assert boxes(stage) == ["done", "done", "waiting"]
        assert b'data-stage-state="active"' not in page.content
        assert b"the pause has ended" in page.content
        assert page.content.count(b"data-go-live-primary") == 1
    # One preparation allowed: once it goes stale, data "keeps changing".
    monkeypatch.setattr(sequencing, "MAX_PREPARATIONS", 1)
    monkeypatch.setattr(go_live_prepare, "MAX_PREPARATIONS", 1)
    (prepared,) = step()
    preparation = ProductionTokenPreparation.objects.get()
    assert run_link_task(harness, preparation.task_id)
    refreshed(harness)
    (discarded,) = step()
    assert discarded.action == "discard"
    cancellation = ProductionTokenCancellation.objects.get()
    assert run_link_task(harness, cancellation.task_id, cleanup=True)
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("again", "exhausted")
    assert boxes(stage) == ["done", "done", "failed"]
    assert b'data-stage-state="active"' not in page.content
    assert b"(1 time)" in page.content
    assert stage.controls["again"]


def test_a_closed_campaign_leaves_only_stop(prepare_page, monkeypatch):
    """The campaign ended while waiting: no primary, Stop go-live stays."""
    harness, browser, admin, request = prepare_page
    start(request, admin)
    ends = request.campaign.active_configuration.ends_at
    monkeypatch.setattr(
        go_live_prepare, "database_now", lambda: ends + timedelta(minutes=1)
    )
    page, stage = shown(browser)
    assert stage.state == "closed"
    assert set(stage.controls) == {"stop"}
    assert b"data-go-live-primary" not in page.content
    assert b"data-go-live-stop" in page.content
    assert b"Stop go-live, then review the campaign dates on Check." in page.content


def test_a_failed_discard_of_earlier_links_offers_retry(prepare_page):
    """The stale links' discard failed: Failed box and Retry, which retries
    that discard; once it runs the producer prepares the next links."""
    harness, browser, admin, request = prepare_page
    preparation = ready(harness, request, admin)
    refreshed(harness)
    step()
    cancellation = ProductionTokenCancellation.objects.get(preparation=preparation)
    fail(cancellation.task_id)
    page, stage = shown(browser)
    assert (stage.state, stage.reason) == ("failed", "retry_cleanup")
    assert boxes(stage)[2] == "failed"
    assert b"Discarding earlier Family links failed" in page.content
    assert b"Its last attempt failed at" in page.content
    retry = {"action": "retry", "control": stage.controls["retry"]}
    with web_login():
        assert post(browser, PREPARE, retry)["Location"] == PREPARE
        assert post(browser, PREPARE, retry).status_code == 303
    assert TaskRun.objects.filter(root_id=cancellation.task_id).count() == 2
    child = TaskRun.objects.filter(root_id=cancellation.task_id).latest(
        "retry_sequence"
    )
    assert run_link_task(harness, child.pk, cleanup=True)
    (again,) = step()
    assert (again.action, again.preparation) == ("prepare", 2)
    assert shown(browser)[1].reason == "repreparing"


def test_a_control_sent_after_the_step_moved_on_does_nothing(prepare_page):
    """Refresh and prepare again from a page drawn before another tab began
    the attempt, and Retry after the failure was retried elsewhere: each
    answers the step as it stands now and changes nothing."""
    harness, browser, admin, request = prepare_page
    old_again = shown(browser)[1].controls["again"]
    start(request, admin)
    with web_login():
        answer = post(browser, PREPARE, {"action": "again", "control": old_again})
    assert answer.status_code == 303 and answer["Location"] == PREPARE
    assert len(attempts(request)) == 1
    refreshed(harness)
    step()
    preparation = ProductionTokenPreparation.objects.get()
    fail(preparation.task_id)
    old_retry = shown(browser)[1].controls["retry"]
    with web_login():
        assert post(browser, PREPARE, {"action": "retry", "control": old_retry})
    assert TaskRun.objects.filter(root_id=preparation.task_id).count() == 2
    # The retry ran and failed again: a new failure, a new control.
    fail(preparation.task_id)
    assert shown(browser)[1].controls["retry"] != old_retry
    with web_login():
        post(browser, PREPARE, {"action": "retry", "control": old_retry})
    assert TaskRun.objects.filter(root_id=preparation.task_id).count() == 2


def test_a_stop_that_finds_go_live_moving_says_so_and_stops_nothing(
    prepare_page, monkeypatch
):
    """Stop go-live's owner finds the state moved under its lock: the
    dialog says so (409), and nothing is stopped or discarded."""
    harness, browser, admin, request = prepare_page
    ready(harness, request, admin)

    def moved(*args, **kwargs):
        """Cleanup's cancellation owner finds its request moved."""
        raise StaleRecordError("moved")

    monkeypatch.setattr(go_live_prepare_views.go_live_progress, "control", moved)
    answer = stop(browser)
    assert answer.status_code == 409
    assert b"data-review-refusal" in answer.content
    assert b"Go-live changed while it was being stopped" in answer.content
    assert not ProductionTokenCancellation.objects.exists()
    assert not ProductionCleanupCancellation.objects.exists()
    assert current(request).state == "cleanup_complete"
    assert shown(browser)[1].state == "ready"
