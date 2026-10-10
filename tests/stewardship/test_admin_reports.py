"""The System logs commands' documents and plumbing (ADM-11 PR 8a).

Pure tests: the golden documents of ``logs list`` and ``logs export``,
built through the projections the commands use from the page's own display
rows, with an exact allowlist of member names (no actor email, Family or
member DUID, or translated sentence at any depth); the page's filters built
from the command's options; and a streamed file reaching standard output
exactly, with every document on standard error. The commands against a
real database are in database/test_admin_logs_cli_postgresql.py.
"""

import contextlib
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_reports
from parishkit.stewardship.audit.log_rows import (
    ACTIVITY_TYPES,
    DETAIL_FIELDS,
    LEVELS,
    LogQuery,
    audit_row,
    operational_row,
)
from parishkit.stewardship.web.dates import UnknownZone

from .test_admin_reads import FORBIDDEN, members

OPERATIONAL = UUID("00000000-0000-4000-8000-000000000021")
AUDITED = UUID("00000000-0000-4000-8000-000000000022")
ACTOR = UUID("00000000-0000-4000-8000-000000000023")
CORRELATION = UUID("00000000-0000-4000-8000-000000000024")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000025")
SUBJECT = UUID("00000000-0000-4000-8000-000000000026")
NOW = datetime(2054, 10, 5, 14, tzinfo=UTC)
PREAMBLE = f"pk-admin-session/1 {'s' * 43} {'a' * 64}\n".encode()


def rows():
    """One operational and one audit row, as the page's reads build them.

    The audit entry's context names a Family and a member by DUID, which
    the screen shows and the terminal document must not; the page's
    ``_annotate`` adds the actor's email, which the document must not show
    either.
    """
    operational = operational_row(
        {
            "id": OPERATIONAL,
            "created_at": NOW,
            "level": "ERROR",
            "event": "task_failed",
            "actor_id": None,
            "correlation_id": CORRELATION,
            "schema": "failure",
            "context": {"failure": "alert_mail", "outcome": "failed", "count": 2},
        }
    )
    audited = audit_row(
        {
            "id": AUDITED,
            "created_at": NOW,
            "event_type": "member_source_viewed",
            "actor_id": ACTOR,
            "correlation_id": CORRELATION,
            "campaign_reference": CAMPAIGN,
            "subject_id": SUBJECT,
            "auditcontext__context": {
                "outcome": "succeeded",
                "family_duid": 4242,
                "member_duid": 4343,
                "field": "email",
            },
            "auditcontext__actor_kind": "portal_user",
        }
    )
    for row, actor in ((operational, None), (audited, "admin@example.org")):
        row["actor"], row["actor_worker"], row["task_subject"] = actor, False, False
    return [operational, audited]


def log_list():
    """One page of both kinds of entry, at its snapshot."""
    table = SimpleNamespace(
        rows=rows(),
        number=1,
        pages=1,
        size=50,
        sort="newest",
        count=2,
        capped=False,
        has_next=False,
    )
    return admin_reports.log_list_model(table, through=NOW, depth_limited=False)


def log_export():
    """The document describing a small CSV download."""
    return admin_reports.log_export_model(
        b"time,source\r\n", fmt="csv", zone_name="UTC", count=0, now=NOW
    )


