"""The schedule change commands' documents and change input (ADM-11 PR 4).

Pure tests: the golden documents of ``schedule preview``, ``schedule
confirm`` and ``config request show``, built through the projection functions
the commands use, with an exact allowlist of member names; the shape checks
of the change document; and how a document becomes the Mail schedules page's
own posted form. The commands against a real database are in
database/test_admin_schedule_cli_postgresql.py.
"""

import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship import admin_changes, admin_cli, admin_reads
from parishkit.stewardship.accounts.configuration_requests import RequestStatus
from parishkit.stewardship.accounts.schedule_changes import describe
from parishkit.stewardship.storage import StaleRecordError

from .campaign_factory import campaign as campaign_record
from .campaign_factory import financial
from .campaign_factory import schedule as schedule_record
from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000001")
SCHEDULE = "00000000-0000-4000-8000-000000000003"
TEMPLATE = "00000000-0000-4000-8000-000000000004"
REQUEST = UUID("00000000-0000-4000-8000-00000000000c")
CANDIDATE = UUID("00000000-0000-4000-8000-00000000000d")
# The factory campaign's own dates and zone, never restated here.
FACTORY = campaign_record()["values"]
START = date.fromisoformat(FACTORY["start_date"])
END = date.fromisoformat(FACTORY["end_date"])
ZONE = FACTORY["timezone"]
SEND_DAY = (START + timedelta(days=1)).isoformat()
REMINDER_DAY = (START + timedelta(days=19)).isoformat()
EARLIER_END = (END - timedelta(days=11)).isoformat()


def due(day, clock):
    """A civil send time in the factory's zone, as documents print it in UTC."""
    local = datetime.combine(date.fromisoformat(day), time.fromisoformat(clock))
    return local.replace(tzinfo=ZoneInfo(ZONE)).astimezone(UTC).isoformat()


def preview_context(*, blocking=0):
    """``build_preview``'s review of moving the invitation from 9:00 to 10:30."""
    owner = campaign_record()
    values = owner["values"]
    row = schedule_record(owner["id"], date=SEND_DAY, template_version=TEMPLATE)
    after = row["values"] | {"time": "10:30:00"}
    return {
        "campaign": SimpleNamespace(pk=CAMPAIGN),
        "changes": [
            {
                "id": SCHEDULE,
                "operation": "update",
                "before": describe(row["values"], values),
                "after": describe(after, values),
                "impact": {
                    "revision": "x",
                    "versions": 3,
                    "occurrences": 1,
                    "cancellable": 1,
                    "blocking": blocking,
                },
                "label": "Initial invitation",
            }
        ],
        "window_changes": {},
        "before_window": values,
        "after_window": values,
        "blocking": blocking,
        "preview": None if blocking else "signed:preview",
    }


def schedule_preview():
    """The review the command prints."""
    return admin_changes.schedule_preview_model(preview_context(), "f" * 64)


def receipt(state="staged", failure_code=""):
    """A configuration request's status as ``request_status`` returns it."""
    return RequestStatus(REQUEST, state, 1, "e" * 64, CANDIDATE, "d" * 64, failure_code)


def schedule_confirm():
    """A confirmation that created its request."""
    return admin_changes.ScheduleConfirm(
        created=True, request=admin_reads.config_request(receipt()).to_document()
    )


def config_request():
    """A staged request."""
    return admin_reads.config_request(receipt())


def side(time, due_at):
    """One side of the golden change."""
    return {
        "kind": "initial",
        "date": SEND_DAY,
        "time": time,
        "weekday": None,
        "template_version": TEMPLATE,
        "subject": "Campaign invitation",
        "timezone": ZONE,
        "resolved": [{"key": "once", "due_at": due_at}],
        "more": False,
    }


