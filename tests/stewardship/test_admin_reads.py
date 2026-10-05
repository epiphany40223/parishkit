"""The read models of ``pk-stewardship admin`` and their documents (ADM-11 PR 3).

Pure tests: a golden document for each of the six read models, built through
the same projection functions the commands use; an exact allowlist of every
member name each document may carry, at any depth, with a personal-data
pattern no member may match; the JSON conversion of instants and
identifiers; and when a ``--watch`` stops. The commands against a real
database are in database/test_admin_status_cli_postgresql.py.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_reads
from parishkit.stewardship.jobs.send_history import ListedSend, SendKey, SendRow
from parishkit.stewardship.jobs.send_progress import SendCounts, progress
from parishkit.stewardship.jobs.task_reads import task_metadata
from parishkit.stewardship.source.refresh_status import FullRefreshStatus

from .campaign_factory import campaign as campaign_record
from .campaign_factory import schedule as schedule_record

NOW = datetime(2054, 10, 5, 14, tzinfo=UTC)
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000001")
TASK = UUID("00000000-0000-4000-8000-000000000002")
SCHEDULE = "00000000-0000-4000-8000-000000000003"
TEMPLATE = "00000000-0000-4000-8000-000000000004"
DEFINITION = UUID("00000000-0000-4000-8000-000000000005")
REVISION = UUID("00000000-0000-4000-8000-000000000006")
# Member names that would carry Family-level personal data, credentials or
# signed controls. No document may have one at any depth.
FORBIDDEN = re.compile(
    r"email|phone|address|duid|family_name|code|token|secret|digest|control"
    r"|password|cipher|substitution|label|reason"
)


def members(value):
    """Every member name in a JSON document, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from members(item)
    elif isinstance(value, list):
        for item in value:
            yield from members(item)


def iso(value):
    """An instant as documents print it."""
    return value.astimezone(UTC).isoformat()


def counts(**values):
    """A send's counts with fixed instants (finished unless overridden)."""
    base = dict(
        kind="initial",
        sent=8,
        failed=1,
        prepare_failed=0,
        uncertain=1,
        remaining=0,
        waiting=0,
        unreachable=2,
        not_needed=3,
        started_at=NOW - timedelta(minutes=30),
        last_settled_at=NOW - timedelta(minutes=10),
        recent=0,
        since=NOW - timedelta(minutes=15),
        now=NOW,
    )
    return SendCounts(**(base | values))


# The finished send above, as every send document shows it.
FINISHED_SEND = {
    "kind": "initial",
    "active": False,
    "paused": False,
    "stalled": False,
    "held": False,
    "total": 10,
    "done": 10,
    "percent": 100,
    "sent": 8,
    "failed": 1,
    "uncertain": 1,
    "remaining": 0,
    "unprepared": 0,
    "unplanned": 0,
    "waiting": 0,
    "unreachable": 2,
    "not_needed": 3,
    "rate_per_minute": 0.5,
    "started_at": iso(NOW - timedelta(minutes=30)),
    "last_settled_at": iso(NOW - timedelta(minutes=10)),
    "finished_at": iso(NOW - timedelta(minutes=10)),
    "finish_at": None,
    "minutes_left": None,
}


