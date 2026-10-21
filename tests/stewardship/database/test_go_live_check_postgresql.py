"""The Go live page's Check step while go-live is stopping (#1585 slice 2).

Real setup, cleanup and link-preparation owners; only the worker runs and
their exhausted retries are driven directly. Check draws each state from
durable rows, so every assertion is a plain GET, as a browser reload is.
"""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts import go_live_progress
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from .test_activation_views_postgresql import ready_links  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_go_live_cleanup_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    setup_service,
)
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)

CHECK = reverse("admin:go_live")


def _cancel_cleanup(login, service, campaign_id, request_id):
    """Stop go-live's cleanup through today's cancellation owner."""
    with web_login():
        page = go_live_progress.progress(login, service, campaign_id, request_id)
        return go_live_progress.control(
            login, service, campaign_id, request_id, token=page["controls"]["cancel"]
        )


def _work(task, handler):
    """Run one queued task to its end as the maintained worker would."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            task.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={task.task_type: handler},
        )


def test_check_follows_a_stop_through_failure_retry_and_end(ready_links, ready_cleanup):
    """Stop failed offers Retry stopping go-live; Check shows each state on reload.

    Prepared Family links are discarded, the discard fails after its
    retries, and go-live's cleanup is cancelled: Check shows Stop failed
    with its one primary and no Start, the retry posts in place and
    redirects to Check (no page drawn by a POST), the discard then runs as
    Stopping, and once it is done Check says when go-live stopped above its
    readiness checks.
    """
    browser, links, ring, login = ready_links
    transition = ProductionTransitionRequest.objects.get()
    with web_login():
        links.act(links.page()["prepare"])
        preparation = ProductionTokenPreparation.objects.get()
    _work(
        TaskRun.objects.get(pk=preparation.task_id), token_handler(public=ring.public)
    )
    with web_login():
        links.act(links.page()["records"][0]["controls"]["cancel"])
        cancellation = ProductionTokenCancellation.objects.get()
    act(
        act(_status(TaskRun.objects.get(pk=cancellation.task_id)), "claim"),
        "permanent_failure",
    )
    _, service, _ = ready_cleanup
    stopped = _cancel_cleanup(login, service, transition.campaign_id, transition.pk)
    assert stopped.state == "cancelled"
    with web_login():
        drawn = set()
        for _ in range(2):  # A reload shows the same state.
            check = browser.get(CHECK)
            assert check.status_code == 200, check.content
            assert check.context["state"] == "stop_failed"
            body = check.content.decode()
            assert "Stopping go-live did not finish" in body
            assert "Retry stopping go-live" in body
            assert "data-review-open" not in body
            assert body.count("data-go-live-primary") == 1
            assert "Its last attempt failed at" in body
            drawn.add(check.context["stage"].retry)
        # The same control on every read, so the live region stays the same.
        (control,) = drawn
        assert browser.post(CHECK, {"action": "retry_stop"}).status_code == 403
        retried = post(browser, CHECK, {"action": "retry_stop", "control": control})
        assert retried.status_code == 303 and retried["Location"] == CHECK
        # Sent again (an answer lost), the retry is not made twice.
        again = post(browser, CHECK, {"action": "retry_stop", "control": control})
        assert again.status_code == 303
        assert TaskRun.objects.filter(root_id=cancellation.task_id).count() == 2
        forged = {"action": "retry_stop", "control": "forged"}
        assert post(browser, CHECK, forged).status_code == 400
        check = browser.get(CHECK)
        assert check.context["state"] == "stopping"
        assert check.context["stage"].stop_kind == "links"
        body = check.content.decode()
        assert "The prepared Family links are being discarded." in body
        # Nothing to do while it runs, and the step follows it closely.
        assert "data-go-live-primary" not in body
        assert "data-live-interval" not in body
    child = TaskRun.objects.filter(root_id=cancellation.task_id).latest(
        "retry_sequence"
    )
    _work(child, token_handler(cleanup=True))
    with web_login():
        check = browser.get(CHECK)
        assert check.context["state"] != "stop_failed"
        assert check.context["stage"].stopped_at is not None
        assert b"data-go-live-stopped" in check.content
        assert b"Go-live stopped at" in check.content


def test_check_shows_stopping_while_cleanup_stops_at_its_safe_point(
    ready_cleanup, settings, real_limiter
):
    """A stop asked while cleanup runs is Stopping, with no primary, until it ends."""
    from parishkit.stewardship.accounts.authentication import AuthRuntime
    from parishkit.stewardship.accounts.go_live_commands import (
        start_cleanup,
        verify_preview,
    )
    from parishkit.stewardship.accounts.setup_completion import setup_is_complete
    from parishkit.stewardship.campaigns.cleanup_tasks import TASK_TYPE as CLEANUP
    from parishkit.stewardship.campaigns.cleanup_tasks import cleanup_handler
    from parishkit.stewardship.jobs.dispatch import claim_hint
    from parishkit.stewardship.jobs.lifetime import maintain_execution

    login, service, campaign = ready_cleanup
    with web_login():
        _, _, token = verify_preview(login, service, campaign.pk)
        status = start_cleanup(
            login, service, campaign.pk, preview_token=token, acknowledge=True
        )
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            status.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={CLEANUP: cleanup_handler()},
        )
    _cancel_cleanup(login, service, campaign.pk, status.request_id)
    from django.test import Client

    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(
        service.store, real_limiter, setup_is_complete
    )
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = login.session.session_key
    with web_login():
        check = browser.get(CHECK)
        assert check.status_code == 200, check.content
        assert check.context["state"] == "stopping"
        body = check.content.decode()
        assert "Testing data deletion is stopping at the next safe point." in body
        assert "data-go-live-primary" not in body
        # Start go-live is not offered, and its review refuses.
        review = post(browser, CHECK, {"action": "review"})
        assert review.status_code == 409
        assert b"Go-live is still stopping." in review.content
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        maintain_execution(execution),
    ):
        execution.handler.execute(execution)
    with web_login():
        check = browser.get(CHECK)
        assert check.context["stage"].stopped_at is not None
        assert check.context["state"] in {"unchecked", "not_ready"}
        assert b"Check readiness and public web address" in check.content
