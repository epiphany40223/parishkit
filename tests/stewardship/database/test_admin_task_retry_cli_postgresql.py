"""``task retry`` of ``pk-stewardship admin`` (ADM-11 PR 9a).

Process admission is replaced by the test's own Django assembly, as in
test_admin_status_cli_postgresql.py; everything after it is the real command
line on the restricted web login, retrying real failed tasks through the
Background work page's own functions: a Family mail preparation, daily
digest work and an export file cleanup. The request key crosses between the
page and the command line in both directions.
"""

import contextlib
import io
import json
from uuid import UUID, uuid4

import pytest
from django.db import OperationalError

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status

from .automation_builders import paired, pairing, start
from .campaign_builders import campaign_clock
from .test_admin_status_cli_postgresql import without_web_runtimes
from .test_background_grants_postgresql import task_login
from .test_daily_digest_planning_postgresql import INSTANT
from .test_daily_digest_retry_postgresql import fail_preparation
from .test_export_jobs_postgresql import scenario  # noqa: F401
from .test_export_retry_views_postgresql import failed_cleanup
from .test_export_views_postgresql import http_scenario  # noqa: F401
from .test_family_mail_preparation_postgresql import allocate, family_mail  # noqa: F401
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_task_retry"


def runner(service, monkeypatch):
    """Run admin commands in this process over ``service``.

    ``run(*argv, secret)`` returns ``(exit code, document, standard error)``
    for a command that prints exactly one document.
    """
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )

    @contextlib.contextmanager
    def admitted(configuration):
        """The test's Django and Valkey stand in for the web's own assembly."""
        yield admin_cli.AdminRuntime(
            store=service.store,
            pairing=automation.PairingStore(
                service.limiter.client, service.limiter.namespace
            ),
            public_origin="https://campaign.example.org",
            setup_complete=service.setup_complete,
        )

    monkeypatch.setattr(admin_cli, "ADMISSION", admitted)

    def run(*argv, secret):
        """One command as the wrapper runs it, on the restricted web login."""
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
        [document] = [json.loads(line) for line in out.getvalue().splitlines()]
        return code, document, err.getvalue()

    run.service = service
    return run


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Commands over the shared authentication service."""
    return runner(auth_service, monkeypatch)


def retry(admin, secret, task_id, key=None):
    """``task retry``, with ``--request-key`` when ``key`` is given."""
    argv = ["task", "retry", str(task_id)]
    if key is not None:
        argv += ["--request-key", str(key)]
    return admin(*argv, secret=secret)


def events():
    """The command events recorded so far."""
    return list(AuditEvent.objects.filter(event_type=EVENT))


def failed_preparation():
    """A real Family mail preparation whose only run failed; returns its task id."""
    ticket = allocate()
    run = _status(TaskRun.objects.get(pk=ticket.task_id))
    act(act(run, "claim"), "permanent_failure")
    return ticket.task_id


def page_retry(browser, path, values):
    """The page's own form post, on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            path, values, HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value
        )


