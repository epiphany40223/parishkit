"""The schedule change commands of ``pk-stewardship admin`` (ADM-11 PR 4).

Process admission is replaced by the test's own Django assembly, as in
test_admin_status_cli_postgresql.py; everything after it is the real command
line on the restricted web login: ``schedule preview`` through the Mail
schedules page's own forms and review, ``schedule confirm`` through the
page's confirmation, the configuration installer applying the request, and
``config request show`` following it. Tokens cross between the page and the
command line in both directions.
"""

import io
import json
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing
from django.db import OperationalError

from parishkit.stewardship import admin_changes, admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationSession,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.schedule_changes import preview_salt
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole

from ..campaign_factory import schedule
from .automation_builders import paired
from .campaign_builders import add_draft, advance, campaign_clock, change, claimed_task
from .test_admin_status_cli_postgresql import runner, without_web_runtimes
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import post
from .test_parish_views_postgresql import token as page_token
from .test_schedule_views_postgresql import fields, pending
from .test_schedule_views_postgresql import setup as campaign_with_reminder

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_schedule_confirm"


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Run admin commands in this process; ``admin(*argv, secret, stdin)``.

    ``stdin`` is what the wrapper forwards after the preamble for a ``-``
    input. Returns ``(exit code, documents)``.
    """
    base = runner(auth_service, monkeypatch)

    def run(*argv, secret, stdin=b""):
        """One command as the wrapper runs it, on the restricted web login."""
        out = io.StringIO()
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            without_web_runtimes(),
        ):
            code = admin_cli.main(
                [*argv, "--config", "web.yaml", "--session-stdin"],
                stdin=io.BytesIO(
                    f"pk-admin-session/1 {secret} {'a' * 64}\n".encode() + stdin
                ),
                stdout=out,
                stderr=io.StringIO(),
            )
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    run.service = base.service
    return run


def one(admin, *argv, secret, stdin=b""):
    """A command that prints exactly one document; returns (code, document)."""
    code, documents = admin(*argv, secret=secret, stdin=stdin)
    assert len(documents) == 1, documents
    return code, documents[0]


def reminder_id(store, campaign):
    """The saved reminder schedule's id."""
    return next(
        row["id"]
        for row in store.active().document()["sections"]["schedules"]
        if row["values"]["kind"] == "reminder"
        and row["values"]["campaign_id"] == str(campaign.pk)
    )


def preview(admin, secret, document, *, version=None, campaign=None):
    """``schedule preview`` of ``document`` against the applied version."""
    argv = [
        "schedule",
        "preview",
        "--expected-version",
        version or admin.service.store.active().digest,
        "--changes",
        "-",
    ]
    if campaign is not None:
        argv += ["--campaign", campaign]
    return one(admin, *argv, secret=secret, stdin=json.dumps(document).encode())


def confirm(admin, secret, token, *, campaign=None):
    """``schedule confirm`` with the token on standard input."""
    argv = ["schedule", "confirm", "--token", "-"]
    if campaign is not None:
        argv += ["--campaign", campaign]
    return one(admin, *argv, secret=secret, stdin=token.encode() + b"\n")


def later(store, campaign):
    """A change moving the reminder to 10:30 on its own day."""
    return {"schedules": [{"id": reminder_id(store, campaign), "time": "10:30:00"}]}


def end_date(campaign, days=0):
    """The campaign's own end date, moved by ``days``."""
    end = date.fromisoformat(campaign.active_configuration.values["end_date"])
    return (end + timedelta(days=days)).isoformat()


def altered(token):
    """The token with its last character changed, always to a different one."""
    return token[:-1] + ("A" if token[-1] != "A" else "B")


def events():
    """The command events recorded so far."""
    return list(AuditEvent.objects.filter(event_type=EVENT))


