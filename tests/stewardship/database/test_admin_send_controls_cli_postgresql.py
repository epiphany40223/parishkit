"""Pause and resume mail's controls on the command line (ADM-11 PR 7b, #463).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The
previews and the confirmation run the page's own functions
(``delivery_control_commands``), so their documents match the page's
previews, a token from either channel confirms on the other, and the SQL
guard beneath ``confirm`` refuses a resume while a delivery is unknown,
whatever the token says.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing
from django.db import DatabaseError

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.admin_editing import editable_configuration
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationNotice,
    AutomationSession,
)
from parishkit.stewardship.admin_delivery_control import preview_model
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
    HeldMessageResolution,
)
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import work_transaction

from .automation_builders import as_staff, paired
from .campaign_builders import add_draft, campaign_clock, close_campaign
from .report_builders import weekly_allocated
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_go_live_cli_postgresql import approved
from .test_admin_test_sample_cli_postgresql import runner
from .test_delivery_closed_postgresql import submit_while_paused
from .test_delivery_control_postgresql import (  # noqa: F401
    accepted_sender_check,
    delivery_scheduled,
)
from .test_delivery_resolution_postgresql import unknown_inventory
from .test_setup_mail_views_postgresql import web_login
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)
from .test_withdrawal_work_postgresql import future_message

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_send_confirm"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


# The page's own System-logs events of a control, which its SQL effects
# record whichever channel confirmed it.
PAGE_EVENTS = (
    "campaign_pause",
    "campaign_resume",
    "delivery_held_release",
    "delivery_held_cancel",
    "delivery_held_clear",
)


def trail():
    """The command events, fresh-gate events and fresh-gated notices so far,
    and the page's own control events by type (sorted), so a control that
    records its page event twice, or not at all, is seen."""
    return (
        AuditEvent.objects.filter(event_type=EVENT).count(),
        AuditEvent.objects.filter(event_type="automation_fresh_gate").count(),
        AutomationNotice.objects.filter(kind="fresh_gated").count(),
        tuple(
            sorted(
                AuditEvent.objects.filter(event_type__in=PAGE_EVENTS).values_list(
                    "event_type", flat=True
                )
            )
        ),
    )


def assert_page_events_by(actor_id):
    """Every page control event so far is the Administrator's."""
    assert set(
        AuditEvent.objects.filter(event_type__in=PAGE_EVENTS).values_list(
            "actor_id", flat=True
        )
    ) <= {actor_id}


def comparable(result):
    """A preview document without the members each preview makes anew."""
    return {
        name: value
        for name, value in result.items()
        if name not in {"preview", "expires_at"}
    }


def page_preview(item, action, reason, **choice):
    """The page's own preview of ``action``, as the command prints it."""
    function = {
        "pause": commands.preview_pause,
        "resume": commands.preview_resume,
        "resolve": commands.preview_resolution,
    }[action]
    with web_login():
        binding, _ = function(*item.arguments, reason=reason, **choice)
    return comparable(preview_model(binding).to_document())


