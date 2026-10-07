"""The go-live reads of ``pk-stewardship admin`` (ADM-11 PR 3b).

``go-live readiness`` and ``go-live progress`` through the real command line
on the restricted web login, as test_admin_status_cli_postgresql.py runs the
3a reads: each read through the function its page uses, passive admission,
the recheck, no view event (the pages record none), ``--watch`` on
progress, and parity with the pages' own context. A plain draft gives the
refusals and the session rules; the real setup, cleanup and confirmation
fixtures give a Testing draft ready to go live, one whose cleanup has
started, and a scheduled and an active Production confirmation.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db.models import F
from django.test import Client
from django.urls import reverse

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts import go_live_inputs
from parishkit.stewardship.accounts.authentication import AuthRuntime, runtime
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationSession,
)
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.confirmation_commands import confirm
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.sessions import database_now, issue_admin
from parishkit.stewardship.accounts.setup_completion import setup_is_complete
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.catchup_tasks import catchup_handler
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from . import test_setup_preparation_postgresql as setup_inputs
from .automation_builders import approve_now, paired, start
from .campaign_builders import add_draft
from .test_admin_status_cli_postgresql import admin, one, runner, session  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_confirmation_readiness_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    prepare,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_confirmation_sql_postgresql import fresh
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def viewed():
    """How many page view events of any kind have been recorded."""
    return AuditEvent.objects.filter(event_type__endswith="_viewed").count()


def command_session():
    """The (single) command session the command line opened."""
    return PortalSession.objects.get(
        pk__in=AutomationLogin.objects.values("portal_session_id")
    )


def approved(service, login, *, scope="full"):
    """An automation session approved by the setup's Administrator; its secret.

    Approval needs a browser session signed in within five minutes, so the
    Administrator signs in again first, as on the approval page.
    """
    issue_admin(
        login,
        login.portal_session.principal_id,
        store=service.store,
        authenticated_at=database_now(),
    )
    secret, code = start(service, scope=scope)
    approve_now(service, code, scope={"read-only": "read_only"}.get(scope, scope))
    return secret


def page_browser(login):
    """A browser on the Administrator's current session."""
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = login.session.session_key
    return browser


def confirmed(request, monkeypatch, *, days):
    """Install, clean, prepare and confirm a campaign starting ``days`` from now.

    As test_withdrawal_postgresql's ``scheduled`` fixture, with the dates
    relative to the SQL clock: a future start is confirmed as scheduled (no
    initial mail to prepare), a past one becomes active with a catch-up
    preparation.
    """
    start_date = database_now().date() + timedelta(days=days)
    original_campaign, original_schedule = (
        setup_inputs.first_campaign,
        setup_inputs.schedule,
    )
    monkeypatch.setattr(
        setup_inputs,
        "first_campaign",
        lambda *args, **kwargs: original_campaign(
            *args,
            **(
                kwargs
                | {
                    "start_date": start_date.isoformat(),
                    "end_date": (start_date + timedelta(days=30)).isoformat(),
                }
            ),
        ),
    )
    monkeypatch.setattr(
        setup_inputs,
        "schedule",
        lambda *args, **kwargs: original_schedule(
            *args, **(kwargs | {"date": start_date.isoformat()})
        ),
    )
    links = request.getfixturevalue("ready_links")
    preparation, arguments = prepare(links)
    with web_login():
        _, _, token = fresh(arguments)
        receipt = confirm(*arguments, token=token, typed="Production")
    campaign = preparation.transition.campaign
    campaign.refresh_from_db()
    return SimpleNamespace(
        login=arguments[0],
        service=arguments[1],
        campaign=campaign,
        receipt=receipt,
        path=reverse("admin:production_progress"),
    )


# ------------------------------------------------------------- a plain draft


def test_readiness_of_a_draft_matches_the_page(admin, google):
    """The page's read, codes and counts only, with no view event or renewal."""
    store = admin.service.store
    _, row, _ = add_draft(store, store.active(), uuid4())
    browser, secret, _ = paired(admin.service, scope="read-only")
    before = viewed()
    code, document = one(admin, "go-live", "readiness", secret=secret)
    assert code == 0 and document["ok"] and document["final"], document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME["go-live readiness"].result_fields)
    assert result["campaign_id"] == row["id"]
    assert not result["checks_passed"]
    assert "full_refresh_required" in result["problems"]
    assert result["cleanup_requests"] == []
    # Passive: no page view event, and the command session's activity was
    # never renewed (inserted at version 1, closed at exit as version 2).
    assert viewed() == before
    assert command_session().version == 2
    # The page reads the same preview through the same function.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = browser.get(reverse("admin:go_live"))
    assert page.status_code == 200, page.content
    preview = page.context["preview"]
    assert result["problems"] == list(preview.problems)
    assert result["version"] == preview.digest
    assert result["target_state"] == preview.target_state
    assert result["families"]["active"] == preview.families.counts.active
    assert result["cleanup"]["total"] == preview.cleanup.inventory.total
    # The explicit campaign is the same read.
    code, again = one(
        admin, "go-live", "readiness", "--campaign", row["id"], secret=secret
    )
    assert code == 0 and again["result"]["version"] == result["version"]


