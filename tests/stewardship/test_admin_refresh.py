"""The source refresh commands' documents and refusals (ADM-11 PR 6a).

Pure tests: the golden documents of ``refresh start`` and ``refresh status``,
built through the projections the commands use, with exact allowlists of
member names; how ``refresh start`` maps the page's refusals; and the exit-6
document naming the request key. The commands against a real database are
in database/test_admin_refresh_cli_postgresql.py.
"""

import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_refresh
from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus

from .test_admin_reads import FORBIDDEN, members

NOW = datetime(2054, 10, 6, 15, 30, tzinfo=UTC)
KEY = UUID("00000000-0000-4000-8000-00000000000f")
REQUEST = UUID("00000000-0000-4000-8000-000000000010")
ROOT = UUID("00000000-0000-4000-8000-000000000002")
SOURCE = {
    "full_succeeded_at": (NOW - timedelta(hours=1)).isoformat(),
    "full_failed_at": None,
    "full_failed_task_id": None,
    "full_running": False,
    "delta_succeeded_at": None,
    "delta_failed_at": None,
    "frequency": "daily",
    "next_full_at": NOW.isoformat(),
    "delta_refresh": None,
    "full_started_at": (NOW - timedelta(hours=1, minutes=7)).isoformat(),
    "data_as_of": (NOW - timedelta(hours=1, minutes=7)).isoformat(),
    "connection": "working",
    "connection_at": (NOW - timedelta(minutes=5)).isoformat(),
    "overdue_full_at": None,
    "out_of_date": False,
    "late_minutes": None,
    "held_for_send": False,
    "resume_at": None,
    "catching_up": False,
}


def refresh_status():
    """A page read with a full refresh waiting behind a running one."""
    values = {
        "pending": {"running": True, "waiting": True},
        "lateness_minutes": 90,
        "full_refresh": FullRefreshStatus(
            NOW - timedelta(hours=1),
            None,
            False,
            None,
            None,
            None,
            "daily",
            NOW,
            full_started_at=NOW - timedelta(hours=1, minutes=7),
            data_as_of=NOW - timedelta(hours=1, minutes=7),
            connection=Connection("working", NOW - timedelta(minutes=5)),
        ),
    }
    return admin_refresh.refresh_status_model(values, NOW)


def refresh_start():
    """A new key's refresh request."""
    return admin_refresh.RefreshStart(
        created=True,
        request_key=KEY,
        refresh={"command_id": KEY, "request_id": REQUEST, "task_root_id": ROOT},
    )


GOLDEN = {
    "refresh status": (
        refresh_status,
        {
            "as_of": NOW.isoformat(),
            "running": True,
            "waiting": True,
            "lateness_minutes": 90,
            "source": SOURCE,
        },
    ),
    "refresh start": (
        refresh_start,
        {
            "created": True,
            "request_key": str(KEY),
            "refresh": {
                "command_id": str(KEY),
                "request_id": str(REQUEST),
                "task_root_id": str(ROOT),
            },
        },
    ),
}
ALLOWED = {
    "refresh status": {"as_of", "running", "waiting", "lateness_minutes", "source"}
    | set(SOURCE),
    "refresh start": {
        "created",
        "request_key",
        "refresh",
        "command_id",
        "request_id",
        "task_root_id",
    },
}


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the projection the command uses."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_documents_members_are_exactly_its_allowlist(command):
    """Members at any depth equal the allowlist; none is personal data."""
    document = GOLDEN[command][0]().to_document()
    assert set(members(document)) == ALLOWED[command], command
    for name in ALLOWED[command]:
        assert FORBIDDEN.search(name) is None, (command, name)


def test_every_refresh_command_has_a_golden_document():
    """The PR 6 commands, and the catalog lists each model's fields."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    assert {spec.name for spec in admin_cli.COMMANDS if spec.pr == 6} == set(GOLDEN)
    for command, (build, _) in GOLDEN.items():
        assert entries[command]["result_fields"] == list(build().field_names())


def test_status_and_refresh_status_share_the_refresh_facts():
    """``status``'s source is ``refreshed_at`` plus the same refresh facts."""
    assert set(GOLDEN["refresh status"][1]["source"]) == set(SOURCE)
    assert list(admin_refresh.refresh_fields(refresh_status_values())) == list(SOURCE)