ENTRY_MEMBERS = {
    "id",
    "source",
    "created_at",
    "level",
    "type",
    "actor_id",
    "actor_kind",
    "correlation_id",
    "campaign_id",
    "subject_id",
    "details",
}
GOLDEN = {
    "logs list": (
        log_list,
        {
            "through": "2054-10-05T14:00:00.000000+00:00",
            "page": 1,
            "pages": 1,
            "size": 50,
            "sort": "newest",
            "matching": 2,
            "matching_capped": False,
            "depth_limited": False,
            "has_next": False,
            "entries": [
                {
                    "id": str(OPERATIONAL),
                    "source": "operational",
                    "created_at": NOW.isoformat(),
                    "level": "ERROR",
                    "type": "task_failed",
                    "actor_id": None,
                    "actor_kind": None,
                    "correlation_id": str(CORRELATION),
                    "campaign_id": None,
                    "subject_id": None,
                    "details": {
                        "count": "2",
                        "failure": "alert_mail",
                        "outcome": "failed",
                    },
                },
                {
                    "id": str(AUDITED),
                    "source": "audit",
                    "created_at": NOW.isoformat(),
                    "level": None,
                    "type": "member_source_viewed",
                    "actor_id": str(ACTOR),
                    "actor_kind": "portal_user",
                    "correlation_id": str(CORRELATION),
                    "campaign_id": str(CAMPAIGN),
                    "subject_id": str(SUBJECT),
                    # The stored field name, never a Family's or member's DUID.
                    "details": {"field": "email", "outcome": "succeeded"},
                },
            ],
        },
    ),
    "logs export": (
        log_export,
        {
            "file_name": "stewardship-logs-20541005-140000Z.csv",
            "content_type": "text/csv",
            "format": "csv",
            "timezone": "UTC",
            "size": 13,
            "sha256": hashlib.sha256(b"time,source\r\n").hexdigest(),
            "count": 0,
            "limit": 10_000,
        },
    ),
}
ALLOWED = {
    "logs list": {
        "through",
        "page",
        "pages",
        "size",
        "sort",
        "matching",
        "matching_capped",
        "depth_limited",
        "has_next",
        "entries",
    }
    | ENTRY_MEMBERS,
    "logs export": {
        "file_name",
        "content_type",
        "format",
        "timezone",
        "size",
        "sha256",
        "count",
        "limit",
    },
}