def test_go_live_refusals_on_a_draft(admin, google):
    """Unknown campaign, Testing progress and no current campaign, each exit 1."""
    secret = session(admin)
    code, document = one(admin, "go-live", "readiness", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available"
    code, document = one(admin, "go-live", "progress", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available"
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    for words in (("go-live", "readiness"), ("go-live", "progress")):
        code, document = one(admin, *words, "--campaign", str(uuid4()), secret=secret)
        assert code == 1 and document["error"]["code"] == "not_available", words
    # In Testing there is no Production progress: the page's own refusal.
    code, document = one(admin, "go-live", "progress", secret=secret)
    assert code == 1 and document["error"]["code"] == "denied", document


def test_production_progress_without_a_receipt_is_not_available(
    admin, google, monkeypatch
):
    """The page's missing confirmation receipt is not_available, after the recheck.

    No fixture reaches Production without a receipt, so the page's read is
    made to miss it as ``ProductionConfirmation...latest()`` would.
    """
    from parishkit.stewardship.accounts import confirmation_progress
    from parishkit.stewardship.campaigns.confirmation_models import (
        ProductionConfirmation,
    )

    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    secret = session(admin)

    def missing(*args):
        """The current Production campaign has no confirmation receipt."""
        raise ProductionConfirmation.DoesNotExist

    monkeypatch.setattr(confirmation_progress, "progress", missing)
    code, document = one(admin, "go-live", "progress", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document


def test_a_session_revoked_during_readiness_is_exit_5(admin, google, monkeypatch):
    """The page's read admits again inside its lock; the recheck reports it."""
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    secret = session(admin)
    row = AutomationSession.objects.get()
    original = go_live_inputs.collect_inputs

    def revoked(*args):
        """The Administrator revokes the session just before the page's read."""
        actor = current_principal(store, row.principal_id)
        automation.revoke(row.pk, actor, reason="revoked_by_owner")
        return original(*args)

    monkeypatch.setattr(go_live_inputs, "collect_inputs", revoked)
    code, document = one(admin, "go-live", "readiness", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    # Revoked between commands: refused at admission, before any read.
    for words in (("go-live", "readiness"), ("go-live", "progress")):
        code, document = one(admin, *words, secret=secret)
        assert code == 5 and document["error"]["code"] == "session_ended", words


def test_go_live_reads_during_a_restore_review_are_unavailable(admin, google):
    """A restore under review withholds both reads: exit 3, retry later."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .auth_builders import unguarded

    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    secret = session(admin)
    with unguarded():
        SystemConfiguration.objects.update(restore_review_required=True)
    for words in (("go-live", "readiness"), ("go-live", "progress")):
        code, document = one(admin, *words, secret=secret)
        assert code == 3 and document["error"]["code"] == "unavailable", words


# ------------------------------------------------- a draft going live


def test_readiness_of_a_draft_ready_to_go_live(
    ready_cleanup, real_limiter, settings, monkeypatch
):
    """Every check passed: the page's own preview, under a read-only session."""
    login, service, campaign = ready_cleanup
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(
        service.store, real_limiter, setup_is_complete
    )
    service = runtime()
    command = runner(service, monkeypatch)
    secret = approved(service, login, scope="read-only")
    code, document = one(command, "go-live", "readiness", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["checks_passed"] and result["problems"] == [], result["problems"]
    assert result["campaign_id"] == str(campaign.pk)
    assert result["mail_test_id"] == str(CampaignMailTest.objects.get().pk)
    assert result["source"]["state"] == "ready"
    assert result["families"]["active"] >= 1
    assert result["cleanup"]["total"] >= 1
    with web_login():
        page = page_browser(login).get(reverse("admin:go_live"))
    assert page.status_code == 200, page.content
    preview = page.context["preview"]
    assert result["version"] == preview.digest
    assert result["cleanup"]["inventory"] == [
        {"category": category, "count": count}
        for category, count in sorted(preview.cleanup.inventory.counts.items())
    ]
    # Never the Admin report recipients or a Testing Family.
    assert "@" not in json.dumps(document)
    # A cleanup started from the command line is PR 12; nothing started one.
    assert not ProductionTransitionRequest.objects.exists()


def test_readiness_once_cleanup_has_started(ready_links, monkeypatch):
    """The page's problem and its recent cleanup requests, as the page lists them."""
    login = ready_links[3]
    service = runtime()
    command = runner(service, monkeypatch)
    secret = approved(service, login)
    code, document = one(command, "go-live", "readiness", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert "cleanup_already_started" in result["problems"]
    [listed] = result["cleanup_requests"]
    cleanup = ProductionTransitionRequest.objects.get()
    assert listed == {
        "id": str(cleanup.pk),
        "state": cleanup.state,
        "created_at": cleanup.created_at.isoformat(),
    }
    with web_login():
        page = page_browser(login).get(reverse("admin:go_live"))
    assert [str(row.pk) for row in page.context["cleanup_requests"]] == [listed["id"]]
    assert result["problems"] == list(page.context["preview"].problems)


# ------------------------------------------------- Production confirmation


def test_progress_of_a_scheduled_confirmation(request, monkeypatch):
    """Confirmed before its start: nothing to prepare, so a watch stops at once."""
    state = confirmed(request, monkeypatch, days=2)
    service = runtime()
    command = runner(service, monkeypatch)
    secret = approved(service, state.login, scope="read-only")
    before = viewed()
    code, documents = command("go-live", "progress", "--watch", "2", secret=secret)
    assert code == 0 and len(documents) == 1 and documents[0]["final"], documents
    result = documents[0]["result"]
    assert result == {
        "campaign_id": str(state.campaign.pk),
        "campaign_state": "scheduled",
        "confirmation_id": str(state.receipt.pk),
        "confirmed_at": state.receipt.created_at.isoformat(),
        "withdrawal_available": True,
        "preparation": None,
        "outcomes": [],
    }
    assert viewed() == before
    with web_login():
        page = page_browser(state.login).get(state.path)
    assert page.status_code == 200, page.content
    assert page.context["receipt"].pk == state.receipt.pk
    assert page.context["withdrawal_available"] and page.context["demand"] is None
    # Now in Production: readiness belongs to a Testing draft only.
    code, document = one(command, "go-live", "readiness", secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version", document


def test_progress_of_an_active_confirmation_is_followed(request, monkeypatch):
    """Preparing: a watch follows it; failed, it stops with the retry offered.

    After the page's retry and the worker's catch-up, the outcomes are the
    page's, by key.
    """
    state = confirmed(request, monkeypatch, days=-2)
    service = runtime()
    command = runner(service, monkeypatch)
    secret = approved(service, state.login)
    demand = ActivationCatchUpDemand.objects.get(campaign=state.campaign)
    clock = iter(range(0, 10_000, 5))
    real_time = admin_cli.time
    monkeypatch.setattr(
        admin_cli,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda seconds: None),
    )
    code, documents = command(
        "go-live", "progress", "--watch", "2", "--timeout", "6", secret=secret
    )
    assert code == 7, documents
    assert documents[-1]["error"]["code"] == "watch_timeout"
    preparation = documents[-1]["result"]["preparation"]
    assert preparation["task_id"] == str(demand.task_root_id)
    assert preparation["task_state"] == "queued" and not preparation["complete"]
    assert not preparation["retry_available"]
    monkeypatch.setattr(admin_cli, "time", real_time)
    with work_transaction():
        act(
            act(_status(TaskRun.objects.get(pk=demand.task_root_id)), "claim"),
            "permanent_failure",
        )
    code, documents = command("go-live", "progress", "--watch", "2", secret=secret)
    assert code == 0 and len(documents) == 1, documents
    preparation = documents[0]["result"]["preparation"]
    assert preparation["task_state"] == "failed" and preparation["retry_available"]
    browser = page_browser(state.login)
    with web_login():
        page = browser.get(state.path)
        assert page.context["control"]
        assert (
            post(browser, state.path, {"control": page.context["control"]}).status_code
            == 302
        )
    latest = TaskRun.objects.filter(root_id=demand.task_root_id).latest(
        "retry_sequence"
    )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            latest.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"activation_catchup": catchup_handler()},
        )
    code, document = one(command, "go-live", "progress", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["preparation"]["complete"] and result["campaign_state"] == "active"
    with web_login():
        page = page_browser(state.login).get(state.path)
    assert page.context["complete"]
    assert result["outcomes"] == [
        {
            name: row[name]
            for name in ("key", "preview", "actual", "difference", "complete")
        }
        for row in page.context["outcomes"]
    ]
    assert [row["key"] for row in result["outcomes"]][:2] == [
        "family_messages",
        "daily_messages",
    ]
    # Withdrawal is for a campaign not yet started; this one is active.
    assert result["withdrawal_available"] is False
    # A disabled Administrator ends the session: exit 5, not a denial.
    from parishkit.stewardship.accounts.models import PortalUser

    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    code, document = one(command, "go-live", "progress", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
