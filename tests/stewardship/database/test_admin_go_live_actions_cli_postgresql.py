"""The Go live page's actions on ``pk-stewardship admin`` (ADM-11 PR 12, #1809).

Through the real command line on the restricted web login, over the real
setup, cleanup, preparation and confirmation fixtures the pages' own suites
use: Check's preview and Start go-live, the Testing submissions file, the
Prepare step's status, Refresh and prepare again and Stop go-live, Confirm
Production's review and Apply, Cancel go-live's review and Apply, and mail
preparation's Retry. Each runs the page's own function, so each test checks
what the page would show afterwards, the command's own audit event, the
irreversible notice where the action is irreversible, and that a repeat of
the same token does nothing new.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import csv
import hashlib
import io
import json

import pytest
from django.urls import reverse

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import go_live_commands, go_live_prepare
from parishkit.stewardship.accounts.authentication import AuthRuntime, runtime
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.setup_completion import setup_is_complete
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.go_live_sequencing import attempts
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand, Campaign
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCancellation,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.withdrawal_models import ProductionWithdrawal
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status

from .test_admin_go_live_cli_postgresql import approved, confirmed, page_browser
from .test_admin_status_cli_postgresql import one, runner, without_web_runtimes
from .test_background_grants_postgresql import task_login
from .test_go_live_confirm_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    confirmable,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_go_live_prepare_postgresql import prepare_page  # noqa: F401
from .test_setup_mail_views_postgresql import web_login
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)

YES = b"yes\n"


def events(verb):
    """How many ``admin_cmd_go_live_<verb>`` events were recorded."""
    return AuditEvent.objects.filter(event_type=f"admin_cmd_go_live_{verb}").count()


def notices(kind):
    """How many automation notices of ``kind`` were recorded."""
    return AutomationNotice.objects.filter(kind=kind).count()


def streamed(secret, *argv):
    """A streaming command (after ``runner``): its standard output and the
    ``result`` of the document it wrote to standard error."""
    out, err = io.StringIO(), io.StringIO()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        without_web_runtimes(),
    ):
        code = admin_cli.main(
            [*argv, "--config", "web.yaml", "--session-stdin"],
            stdin=io.BytesIO(f"pk-admin-session/1 {secret} {'a' * 64}\n".encode()),
            stdout=out,
            stderr=err,
        )
    assert code == 0, err.getvalue()
    lines = [line for line in err.getvalue().splitlines() if line.startswith("{")]
    return out.getvalue(), json.loads(lines[-1])["result"]


def command_line(service, login, monkeypatch, *, scope="full"):
    """The command runner over ``service`` and a session of ``login``'s
    Administrator; returns ``(run, secret)``."""
    command = runner(service, monkeypatch)
    return command, approved(service, login, scope=scope)


def test_check_preview_families_and_start_go_live(
    ready_cleanup, real_limiter, settings, monkeypatch
):
    """Check readiness and public web address, the Testing submissions file,
    Start go-live (prompted, fresh-gated and irreversible) and its repeat."""
    login, service, campaign = ready_cleanup
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(
        service.store, real_limiter, setup_is_complete
    )
    service = runtime()
    command, secret = command_line(service, login, monkeypatch)
    # A failing public web address check: the readiness, but no token.
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *a: False)
    code, document = one(command, "go-live", "preview", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME["go-live preview"].result_fields)
    assert result["origin_verified"] is False and result["startable"] is False
    assert result["preview"]["token"] is None
    assert result["readiness"]["checks_passed"] is True
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *a: True)
    code, document = one(command, "go-live", "preview", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["origin_verified"] and result["startable"]
    token = result["preview"]["token"]
    assert token and result["campaign_id"] == str(campaign.pk)
    assert "@" not in str(result["readiness"])

    # The Testing submissions list: the page's rows, as CSV on standard
    # output, and its document on standard error.
    body, families = streamed(secret, "go-live", "families")
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0] == ["duid", "name"] and len(rows) - 1 == families["count"]
    assert families["count"] == result["readiness"]["cleanup"]["families"]
    assert families["sha256"] == hashlib.sha256(body.encode()).hexdigest()

    # A read-only session cannot start go-live; nor can a declined prompt.
    readonly = approved(service, login, scope="read-only")
    code, document = one(
        command, "go-live", "start", "--token", token, "--yes", secret=readonly
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    code, document = one(command, "go-live", "start", "--token", token, secret=secret)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    code, document = one(
        command, "go-live", "start", "--token", token + "x", "--yes", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    # Apply runs the public web address check again.
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *a: False)
    code, document = one(
        command, "go-live", "start", "--token", token, "--yes", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert not ProductionTransitionRequest.objects.exists()
    assert events("start") == 0
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *a: True)

    # Start go-live, answered at the prompt.
    code, document = command(
        "go-live", "start", "--token", token, secret=secret, answer=YES
    )
    assert code == 0, document
    result = document[0]["result"]
    request = ProductionTransitionRequest.objects.get()
    assert result["created"] and result["request_id"] == str(request.pk)
    assert len(attempts(request)) == 1
    assert result["status"]["state"] == "running"
    assert events("start") == 1
    assert notices("fresh_gated") == 1 and notices("irreversible") == 1
    # The same token again: nothing new, nothing recorded, no prompt.
    code, document = one(command, "go-live", "start", "--token", token, secret=secret)
    assert code == 0 and not document["result"]["created"], document
    assert ProductionTransitionRequest.objects.count() == 1 and events("start") == 1
    assert notices("fresh_gated") == 1 and notices("irreversible") == 1

    # The Prepare step, as its page reads it.
    code, document = one(command, "go-live", "status", secret=readonly)
    assert code == 0, document
    status = document["result"]
    with web_login():
        shown = go_live_prepare.read(login, service, campaign.pk)
    assert status["state"] == shown.state and status["reason"] == shown.reason
    assert [box["state"] for box in status["boxes"]] == [
        box["state"] for box in shown.boxes
    ]
    assert status["stop_available"] and status["stopping"] is None

    # Stop go-live: prompted, then Testing cleanup stops.
    code, document = one(command, "go-live", "stop", secret=secret)
    assert code == 4, document
    code, document = one(command, "go-live", "stop", "--yes", secret=secret)
    assert code == 0, document
    assert ProductionCleanupCancellation.objects.filter(request=request).exists()
    assert document["result"]["status"]["state"] == "stopped"
    assert events("stop") == 1
    # Nothing is left to stop.
    code, document = one(command, "go-live", "stop", "--yes", secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert events("stop") == 1


def test_refresh_and_prepare_again_begins_the_next_attempt(ready_links, monkeypatch):
    """A go-live with no attempt of its own (cleanup started before #1585
    slice 3) waits for Refresh and prepare again; the command begins it."""
    login = ready_links[3]
    service = runtime()
    command, secret = command_line(service, login, monkeypatch)
    request = ProductionTransitionRequest.objects.get()
    code, document = one(command, "go-live", "status", secret=secret)
    assert code == 0, document
    status = document["result"]
    assert status["state"] == "again" and status["reason"] == "no_attempt"
    assert status["again_available"] and not status["retry_available"]
    assert status["request_id"] == str(request.pk)
    # Nothing failed, so there is nothing to retry.
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert events("retry") == 0
    code, document = command("go-live", "refresh-again", secret=secret, answer=b"no\n")
    assert code == 4, document
    assert not attempts(request)
    code, document = command("go-live", "refresh-again", secret=secret, answer=YES)
    assert code == 0, document
    result = document[0]["result"]
    assert result["attempt"] == 1 and len(attempts(request)) == 1
    assert result["status"]["state"] == "running"
    assert result["status"]["reason"] == "refresh"
    assert events("refresh_again") == 1
    # Not offered any more: the refresh is running.
    code, document = one(command, "go-live", "refresh-again", "--yes", secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert len(attempts(request)) == 1 and events("refresh_again") == 1


def test_confirm_production_from_its_review(confirmable, monkeypatch):
    """The dialog's Review and Apply: the typed word, the public web address
    check again, the real final owner and SQL guard, and a repeat."""
    _browser, login, arguments, preparation = confirmable
    service = runtime()
    command, secret = command_line(service, login, monkeypatch)
    code, document = one(command, "go-live", "confirm-preview", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert set(result) == set(
        admin_cli.BY_NAME["go-live confirm-preview"].result_fields
    )
    assert result["preparation_id"] == str(preparation.pk)
    assert result["target_state"] in {"active", "scheduled"}
    token = result["preview"]["token"]
    # The typed word is Production, exactly (spaces around it aside).
    code, document = command(
        "go-live", "confirm", "--token", token, secret=secret, answer=b"production\n"
    )
    assert code == 4, document
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *a: False)
    code, document = command(
        "go-live", "confirm", "--token", token, secret=secret, answer=b"Production\n"
    )
    assert code == 1 and document[0]["error"]["code"] == "stale_version"
    assert not ProductionConfirmation.objects.exists()
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *a: True)
    code, document = command(
        "go-live", "confirm", "--token", token, secret=secret, answer=b" Production \n"
    )
    assert code == 0, document
    result = document[0]["result"]
    receipt = ProductionConfirmation.objects.get()
    assert result["created"] and result["confirmation_id"] == str(receipt.pk)
    assert result["progress"]["confirmation_id"] == str(receipt.pk)
    assert SystemConfiguration.objects.get().mode == "production"
    assert events("confirm") == 1 and notices("irreversible") == 1
    # The same token again: nothing new.
    code, document = one(
        command, "go-live", "confirm", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and not document["result"]["created"], document
    assert ProductionConfirmation.objects.count() == 1 and events("confirm") == 1
    # Production is confirmed: the review is refused now.
    code, document = one(command, "go-live", "confirm-preview", secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version", document


def test_cancel_go_live_before_the_start(request, monkeypatch):
    """Cancel go-live's review with its reason, the prompt, the real owner and
    SQL guard; the campaign is a Testing draft again, and a repeat."""
    state = confirmed(request, monkeypatch, days=2)
    service = runtime()
    command, secret = command_line(service, state.login, monkeypatch)
    code, document = one(
        command, "go-live", "cancel-preview", "--reason", "  ", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = one(
        command, "go-live", "cancel-preview", "--reason", "Dates moved", secret=secret
    )
    assert code == 0, document
    result = document["result"]
    assert result["confirmation_id"] == str(state.receipt.pk)
    assert result["reason"] == "Dates moved" and result["cancellable"]
    assert set(result["inventory"]) == {
        "occurrences",
        "cancellable",
        "messages",
        "failed",
        "delivered",
        "blocking",
    }
    token = result["preview"]["token"]
    code, document = one(command, "go-live", "cancel", "--token", token, secret=secret)
    assert code == 4, document
    assert not ProductionWithdrawal.objects.exists()
    code, document = one(
        command,
        "go-live",
        "cancel",
        "--token",
        token[:-2] + "xx",
        "--yes",
        secret=secret,
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = command(
        "go-live", "cancel", "--token", token, secret=secret, answer=YES
    )
    assert code == 0, document
    result = document[0]["result"]
    withdrawal = ProductionWithdrawal.objects.get()
    assert result["created"] and result["withdrawal_id"] == str(withdrawal.pk)
    assert withdrawal.reason == "Dates moved"
    assert Campaign.objects.get(pk=state.campaign.pk).state == "draft"
    assert events("cancel") == 1 and notices("irreversible") == 1
    code, document = one(
        command, "go-live", "cancel", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and not document["result"]["created"], document
    assert ProductionWithdrawal.objects.count() == 1 and events("cancel") == 1
    # The page agrees: Check says when go-live was cancelled.
    with web_login():
        page = page_browser(state.login).get(reverse("admin:go_live"))
    assert page.status_code == 200, page.content
    assert page.context["stage"].cancelled_at == withdrawal.created_at


def test_retry_failed_mail_preparation(request, monkeypatch):
    """Production activation's Retry: the failed preparation's own retry,
    once; afterwards nothing is left to retry."""
    state = confirmed(request, monkeypatch, days=-2)
    service = runtime()
    command, secret = command_line(service, state.login, monkeypatch)
    demand = ActivationCatchUpDemand.objects.get(campaign=state.campaign)
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    with work_transaction():
        act(
            act(_status(TaskRun.objects.get(pk=demand.task_root_id)), "claim"),
            "permanent_failure",
        )
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["step"] == "activation" and result["retried"] == "mail_preparation"
    latest = TaskRun.objects.filter(root_id=demand.task_root_id).latest(
        "retry_sequence"
    )
    assert latest.retry_sequence == 1 and latest.state == "queued"
    assert result["status"]["preparation"]["task_state"] == "queued"
    assert events("retry") == 1
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert events("retry") == 1


def test_retry_a_failed_box_then_a_failed_stop(prepare_page, auth_service, monkeypatch):
    """The Prepare step's Retry of failed link preparation, then Retry
    stopping go-live on Check, each through the page's own owner, once."""
    from parishkit.stewardship.campaigns.activation_models import (
        ProductionTokenCancellation,
        ProductionTokenPreparation,
    )

    from .automation_builders import paired
    from .test_go_live_prepare_postgresql import fail
    from .test_go_live_sequencing_postgresql import refreshed, start, step

    harness, _browser, admin, request = prepare_page
    command = runner(auth_service, monkeypatch)
    _, secret, _ = paired(auth_service)
    start(request, admin)
    refreshed(harness)
    step()
    preparation = ProductionTokenPreparation.objects.get()
    fail(preparation.task_id)
    code, document = one(command, "go-live", "status", secret=secret)
    assert code == 0, document
    status = document["result"]
    assert (status["state"], status["reason"]) == ("failed", "retry")
    assert status["retry_available"]
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert (result["step"], result["retried"]) == ("prepare", "links")
    assert TaskRun.objects.filter(root_id=preparation.task_id).count() == 2
    assert result["status"]["state"] == "running"
    assert events("retry") == 1
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document

    # Stop go-live with the links being prepared; their discard fails.
    code, document = one(command, "go-live", "stop", "--yes", secret=secret)
    assert code == 0, document
    cancellation = ProductionTokenCancellation.objects.get(preparation=preparation)
    fail(cancellation.task_id)
    code, document = one(command, "go-live", "status", secret=secret)
    stopping = document["result"]["stopping"]
    assert document["result"]["state"] == "stopped"
    assert stopping["state"] == "stop_failed" and stopping["retry_available"]
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 0, document
    assert (document["result"]["step"], document["result"]["retried"]) == (
        "check",
        "stop",
    )
    assert TaskRun.objects.filter(root_id=cancellation.task_id).count() == 2
    assert events("retry") == 2
    code, document = one(command, "go-live", "retry", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert events("retry") == 2
