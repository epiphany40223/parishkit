"""``refresh start`` and ``refresh status`` of ``pk-stewardship admin`` (PR 6a).

Process admission is replaced by the test's own Django assembly, as in
test_admin_task_retry_cli_postgresql.py; everything after it is the real
command line on the restricted web login, requesting real manual refreshes
through the Source refresh page's own functions. The request key crosses
between the page and the command line in both directions.
"""

from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshCommand,
    SourceRefreshRequest,
)
from parishkit.stewardship.source.requests import TASK_TYPE

from ..test_source_corpus import source
from .automation_builders import paired
from .test_admin_task_retry_cli_postgresql import runner
from .test_chair_suggestions_postgresql import publish
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_refresh_views_postgresql import post, run_of, web
from .test_source_requests_postgresql import claim

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("source_singletons"),
]

EVENT = "admin_cmd_refresh_start"


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Commands over the shared authentication service, with a source."""
    publish(source())
    return runner(auth_service, monkeypatch)


def start(admin, secret, key=None):
    """``refresh start``, with ``--request-key`` when ``key`` is given."""
    argv = ["refresh", "start"]
    if key is not None:
        argv += ["--request-key", str(key)]
    return admin(*argv, secret=secret)


def events():
    """The command events recorded so far."""
    return list(AuditEvent.objects.filter(event_type=EVENT))


def test_a_refresh_is_requested_once_per_key(admin, google):
    """The page's request, keyed, audited, and shared with the page both ways."""
    browser, secret, row = paired(admin.service)
    key = uuid4()
    code, document, _ = start(admin, secret, key)
    assert code == 0, document
    result = document["result"]
    request = SourceRefreshRequest.objects.get()
    assert result == {
        "created": True,
        "request_key": str(key),
        "refresh": {
            "command_id": str(key),
            "request_id": str(request.pk),
            "task_root_id": str(request.task_root_id),
        },
    }
    command = SourceRefreshCommand.objects.get(pk=key)
    assert (command.cause, command.kind) == ("manual", "full")
    assert command.actor_id == row.principal_id
    # Attributed as the specification says: the Administrator as actor, the
    # automation session as subject, the invocation's correlation.
    [event] = events()
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert event.correlation_id == UUID(document["correlation_id"])

    # A repeat with the key returns the same receipt and records nothing.
    code, again, _ = start(admin, secret, key)
    assert code == 0 and again["result"]["created"] is False
    assert again["result"]["refresh"] == result["refresh"]
    # The page's form with the same key leads to the same run, too.
    with web():
        assert run_of(post(browser, key)) == str(request.task_root_id)
    # A new key while the full refresh waits joins it: not an error.
    code, joined, _ = start(admin, secret, uuid4())
    assert code == 0 and joined["result"]["created"] is True
    assert joined["result"]["refresh"]["task_root_id"] == str(request.task_root_id)
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 1
    assert SourceRefreshCommand.objects.count() == 2
    assert len(events()) == 2


def test_a_page_key_repeated_from_the_command_line(admin, google):
    """A refresh the page requested is returned, not repeated, for its key."""
    browser, secret, _ = paired(admin.service)
    key = uuid4()
    with web():
        root = run_of(post(browser, key))
    code, document, _ = start(admin, secret, key)
    assert code == 0 and document["result"]["created"] is False, document
    assert document["result"]["refresh"]["task_root_id"] == root
    assert SourceRefreshCommand.objects.count() == 1
    assert not events()


def test_without_a_key_one_is_made_and_written_first(admin, google):
    """The generated key is on standard error, and in the result."""
    _, secret, _ = paired(admin.service)
    code, document, errors = start(admin, secret)
    assert code == 0, document
    key = document["result"]["request_key"]
    assert f"request key {key}" in errors
    assert SourceRefreshCommand.objects.filter(pk=UUID(key)).exists()


