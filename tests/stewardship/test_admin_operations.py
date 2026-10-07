"""The operations commands' documents (ADM-11 PR 9).

Pure tests: the golden documents of ``task retry`` and the delivery
commands, built through the projections the commands use, with an exact
allowlist of member names (no recipient, address, DUID or note text at any
depth); which of the page's retries a task type selects; and the exit-6
document naming the request key. The commands against a real database are
in database/test_admin_task_retry_cli_postgresql.py and
database/test_admin_delivery_cli_postgresql.py.
"""

import io
import json
from datetime import UTC, datetime
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
MESSAGE = UUID("00000000-0000-4000-8000-000000000010")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000001")
REFUSAL = UUID("00000000-0000-4000-8000-000000000011")
SNAPSHOT = UUID("00000000-0000-4000-8000-000000000012")
NOW = datetime(2054, 10, 5, 14, tzinfo=UTC)
WINDOW = SimpleNamespace(page=1, size=25)
# A message as delivery_metadata.FIELDS reads it, recipient included: the
# projection must drop the Family, its DUID and the semantic key.
MESSAGE_ROW = {
    "id": MESSAGE,
    "campaign_id": CAMPAIGN,
    "family_id": UUID("00000000-0000-4000-8000-000000000013"),
    "family__family_duid": 4242,
    "semantic_key": "initial:4242",
    "purpose": "receipt",
    "mode": "production",
    "state": "permanent_failure",
    "version": 3,
    "attempt": 2,
    "task_id": RUN,
    "created_at": NOW,
    "updated_at": NOW,
    "finished_at": None,
}
DELIVERY = {
    "id": str(MESSAGE),
    "campaign_id": str(CAMPAIGN),
    "purpose": "receipt",
    "mode": "production",
    "state": "permanent_failure",
    "version": 3,
    "attempt": 2,
    "task_id": str(RUN),
    "created_at": NOW.isoformat(),
    "updated_at": NOW.isoformat(),
    "finished_at": None,
}


def task_retry():
    """A retry that created its run."""
    status = TaskStatus(
        RUN, ROOT, "family_mail_prepare", None, "queued", 1, 0, 0, None, ROOT, 1
    )
    return admin_operations.task_retry_model(status, created=True, request_key=KEY)


def delivery_list():
    """One page of Outgoing mail with one message."""
    return admin_operations.delivery_list_model(
        {
            "values": {"state": "all", "q": "", "sort": "-created"},
            "window": WINDOW,
            "rows": [MESSAGE_ROW],
            "has_next": False,
            "total": (1, False),
            "send": None,
        }
    )


def delivery_show():
    """One delivery with an event and a note (whose text is not shown)."""
    return admin_operations.delivery_show_model(
        {
            "delivery": MESSAGE_ROW,
            "events": [
                {
                    "created_at": NOW,
                    "version": 3,
                    "state": "permanent_failure",
                    "action": "fail",
                    "attempt": 2,
                    "reason": "rejected",
                }
            ],
            "task": {"id": RUN, "state": "failed", "version": 4, "retry_sequence": 0},
            "notes": [
                {
                    "created_at": NOW,
                    "action": "note",
                    "evidence_note": "private-evidence-marker",
                }
            ],
            "window": WINDOW,
            "has_next": False,
            "actions": ["note", "retry_failed"],
            "retry_unavailable": False,
        }
    )


def delivery_refusals():
    """One unresolved refusal, by id and time only."""
    row = SimpleNamespace(
        pk=REFUSAL, created_at=NOW, address="family@example.org", family_duid=4242
    )
    return admin_operations.refusal_list_model(
        {
            "values": {"sort": "duid"},
            "window": WINDOW,
            "rows": [row],
            "has_next": False,
            "total": (1, False),
        }
    )


def delivery_refusal_show():
    """A refusal, unresolved, with the current source to verify."""
    return admin_operations.refusal_show_model(
        {
            "refusal": SimpleNamespace(
                pk=REFUSAL, created_at=NOW, address="family@example.org"
            ),
            "resolved": None,
            "source": SimpleNamespace(snapshot_id=SNAPSHOT, generation=7),
            "can_clear": True,
        }
    )