WINDOW = {
    "start_date": FACTORY["start_date"],
    "end_date": FACTORY["end_date"],
    "timezone": ZONE,
}
REQUEST_DOCUMENT = {
    "request_id": str(REQUEST),
    "state": "staged",
    "sequence": 1,
    "failure": None,
    "candidate_version_id": str(CANDIDATE),
    "applied_version_id": None,
}
GOLDEN = {
    "schedule preview": (
        schedule_preview,
        {
            "campaign_id": str(CAMPAIGN),
            "version": "f" * 64,
            "window": {"before": WINDOW, "after": WINDOW, "changed": []},
            "changes": [
                {
                    "id": SCHEDULE,
                    "operation": "update",
                    "kind": "initial",
                    "before": side("09:00:00", due(SEND_DAY, "09:00:00")),
                    "after": side("10:30:00", due(SEND_DAY, "10:30:00")),
                    "impact": {
                        "delivered": 0,
                        "cancellable": 1,
                        "failed": 0,
                        "blocking": 0,
                        "occurrences": 1,
                        "outboxes": 0,
                    },
                }
            ],
            "blocking": 0,
            "preview": {"token": "signed:preview"},
        },
    ),
    "schedule confirm": (
        schedule_confirm,
        {"created": True, "request": REQUEST_DOCUMENT},
    ),
    "config request show": (config_request, REQUEST_DOCUMENT),
}
IMPACT = {"delivered", "cancellable", "failed", "blocking", "occurrences", "outboxes"}
SIDE = {
    "kind",
    "date",
    "time",
    "weekday",
    "template_version",
    "subject",
    "timezone",
    "resolved",
    "key",
    "due_at",
    "more",
}
ALLOWED = {
    "schedule preview": {
        "campaign_id",
        "version",
        "window",
        "before",
        "after",
        "changed",
        "start_date",
        "end_date",
        "changes",
        "id",
        "operation",
        "impact",
        "blocking",
        "preview",
        # The signed binding the specification has the preview print; the
        # page embeds the same value in its form.
        "token",
    }
    | SIDE
    | IMPACT,
    "schedule confirm": {"created", "request"} | set(REQUEST_DOCUMENT),
    "config request show": set(REQUEST_DOCUMENT),
}


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the same projection functions the commands use."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_documents_members_are_exactly_its_allowlist(command):
    """Members at any depth equal the allowlist; none is personal data.

    ``token`` is the one exception to the personal-data pattern: it is the
    signed preview the specification has ``schedule preview`` print.
    """
    document = GOLDEN[command][0]().to_document()
    assert set(members(document)) == ALLOWED[command], command
    for name in ALLOWED[command] - {"token"}:
        assert FORBIDDEN.search(name) is None, (command, name)
    assert "@" not in json.dumps(document)