def test_preview_confirm_and_status_change_a_schedule(admin, google):
    """The round trip: review, confirm, apply, and the request followed."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, row = paired(admin.service)
    identifier = reminder_id(store, campaign)
    code, document = preview(admin, secret, later(store, campaign))
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == str(campaign.pk)
    assert result["version"] == store.active().digest
    assert result["window"]["changed"] == [] and result["blocking"] == 0
    [moved] = result["changes"]
    assert moved["id"] == identifier and moved["operation"] == "update"
    assert moved["kind"] == "reminder"
    assert moved["before"]["time"] == "09:00:00"
    assert moved["after"]["time"] == "10:30:00"
    assert moved["after"]["resolved"][0]["due_at"].endswith("+00:00")
    token = result["preview"]["token"]
    # Previewing changes nothing, and records no command event.
    assert ConfigurationChangeRequest.objects.count() == 2 and not events()

    code, confirmed = confirm(admin, secret, token)
    assert code == 0, confirmed
    assert confirmed["result"]["created"] is True
    request = confirmed["result"]["request"]
    assert request["state"] == "staged" and request["sequence"] == 1
    created = ConfigurationChangeRequest.objects.get(pk=request["request_id"])
    # Attributed as the specification says: the Administrator as actor, the
    # automation session as subject, the invocation's correlation.
    correlation = UUID(confirmed["correlation_id"])
    assert created.actor_id == row.principal_id
    assert created.correlation_id == correlation
    [event] = events()
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert event.correlation_id == correlation

    code, status = one(
        admin, "config", "request", "show", request["request_id"], secret=secret
    )
    assert code == 0 and status["result"] == request
    install_request(store, request_id=created.pk, correlation_id=uuid4())
    code, status = one(
        admin,
        "config",
        "request",
        "show",
        request["request_id"],
        "--watch",
        "2",
        secret=secret,
    )
    # Applied is terminal: one final document, no polling.
    assert code == 0 and status["final"], status
    assert status["result"]["state"] == "applied"
    assert status["result"]["applied_version_id"] == str(created.candidate_version_id)
    definition = ScheduleDefinition.objects.get(kind="reminder")
    assert definition.current_revision.values["time"] == "10:30:00"

    # A repeated confirm returns the original request and records nothing.
    code, again = confirm(admin, secret, token)
    assert code == 0, again
    assert again["result"]["created"] is False
    assert again["result"]["request"]["request_id"] == request["request_id"]
    assert len(events()) == 1


def test_tokens_cross_between_the_page_and_the_command_line(admin, google):
    """A browser review confirms from the command line, and the other way."""
    store = admin.service.store
    campaign, path = campaign_with_reminder(store)
    browser, secret, _ = paired(admin.service)
    data, indexes = fields(store, campaign)
    data[f"schedules-{indexes['reminder']}-time"] = "10:30:00"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = page_token(post(browser, path, data))
    code, document = preview(admin, secret, later(store, campaign))
    assert code == 0, document
    command = document["result"]["preview"]["token"]
    # The same reviewed intent: base, work snapshot and patch.
    salt = preview_salt(campaign.pk)
    signed = [signing.loads(value, salt=salt) for value in (page, command)]
    for name in ("actor", "base", "snapshot", "patch"):
        assert signed[0][name] == signed[1][name], name

    code, confirmed = confirm(admin, secret, page)
    assert code == 0 and confirmed["result"]["created"], confirmed
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        accepted = post(browser, path, {"action": "confirm", "preview": command})
    assert accepted.status_code == 302
    assert ConfigurationChangeRequest.objects.filter(
        pk=accepted["Location"].rstrip("/").rsplit("/", 1)[-1]
    ).exists()
    # Only the command line's confirmation records the command event.
    assert len(events()) == 1


def test_a_stale_version_or_campaign_state_is_refused(admin, google):
    """An old version, an unknown campaign and no campaign each refuse."""
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, {})
    assert code == 1 and document["error"]["code"] == "not_available"
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    code, document = preview(admin, secret, later(store, campaign), version="0" * 64)
    assert code == 1 and document["error"]["code"] == "stale_version"
    code, document = preview(
        admin, secret, later(store, campaign), campaign=str(uuid4())
    )
    assert code == 1 and document["error"]["code"] == "not_available"
    assert ConfigurationChangeRequest.objects.count() == 2


def test_the_pages_form_errors_are_reported_by_field(admin, google):
    """A reminder outside the campaign: invalid, with the page's message."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    identifier = reminder_id(store, campaign)
    bad = {"schedules": [{"id": identifier, "date": end_date(campaign, 30)}]}
    code, document = preview(admin, secret, bad)
    assert code == 1 and document["error"]["code"] == "invalid"
    [problem] = document["error"]["fields"]
    assert problem["field"] == f"schedules.{identifier}.date"
    assert problem["code"] == "invalid"
    assert "within the campaign" in problem["message"]
    # No change at all is the page's own refusal, as a form problem.
    code, document = preview(admin, secret, {})
    assert code == 1 and document["error"]["code"] == "invalid"
    assert document["error"]["fields"][0]["field"] == "window"
    code, document = preview(admin, secret, {"schedules": [{"id": str(uuid4())}]})
    assert code == 1 and document["error"]["fields"][0]["field"].startswith(
        "schedules."
    )