def status():
    """A Status built from a fixed home summary, with every section shown."""
    campaign = SimpleNamespace(
        pk=CAMPAIGN,
        state="active",
        version=7,
        delivery_paused=False,
        active_configuration=SimpleNamespace(
            name="Stewardship 2055",
            starts_at=NOW - timedelta(days=1),
            ends_at=NOW + timedelta(days=30),
        ),
    )
    data = {
        "campaign": campaign,
        "refreshed_at": NOW - timedelta(hours=1),
        "full_refresh": FullRefreshStatus(
            NOW - timedelta(hours=1), None, False, None, None, None, "daily", NOW
        ),
        # Members the page uses but the document must not copy are dropped.
        "next_mail": {
            "kind": "reminder",
            "due_at": NOW + timedelta(days=2),
            "subject": "x",
        },
        "families": {
            "active": 10,
            "eligible": 9,
            "responded": 4,
            "eligible_responded": 4,
            "participation": object(),
        },
        "unreachable": 1,
        "offsite_unset": True,
        "unfinished_keys": [{"target": "slack", "label": "x", "stopped": "y"}],
        "ministry_catalog": {
            "refreshes": [{}, {}],
            "missing": [{"name": "Choir"}],
            "retired": [],
            "campaign_id": CAMPAIGN,
        },
        "security_events": [{"target": "someone@example.org"}],
        "recent_failures": [
            {"id": TASK, "task_type": "source_refresh", "updated_at": NOW, "name": "x"}
        ],
    }
    return admin_reads.Status.build(
        SimpleNamespace(mode="production"),
        data,
        NOW,
        {
            "active": 1,
            "queued": 2,
            "running": 1,
            "retry_wait": 0,
            "abandoned": 0,
            "delivery_unknown": 0,
        },
        {"count": 3},
        4,
    )


def task_row(state="queued"):
    """A task run's columns, as task_reads reads them."""
    return SimpleNamespace(
        pk=TASK,
        root_id=TASK,
        parent_id=None,
        retry_sequence=0,
        task_type="source_refresh",
        state=state,
        action="created",
        version=1,
        attempt=0,
        initiated_by_id=None,
        phase="queued",
        progress_current=0,
        progress_total=4,
        created_at=NOW,
        updated_at=NOW,
        not_before=NOW,
        heartbeat_at=None,
        # Only a running task holds a lease.
        lease_expires_at=NOW + timedelta(minutes=1) if state == "running" else None,
    )


TASK_DOCUMENT = {
    "id": str(TASK),
    "root_id": str(TASK),
    "parent_id": None,
    "retry_sequence": 0,
    "type": "source_refresh",
    "state": "queued",
    "action": "created",
    "version": 1,
    "attempt": 0,
    "initiator_id": None,
    "progress": {"phase": "queued", "current": 0, "total": 4, "percent": 0.0},
    "created_at": iso(NOW),
    "updated_at": iso(NOW),
    "not_before": iso(NOW),
    "heartbeat_at": None,
    "lease_expires_at": None,
    "active": False,
}


def task_list():
    """One page of one queued task."""
    return admin_reads.TaskList(
        as_of=NOW,
        counts={
            "active": 0,
            "queued": 1,
            "running": 0,
            "retry_wait": 0,
            "abandoned": 0,
        },
        state="nonterminal",
        task_type=None,
        page=1,
        size=50,
        sort="-created",
        has_next=False,
        matching=1,
        matching_capped=False,
        tasks=[task_metadata(task_row(), NOW)],
    )


def task_show(state="queued"):
    """One task in ``state`` with one history event."""
    return admin_reads.TaskShow(
        as_of=NOW,
        task=task_metadata(task_row(state), NOW),
        latest_run_id=str(TASK),
        page=1,
        size=20,
        sort="-version",
        has_next=False,
        matching=1,
        matching_capped=False,
        events=[
            {
                "version": 1,
                "at": NOW,
                "action": "created",
                "state": "queued",
                "attempt": 0,
                "progress": {"phase": "queued", "current": 0, "total": 4},
            }
        ],
    )


def send_history():
    """One page holding one finished invitation."""
    row = SendRow(
        listed=ListedSend(
            key=SendKey(DEFINITION, REVISION, "production", 1),
            kind="initial",
            scheduled=NOW - timedelta(hours=1),
            number=None,
            replaced=False,
        ),
        current=True,
        earlier=False,
        send=progress(counts()),
        emails={"cancelled": 2},
    )
    return admin_reads.SendHistory(CAMPAIGN, 1, 1, 1, 25, [admin_reads.send_row(row)])


def schedule_show():
    """The applied invitation schedule of a fixed draft."""
    owner = campaign_record()
    row = schedule_record(owner["id"], date="2054-10-02", template_version=TEMPLATE)
    row["id"] = SCHEDULE
    values = owner["values"]
    return admin_reads.ScheduleShow(
        campaign_id=CAMPAIGN,
        version="f" * 64,
        editable=True,
        window={
            "start_date": values["start_date"],
            "end_date": values["end_date"],
            "timezone": values["timezone"],
        },
        schedules=[admin_reads.schedule_entry(row, values)],
    )