def test_every_change_command_has_a_golden_document():
    """The three PR 4 commands, and the catalog lists each model's fields."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    assert {spec.name for spec in admin_cli.COMMANDS if spec.pr == 4} == set(GOLDEN)
    for command, (build, _) in GOLDEN.items():
        assert entries[command]["result_fields"] == list(build().field_names())


def test_blocking_work_leaves_nothing_to_confirm():
    """As on the page: while work blocks the change, there is no token."""
    model = admin_changes.schedule_preview_model(preview_context(blocking=2), "f" * 64)
    document = model.to_document()
    assert document["blocking"] == 2 and document["preview"] is None
    assert document["changes"][0]["impact"]["blocking"] == 2


def test_a_request_watch_stops_once_the_request_is_settled():
    """Staged and installing requests are followed; settled ones are not."""
    for state in ("staged", "validating", "prepared", "yaml_activated"):
        assert not admin_reads.config_request(receipt(state)).terminal, state
    for state in ("applied", "cancelled"):
        assert admin_reads.config_request(receipt(state)).terminal, state
    failed = admin_reads.config_request(receipt("failed", "stale_base"))
    assert failed.terminal and failed.to_document()["failure"] == "stale_base"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"other": 1}',
        '{"window": []}',
        '{"window": {"name": "x"}}',
        '{"window": {"overlap_confirmed": "yes"}}',
        '{"window": {"start_date": 1}}',
        # A saved schedule's mail type never changes; a new one needs fields.
        f'{{"schedules": [{{"id": "{SCHEDULE}", "kind": "reminder"}}]}}',
        '{"schedules": [{}]}',
        '{"schedules": {}}',
        '{"schedules": [1]}',
        '{"schedules": [{"label": "x"}]}',
        '{"schedules": [{"weekday": true}]}',
        '{"schedules": [{"weekday": "3"}]}',
        '{"schedules": [{"time": null}]}',
        '{"schedules": [{"id": 7}]}',
        f'{{"schedules": [{{"id": "{SCHEDULE}"}}, {{"id": "{SCHEDULE}"}}]}}',
        '{"schedules": [{"delete": true}]}',
        f'{{"schedules": [{{"id": "{SCHEDULE}", "delete": false}}]}}',
        f'{{"schedules": [{{"id": "{SCHEDULE}", "delete": true, "time": "x"}}]}}',
        '{"schedules": [' + ",".join(["{}"] * 102) + "]}",
        " " * (admin_changes.CHANGES_LIMIT + 1),
    ],
)
def test_malformed_change_documents_are_invalid(text):
    """Shape problems are refused before any form is bound (exit 1, invalid)."""
    with pytest.raises(ValueError):
        admin_changes.parse_changes(text)


def test_an_empty_document_changes_nothing_and_is_well_formed():
    """Both members are optional; the page then finds no change to review."""
    assert admin_changes.parse_changes("{}") == {"window": {}, "schedules": []}


def saved(owner, **values):
    """A saved schedule record of ``owner``."""
    return schedule_record(owner["id"], template_version=TEMPLATE, **values)


def bind(document, previous, campaign, *, editable=True):
    """The posted form for a change document."""
    return admin_changes.form_data(
        admin_changes.parse_changes(json.dumps(document)),
        previous,
        campaign,
        editable=editable,
        base_digest="f" * 64,
    )


def test_named_schedules_keep_what_the_document_leaves_out():
    """Saved rows in the page's order, merged; omitted ones unchanged; new last."""
    owner = campaign_record()
    reminder = saved(owner, kind="reminder", date=REMINDER_DAY)
    initial = saved(owner, date=SEND_DAY)
    document = {
        "schedules": [
            {"id": reminder["id"], "delete": True},
            {"kind": "daily_digest", "time": "07:00:00", "template_version": TEMPLATE},
            {"id": initial["id"], "time": "10:30:00"},
        ]
    }
    data, identifiers = bind(document, [reminder, initial], owner["values"])
    # The invitation sends first, so the page lists it first.
    assert identifiers == [initial["id"], reminder["id"], "new0"]
    assert data["action"] == "preview" and data["base_digest"] == "f" * 64
    assert data["schedules-TOTAL_FORMS"] == "3"
    assert data["schedules-INITIAL_FORMS"] == "2"
    assert data["schedules-0-id"] == initial["id"]
    assert data["schedules-0-time"] == "10:30:00"
    assert data["schedules-0-date"] == SEND_DAY
    assert data["schedules-0-weekday"] == ""
    assert "schedules-0-DELETE" not in data
    assert data["schedules-1-DELETE"] == "on"
    assert data["schedules-1-date"] == REMINDER_DAY
    assert "schedules-2-id" not in data
    assert data["schedules-2-kind"] == "daily_digest"
    assert data["schedules-2-date"] == ""
    # The window is posted as the page posts it, unchanged.
    assert data["window-end_date"] == FACTORY["end_date"]
    assert "window-overlap_confirmed" not in data


def test_the_window_is_posted_only_while_the_dates_may_change():
    """Locked dates: no window fields; a changed window is the page's refusal."""
    owner = campaign_record()
    data, _ = bind({}, [], owner["values"], editable=False)
    assert not any(name.startswith("window-") for name in data)
    same = {"window": {"end_date": FACTORY["end_date"]}}
    data, _ = bind(same, [], owner["values"], editable=False)
    assert not any(name.startswith("window-") for name in data)
    with pytest.raises(StaleRecordError):
        bind({"window": {"end_date": EARLIER_END}}, [], owner["values"], editable=False)
    data, _ = bind({"window": {"end_date": EARLIER_END}}, [], owner["values"])
    assert data["window-end_date"] == EARLIER_END


