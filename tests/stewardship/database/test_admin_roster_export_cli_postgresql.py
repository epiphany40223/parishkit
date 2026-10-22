"""``export roster``: the roster changes to enter, as a file (#463, ADM-11 PR 8g).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, reading the
list through its own admission, Show and Ministry choices and
``roster_rows`` under the campaign read guard and writing it through the
list's own writer. The list's page, Roster changes to enter, was folded into
Ministry requests' To enter by hand Stage (R39 finding 2); the command keeps
the file and its ``roster_changes_exported`` event.
"""

import hashlib

import pytest

from parishkit.stewardship.audit.models import AuditEvent

from .auth_builders import signed_in
from .test_admin_census_export_cli_postgresql import workbook
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_logs_cli_postgresql import counted, stream
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_roster_cli_postgresql import with_roles
from .test_admin_status_cli_postgresql import admin, session  # noqa: F401
from .test_roster_entries_postgresql import resolved, tick

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "roster_changes_exported"


def export(run, *argv, secret=None):
    """``export roster`` with ``argv``: (exit code, bytes, document)."""
    return stream(None, "export", "roster", *argv, secret=secret or run.secret)


def test_the_list_s_files(response_service, google, cli):  # noqa: F811
    """CSV, XLSX (the list's sheet) and PDF; the Show and Ministry choices
    narrow the file. Each records the list's event with the rows in the
    file, and nothing else."""
    harness, admin_id, join, _ = resolved(response_service)
    signed_in()
    run = cli()
    argv = ("--timezone", "America/New_York")

    code, body, document = export(run, *argv, "--format", "csv")
    assert code == 0, document
    assert b",To enter by hand," in body
    result = document["result"]
    assert result["campaign_id"] == str(harness.campaign.pk)
    assert result["format"] == "csv" and result["content_type"] == "text/csv"
    assert result["timezone"] == "America/New_York"
    assert result["count"] == 1
    assert result["size"] == len(body)
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["file_name"].startswith("stewardship-roster-changes-")
    assert result["file_name"].endswith(".csv")
    assert counted(EVENT)[-1:] == [1]

    code, body, document = export(run, *argv, "--format", "xlsx")
    assert code == 0, document
    assert document["result"]["content_type"].endswith("spreadsheetml.sheet")
    command_book, _ = workbook(body)
    assert list(command_book) == ["Roster changes"]

    code, body, document = export(run, *argv, "--format", "pdf")
    assert code == 0 and body.startswith(b"%PDF-"), document
    assert document["result"]["content_type"] == "application/pdf"

    # Marked entered: gone from the default file, listed under --show all;
    # another Ministry's file is empty. Only the list's event is recorded.
    tick(harness, admin_id, join, True)
    before = set(AuditEvent.objects.values_list("pk", flat=True))
    code, body, document = export(run, "--format", "csv")
    assert code == 0 and document["result"]["count"] == 0, document
    code, body, document = export(run, "--show", "all", "--format", "csv")
    assert code == 0 and document["result"]["count"] == 1, document
    assert b",Entered by hand," in body
    code, _, document = export(run, "--show", "all", "--ministry", "4")
    assert code == 0 and document["result"]["count"] == 0, document
    code, _, document = export(run, "--show", "all", "--ministry", "9")
    assert code == 0 and document["result"]["count"] == 1, document
    added = AuditEvent.objects.exclude(pk__in=before)
    assert set(added.values_list("event_type", flat=True)) == {EVENT}
    assert added.count() == 4


def test_refusals_print_nothing_and_record_nothing(response_service, google, cli):  # noqa: F811
    """A choice the page refuses is invalid; a read-only session is refused;
    neither writes a byte or records the page's event."""
    resolved(response_service)
    signed_in()
    run = cli()
    # Pairing the read-only session records its own events first.
    reader = read_only_session(run.service)
    before = AuditEvent.objects.count()
    for argv in (("--timezone", "Mars/Olympus"), ("--ministry", "0")):
        code, body, document = export(run, *argv)
        assert code == 1 and document["error"]["code"] == "invalid", document
        assert body == b""
    code, body, document = export(run, secret=reader)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert body == b""
    assert AuditEvent.objects.count() == before


def test_staff_export_and_a_leader_is_denied(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """Staff export as on the page; a Ministry leader is refused, even for
    their own Ministry, with nothing written or recorded."""
    resolved(response_service)
    signed_in()
    run = cli()
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, body, document = export(run, "--format", "csv")
        assert code == 0 and document["result"]["count"] == 1, document
        assert body.startswith(b"Member,")
    before = AuditEvent.objects.filter(event_type=EVENT).count()
    with_roles(monkeypatch, {"ministry_leader"}, frozenset({9}))
    code, body, document = export(run, "--ministry", "9")
    assert code == 1 and document["error"]["code"] == "denied", document
    assert body == b"" and "result" not in document
    assert AuditEvent.objects.filter(event_type=EVENT).count() == before


def test_an_ended_session_exports_nothing(response_service, google, cli):  # noqa: F811
    """A revoked session is exit 5, before the choice check, with nothing
    written or recorded."""
    resolved(response_service)
    signed_in()
    run = cli()
    revoke(run.service, run.secret)
    for argv in ((), ("--timezone", "Mars/Olympus"), ("--ministry", "0")):
        code, body, document = export(run, *argv)
        assert code == 5 and document["error"]["code"] == "session_ended", argv
        assert body == b""
    assert not AuditEvent.objects.filter(event_type=EVENT).exists()


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: not_available, and nothing recorded."""
    code, body, document = stream(
        admin, "export", "roster", secret=session(admin, scope="full")
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert body == b""
    assert not AuditEvent.objects.filter(event_type=EVENT).exists()