GOLDEN = {
    "status": (
        status,
        {
            "as_of": iso(NOW),
            "mode": "production",
            "campaign": {
                "id": str(CAMPAIGN),
                "name": "Stewardship 2055",
                "state": "active",
                "version": 7,
                "starts_at": iso(NOW - timedelta(days=1)),
                "ends_at": iso(NOW + timedelta(days=30)),
                "delivery_paused": False,
            },
            "source": {
                "refreshed_at": iso(NOW - timedelta(hours=1)),
                "full_succeeded_at": iso(NOW - timedelta(hours=1)),
                "full_failed_at": None,
                "full_failed_task_id": None,
                "full_running": False,
                "delta_succeeded_at": None,
                "delta_failed_at": None,
                "frequency": "daily",
                "next_full_at": iso(NOW),
                "delta_refresh": None,
            },
            "next_mail": {"kind": "reminder", "due_at": iso(NOW + timedelta(days=2))},
            "families": {
                "active": 10,
                "eligible": 9,
                "responded": 4,
                "eligible_responded": 4,
            },
            "unreachable_families": 1,
            "offsite_backup": {"state": "unset"},
            "unfinished_keys": [{"target": "slack"}],
            "ministry_catalog": {"refreshes": 2, "missing": 1, "retired": 0},
            "security_events": 1,
            "recent_failures": [
                {"id": str(TASK), "type": "source_refresh", "updated_at": iso(NOW)}
            ],
            "automation_notices": 4,
            "tasks": {
                "active": 1,
                "queued": 2,
                "running": 1,
                "retry_wait": 0,
                "abandoned": 0,
                "delivery_unknown": 0,
            },
            "presence": {"count": 3},
        },
    ),
    "task list": (
        task_list,
        {
            "as_of": iso(NOW),
            "counts": {
                "active": 0,
                "queued": 1,
                "running": 0,
                "retry_wait": 0,
                "abandoned": 0,
            },
            "state": "nonterminal",
            "task_type": None,
            "page": 1,
            "size": 50,
            "sort": "-created",
            "has_next": False,
            "matching": 1,
            "matching_capped": False,
            "tasks": [TASK_DOCUMENT],
        },
    ),
    "task show": (
        task_show,
        {
            "as_of": iso(NOW),
            "task": TASK_DOCUMENT,
            "latest_run_id": str(TASK),
            "page": 1,
            "size": 20,
            "sort": "-version",
            "has_next": False,
            "matching": 1,
            "matching_capped": False,
            "events": [
                {
                    "version": 1,
                    "at": iso(NOW),
                    "action": "created",
                    "state": "queued",
                    "attempt": 0,
                    "progress": {"phase": "queued", "current": 0, "total": 4},
                }
            ],
        },
    ),
    "send progress": (
        lambda: admin_reads.SendProgressRead(
            CAMPAIGN,
            "production",
            False,
            False,
            admin_reads._send_counts(progress(counts())),
        ),
        {
            "campaign_id": str(CAMPAIGN),
            "mode": "production",
            "paused": False,
            "upcoming": False,
            "send": FINISHED_SEND,
        },
    ),
    "send history": (
        send_history,
        {
            "campaign_id": str(CAMPAIGN),
            "page": 1,
            "pages": 1,
            "count": 1,
            "size": 25,
            "sends": [
                {
                    "send": f"{DEFINITION}:{REVISION}:production:1",
                    "number": None,
                    "mode": "production",
                    "cycle": 1,
                    "scheduled_at": iso(NOW - timedelta(hours=1)),
                    "replaced": False,
                    "current": True,
                    "earlier": False,
                    "live": False,
                    "cancelled": 2,
                    "minutes": 20,
                    **FINISHED_SEND,
                }
            ],
        },
    ),
    "schedule show": (
        schedule_show,
        {
            "campaign_id": str(CAMPAIGN),
            "version": "f" * 64,
            "editable": True,
            "window": {
                "start_date": "2054-10-01",
                "end_date": "2054-10-31",
                "timezone": "America/New_York",
            },
            "schedules": [
                {
                    "id": SCHEDULE,
                    "kind": "initial",
                    "date": "2054-10-02",
                    "time": "09:00:00",
                    "weekday": None,
                    "subject": "Campaign invitation",
                    "template_version": TEMPLATE,
                    "resolved": [
                        {
                            "key": "once",
                            "due_at": "2054-10-02T13:00:00+00:00",
                        }
                    ],
                    "more": False,
                }
            ],
        },
    ),
}