def test_times_take_the_pages_common_forms(admin, google):
    """``--changes`` times are read by the page's own parser (#631)."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    identifier = reminder_id(store, campaign)
    code, document = preview(
        admin, secret, {"schedules": [{"id": identifier, "time": "10:30 am"}]}
    )
    assert code == 0, document
    [moved] = document["result"]["changes"]
    assert moved["after"]["time"] == "10:30:00"
    code, document = preview(
        admin, secret, {"schedules": [{"id": identifier, "time": "13pm"}]}
    )
    assert code == 1 and document["error"]["code"] == "invalid"
    [problem] = document["error"]["fields"]
    assert problem["field"] == f"schedules.{identifier}.time"
    assert problem["code"] == "invalid"
    assert "with AM or PM the hour runs from 1 to 12" in problem["message"]


def test_bad_tokens_change_nothing(admin, google, monkeypatch):
    """Expired, altered, other campaign, other Administrator, stale work."""
    from parishkit.stewardship.web import refusals

    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    token = document["result"]["preview"]["token"]
    before = ConfigurationChangeRequest.objects.count()

    code, document = confirm(admin, secret, altered(token))
    assert code == 1 and document["error"]["code"] == "invalid"
    code, document = confirm(admin, secret, token, campaign=str(uuid4()))
    assert code == 1 and document["error"]["code"] == "invalid"
    intent = signing.loads(token, salt=preview_salt(campaign.pk))
    foreign = signing.dumps(
        intent | {"actor": str(uuid4())}, salt=preview_salt(campaign.pk)
    )
    code, document = confirm(admin, secret, foreign)
    assert code == 1 and document["error"]["code"] == "denied"
    with monkeypatch.context() as expired:
        expired.setattr(refusals, "PREVIEW_MAX_AGE", -1)
        code, document = confirm(admin, secret, token)
    assert code == 1 and document["error"]["code"] == "stale_version"
    # Another change applied since the review: the base moved on.
    owner = str(campaign.pk)
    digest = schedule(owner, kind="daily_digest", date=None, time="07:00:00")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **digest}],
        ).state
        == "applied"
    )
    code, document = confirm(admin, secret, token)
    assert code == 1 and document["error"]["code"] == "stale_version"
    assert ConfigurationChangeRequest.objects.count() == before + 1
    assert not events()


def test_read_only_and_ended_sessions_change_nothing(admin, google):
    """Read-only: refused (exit 1); ended: exit 5; no request, no event."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, full, row = paired(admin.service)
    code, document = preview(admin, full, later(store, campaign))
    token = document["result"]["preview"]["token"]
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document = preview(admin, reader, later(store, campaign))
    assert code == 1 and document["error"]["code"] == "denied"
    code, document = confirm(admin, reader, token)
    assert code == 1 and document["error"]["code"] == "denied"
    actor = current_principal(store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, document = confirm(admin, full, token)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert ConfigurationChangeRequest.objects.count() == 2 and not events()


def test_a_session_ended_during_the_confirmation_is_exit_5(admin, google, monkeypatch):
    """Revoked after admission, before intake: the page's refusal becomes exit 5."""
    from parishkit.stewardship.accounts import admin_editing

    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, row = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    token = document["result"]["preview"]["token"]
    load = admin_editing.load_preview

    def revoked(*args, **kwargs):
        """The session is revoked while the token is read."""
        actor = current_principal(store, row.principal_id)
        automation.revoke(row.pk, actor, reason="revoked_by_owner")
        return load(*args, **kwargs)

    monkeypatch.setattr(admin_editing, "load_preview", revoked)
    code, document = confirm(admin, secret, token)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert ConfigurationChangeRequest.objects.count() == 2 and not events()


def test_an_error_after_the_request_commits_is_an_unknown_outcome(
    admin, google, monkeypatch
):
    """Committed, then a failure: exit 6, and the request is there to read."""
    from parishkit.stewardship import admin_reads

    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    token = document["result"]["preview"]["token"]

    def broken(status):
        """The receipt cannot be reported."""
        raise RuntimeError("after the commit")

    with monkeypatch.context() as patched:
        patched.setattr(admin_reads, "config_request", broken)
        patched.setattr(admin_changes, "config_request", broken)
        code, document = confirm(admin, secret, token)
    assert code == 6 and document["error"]["code"] == "outcome_unknown"
    assert ConfigurationChangeRequest.objects.count() == 3 and len(events()) == 1
    # The document names the request, which config request show reads.
    code, status = one(
        admin,
        "config",
        "request",
        "show",
        document["error"]["request_id"],
        secret=secret,
    )
    assert code == 0 and status["result"]["state"] == "staged"
    # Repeating with the same token returns that request.
    code, document = confirm(admin, secret, token)
    assert code == 0 and document["result"]["created"] is False


def test_a_database_failure_before_the_write_is_unavailable(admin, google, monkeypatch):
    """An outage before the request row exists rolled back: exit 3."""
    from parishkit.stewardship.audit import services

    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    token = document["result"]["preview"]["token"]
    record = services.record_action

    def lost(action, **values):
        """The connection drops as the command event is written."""
        if action == "admin_cmd_schedule_confirm":
            raise OperationalError("connection lost")
        return record(action, **values)

    monkeypatch.setattr(services, "record_action", lost)
    code, document = confirm(admin, secret, token)
    assert code == 3 and document["error"]["code"] == "unavailable"
    assert ConfigurationChangeRequest.objects.count() == 2 and not events()


def test_a_database_failure_after_the_write_is_never_called_harmless(
    admin, google, monkeypatch
):
    """Once the request row is written, an outage may have struck the commit.

    Exit 6, naming the request to read; here it rolled back, which the
    command could not have known, and the same token then records it.
    """
    from parishkit.stewardship.accounts import configuration_requests

    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, row = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    token = document["result"]["preview"]["token"]
    key = UUID(signing.loads(token, salt=preview_salt(campaign.pk))["key"])

    def lost(request):
        """The connection drops after the request and its event are written."""
        raise OperationalError("connection lost")

    with monkeypatch.context() as patched:
        patched.setattr(configuration_requests, "_status", lost)
        code, document = confirm(admin, secret, token)
    assert code == 6 and document["error"]["code"] == "outcome_unknown"
    identifier = document["error"]["request_id"]
    assert identifier == str(
        configuration_requests.policy_operation_id(row.principal_id, key)
    )
    code, status = one(admin, "config", "request", "show", identifier, secret=secret)
    assert code == 1 and status["error"]["code"] == "not_available"
    code, again = confirm(admin, secret, token)
    assert code == 0 and again["result"]["request"]["request_id"] == identifier


def test_request_status_is_the_administrators_own(admin, google, monkeypatch):
    """Unknown or another's request: not available; no view event; watch timeout."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service, scope="read-only")
    for identifier in (
        str(uuid4()),
        # The setup's own requests belong to other actors.
        str(ConfigurationChangeRequest.objects.first().pk),
    ):
        code, document = one(
            admin, "config", "request", "show", identifier, secret=secret
        )
        assert code == 1 and document["error"]["code"] == "not_available"
    _, full, _ = paired(admin.service)
    code, document = preview(admin, full, later(store, campaign))
    code, confirmed = confirm(admin, full, document["result"]["preview"]["token"])
    request = confirmed["result"]["request"]["request_id"]
    # The page records no view event, so neither does the command, even for
    # a read-only session following another session's change.
    before = AuditEvent.objects.count()
    code, document = one(admin, "config", "request", "show", request, secret=secret)
    assert code == 0 and document["result"]["state"] == "staged"
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(
        admin_cli,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda seconds: None),
    )
    code, documents = admin(
        "config",
        "request",
        "show",
        request,
        "--watch",
        "2",
        "--timeout",
        "6",
        secret=full,
    )
    assert code == 7, documents
    assert not documents[0]["final"] and documents[0]["result"]["state"] == "staged"
    assert documents[-1]["error"]["code"] == "watch_timeout"
    assert documents[-1]["result"]["state"] == "staged"
    assert AuditEvent.objects.count() == before


def test_preview_records_activity_like_the_pages_post(admin, google):
    """The page's state-changing admission renews the command session."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    code, _ = preview(admin, secret, later(store, campaign))
    assert code == 0
    command = PortalSession.objects.get(
        pk__in=AutomationLogin.objects.values("portal_session_id")
    )
    # Inserted at 1, renewed by the admission, closed at exit.
    assert command.revoked_at is not None and command.version > 2
    assert AutomationSession.objects.count() == 1


def test_a_draft_window_change_previews(admin, google):
    """A plain draft's dates may change: a window change previews."""
    store = admin.service.store
    _, row, _ = add_draft(store, store.active(), uuid4())
    _, secret, _ = paired(admin.service)
    from parishkit.stewardship.campaigns.models import Campaign

    earlier = end_date(Campaign.objects.get(), -1)
    code, document = preview(admin, secret, {"window": {"end_date": earlier}})
    assert code == 0, document
    assert document["result"]["window"]["changed"] == ["end_date"]
    assert document["result"]["window"]["after"]["end_date"] == earlier
    assert document["result"]["campaign_id"] == row["id"]


def test_new_work_since_the_review_makes_the_token_stale(admin, google):
    """The page's stale inventory: a send planned after the review refuses it."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    token = document["result"]["preview"]["token"]
    pending(ScheduleDefinition.objects.get(kind="reminder"), uuid4())
    code, document = confirm(admin, secret, token)
    assert code == 1 and document["error"]["code"] == "stale_version"
    assert ConfigurationChangeRequest.objects.count() == 2 and not events()


def test_running_work_blocks_the_change_with_no_token(admin, google):
    """A send in progress blocks it: counted, and nothing to confirm."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    actor = uuid4()
    row = pending(ScheduleDefinition.objects.get(kind="reminder"), actor)
    with campaign_clock(row.due_at):
        run = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    assert code == 0, document
    result = document["result"]
    assert result["blocking"] >= 1 and result["preview"] is None
    assert result["changes"][0]["impact"]["blocking"] == result["blocking"]


def test_locked_dates_and_other_campaigns_are_refused(admin, google, monkeypatch):
    """A date change once dates are locked, and a campaign not current: stale."""
    from django.db import connection, transaction

    from parishkit.stewardship.accounts import schedule_reads
    from parishkit.stewardship.campaigns.models import Campaign

    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    state = schedule_reads.schedule_state

    def locked(service, campaign_id):
        """The campaign's dates may no longer change."""
        values, current, _ = state(service, campaign_id)
        return values, current, False

    with monkeypatch.context() as patched:
        patched.setattr(schedule_reads, "schedule_state", locked)
        moved = {"window": {"end_date": end_date(campaign, -1)}}
        code, document = preview(admin, secret, moved)
        assert code == 1 and document["error"]["code"] == "stale_version"
        # The schedules themselves may still change.
        code, document = preview(admin, secret, later(store, campaign))
        assert code == 0 and document["result"]["preview"], document
    # An archived campaign beside the current one.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE stewardship_campaign DISABLE TRIGGER USER")
        historical = Campaign.objects.create(
            state="archived", active_configuration=campaign.active_configuration
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign ENABLE TRIGGER USER")
    code, document = preview(
        admin, secret, later(store, campaign), campaign=str(historical.pk)
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert ConfigurationChangeRequest.objects.count() == 2


def test_oversized_or_undecodable_input_is_invalid(admin, google):
    """Standard input past the limit, or not UTF-8, is refused as input."""
    store = admin.service.store
    campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    argv = (
        "schedule",
        "preview",
        "--expected-version",
        store.active().digest,
        "--changes",
        "-",
    )
    for stdin in (b"{" * (admin_changes.CHANGES_LIMIT + 1), b"\xff\xfe{}"):
        code, document = one(admin, *argv, secret=secret, stdin=stdin)
        assert code == 1 and document["error"]["code"] == "invalid"
    code, document = one(
        admin,
        "schedule",
        "confirm",
        "--token",
        "-",
        secret=secret,
        stdin=b"x" * (admin_changes.TOKEN_LIMIT + 1),
    )
    assert code == 1 and document["error"]["code"] == "invalid"


def test_a_missing_capability_is_denied(admin, google, monkeypatch):
    """Without the page's capability, both commands are refused (exit 1)."""
    from parishkit.stewardship.accounts import admin_editing
    from parishkit.stewardship.accounts.policy import Capability

    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, later(store, campaign))
    token = document["result"]["preview"]["token"]
    allows = admin_editing.allows
    monkeypatch.setattr(
        admin_editing,
        "allows",
        lambda principal, capability, **scope: (
            capability != Capability.CONFIGURE
            and allows(principal, capability, **scope)
        ),
    )
    code, document = preview(admin, secret, later(store, campaign))
    assert code == 1 and document["error"]["code"] == "denied"
    code, document = confirm(admin, secret, token)
    assert code == 1 and document["error"]["code"] == "denied"
    assert ConfigurationChangeRequest.objects.count() == 2 and not events()
