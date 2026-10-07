"""The operations commands' documents (ADM-11 PR 9a).

Pure tests: the golden document of ``task retry``, built through the
projection the command uses, with an exact allowlist of member names; which
of the page's retries a task type selects; and the exit-6 document naming
the request key. The command against a real database is in
database/test_admin_task_retry_cli_postgresql.py.
"""

import io
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_operations
from parishkit.stewardship.jobs import task_retries
from parishkit.stewardship.jobs.storage import TaskStatus

from .test_admin_reads import FORBIDDEN, members

ROOT = UUID("00000000-0000-4000-8000-000000000002")
RUN = UUID("00000000-0000-4000-8000-00000000000e")
KEY = UUID("00000000-0000-4000-8000-00000000000f")


def task_retry():
    """A retry that created its run."""
    status = TaskStatus(
        RUN, ROOT, "family_mail_prepare", None, "queued", 1, 0, 0, None, ROOT, 1
    )
    return admin_operations.task_retry_model(status, created=True, request_key=KEY)


GOLDEN = {
    "task retry": (
        task_retry,
        {
            "created": True,
            "request_key": str(KEY),
            "task": {
                "id": str(RUN),
                "root_id": str(ROOT),
                "parent_id": str(ROOT),
                "retry_sequence": 1,
                "type": "family_mail_prepare",
                "state": "queued",
            },
        },
    ),
}
ALLOWED = {
    "task retry": {
        "created",
        "request_key",
        "task",
        "id",
        "root_id",
        "parent_id",
        "retry_sequence",
        "type",
        "state",
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


def test_every_operations_command_has_a_golden_document():
    """The PR 9 commands, and the catalog lists each model's fields."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    assert {spec.name for spec in admin_cli.COMMANDS if spec.pr == 9} == set(GOLDEN)
    for command, (build, _) in GOLDEN.items():
        assert entries[command]["result_fields"] == list(build().field_names())


@pytest.mark.parametrize(
    "task_type,kind",
    [
        ("family_mail_prepare", task_retries.FAMILY_PREPARATION),
        ("daily_digest_prepare", task_retries.DAILY_DIGEST),
        ("daily_digest_finalize", task_retries.DAILY_DIGEST),
        ("weekly_digest_prepare", task_retries.WEEKLY_DIGEST),
        ("weekly_digest_finalize", task_retries.WEEKLY_DIGEST),
        ("report_export_cleanup", task_retries.EXPORT_CLEANUP),
        # The page offers no retry for any other work.
        ("outbox_delivery", None),
        ("source_refresh", None),
    ],
)
def test_a_task_type_selects_the_pages_retry(task_type, kind):
    """The four retries a Background work task page offers, by task type."""
    assert task_retries.retry_kind(task_type) == kind


def test_an_unknown_outcome_names_the_request_key(monkeypatch):
    """Exit 6 from a retry reports the key, so it can be repeated."""
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

    def lost(caller, service, task_id, *, request_key, context):
        """The retry is written; then the commit's outcome is lost."""
        keys.append(request_key)
        context["request_id"] = str(request_key)
        context["committed"] = True
        raise RuntimeError("connection lost at commit")

    monkeypatch.setattr(admin_operations, "retry_task", lost)
    out, err = io.StringIO(), io.StringIO()
    code = admin_cli.main(
        ["task", "retry", str(ROOT), "--config", "x", "--session-stdin"],
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