def status_with_backup():
    """A Status whose off-site copy exists, for the backup members."""
    from parishkit.stewardship.accounts.backup_destination import OffsiteStatus

    return admin_reads.Status.build(
        SimpleNamespace(mode="testing"),
        {
            "campaign": None,
            "refreshed_at": None,
            "full_refresh": FullRefreshStatus(None, None, False),
            "offsite": OffsiteStatus("uploaded", "Copied.", NOW, NOW, "set-2054-10-05"),
        },
        NOW,
        None,
        None,
    )


# Further documents per command, so every allowlisted member appears in
# some document and the allowlist can be checked for equality.
EXTRA = {
    "status": [
        (
            status_with_backup,
            {
                "state": "uploaded",
                "at": iso(NOW),
                "last_copy_at": iso(NOW),
                "set_name": "set-2054-10-05",
            },
        )
    ],
}

# Every member name each document may carry, at any depth: the allowlist.
# A new member must be added here deliberately, after checking it carries
# no personal data.
SEND_MEMBERS = set(FINISHED_SEND)
TASK_MEMBERS = set(TASK_DOCUMENT) | {"phase", "current", "total", "percent"}
ALLOWED = {
    "status": {
        "as_of",
        "mode",
        "campaign",
        "id",
        "name",
        "state",
        "version",
        "starts_at",
        "ends_at",
        "delivery_paused",
        "source",
        "refreshed_at",
        "full_succeeded_at",
        "full_failed_at",
        "full_failed_task_id",
        "full_running",
        "delta_succeeded_at",
        "delta_failed_at",
        "frequency",
        "next_full_at",
        "delta_refresh",
        "next_mail",
        "kind",
        "due_at",
        "families",
        "active",
        "eligible",
        "responded",
        "eligible_responded",
        "unreachable_families",
        "offsite_backup",
        "at",
        "last_copy_at",
        "set_name",
        "unfinished_keys",
        "target",
        "ministry_catalog",
        "refreshes",
        "missing",
        "retired",
        "security_events",
        "recent_failures",
        "type",
        "updated_at",
        "automation_notices",
        "tasks",
        "queued",
        "running",
        "retry_wait",
        "abandoned",
        "delivery_unknown",
        "presence",
        "count",
    },
    "task list": {
        "as_of",
        "counts",
        "active",
        "queued",
        "running",
        "retry_wait",
        "abandoned",
        "state",
        "task_type",
        "page",
        "size",
        "sort",
        "has_next",
        "matching",
        "matching_capped",
        "tasks",
        *TASK_MEMBERS,
    },
    "task show": {
        "as_of",
        "task",
        "latest_run_id",
        "page",
        "size",
        "sort",
        "has_next",
        "matching",
        "matching_capped",
        "events",
        "at",
        *TASK_MEMBERS,
    },
    "send progress": {"campaign_id", "mode", "paused", "upcoming", "send"}
    | SEND_MEMBERS,
    "send history": {
        "campaign_id",
        "page",
        "pages",
        "count",
        "size",
        "sends",
        "send",
        "number",
        "mode",
        "cycle",
        "scheduled_at",
        "replaced",
        "current",
        "earlier",
        "live",
        "cancelled",
        "minutes",
    }
    | SEND_MEMBERS,
    "schedule show": {
        "campaign_id",
        "version",
        "editable",
        "window",
        "start_date",
        "end_date",
        "timezone",
        "schedules",
        "id",
        "kind",
        "date",
        "time",
        "weekday",
        "subject",
        "template_version",
        "resolved",
        "key",
        "due_at",
        "more",
    },
}


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the same projection functions the commands use."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_documents_members_are_exactly_its_allowlist(command):
    """Across its documents, a command's members at any depth equal its allowlist.

    Equality, not inclusion: an allowlisted member that no document shows
    would go unchecked, so every one appears in a golden or extra document.
    No allowlisted name matches the personal-data pattern; ``name`` (the
    campaign's) and ``subject`` (a schedule's email subject) are
    configuration, never a Family's.
    """
    documents = [GOLDEN[command][0]().to_document()]
    for build, expected in EXTRA.get(command, []):
        document = build().to_document()
        assert document["offsite_backup"] == expected
        documents.append(document)
    found = {name for document in documents for name in members(document)}
    assert found == ALLOWED[command], command
    for name in ALLOWED[command]:
        assert FORBIDDEN.search(name) is None, (command, name)
    text = json.dumps(documents)
    assert "@" not in text and "Choir" not in text