def without_details(value):
    """A document with each entry's ``details`` emptied.

    Detail keys are the page's reviewed stored field names, checked against
    ``DETAIL_FIELDS`` on their own; they are data, not document members.
    """
    if isinstance(value, dict):
        return {
            key: {} if key == "details" else without_details(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [without_details(item) for item in value]
    return value


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the projection the command uses."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_documents_members_are_exactly_its_allowlist(command):
    """Members at any depth equal the allowlist; none is personal data."""
    document = GOLDEN[command][0]().to_document()
    assert set(members(without_details(document))) == ALLOWED[command], command
    for name in ALLOWED[command]:
        assert FORBIDDEN.search(name) is None, (command, name)
    text = json.dumps(document)
    assert "@" not in text and "4242" not in text and "4343" not in text
    for entry in document.get("entries", []):
        assert set(entry["details"]) <= DETAIL_FIELDS - admin_reports.FAMILY_DETAILS


def test_every_family_or_member_detail_is_left_out():
    """Each reviewed field naming a Family or a member is in FAMILY_DETAILS.

    Any field whose name looks like a person's identifier or contact detail
    is either left out or reviewed here as not naming anyone: Ministry DUIDs
    name Ministries, ``directory_phone`` is the directory's filter choice
    (any, yes or no) and ``exact_code_used`` a flag. A new field of that
    shape fails here until it is reviewed or left out of the document.
    """
    reviewed = {"directory_phone", "exact_code_used"}
    people = {
        name
        for name in DETAIL_FIELDS
        if re.search(r"duid|email|name|phone|address|code|envelope", name)
        and "ministry" not in name
    }
    assert people - reviewed == admin_reports.FAMILY_DETAILS


def test_every_report_command_has_a_golden_document():
    """The PR 8a commands, and the catalog lists each model's fields.

    The export lifecycle's (PR 8b) are in test_admin_exports.py.
    """
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    logs = {spec.name for spec in admin_cli.COMMANDS if spec.name[:5] == "logs "}
    assert logs == set(GOLDEN)
    for command, (build, _) in GOLDEN.items():
        assert entries[command]["result_fields"] == list(build().field_names())


def test_the_kinds_of_entry_are_the_pages():
    """Spelled out before Django is set up; kept equal to the page's six."""
    assert (*(level.lower() for level in LEVELS), "audit") == admin_cli.LOG_KINDS


def test_the_activity_groups_are_the_pages():
    """Spelled out before Django is set up; kept equal to the page's (#953)."""
    assert tuple(sorted(ACTIVITY_TYPES)) == admin_cli.LOG_ACTIVITIES


@pytest.mark.parametrize("command", ["list", "export"])
def test_activity_is_the_pages_choice(command):
    """``--activity`` reaches LogQuery as the page's Activity choice (#953),
    on both commands, and refuses a group the page does not list."""
    from argparse import ArgumentParser

    parser = ArgumentParser()
    options = {"list": admin_cli._logs_list_options}
    options["export"] = admin_cli._logs_export_options
    options[command](parser)
    args = parser.parse_args(["--activity", "sign_in", "--show", "audit"])
    query = admin_reports.log_query(admin_cli._log_filters(args))
    assert query.activity == "sign_in" and query.audits and not query.levels
    assert query.activity_types == ACTIVITY_TYPES["sign_in"]
    unchosen = admin_reports.log_query(admin_cli._log_filters(parser.parse_args([])))
    assert unchosen.activity == "" and unchosen.activity_types is None
    with pytest.raises(SystemExit):
        parser.parse_args(["--activity", "admin_login"])


def test_no_kind_shown_is_the_pages_first_visit_default():
    """Without --show the page's default: every level but debug, and audits."""
    query = admin_reports.log_query({"show": None, "event": None})
    assert query == LogQuery.parse({})
    assert query.levels == LEVELS[1:] and query.audits


def test_shown_kinds_are_an_applied_form():
    """--show ticks exactly the kinds given, as an applied form."""
    query = admin_reports.log_query(
        {"show": ["error", "audit"], "correlation": CORRELATION, "page": 2}
    )
    assert query.applied == "yes" and query.levels == ("ERROR",) and query.audits
    assert query.correlation == str(CORRELATION) and query.page_number == 2
    only = admin_reports.log_query({"show": ["critical"]})
    assert only.levels == ("CRITICAL",) and not only.audits


@pytest.mark.parametrize(
    "filters,error",
    [
        # The page's own refusals, which the command reports as invalid.
        ({"event": "Not An Event"}, ValueError),
        ({"start": "2054-13-01", "zone": "UTC"}, ValueError),
        ({"start": "2054-10-05", "end": "2054-10-01", "zone": "UTC"}, ValueError),
        ({"size": 7}, ValueError),
        ({"through": "2054-10-05T14:00:00+00:00"}, ValueError),
        # Days need the zone they fall in, as on the page.
        ({"start": "2054-10-05"}, UnknownZone),
    ],
)
def test_invalid_filters_are_the_pages_refusals(filters, error):
    """Each refusal is LogQuery's own, a ValueError the command maps to invalid."""
    with pytest.raises(error):
        admin_reports.log_query(filters)
    assert issubclass(error, ValueError)


def test_the_log_filter_options_are_the_pages_filters():
    """Every page filter has an option: LogQuery's fields less the six ticks
    (``--show``), the legacy ``source``, the applied mark and the paging."""
    from argparse import ArgumentParser

    parser = ArgumentParser()
    admin_cli._log_filter_options(parser)
    args = parser.parse_args([])
    filters = set(admin_cli._log_filters(args)) - {"show"}
    form = {"applied", "source", "debug", "info", "warning", "error"}
    form |= {"critical", "audit", "through", "page", "size", "sort"}
    assert filters == set(LogQuery.__dataclass_fields__) - form


@pytest.mark.parametrize(
    "filters",
    [
        {"text": "someone@example.org"},
        {"ministry": "012"},
        {"subject": "not-a-uuid"},
    ],
)
def test_the_new_filters_keep_the_pages_refusals(filters):
    """An address in the search, a non-canonical Ministry or subject: invalid."""
    with pytest.raises(ValueError):
        admin_reports.log_query(filters)


def test_the_command_reads_under_the_pages_bound(monkeypatch):
    """``logs list`` and ``logs export`` read inside ``log_reads.bounded_read``
    (the page's statement limit with a recorded stop), not a bare transaction."""
    import contextlib

    from parishkit.stewardship.audit import log_reads

    used = []

    @contextlib.contextmanager
    def bound():
        """Note the bounded read; stop inside it, as a killed statement would."""
        used.append(True)
        yield
        raise RuntimeError("stopped")

    monkeypatch.setattr(log_reads, "bounded_read", bound)
    monkeypatch.setattr(admin_reports, "_admit", lambda *args: object())
    monkeypatch.setattr(admin_reports, "_export_admit", lambda *args: object())
    monkeypatch.setattr(admin_reports, "_configured", lambda: None)
    monkeypatch.setattr(log_reads, "load_page", lambda *a, **k: (None, False))
    monkeypatch.setattr(log_reads, "export_rows", lambda query: [])
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.ownership.database_now", lambda: None
    )
    monkeypatch.setattr(admin_reports, "_held", lambda step: step())
    service = SimpleNamespace(store=None)
    with pytest.raises(RuntimeError, match="stopped"):
        admin_reports.read_logs(None, service, {})
    with pytest.raises(RuntimeError, match="stopped"):
        admin_reports.export_logs(
            None, service, {}, fmt="csv", zone_name="UTC", context={}
        )
    assert used == [True, True]


def test_a_snapshot_printed_by_logs_list_pages_through_it():
    """``through`` as printed is the spelling the page's snapshot accepts."""
    through = log_list().to_document()["through"]
    query = admin_reports.log_query({"through": through, "page": 2})
    assert query.snapshot == NOW


def test_a_streamed_file_reaches_the_binary_buffer_exactly():
    """Bytes go under the text layer, never through its encoding."""
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="ascii")
    body = "Ünïcode,ok\r\n".encode()
    admin_cli.write_stream(stdout, body)
    assert raw.getvalue() == body
    text = io.StringIO()
    admin_cli.write_stream(text, body)
    assert text.getvalue() == body.decode()