def test_status_shows_what_is_running_and_waiting(admin, google):
    """``refresh status`` is the page's read, for a read-only session too."""
    _, secret, _ = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    recorded = AuditEvent.objects.count()
    code, document, _ = admin("refresh", "status", secret=reader)
    assert code == 0, document
    # The page's view records no event, so neither does the read.
    assert AuditEvent.objects.count() == recorded
    status = document["result"]
    assert (status["running"], status["waiting"]) == (False, False)
    assert status["lateness_minutes"] > 0
    assert "refreshed_at" not in status["source"]
    assert status["source"]["full_running"] is False
    assert start(admin, secret)[0] == 0
    recorded = AuditEvent.objects.count()
    code, document, _ = admin("refresh", "status", secret=reader)
    assert (document["result"]["running"], document["result"]["waiting"]) == (
        False,
        True,
    )
    assert AuditEvent.objects.count() == recorded
    # Once the run executes, it is running and nothing waits behind it.
    claim(SourceRefreshRequest.objects.get())
    recorded = AuditEvent.objects.count()
    code, document, _ = admin("refresh", "status", secret=reader)
    assert (document["result"]["running"], document["result"]["waiting"]) == (
        True,
        False,
    )
    assert AuditEvent.objects.count() == recorded


def test_refusals_change_nothing(admin, google):
    """Read-only scope and an ended session; no refresh, no event."""
    _, secret, row = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document, _ = start(admin, reader, uuid4())
    assert code == 1 and document["error"]["code"] == "denied"
    actor = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, document, _ = start(admin, secret, uuid4())
    assert code == 5 and document["error"]["code"] == "session_ended"
    code, document, _ = admin("refresh", "status", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert not SourceRefreshCommand.objects.exists()
    assert not events()


def test_a_request_while_a_refresh_runs_follows_it(admin, google):
    """A running refresh is not joined: one full refresh is queued behind it."""
    _, secret, _ = paired(admin.service)
    code, first, _ = start(admin, secret)
    assert code == 0, first
    claim(SourceRefreshRequest.objects.get())
    code, later, _ = start(admin, secret)
    assert code == 0 and later["result"]["created"] is True, later
    root = later["result"]["refresh"]["task_root_id"]
    assert root != first["result"]["refresh"]["task_root_id"]
    # Another new key while that one waits joins it, as on the page.
    code, joined, _ = start(admin, secret)
    assert joined["result"]["refresh"]["task_root_id"] == root
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 2


def test_a_restore_under_review_holds_both_commands(admin, google):
    """The page answers 503 during a restore review: exit 3, nothing recorded."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .auth_builders import unguarded

    _, secret, _ = paired(admin.service)
    with unguarded():
        SystemConfiguration.objects.update(restore_review_required=True)
    for argv in (("refresh", "start"), ("refresh", "status")):
        code, document, _ = admin(*argv, secret=secret)
        assert code == 3 and document["error"]["code"] == "unavailable", argv
    assert not SourceRefreshCommand.objects.exists() and not events()


def test_a_key_bound_to_another_refresh_is_invalid(admin, google):
    """A key the scheduler used for its own refresh is refused, nothing added."""
    from parishkit.stewardship.source.requests import request_refresh

    _, secret, _ = paired(admin.service)
    key = uuid4()
    request_refresh(
        command_id=key,
        cause="delta",
        actor_id=None,
        correlation_id=uuid4(),
        authorize=lambda scope: True,
    )
    code, document, _ = start(admin, secret, key)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert SourceRefreshCommand.objects.count() == 1 and not events()


def test_one_request_records_the_sessions_activity_once(admin, google, monkeypatch):
    """The scope's rechecks under the work lock are read-only: one write."""
    from parishkit.stewardship.accounts import sessions

    _, secret, _ = paired(admin.service)
    real = sessions.authenticated_admin
    writes = []

    def counted(caller, **options):
        """Count the reads that record activity."""
        if options.get("activity") and not options.get("read_only"):
            writes.append(True)
        return real(caller, **options)

    monkeypatch.setattr(sessions, "authenticated_admin", counted)
    assert start(admin, secret)[0] == 0
    assert writes == [True]