def test_a_failed_preparation_is_retried_once_per_key(admin, family_mail, google):  # noqa: F811
    """The page's retry, keyed, audited, and shared with the page both ways."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        task_id = failed_preparation()
        browser, secret, row = paired(admin.service)
        key = uuid4()
        code, document, _ = retry(admin, secret, task_id, key)
        assert code == 0, document
        result = document["result"]
        assert result["created"] is True and result["request_key"] == str(key)
        created = TaskRun.objects.get(parent_id=task_id)
        assert result["task"] == {
            "id": str(created.pk),
            "root_id": str(task_id),
            "parent_id": str(task_id),
            "retry_sequence": 1,
            "type": "family_mail_prepare",
            "state": created.state,
        }
        assert created.retry_command_id == key
        assert created.initiated_by_id == row.principal_id
        # Attributed as the specification says: the Administrator as actor,
        # the automation session as subject, the invocation's correlation.
        [event] = events()
        assert event.actor_id == row.principal_id and event.subject_id == row.pk
        assert event.correlation_id == UUID(document["correlation_id"])

        # A repeat with the key returns the same retry and records nothing.
        code, again, _ = retry(admin, secret, task_id, key)
        assert code == 0 and again["result"]["created"] is False
        assert again["result"]["task"] == result["task"]
        # The page's form with the same key is the same retry, too.
        path = f"/admin/background/tasks/{task_id}/retry-family-preparation"
        response = page_retry(browser, path, {"command_id": str(key)})
        assert response.status_code == 302
        assert response["Location"].endswith(f"/background/task/{created.pk}")
        assert TaskRun.objects.filter(root_id=task_id).count() == 2
        assert len(events()) == 1

        # The failed run is no longer the latest: a new key is the page's 409.
        code, stale, _ = retry(admin, secret, task_id, uuid4())
        assert code == 1 and stale["error"]["code"] == "stale_version", stale
        # The retry run has not failed: nothing to retry either.
        code, stale, _ = retry(admin, secret, created.pk, uuid4())
        assert code == 1 and stale["error"]["code"] == "stale_version", stale
        assert TaskRun.objects.filter(root_id=task_id).count() == 2
        assert len(events()) == 1


def test_a_page_key_repeated_from_the_command_line(admin, family_mail, google):  # noqa: F811
    """A retry the page made is returned, not repeated, for the page's key."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        task_id = failed_preparation()
        browser, secret, _ = paired(admin.service)
        key = uuid4()
        path = f"/admin/background/tasks/{task_id}/retry-family-preparation"
        assert page_retry(browser, path, {"command_id": str(key)}).status_code == 302
        code, document, _ = retry(admin, secret, task_id, key)
        assert code == 0 and document["result"]["created"] is False, document
        assert TaskRun.objects.filter(root_id=task_id).count() == 2
        assert not events()


def test_without_a_key_one_is_made_and_written_first(admin, family_mail, google):  # noqa: F811
    """The generated key is on standard error, and in the result."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        task_id = failed_preparation()
        _, secret, _ = paired(admin.service)
        code, document, errors = retry(admin, secret, task_id)
        assert code == 0, document
        key = document["result"]["request_key"]
        assert f"request key {key}" in errors
        assert TaskRun.objects.get(parent_id=task_id).retry_command_id == UUID(key)


def test_failed_daily_digest_work_is_retried(admin, family_mail, google):  # noqa: F811
    """A digest task selects the page's daily digest retry."""
    with campaign_clock(INSTANT):
        status = fail_preparation(family_mail)
        _, secret, _ = paired(admin.service)
        key = uuid4()
        code, document, _ = retry(admin, secret, status.run_id, key)
        assert code == 0, document
        created = TaskRun.objects.get(parent_id=status.run_id)
        assert document["result"]["task"]["id"] == str(created.pk)
        assert document["result"]["task"]["type"] == status.task_type
        assert created.retry_command_id == key
        assert len(events()) == 1


def approved_by_browser(service):
    """A full-scope session approved from the one signed-in browser session.

    The export scenario's policy has its own Administrator record, so the
    approval uses the browser's session directly rather than an address.
    """
    from django.db import transaction

    from parishkit.stewardship.accounts.models import PortalSession

    secret, code = start(service)
    portal = PortalSession.objects.get(revoked_at__isnull=True)
    principal = current_principal(service.store, portal.principal_id)
    with transaction.atomic():
        automation.approve(
            principal, portal, pairing(service).request(code), scope="full", days=30
        )
    return secret


def test_a_failed_export_cleanup_is_retried(http_scenario, settings, monkeypatch):  # noqa: F811
    """An export cleanup task selects the page's cleanup retry."""
    task, _ = failed_cleanup(http_scenario)
    admin = runner(settings.STEWARDSHIP_AUTH_RUNTIME, monkeypatch)
    secret = approved_by_browser(admin.service)
    key = uuid4()
    code, document, _ = retry(admin, secret, task.run_id, key)
    assert code == 0, document
    created = TaskRun.objects.get(parent_id=task.run_id)
    assert document["result"]["task"]["type"] == "report_export_cleanup"
    assert document["result"]["task"]["id"] == str(created.pk)
    assert created.retry_command_id == key
    code, again, _ = retry(admin, secret, task.run_id, key)
    assert code == 0 and again["result"]["created"] is False
    assert len(events()) == 1