def test_the_overlap_acknowledgement_needs_a_financial_period():
    """Posted as the page's checkbox; refused, by field, without a period."""
    owner = campaign_record()
    with pytest.raises(admin_changes.InvalidChange) as refused:
        bind({"window": {"overlap_confirmed": True}}, [], owner["values"])
    assert refused.value.fields[0]["field"] == "window.overlap_confirmed"
    funded = campaign_record(modules=["census", "financial"], financial=financial())
    data, _ = bind({"window": {"overlap_confirmed": True}}, [], funded["values"])
    assert data["window-overlap_confirmed"] == "on"
    data, _ = bind({}, [], funded["values"])
    assert "window-overlap_confirmed" not in data


def test_an_unknown_schedule_id_is_named_in_the_refusal():
    """No saved schedule of this campaign: invalid, with that id as the field."""
    owner = campaign_record()
    with pytest.raises(admin_changes.InvalidChange) as refused:
        bind({"schedules": [{"id": SCHEDULE, "time": "10:00:00"}]}, [], owner["values"])
    assert refused.value.fields == [
        {
            "field": f"schedules.{SCHEDULE}",
            "code": "invalid",
            "message": "No saved schedule of this campaign has this id.",
        }
    ]


def test_invalid_change_field_errors_are_in_the_document(monkeypatch):
    """The error document lists each refused field with the page's message."""
    import io

    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )
    fields = [
        {"field": "schedules.new0.date", "code": "required", "message": "Choose."}
    ]

    class Admitted:
        """Admission and the session stand-ins; the handler refuses."""

        def __init__(self, configuration):
            """Nothing to assemble."""

        def __enter__(self):
            """No runtime is needed."""
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

    def refuse(*args, **kwargs):
        """The page's forms found a problem."""
        raise admin_changes.InvalidChange(fields)

    monkeypatch.setattr(admin_changes, "preview_schedule", refuse)
    out = io.StringIO()
    code = admin_cli.main(
        [
            "schedule",
            "preview",
            "--expected-version",
            "f" * 64,
            "--changes",
            "-",
            "--config",
            "x",
            "--session-stdin",
        ],
        stdin=io.BytesIO(f"pk-admin-session/1 {'A' * 43} {'f' * 64}\n{{}}\n".encode()),
        stdout=out,
        stderr=io.StringIO(),
    )
    document = json.loads(out.getvalue())
    assert code == 1 and document["error"]["code"] == "invalid"
    assert document["error"]["fields"] == fields


def test_field_problems_carry_the_closed_error_code():
    """A missing value is ``required``; any other problem is ``invalid``."""
    from django.forms.utils import ErrorList

    from parishkit.stewardship.accounts.schedule_forms import ScheduleWindow

    window = ScheduleWindow(
        {"window-timezone": ZONE, "window-start_date": "", "window-end_date": "x"},
        prefix="window",
        previous=FACTORY,
        editable=True,
    )
    assert not window.is_valid()
    between = ErrorList(["Only one Initial invitation is allowed."])
    schedules = SimpleNamespace(forms=[], non_form_errors=lambda: between)
    problems = admin_changes.field_errors(window, schedules, [])
    codes = {problem["field"]: problem["code"] for problem in problems}
    assert codes == {
        "window.start_date": "required",
        "window.end_date": "invalid",
        "schedules": "invalid",
    }
    assert all(problem["message"] for problem in problems)


def test_an_unknown_outcome_names_the_request_to_read(monkeypatch):
    """Exit 6 from a confirmation reports the request id fixed before intake."""
    import io

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

    def lost(caller, service, campaign_id, *, token, context):
        """The request id is known; then the commit's outcome is lost."""
        context["request_id"] = str(REQUEST)
        context["committed"] = True
        raise RuntimeError("connection lost at commit")

    monkeypatch.setattr(admin_changes, "confirm_schedule", lost)
    out = io.StringIO()
    code = admin_cli.main(
        ["schedule", "confirm", "--token", "t", "--config", "x", "--session-stdin"],
        stdin=io.BytesIO(f"pk-admin-session/1 {'A' * 43} {'f' * 64}\n".encode()),
        stdout=out,
        stderr=io.StringIO(),
    )
    document = json.loads(out.getvalue())
    assert code == 6 and document["error"]["code"] == "outcome_unknown"
    assert document["error"]["request_id"] == str(REQUEST)