def refresh_status_values():
    """The page's full refresh facts alone."""
    return FullRefreshStatus(None, None, False)


@pytest.fixture
def scoped(monkeypatch):
    """``start_refresh`` with its admission, scope and records stubbed.

    Returns the list of recorded events and a setter for the page's request.
    """
    from parishkit.stewardship.accounts import refresh_views
    from parishkit.stewardship.audit import services
    from parishkit.stewardship.jobs import task_retries
    from parishkit.stewardship.source import refresh_models

    actor = SimpleNamespace(identity=UUID(int=1))
    monkeypatch.setattr(admin_refresh, "_admit_start", lambda *a, **k: actor)
    monkeypatch.setattr(admin_refresh, "_recheck", lambda *a, **k: actor)

    @contextmanager
    def scope(caller, service, actor, *, admit):
        """No transaction or session lock."""
        yield

    monkeypatch.setattr(task_retries, "command_scope", scope)
    known = set()

    class Commands:
        """The stored refresh commands, by key."""

        class objects:  # noqa: N801 - mimics a model manager
            """``SourceRefreshCommand.objects``."""

            @staticmethod
            def filter(pk):
                """Whether the key was stored."""
                return SimpleNamespace(exists=lambda: pk in known)

    monkeypatch.setattr(refresh_models, "SourceRefreshCommand", Commands)
    events = []
    monkeypatch.setattr(
        services, "record_action", lambda action, **kwargs: events.append(action)
    )
    state = {}

    def request(caller, service, actor, key):
        """The page's request: raise the set error, or store the key."""
        if "error" in state:
            raise state["error"]
        known.add(key)
        return SimpleNamespace(command_id=key, request_id=REQUEST, task_root_id=ROOT)

    monkeypatch.setattr(refresh_views, "request_manual_refresh", request)
    return events, state


def start(context=None):
    """Run ``start_refresh`` with a stand-in caller and service."""
    caller = SimpleNamespace(automation_session_id=UUID(int=2))
    return admin_refresh.start_refresh(
        caller,
        SimpleNamespace(store=None),
        request_key=KEY,
        context={} if context is None else context,
    )


def test_a_repeated_key_returns_the_receipt_and_records_one_event(scoped):
    """The first use records the command's event; a repeat records none."""
    events, _ = scoped
    context = {}
    first = start(context)
    again = start()
    assert first.created and not again.created
    assert first.refresh == again.refresh
    assert events == ["admin_cmd_refresh_start"]
    assert context == {"request_id": str(KEY), "committed": True}


def test_a_key_bound_to_another_refresh_is_invalid(scoped):
    """The domain's bound-key refusal is ``invalid``, never an internal error."""
    from parishkit.stewardship.storage import StorageInvariantError

    events, state = scoped
    state["error"] = StorageInvariantError("bound")
    with pytest.raises(ValueError) as caught:
        start()
    assert classify(caught.value) == "invalid" and events == []


@pytest.mark.parametrize("error", ["organization", "scope", "configuration"])
def test_a_source_refusal_is_unavailable(scoped, error):
    """Another organization or scope, or none configured, is the page's 503."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.source.errors import (
        SourceOrganizationChanged,
        SourceScopeChanged,
    )

    events, state = scoped
    state["error"] = {
        "organization": SourceOrganizationChanged("changed"),
        "scope": SourceScopeChanged("changed"),
        "configuration": ConfigError("no organization"),
    }[error]
    context = {}
    with pytest.raises(Exception) as caught:
        start(context)
    assert classify(caught.value) == "unavailable" and events == []
    assert "committed" not in context


def test_a_denial_stays_denied(scoped):
    """A plain refusal of this Administrator is ``denied``."""
    events, state = scoped
    state["error"] = PermissionError("not authorized")
    with pytest.raises(PermissionError) as caught:
        start()
    assert classify(caught.value) == "denied" and events == []


def classify(error):
    """The command line's error code for ``error`` after admission."""
    return admin_cli.classify(error, admitted_process=True, changed=True)


