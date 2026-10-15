"""``send controls``: the Pause and resume mail page's read (ADM-11 PR 7b).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The command
reads the page's own status (``delivery_control_commands.controls``), so a
scheduled Production campaign's document is the page's, before and after a
pause made through the page's own preview and confirmation.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.admin_delivery_control import controls_model
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from .automation_builders import as_staff, paired
from .campaign_builders import add_draft, campaign_clock, close_campaign
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_go_live_cli_postgresql import approved
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_delivery_closed_postgresql import submit_while_paused
from .test_setup_mail_views_postgresql import web_login
from .test_weekly_tasks_postgresql import execute as execute_weekly
from .test_weekly_tasks_postgresql import queued as queue_weekly
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


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def controls(admin, secret):
    """Run ``send controls``; returns (exit code, document)."""
    code, document, _ = admin("send", "controls", secret=secret)
    return code, document


def page_document(item):
    """The page's own status, projected as the command prints it."""
    with web_login():
        status = commands.page(*item.arguments)
    return controls_model(status["campaign"], status).to_document()


def test_a_testing_draft_offers_no_control(admin):
    """In Testing the page offers no control; a read-only session reads the
    counts, and nothing is recorded."""
    store = admin.service.store
    _, row, _ = add_draft(store, store.active(), uuid4())
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    code, document = controls(admin, reader)
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == row["id"]
    assert not result["available"] and not result["paused"]
    assert result["paused_at"] is None and result["reason"] is None
    assert result["inventory"]["queued"] == result["inventory"]["held"] == 0
    assert result["health"] == {
        "ready": False,
        "required": False,
        "trouble": None,
        "checked_at": None,
        "expires_at": None,
    }
    assert not result["resume_available"] and not result["resolve_available"]
    assert result["resolution"] is None
    assert result["family_recovery"] is None and result["report_recovery"] is None
    assert AuditEvent.objects.count() == events


def test_no_current_campaign_is_not_available(admin):
    """Nothing to show: exit 1, nothing printed."""
    _, secret, _ = paired(admin.service)
    code, document = controls(admin, secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document


def test_a_reader_who_is_not_an_administrator_is_denied(admin, monkeypatch):
    """Staff cannot open Pause and resume mail: denied, nothing printed."""
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    _, secret, _ = paired(admin.service)
    as_staff(monkeypatch)
    code, document = controls(admin, secret)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document


@pytest.mark.parametrize("campaign", [False, True])
def test_an_ended_session_reads_nothing(admin, campaign):
    """A revoked session is exit 5, with or without a current campaign: the
    session is rechecked before a refusal is reported."""
    if campaign:
        store = admin.service.store
        add_draft(store, store.active(), uuid4())
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document = controls(admin, secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert "result" not in document


def raw(view, campaign_id):
    """One of the page's SQL count documents, read directly, without its
    fingerprint: the projection is checked against the source, not itself."""
    with web_login():
        values = view(campaign_id)
    values.pop("fingerprint")
    return values


def test_a_production_campaign_is_the_pages_status(scheduled, monkeypatch):
    """Scheduled in Production: the page's status, then paused through the
    page's own preview and confirmation, holding a queued email, with its
    reason and recovery counts."""
    item = scheduled
    login, service, campaign_id = item.arguments
    command = runner(service, monkeypatch)
    secret = approved(service, login, scope="read-only")
    future_message(item)
    events = AuditEvent.objects.count()
    code, document, _ = command("send", "controls", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == str(campaign_id)
    assert result["available"] and not result["paused"]
    assert result["pause_available"] and not result["resume_available"]
    assert result["inventory"]["queued"] == 1 and result["inventory"]["held"] == 0
    assert result["inventory"]["types"] == [
        {"kind": "initial", "queued": 1, "held": 0, "submitting": 0, "unknown": 0}
    ]
    assert result["next_due"]["types"] == [{"kind": "initial", "count": 1}]
    assert result["family_recovery"] is None and result["report_recovery"] is None
    assert result == page_document(item)
    assert AuditEvent.objects.count() == events
    with web_login():
        _, token = commands.preview_pause(*item.arguments, reason="Wrong reminder")
        commands.confirm(*item.arguments, token=token)
    events = AuditEvent.objects.count()
    code, document, _ = command("send", "controls", secret=secret)
    assert code == 0, document
    assert AuditEvent.objects.count() == events
    result = document["result"]
    item.campaign.refresh_from_db()
    assert result["paused"] and result["reason"] == "Wrong reminder"
    assert result["paused_at"] == item.campaign.paused_at.isoformat()
    # The queued initial email is now held; a held email is still unsent,
    # so the page counts it as queued too.
    assert result["inventory"]["queued"] == 1 and result["inventory"]["held"] == 1
    assert result["inventory"]["types"] == [
        {"kind": "initial", "queued": 1, "held": 1, "submitting": 0, "unknown": 0}
    ]
    # Before its start, resuming only releases held mail; an ordinary pause
    # needs no sender check (#1288).
    assert not result["pause_available"]
    assert result["resume_available"] and not result["resolve_available"]
    assert result["resolution"] is None
    assert result["health"]["ready"] and not result["health"]["required"]
    assert result["family_recovery"] == raw(
        commands.family_recovery_impact, campaign_id
    )
    assert result["report_recovery"] == {
        key: value
        for key, value in raw(commands.digest_recovery_impact, campaign_id).items()
        if key != "types"
    }
    assert result == page_document(item)


def test_a_paused_closed_campaign_offers_its_resolution(
    scheduled, settings, monkeypatch
):
    """Paused, a receipt and weekly reports held, then closed: the page's
    Resolve held messages choices, with the page's labels as text."""
    item = scheduled
    login, service, campaign_id = item.arguments
    starts = item.campaign.active_configuration.starts_at
    with campaign_clock(starts + timedelta(hours=1)):
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Review mail")
            commands.confirm(*item.arguments, token=token)
        submit_while_paused(item, settings)
    with campaign_clock(starts + timedelta(days=9)):
        harness = SimpleNamespace(campaign=item.campaign, service=service)
        status = queue_weekly(harness)
        with task_login(ServiceRole.WORKER, exact=True):
            execute_weekly(status)
        close_campaign(item.campaign, uuid4())
    command = runner(service, monkeypatch)
    secret = approved(service, login, scope="read-only")
    with campaign_clock(
        item.campaign.active_configuration.ends_at + timedelta(hours=1)
    ):
        events = AuditEvent.objects.count()
        code, document, _ = command("send", "controls", secret=secret)
        assert code == 0, document
        assert AuditEvent.objects.count() == events
        result = document["result"]
        assert result["available"] and result["paused"]
        assert result["resolve_available"] and not result["resume_available"]
        kinds = {row["kind"]: row for row in result["inventory"]["types"]}
        assert kinds["receipt"]["held"] == 1 and kinds["weekly_digest"]["held"] == 2
        assert result["inventory"]["held"] >= 3
        resolution = result["resolution"]
        assert resolution["types"] == [
            {
                "kind": "receipt",
                "name": "Submission receipts",
                "held": 1,
                "cancellable": True,
            },
            {
                "kind": "weekly_digest",
                "name": "Weekly Admin reports",
                "held": 2,
                "cancellable": True,
            },
        ]
        # An ordinary pause needs no sender check (#1288): both are offered.
        assert resolution["release"] and resolution["cancel"]
        assert result == page_document(item)
