"""A failed Admin command line leaves one durable log entry (#617).

The command runs as it does in the web container, on the web's own SQL login
(``pk_stewardship_web``, set on every new connection), with process admission
replaced by an empty context. An unexpected failure after admission writes
one ERROR ``admin_command_failed`` row whose context holds only closed values,
never the exception's text; a refusal writes none; and a write the database
refuses never changes the command's document or exit code.
"""

import contextlib
import dataclasses
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import OperationalError

from parishkit.stewardship import admin_cli
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event, SafeJsonFormatter

from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

# The text every forced error carries; no row or log line may show it.
PRIVATE = "private-value /private/path jane@example.org"
PREAMBLE = f"pk-admin-session/1 {'A' * 43} {'f' * 64}\n".encode()


@pytest.fixture
def command(monkeypatch):
    """Run a catalog command whose handler is replaced; returns (exit, document).

    The stand-in keeps the command's catalog name and runs session-less, so
    the failure happens after admission with no caller, as a read would.
    """
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )
    monkeypatch.setattr(
        admin_cli, "ADMISSION", lambda configuration: contextlib.nullcontext(None)
    )
    monkeypatch.delenv("PARISHKIT_DEBUG_LOGGING", raising=False)

    def run(name, handler, *, changes_state=False):
        """Run ``name`` with ``handler`` in place of its own."""
        spec = dataclasses.replace(
            admin_cli.BY_NAME[name],
            handler=handler,
            scope="none",
            changes_state=changes_state,
        )
        monkeypatch.setitem(admin_cli.BY_NAME, name, spec)
        out = io.StringIO()
        code = admin_cli.run(
            argparse_namespace(name),
            stdin=io.BytesIO(PREAMBLE),
            stdout=out,
            stderr=io.StringIO(),
        )
        lines = out.getvalue().splitlines()
        assert len(lines) == 1, lines
        return code, json.loads(lines[0])

    return run


def argparse_namespace(name):
    """The parsed arguments ``run`` reads for a stand-in command."""
    return SimpleNamespace(command_name=name, config=Path("web.yaml"))


def broken(args, preamble, runtime, context):
    """A handler that fails with an error nothing classifies."""
    raise RuntimeError(PRIVATE)


def entries():
    """Every ``admin_command_failed`` row, read as the schema owner."""
    return list(OperationalLog.objects.filter(event="admin_command_failed"))


def test_an_unexpected_failure_writes_one_closed_entry_as_web(command):
    """One ERROR row under the document's correlation id; no exception text."""
    with task_login(ServiceRole.WEB, reconnect=True):
        code, document = command("status", broken)
    assert code == 3 and document["error"]["code"] == "internal"
    [row] = entries()
    assert (row.level, row.schema, row.actor_id) == ("ERROR", "failure", None)
    assert str(row.correlation_id) == document["correlation_id"]
    assert row.context == {
        "failure": "admin_command",
        "failure_kind": "unexpected_failure",
        "command": "status",
    }
    stored = json.dumps(
        [str(row.correlation_id), row.event, row.level, row.schema, row.context]
    )
    assert "private" not in stored and "@" not in stored


def test_an_unknown_outcome_says_so(command):
    """A change that may have committed records the outcome-unknown word."""

    def committed(args, preamble, runtime, context):
        """Commit, then fail."""
        context["committed"] = True
        raise OperationalError(PRIVATE)

    with task_login(ServiceRole.WEB, reconnect=True):
        code, document = command("export create", committed, changes_state=True)
    assert code == 6 and document["error"]["code"] == "outcome_unknown"
    [row] = entries()
    assert row.context == {
        "failure": "admin_command_outcome_unknown",
        "failure_kind": "database_unavailable",
        "command": "export create",
    }


def test_a_refusal_writes_no_entry(command):
    """Refusals and outages have their own codes; only the unexpected is kept."""

    def refused(args, preamble, runtime, context):
        """A domain refusal."""
        raise PermissionError(PRIVATE)

    with task_login(ServiceRole.WEB, reconnect=True):
        code, document = command("status", refused)
    assert code == 1 and document["error"]["code"] == "denied"
    assert entries() == []


def test_a_refused_write_never_masks_the_exit_code(command, caplog):
    """A login the writer refuses loses the row, not the command's outcome.

    The worker login may not write this entry, so the database refuses it;
    the document and exit code are those of the original failure, and one
    WARNING line says the entry was lost, with its category and no text.
    """
    with (
        caplog.at_level(logging.DEBUG),
        task_login(ServiceRole.WORKER, reconnect=True),
    ):
        code, document = command("status", broken)
    assert code == 3
    assert document["error"] == {
        "code": "internal",
        "message": admin_cli.MESSAGES["internal"],
    }
    assert entries() == []
    lines = [
        json.loads(SafeJsonFormatter().format(record))
        for record in caplog.records
        if isinstance(record.msg, Event)
    ]
    assert all(PRIVATE not in json.dumps(line) for line in lines)
    lost = [line for line in lines if line["message"] == "admin_command_failed"]
    assert [line["level"] for line in lost] == ["WARNING"]
    assert lost[0]["extra"]["failure_kind"] == "database_write_refused"