def test_every_command_has_a_golden_document_and_an_allowlist():
    """The six read commands, no more and no fewer."""
    reads = {spec.name for spec in admin_cli.COMMANDS if spec.pr == 3}
    assert reads == set(GOLDEN) == set(ALLOWED)


def test_sections_the_reader_cannot_see_are_none():
    """A summary without a section (no capability) leaves its member None."""
    model = admin_reads.Status.build(
        SimpleNamespace(mode="testing"),
        {
            "campaign": None,
            "refreshed_at": None,
            "full_refresh": FullRefreshStatus(None, None, False),
        },
        NOW,
        None,
        None,
    )
    document = model.to_document()
    for name in (
        "campaign",
        "next_mail",
        "families",
        "offsite_backup",
        "tasks",
        "security_events",
        "automation_notices",
        "presence",
    ):
        assert document[name] is None, name


def test_when_a_send_watch_stops():
    """A running send keeps a watch going; a finished one or none stops it."""
    running = progress(counts(remaining=5, recent=5, last_settled_at=None))
    model = admin_reads.SendProgressRead(
        CAMPAIGN, "production", False, False, admin_reads._send_counts(running)
    )
    assert model.to_document()["send"]["rate_per_minute"] == float(Decimal("0.3"))
    assert not model.terminal
    finished = GOLDEN["send progress"][0]()
    assert finished.terminal
    assert admin_reads.SendProgressRead(None, "testing", False, False, None).terminal
    # A Production send about to start keeps the watch going.
    soon = admin_reads.SendProgressRead(None, "production", False, True, None)
    assert not soon.terminal


def test_task_show_stops_once_the_task_is_terminal():
    """Queued, running and waiting tasks are followed; finished ones are not."""
    assert not task_show("running").terminal and not task_show("retry_wait").terminal
    assert task_show("succeeded").terminal and task_show("failed").terminal
    assert admin_reads.TaskList.terminal


def test_catalog_result_fields_are_each_models_fields():
    """The catalog lists exactly the projection's top-level members."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    for command, (build, _) in GOLDEN.items():
        assert entries[command]["result_fields"] == list(build().field_names())


@pytest.mark.parametrize(
    "value,expected",
    [
        (datetime(2054, 10, 5, 9, tzinfo=UTC), "2054-10-05T09:00:00+00:00"),
        (date(2054, 10, 5), "2054-10-05"),
        (TASK, str(TASK)),
        (Decimal("12.5"), 12.5),
        ((1, [TASK]), [1, [str(TASK)]]),
        ({"a": None}, {"a": None}),
    ],
)
def test_plain_converts_instants_and_identifiers(value, expected):
    """Instants in UTC ISO 8601, UUIDs as strings, containers member by member."""
    assert admin_reads.plain(value) == expected


def test_a_local_instant_is_printed_in_utc():
    """An instant from another zone is converted, never printed as local time."""
    from zoneinfo import ZoneInfo

    local = datetime(2054, 10, 5, 9, tzinfo=ZoneInfo("America/New_York"))
    assert admin_reads.plain(local) == "2054-10-05T13:00:00+00:00"