def test_refusals_change_nothing(admin, family_mail, google):  # noqa: F811
    """Unknown task, read-only scope and an ended session; no retry, no event."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        task_id = failed_preparation()
        _, secret, row = paired(admin.service)
        code, document, _ = retry(admin, secret, uuid4(), uuid4())
        assert code == 1 and document["error"]["code"] == "not_available"
        _, reader, _ = paired(admin.service, scope="read-only")
        code, document, _ = retry(admin, reader, task_id, uuid4())
        assert code == 1 and document["error"]["code"] == "denied"
        actor = current_principal(admin.service.store, row.principal_id)
        automation.revoke(row.pk, actor, reason="revoked_by_owner")
        code, document, _ = retry(admin, secret, task_id, uuid4())
        assert code == 5 and document["error"]["code"] == "session_ended"
        assert not TaskRun.objects.filter(parent_id=task_id).exists()
        assert not events()


def test_a_session_ended_during_the_retry_is_exit_5(
    admin,
    family_mail,  # noqa: F811
    google,
    monkeypatch,
):
    """Expired after the effect, before commit: rolled back, and exit 5."""
    from parishkit.stewardship.accounts import sessions
    from parishkit.stewardship.accounts.models import PortalSession
    from parishkit.stewardship.jobs import family_mail_tasks

    original = family_mail_tasks.retry_preparation
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        task_id = failed_preparation()
        _, secret, _ = paired(admin.service)

        def expires(*args, **kwargs):
            """Advance authentication's clock past the command session's end."""
            result = original(*args, **kwargs)
            deadline = PortalSession.objects.latest("created_at").expires_at
            monkeypatch.setattr(sessions, "database_now", lambda: deadline)
            return result

        monkeypatch.setattr(family_mail_tasks, "retry_preparation", expires)
        code, document, _ = retry(admin, secret, task_id, uuid4())
        assert code == 5 and document["error"]["code"] == "session_ended", document
        assert not TaskRun.objects.filter(parent_id=task_id).exists()
        assert not events()


def test_a_lost_commit_is_an_unknown_outcome_safe_to_repeat(
    admin,
    family_mail,  # noqa: F811
    google,
    monkeypatch,
):
    """A database error once the retry is written: exit 6, then repeat the key."""
    from parishkit.stewardship import admin_operations

    original, calls = admin_operations.task_retry_model, []

    def lost(*args, **kwargs):
        """The connection drops once the retry and its event are written."""
        if not calls:
            calls.append(True)
            raise OperationalError("connection lost")
        return original(*args, **kwargs)

    monkeypatch.setattr(admin_operations, "task_retry_model", lost)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        task_id = failed_preparation()
        _, secret, _ = paired(admin.service)
        key = uuid4()
        code, document, _ = retry(admin, secret, task_id, key)
        assert code == 6 and document["error"]["code"] == "outcome_unknown"
        assert document["error"]["request_id"] == str(key)
        # Here the transaction rolled back; the same key retries it once.
        code, document, _ = retry(admin, secret, task_id, key)
        assert code == 0 and document["result"]["created"] is True, document
        assert TaskRun.objects.filter(parent_id=task_id).count() == 1
        assert len(events()) == 1


def test_a_key_another_administrator_used_is_invalid(admin, family_mail, google):  # noqa: F811
    """Keys are bound within the chain to the Administrator who used them."""
    from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
    from parishkit.stewardship.jobs.family_mail_tasks import retry_preparation

    from ..policy_factory import address
    from .campaign_builders import change
    from .test_policy_postgresql import user

    store = admin.service.store
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        task_id = failed_preparation()
        _, secret, row = paired(admin.service)
        other = user("other@example.org")
        change(
            store,
            store.active(),
            row.principal_id,
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("other@example.org"),
                }
            ],
        )
        key = uuid4()
        # The other Administrator retries first, with this key (as the page).
        ticket = FamilyMailPreparation.objects.get(task_id=task_id)
        retry_preparation(store, other.pk, ticket.pk, command_id=key)
        code, document, _ = retry(admin, secret, task_id, key)
        assert code == 1 and document["error"]["code"] == "invalid", document
        assert TaskRun.objects.filter(parent_id=task_id).count() == 1
        assert not events()