def streaming(monkeypatch, handler):
    """Run a stand-in streaming command; return (code, stdout, stderr)."""
    spec = admin_cli.CommandSpec(
        name="stream probe",
        help="probe",
        handler=handler,
        scope="none",
        changes_state=False,
        result_fields=(),
        pr=0,
        streams=True,
    )
    monkeypatch.setitem(admin_cli.BY_NAME, "stream probe", spec)
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )
    monkeypatch.setattr(
        admin_cli, "ADMISSION", lambda configuration: contextlib.nullcontext(None)
    )
    raw, err = io.BytesIO(), io.StringIO()
    # Kept until the bytes are read: a collected wrapper closes its buffer.
    stdout = io.TextIOWrapper(raw, encoding="utf-8")
    code = admin_cli.run(
        SimpleNamespace(command_name="stream probe", config=Path("x")),
        stdin=io.BytesIO(PREAMBLE),
        stdout=stdout,
        stderr=err,
    )
    return code, raw.getvalue(), err.getvalue().splitlines()


def test_a_streaming_command_writes_only_the_file_to_standard_output(monkeypatch):
    """The bytes alone on standard output; the document on standard error."""

    def handler(args, preamble, runtime, context):
        """A file and its description."""
        context["stream"] = b"a,b\r\n1,2\r\n"
        return {"size": 10}

    code, out, err = streaming(monkeypatch, handler)
    assert code == 0 and out == b"a,b\r\n1,2\r\n"
    [document] = [json.loads(line) for line in err]
    assert document["ok"] and document["result"] == {"size": 10}


def test_a_streaming_commands_refusal_writes_nothing_to_standard_output(
    monkeypatch,
):
    """A refusal is a document on standard error and no bytes at all."""

    def handler(args, preamble, runtime, context):
        """Refused before any file was built."""
        raise PermissionError("no")

    code, out, err = streaming(monkeypatch, handler)
    assert code == 1 and out == b""
    [document] = [json.loads(line) for line in err]
    assert document["error"]["code"] == "denied"


@pytest.mark.parametrize(
    "argv,streamed",
    [
        (["logs", "export", "--show", "bogus"], True),
        (["logs", "export", "--format", "pdf"], True),
        (["logs", "list", "--show", "bogus"], False),
        # Options before the command words: still the streaming command.
        (["--config", "web.yaml", "logs", "export", "--bad"], True),
        (["--config", "web.yaml", "logs", "list", "--bad"], False),
    ],
)
def test_a_streaming_commands_usage_error_goes_to_standard_error(argv, streamed):
    """A usage error never lands in the operator's file, only on standard error."""
    out, err = io.StringIO(), io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(PREAMBLE),
        stdout=out,
        stderr=err,
    )
    assert code == 2
    written = err.getvalue() if streamed else out.getvalue()
    assert (out.getvalue() if streamed else err.getvalue()) == ""
    assert json.loads(written)["error"]["code"] == "usage"