def delivery_resolve():
    """A retry recorded for a failed receipt."""
    receipt = SimpleNamespace(
        pk=KEY,
        message_id=MESSAGE,
        action="retry_failed",
        expected_version=3,
        previous_task_id=RUN,
        retry_task_id=ROOT,
        created_at=NOW,
        evidence_note="private-evidence-marker",
    )
    return admin_operations.delivery_resolve_model(
        receipt, created=True, request_key=KEY
    )


def delivery_resend():
    """A resend authorized for an uncertain Family email (PR 9c)."""
    receipt = SimpleNamespace(
        pk=KEY,
        message_id=MESSAGE,
        action="resend",
        expected_version=5,
        previous_task_id=RUN,
        retry_task_id=ROOT,
        created_at=NOW,
        evidence_note="private-evidence-marker",
        duplicate_acknowledged=True,
    )
    return admin_operations.delivery_resolve_model(
        receipt, created=False, request_key=KEY
    )


def delivery_refusal_clear():
    """A verified clearance of a refused address (PR 9c)."""
    receipt = SimpleNamespace(
        pk=KEY,
        refusal_id=REFUSAL,
        source_snapshot_id=SNAPSHOT,
        source_generation=7,
        created_at=NOW,
        evidence_note="private-evidence-marker",
        reason="verified_admin",
    )
    return admin_operations.refusal_clear_model(receipt, created=True, request_key=KEY)


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
GOLDEN |= {
    "delivery list": (
        delivery_list,
        {
            "state": "all",
            "send": None,
            "page": 1,
            "size": 25,
            "sort": "-created",
            "has_next": False,
            "matching": 1,
            "matching_capped": False,
            "deliveries": [DELIVERY],
        },
    ),
    "delivery show": (
        delivery_show,
        {
            "delivery": DELIVERY,
            "task": {
                "id": str(RUN),
                "state": "failed",
                "version": 4,
                "retry_sequence": 0,
            },
            "actions": ["note", "retry_failed"],
            "retry_unavailable": False,
            "page": 1,
            "size": 25,
            "has_next": False,
            "events": [
                {
                    "version": 3,
                    "at": NOW.isoformat(),
                    "state": "permanent_failure",
                    "action": "fail",
                    "attempt": 2,
                    "result": "rejected",
                }
            ],
            "notes": [{"created_at": NOW.isoformat(), "action": "note"}],
        },
    ),
    "delivery refusals": (
        delivery_refusals,
        {
            "page": 1,
            "size": 25,
            "sort": "duid",
            "has_next": False,
            "matching": 1,
            "matching_capped": False,
            "refusals": [{"id": str(REFUSAL), "created_at": NOW.isoformat()}],
        },
    ),
    "delivery refusal-show": (
        delivery_refusal_show,
        {
            "id": str(REFUSAL),
            "created_at": NOW.isoformat(),
            "resolved": None,
            "source": {"snapshot_id": str(SNAPSHOT), "generation": 7},
            "can_clear": True,
        },
    ),
    "delivery resolve": (
        delivery_resolve,
        {
            "created": True,
            "request_key": str(KEY),
            "resolution": {
                "id": str(KEY),
                "message_id": str(MESSAGE),
                "action": "retry_failed",
                "expected_version": 3,
                "previous_task_id": str(RUN),
                "retry_task_id": str(ROOT),
                "created_at": NOW.isoformat(),
            },
        },
    ),
    "delivery resend": (
        delivery_resend,
        {
            "created": False,
            "request_key": str(KEY),
            "resolution": {
                "id": str(KEY),
                "message_id": str(MESSAGE),
                "action": "resend",
                "expected_version": 5,
                "previous_task_id": str(RUN),
                "retry_task_id": str(ROOT),
                "created_at": NOW.isoformat(),
            },
        },
    ),
    "delivery refusal-clear": (
        delivery_refusal_clear,
        {
            "created": True,
            "request_key": str(KEY),
            "resolution": {
                "id": str(KEY),
                "refusal_id": str(REFUSAL),
                "source_snapshot_id": str(SNAPSHOT),
                "source_generation": 7,
                "created_at": NOW.isoformat(),
            },
        },
    ),
}
DELIVERY_MEMBERS = {
    "id",
    "campaign_id",
    "purpose",
    "mode",
    "state",
    "version",
    "attempt",
    "task_id",
    "created_at",
    "updated_at",
    "finished_at",
}
PAGE_MEMBERS = {"page", "size", "sort", "has_next", "matching", "matching_capped"}
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
    "delivery list": {"state", "send", "deliveries"} | PAGE_MEMBERS | DELIVERY_MEMBERS,
    "delivery show": {
        "delivery",
        "task",
        "retry_sequence",
        "actions",
        "retry_unavailable",
        "page",
        "size",
        "has_next",
        "events",
        "at",
        "action",
        "result",
        "notes",
    }
    | DELIVERY_MEMBERS,
    "delivery refusals": {"refusals", "id", "created_at"} | PAGE_MEMBERS,
    "delivery refusal-show": {
        "id",
        "created_at",
        "resolved",
        "source",
        "snapshot_id",
        "generation",
        "can_clear",
    },
    "delivery resolve": {
        "created",
        "request_key",
        "resolution",
        "id",
        "message_id",
        "action",
        "expected_version",
        "previous_task_id",
        "retry_task_id",
        "created_at",
    },
}
ALLOWED["delivery resend"] = ALLOWED["delivery resolve"]
ALLOWED["delivery refusal-clear"] = {
    "created",
    "request_key",
    "resolution",
    "id",
    "refusal_id",
    "source_snapshot_id",
    "source_generation",
    "created_at",
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
    text = json.dumps(document)
    assert "@" not in text and "4242" not in text and "private-evidence" not in text


def test_the_parsers_vocabularies_match_the_pages():
    """Spelled out before Django is set up; kept equal to their sources."""
    from parishkit.stewardship.jobs.delivery_metadata import STATES

    assert admin_cli.DELIVERY_STATES == STATES
    assert admin_cli.RESOLVE_ACTIONS == admin_operations.RESOLVE_ACTIONS


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


def test_delivery_show_lists_every_action_the_page_offers():
    """Each listed action has a command: resend is delivery resend (PR 9c)."""
    document = delivery_show().to_document()
    assert document["actions"] == ["note", "retry_failed"]
    offered = admin_cli.RESOLVE_ACTIONS + ("resend",)
    from parishkit.stewardship.jobs.delivery_resolution import ACTIONS

    assert set(offered) == set(ACTIONS)


class Cause(Exception):
    """A driver error carrying its SQLSTATE."""

    def __init__(self, sqlstate):
        """Keep the state."""
        super().__init__("refused")
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    "state,stale", [("23514", True), ("23505", True), ("40001", False)]
)
@pytest.mark.parametrize("command", ["task retry", "delivery resolve"])
def test_a_constraint_conflict_is_stale_as_on_the_page(
    monkeypatch, command, state, stale
):
    """A check or uniqueness refusal is stale_version; others keep their class."""
    from django.db import IntegrityError

    from parishkit.stewardship.storage import StaleRecordError

    error = IntegrityError("conflict")
    error.__cause__ = Cause(state)

    def conflict(step):
        """The step's transaction was refused by the database."""
        raise error

    monkeypatch.setattr(admin_operations, "_admit", lambda caller, service: None)
    monkeypatch.setattr(admin_operations, "_held", conflict)
    caller = SimpleNamespace(automation_session_id=KEY)
    if command == "task retry":

        def act():
            """Retry a task."""
            admin_operations.retry_task(caller, None, RUN, request_key=KEY, context={})

    else:

        def act():
            """Resolve a delivery."""
            admin_operations.resolve_delivery_command(
                caller,
                SimpleNamespace(public_origin="https://x"),
                MESSAGE,
                action="note",
                expected_version=3,
                note="n",
                request_key=KEY,
                context={},
            )

    with pytest.raises(StaleRecordError if stale else IntegrityError):
        act()
    assert admin_cli.classify(
        StaleRecordError("x") if stale else error, admitted_process=True, changed=True
    ) == ("stale_version" if stale else "unavailable")