def test_a_pause_and_resume_from_the_command_line(scheduled, monkeypatch):
    """The page's previews; the prompt; the control, its trail and a repeat."""
    item = scheduled
    login, service, campaign_id = item.arguments
    command = runner(service, monkeypatch)
    secret = approved(service, login)
    future_message(item)

    code, document, _ = command(
        "send", "pause-preview", "--reason", "  Wrong reminder  ", secret=secret
    )
    assert code == 0, document
    result = document["result"]
    assert result["action"] == "pause" and result["campaign_id"] == str(campaign_id)
    assert result["reason"] == "Wrong reminder"
    assert result["inventory"]["queued"] == 1
    assert result["plan"] is None and result["resolution"] is None
    assert comparable(result) == page_preview(item, "pause", "Wrong reminder")
    token = result["preview"]["token"]
    # A preview changes nothing and records no event.
    assert trail() == (0, 0, 0, ()) and not DeliveryControlCommand.objects.exists()

    # A refused prompt changes nothing; the prompt shows what it does.
    for answer in (b"", b"no\n"):
        code, refused, errors = command(
            "send", "confirm", "--token", token, secret=secret, answer=answer
        )
        assert code == 4 and refused["error"]["code"] == "confirmation_required"
        assert "Pause live Family email" in errors
        assert "Reason: Wrong reminder" in errors
        assert "Unsent messages to hold: 1." in errors
        assert "Email may keep going out while you read this." in errors
    assert trail() == (0, 0, 0, ()) and not DeliveryControlCommand.objects.exists()

    code, document, _ = command(
        "send", "confirm", "--token", token, secret=secret, answer=b"yes\n"
    )
    assert code == 0, document
    result = document["result"]
    assert result["created"] is True and result["action"] == "pause"
    [control] = DeliveryControlCommand.objects.all()
    assert result["command_id"] == str(control.pk)
    assert control.action == "pause" and control.reason == "Wrong reminder"
    # The command session's sign-in is recorded.
    row = session_row(secret)
    assert AutomationLogin.objects.filter(
        portal_session_id=control.session_id, automation_session=row
    ).exists()
    item.campaign.refresh_from_db()
    assert item.campaign.delivery_paused
    assert item.campaign.pause_reason == "Wrong reminder"
    status = result["status"]
    assert status["paused"] and status["reason"] == "Wrong reminder"
    assert status["inventory"]["held"] == 1 and status["resume_available"]
    [event] = AuditEvent.objects.filter(event_type=EVENT)
    assert event.actor_id == login.portal_session.principal_id
    assert event.subject_id == row.pk
    assert event.auditcontext.context == {"outcome": "succeeded"}
    assert trail() == (1, 1, 1, ("campaign_pause",))

    # An exact repeat returns the original control and records nothing.
    code, document, _ = command(
        "send", "confirm", "--token", token, "--yes", secret=secret
    )
    assert code == 0, document
    assert document["result"]["created"] is False
    assert document["result"]["command_id"] == str(control.pk)
    assert trail() == (1, 1, 1, ("campaign_pause",))
    assert DeliveryControlCommand.objects.count() == 1

    # Pausing again is not offered: the page's refusal, nothing recorded.
    code, document, _ = command(
        "send", "pause-preview", "--reason", "Again", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document

    # Before the start, resume only releases held mail (reason from stdin).
    code, document, _ = command(
        "send",
        "resume-preview",
        "--reason",
        "-",
        secret=secret,
        answer=b"Fixed the reminder\n",
    )
    assert code == 0, document
    result = document["result"]
    assert result["action"] == "resume" and result["plan"] == "before_start"
    assert result["family_recovery"] is None and result["report_recovery"] is None
    assert comparable(result) == page_preview(item, "resume", "Fixed the reminder")
    code, document, errors = command(
        "send",
        "confirm",
        "--token",
        result["preview"]["token"],
        secret=secret,
        answer=b"yes\n",
    )
    assert code == 0, document
    assert "Resume live Family email" in errors
    assert "Held messages to release: 1." in errors
    assert document["result"]["action"] == "resume"
    assert not document["result"]["status"]["paused"]
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    assert trail() == (2, 2, 2, ("campaign_pause", "campaign_resume"))
    assert_page_events_by(login.portal_session.principal_id)


def session_row(secret):
    """The automation session ``secret`` names."""
    return AutomationSession.objects.get(secret_digest=automation.secret_digest(secret))


def test_tokens_cross_channels(scheduled, monkeypatch):
    """A page preview confirms from the command line, and the other way round."""
    item = scheduled
    login, service, _ = item.arguments
    command = runner(service, monkeypatch)
    secret = approved(service, login)
    with web_login():
        _, token = commands.preview_pause(*item.arguments, reason="From the page")
    code, document, _ = command(
        "send", "confirm", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and document["result"]["created"], document
    item.campaign.refresh_from_db()
    assert item.campaign.delivery_paused
    assert trail() == (1, 1, 1, ("campaign_pause",))

    code, document, _ = command(
        "send", "resume-preview", "--reason", "From the CLI", secret=secret
    )
    assert code == 0, document
    with web_login():
        control = commands.confirm(
            *item.arguments, token=document["result"]["preview"]["token"]
        )
    assert control.action == "resume" and control.reason == "From the CLI"
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    # The page's confirmation records its own event, and no command event.
    assert trail() == (1, 1, 1, ("campaign_pause", "campaign_resume"))


def test_a_failed_audit_write_changes_nothing(scheduled, monkeypatch):
    """The command event is written in the control's transaction: when that
    write fails, nothing is recorded (exit 3, retry), and the same token
    then confirms once."""
    from parishkit.stewardship import admin_integration_actions

    item = scheduled
    login, service, _ = item.arguments
    command = runner(service, monkeypatch)
    secret = approved(service, login)
    code, document, _ = command(
        "send", "pause-preview", "--reason", "Audit check", secret=secret
    )
    assert code == 0, document
    token = document["result"]["preview"]["token"]
    original = admin_integration_actions._command_event

    def failing(*args):
        """The command event's write fails, as an outage would."""
        raise DatabaseError("the audit write failed")

    monkeypatch.setattr(admin_integration_actions, "_command_event", failing)
    code, document, _ = command(
        "send", "confirm", "--token", token, "--yes", secret=secret
    )
    assert code == 3 and document["error"]["code"] == "unavailable", document
    assert "result" not in document
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    assert trail() == (0, 0, 0, ()) and not DeliveryControlCommand.objects.exists()

    monkeypatch.setattr(admin_integration_actions, "_command_event", original)
    code, document, _ = command(
        "send", "confirm", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and document["result"]["created"] is True, document
    item.campaign.refresh_from_db()
    assert item.campaign.delivery_paused
    assert trail() == (1, 1, 1, ("campaign_pause",))
    # Once confirmed, the token is spent: a repeat records nothing.
    code, document, _ = command(
        "send", "confirm", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and document["result"]["created"] is False, document
    assert trail() == (1, 1, 1, ("campaign_pause",))


def forged_resume(item):
    """A resume binding signed with the page's salt, past the page's preview.

    The page's preview refuses a resume while a delivery is unknown, so
    this builds the same binding it would sign from the live inventory and
    recovery plan, to reach ``confirm`` and the SQL guard beneath it.
    """
    login, service, campaign_id = item.arguments
    with web_login(), work_transaction():
        runtime = editable_configuration(service)
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=campaign_id
        )
        now = _now()
        binding = {
            "key": str(uuid4()),
            "actor": str(login.portal_session.principal_id),
            "campaign": str(campaign_id),
            "action": "resume",
            "campaign_version": campaign.version,
            "runtime_version": runtime.version,
            "observed": now.isoformat(),
            "expires": (now + timedelta(minutes=5)).isoformat(),
            "inventory": commands.inventory(campaign_id),
            "selection": commands._resume_selection(campaign),
            "reason": "Forged",
        }
    return signing.dumps(binding, salt=commands.SALT)


def test_the_unknown_delivery_guard_refuses_a_resume(
    delivery_scheduled, monkeypatch, tmp_path
):
    """While a delivery is unknown the page's preview refuses a resume, and a
    token that skips it is refused by the SQL guard: nothing is recorded."""
    from parishkit.stewardship.jobs.delivery_states import DeliveryAction as Action

    from .campaign_builders import advance
    from .test_outbox_postgresql import change, claim, submit
    from .test_taskrun_postgresql import act

    item = delivery_scheduled
    login, service, _ = item.arguments
    command = runner(service, monkeypatch)
    occurrence, status = future_message(item)
    with campaign_clock(occurrence.due_at):
        task = claim(status)
        advance(
            occurrence, task.worker_id, "running", task_id=task.run_id, fence=task.fence
        )
        change(submit(status, task=task), Action.MARK_UNKNOWN)
        advance(occurrence, task.worker_id, "delivery_unknown", fence=task.fence)
        act(task, "permanent_failure")
    due = item.campaign.active_configuration.starts_at + timedelta(days=1)
    with campaign_clock(due):
        # Paused and the sender checked on the page, whose browser the
        # command session's approval below would sign out.
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Review unknown")
            commands.confirm(*item.arguments, token=token)
        accepted_sender_check(item, monkeypatch, tmp_path)
        assert unknown_inventory(item.campaign) == 1
        secret = approved(service, login)
        before = trail()

        # The page's preview refuses: not sure it arrived.
        code, document, _ = command(
            "send", "resume-preview", "--reason", "Too early", secret=secret
        )
        assert code == 1 and document["error"]["code"] == "stale_version", document

        # The SQL guard refuses a token that skipped the preview's check.
        refusals = []
        original = DeliveryControlCommand.objects.create

        def create(**values):
            """The page's insert, keeping the guard's refusal."""
            try:
                return original(**values)
            except DatabaseError as error:
                refusals.append(str(error))
                raise

        monkeypatch.setattr(DeliveryControlCommand.objects, "create", create)
        code, document, _ = command(
            "send", "confirm", "--token", forged_resume(item), "--yes", secret=secret
        )
        assert code == 1 and document["error"]["code"] == "denied", document
        assert "result" not in document
        [refusal] = refusals
        assert "Resume requires current health and resolved uncertainty" in refusal
    item.campaign.refresh_from_db()
    assert item.campaign.delivery_paused
    assert not DeliveryControlCommand.objects.filter(action="resume").exists()
    assert trail() == before


def test_a_closed_campaign_resolution(scheduled, settings, monkeypatch):
    """Paused, then closed with a receipt and weekly reports held: the page's
    resolution preview and its confirmation cancel exactly those."""
    item = scheduled
    login, service, _ = item.arguments
    starts = item.campaign.active_configuration.starts_at
    with campaign_clock(starts + timedelta(hours=1)):
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Review mail")
            commands.confirm(*item.arguments, token=token)
        submit_while_paused(item, settings)
    with campaign_clock(starts + timedelta(days=9)):
        harness = SimpleNamespace(campaign=item.campaign, service=service)
        # A Weekly report's send, held by the pause (#1786).
        weekly_allocated(harness)
        close_campaign(item.campaign, uuid4())
    command = runner(service, monkeypatch)
    secret = approved(service, login)
    choice = ("--decision", "cancel", "--type", "admin_report", "--type", "receipt")
    with campaign_clock(
        item.campaign.active_configuration.ends_at + timedelta(hours=1)
    ):
        # Clearing is refused while messages are still held.
        code, document, _ = command(
            "send",
            "resolve-preview",
            "--decision",
            "clear",
            "--reason",
            "Done",
            secret=secret,
        )
        assert code == 1 and document["error"]["code"] == "stale_version", document
        code, document, _ = command(
            "send", "resolve-preview", *choice, "--reason", "Closed", secret=secret
        )
        assert code == 0, document
        result = document["result"]
        assert result["action"] == "resolve"
        assert result["resolution"]["decision"] == "cancel"
        assert result["resolution"]["types"] == ["admin_report", "receipt"]
        assert comparable(result) == page_preview(
            item,
            "resolve",
            "Closed",
            decision="cancel",
            types=["admin_report", "receipt"],
        )
        token = result["preview"]["token"]
        code, document, errors = command(
            "send", "confirm", "--token", token, secret=secret, answer=b"yes\n"
        )
        assert code == 0, document
        assert "cancel admin_report, receipt." in errors
        assert "Other held types and emails that may not have arrived" in errors
        assert "receipt: 1 held messages." in errors
        assert "admin_report: 2 held messages." in errors
    assert document["result"]["action"] == "resolve"
    cancelled = HeldMessageResolution.objects.filter(decision="cancel")
    assert cancelled.count() == 3
    # Nothing else was held, so cancelling ends the pause: the page's pause,
    # the resolution's own event and the pause's end, once each.
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    events = ("campaign_pause", "campaign_resume", "delivery_held_cancel")
    assert trail() == (1, 1, 1, events)
    assert_page_events_by(login.portal_session.principal_id)
    code, document, _ = command(
        "send", "confirm", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and document["result"]["created"] is False, document
    assert trail() == (1, 1, 1, events)


def test_refusals_change_nothing(scheduled, monkeypatch):
    """Bad reasons, a read-only session, an altered token, a Testing draft
    and no current campaign: each refused with its code, nothing recorded."""
    item = scheduled
    login, service, _ = item.arguments
    command = runner(service, monkeypatch)
    secret = approved(service, login)
    reader = approved(service, login, scope="read-only")
    for reason in ("   ", "x" * 1025):
        code, document, _ = command(
            "send", "pause-preview", "--reason", reason, secret=secret
        )
        assert code == 1 and document["error"]["code"] == "invalid", document
    code, document, _ = command(
        "send", "pause-preview", "--reason", "Look", secret=reader
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    code, document, _ = command(
        "send", "pause-preview", "--reason", "Look", secret=secret
    )
    assert code == 0, document
    token = document["result"]["preview"]["token"]
    code, document, errors = command(
        "send", "confirm", "--token", token[:-2] + "xx", secret=secret, answer=b"yes\n"
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert "Type yes" not in errors
    code, document, _ = command(
        "send", "confirm", "--token", token, "--yes", secret=reader
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    # Out of date: the page's five minutes have passed.
    with campaign_clock(_now() + timedelta(minutes=6)):
        code, document, _ = command(
            "send", "confirm", "--token", token, "--yes", secret=secret
        )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert trail() == (0, 0, 0, ()) and not DeliveryControlCommand.objects.exists()


def test_a_testing_draft_or_no_campaign_offers_nothing(admin):
    """No current campaign is not available; in Testing nothing is offered."""
    _, secret, _ = paired(admin.service)
    code, document, _ = admin(
        "send", "pause-preview", "--reason", "Look", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    code, document, _ = admin(
        "send", "pause-preview", "--reason", "Look", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert trail() == (0, 0, 0, ())


def test_staff_is_denied_before_the_prompt(scheduled, monkeypatch):
    """The role comes first (#1339): a full-scope session whose user is now
    Staff is denied before the token is read or the prompt shown."""
    item = scheduled
    login, service, _ = item.arguments
    command = runner(service, monkeypatch)
    secret = approved(service, login)
    code, document, _ = command(
        "send", "pause-preview", "--reason", "Look", secret=secret
    )
    assert code == 0, document
    token = document["result"]["preview"]["token"]
    as_staff(monkeypatch)
    code, document, errors = command(
        "send", "confirm", "--token", token, secret=secret, answer=b"yes\n"
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "Type yes" not in errors and "Pause" not in errors
    code, document, _ = command(
        "send", "resume-preview", "--reason", "Look", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    assert trail() == (0, 0, 0, ()) and not DeliveryControlCommand.objects.exists()


def test_an_ended_session_is_exit_5(admin):
    """A revoked session changes and reads nothing: exit 5."""
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    for argv in (
        ("send", "pause-preview", "--reason", "Look"),
        ("send", "confirm", "--token", "x", "--yes"),
    ):
        code, document, _ = admin(*argv, secret=secret)
        assert code == 5 and document["error"]["code"] == "session_ended", document