def test_an_unknown_outcome_names_the_request_key(monkeypatch):
    """Exit 6 from a refresh request reports the key, so it can be repeated."""
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )

    class Admitted:
        """Admission stand-in; no runtime is needed."""

        def __init__(self, configuration):
            """Nothing to assemble."""

        def __enter__(self):
            """No runtime."""
            return None

        def __exit__(self, *exc):
            """Nothing to release."""
            return False

    monkeypatch.setattr(admin_cli, "ADMISSION", Admitted)
    monkeypatch.setattr(
        admin_cli,
        "admit_session",
        lambda *args: SimpleNamespace(automation_session=None, portal_session=None),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.automation_sessions.close_command_session",
        lambda portal_session: None,
    )
    keys = []

    def lost(caller, service, *, request_key, context):
        """The request is written; then the commit's outcome is lost."""
        keys.append(request_key)
        context["request_id"] = str(request_key)
        context["committed"] = True
        raise RuntimeError("connection lost at commit")

    monkeypatch.setattr(admin_refresh, "start_refresh", lost)
    out, err = io.StringIO(), io.StringIO()
    code = admin_cli.main(
        ["refresh", "start", "--config", "x", "--session-stdin"],
        stdin=io.BytesIO(f"pk-admin-session/1 {'A' * 43} {'f' * 64}\n".encode()),
        stdout=out,
        stderr=err,
    )
    document = json.loads(out.getvalue())
    assert code == 6 and document["error"]["code"] == "outcome_unknown"
    # No --request-key: the one made was written before the command acted.
    [key] = keys
    assert document["error"]["request_id"] == str(key)
    assert f"--request-key {key}" in err.getvalue()


def test_the_final_rechecks_write_nothing(monkeypatch):
    """Only the first admission records activity; the rechecks are read-only."""
    from parishkit.stewardship.accounts import sessions

    calls = []

    def admin(caller, *, store, activity=False, read_only=False):
        """Record how the session is read."""
        calls.append((activity, read_only))
        return SimpleNamespace(identity=UUID(int=1), roles=())

    monkeypatch.setattr(sessions, "authenticated_admin", admin)
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.policy.allows", lambda principal, cap: True
    )
    admin_refresh._admit_start(None, None)
    admin_refresh._admit_start(None, None, final=True)
    assert calls == [(True, False), (False, True)]


@pytest.mark.parametrize(
    "message", ["Configuration is unavailable.", "A restore is under review."]
)
def test_refresh_status_during_a_restore_or_setup_is_unavailable(monkeypatch, message):
    """The page's read refuses (503 on the page): exit 3, never ``invalid``."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts import refresh_views
    from parishkit.stewardship.campaigns import work_locks

    actor = SimpleNamespace(identity=UUID(int=1))
    monkeypatch.setattr(admin_refresh, "_admit", lambda *a: actor)
    monkeypatch.setattr(admin_refresh, "_recheck", lambda *a: actor)

    @contextmanager
    def snapshot():
        """No database snapshot."""
        yield

    monkeypatch.setattr(work_locks, "read_transaction", snapshot)

    def refused(service):
        """``editable_configuration``'s refusal."""
        raise ConfigError(message)

    monkeypatch.setattr(refresh_views, "read_refresh_page", refused)
    with pytest.raises(admin_refresh.Unavailable) as caught:
        admin_refresh.read_refresh_status(None, SimpleNamespace(store=None))
    assert classify(caught.value) == "unavailable"


def test_a_request_key_must_be_a_version_4_uuid():
    """The page's key is a version 4 UUID; anything else is a usage error."""
    code, document = None, None
    out, err = io.StringIO(), io.StringIO()
    code = admin_cli.main(
        [
            "refresh",
            "start",
            "--request-key",
            "00000000-0000-1000-8000-000000000001",
            "--config",
            "x",
            "--session-stdin",
        ],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=err,
    )
    document = json.loads(out.getvalue())
    assert code == 2 and document["error"]["code"] == "usage"
