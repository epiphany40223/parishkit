"""``export limitations``: the Limitations download (#463, ADM-11 PR 8h).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, reading the
report through the page's download admission, Show choice and
``limitations_report`` under the campaign read guard and writing it through
the page's own writer. Its CSV is the page's download byte for byte, and it
records the page's ``talents_report_exported`` (the name the report kept
from Talents and limitations, #970) with the same outcomes and context
members.
"""

import csv
import hashlib
import io
from dataclasses import replace

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.reports import limitation_views

from .auth_builders import signed_in
from .automation_builders import as_staff
from .response_builders import activate_response_service
from .test_admin_census_export_cli_postgresql import workbook
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_logs_cli_postgresql import counted, stream
from .test_admin_report_cli_postgresql import cli, parity  # noqa: F401
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_status_cli_postgresql import admin, session  # noqa: F401
from .test_information_followup_postgresql import search
from .test_limitations_report_postgresql import submit_live
from .test_ministry_responses_postgresql import start

pytestmark = pytest.mark.django_db(transaction=True)

ROUTE = "/admin/reports/limitations/exports/"
EVENT = "talents_report_exported"


def submitted(response_service):
    """A live response: a Family that cannot attend, a Member who cannot serve."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness, cannot_serve=True)
    return harness


def export(run, *argv, secret=None):
    """``export limitations`` with ``argv``: (exit code, bytes, document)."""
    return stream(None, "export", "limitations", *argv, secret=secret or run.secret)


def contexts():
    """The context of each recorded download event, oldest first."""
    return list(
        AuditContext.objects.filter(event__event_type=EVENT)
        .order_by("event__created_at", "event__id")
        .values_list("context", flat=True)
    )


def with_roles(monkeypatch, roles, ministries=frozenset()):
    """Give the session's principal these roles from now on.

    Pairing needs an Administrator, so the admission's principal is changed
    afterwards. The page's module holds its own reference to the admission,
    so it is patched there too.
    """
    real = sessions.authenticated_admin

    def changed(*arguments, **options):
        """The real admission, its principal holding only ``roles``."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset(roles), ministries=ministries)

    monkeypatch.setattr(sessions, "authenticated_admin", changed)
    monkeypatch.setattr(limitation_views, "authenticated_admin", changed)


def test_the_page_s_files(response_service, google, cli):  # noqa: F811
    """CSV is the page's download byte for byte; XLSX holds the same tables
    as the page's; PDF is a PDF; the Show choice narrows the file. Each
    records the page's event with the rows in the file and its choices, and
    nothing else."""
    harness = submitted(response_service)
    browser, _ = signed_in()
    run = cli()
    form = {"format": "csv", "timezone": "America/New_York", "show": "any"}
    argv = ("--timezone", "America/New_York")
    found = {}

    def page():
        """The page's download, as the browser posts it (in memory)."""
        response, found["page"] = search(browser, ROUTE, form)
        assert response.status_code == 200

    def command():
        """The command's CSV of the same report."""
        found["command"] = export(run, *argv, "--format", "csv")

    parity(EVENT, page, command)
    code, body, document = found["command"]
    assert code == 0, document
    assert body == found["page"]
    table = list(csv.reader(io.StringIO(body.decode())))
    result = document["result"]
    assert result["campaign_id"] == str(harness.campaign.pk)
    assert result["format"] == "csv" and result["content_type"] == "text/csv"
    assert result["timezone"] == "America/New_York"
    # One Member who cannot serve and one Family that cannot attend.
    assert result["count"] == 2
    assert result["size"] == len(body)
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["file_name"].startswith("stewardship-limitations-")
    assert result["file_name"].endswith(".csv")
    # The page counted its two rows, and so did the command, with the same
    # choices: no search, the Show word and the snapshot read.
    assert counted(EVENT)[-2:] == [2, 2]
    page_context, command_context = contexts()[-2:]
    for context in (page_context, command_context):
        assert context["search_used"] is False and context["report_filter"] == "any"
    assert page_context["snapshot_id"] == command_context["snapshot_id"]

    code, body, document = export(run, *argv, "--format", "xlsx")
    assert code == 0, document
    assert document["result"]["content_type"].endswith("spreadsheetml.sheet")
    command_book, _ = workbook(body)
    response, page_body = search(browser, ROUTE, form | {"format": "xlsx"})
    assert response.status_code == 200
    page_book, _ = workbook(page_body)
    assert list(page_book) == list(command_book)
    assert page_book["Members"] == command_book["Members"]
    assert page_book["Families"] == command_book["Families"]
    # The CSV's two tables are the workbook's two sheets.
    cells = [cell for row in command_book["Members"] for cell in row]
    assert all(value in cells for value in table[1] if value)

    code, body, document = export(run, *argv, "--format", "pdf")
    assert code == 0 and body.startswith(b"%PDF-"), document
    assert document["result"]["content_type"] == "application/pdf"

    # The Show choice narrows the file as it does the page's, and the
    # command records the page's event and nothing else.
    before = set(AuditEvent.objects.values_list("pk", flat=True))
    code, body, document = export(run, "--show", "cannot_serve", "--format", "csv")
    assert code == 0 and document["result"]["count"] == 1, document
    response, page_body = search(
        browser, ROUTE, {"format": "csv", "timezone": "UTC", "show": "cannot_serve"}
    )
    assert body == page_body
    added = AuditEvent.objects.exclude(pk__in=before)
    assert sorted(added.values_list("event_type", flat=True)) == [EVENT, EVENT]
    assert contexts()[-2]["report_filter"] == "cannot_serve"


def test_refusals_print_nothing_and_record_nothing(response_service, google, cli):  # noqa: F811
    """A choice the page refuses is invalid; a read-only session is refused;
    neither writes a byte or records the page's event."""
    submitted(response_service)
    signed_in()
    run = cli()
    before = AuditEvent.objects.filter(event_type=EVENT).count()
    code, body, document = export(run, "--timezone", "Mars/Olympus")
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert body == b""
    reader = read_only_session(run.service)
    code, body, document = export(run, secret=reader)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert body == b""
    assert AuditEvent.objects.filter(event_type=EVENT).count() == before


def test_staff_export_and_a_leader_is_denied(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """Staff export as on the page; a Ministry leader is refused with
    nothing written or recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    with monkeypatch.context() as patch:
        as_staff(patch)
        # The page's module holds its own reference to the admission.
        patch.setattr(
            limitation_views, "authenticated_admin", sessions.authenticated_admin
        )
        code, body, document = export(run, "--format", "csv")
        assert code == 0 and document["result"]["count"] == 2, document
        assert body.startswith(b"Member,")
    before = AuditEvent.objects.filter(event_type=EVENT).count()
    with_roles(monkeypatch, {"ministry_leader"}, frozenset({4}))
    code, body, document = export(run)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert body == b"" and "result" not in document
    assert AuditEvent.objects.filter(event_type=EVENT).count() == before


def test_an_ended_session_exports_nothing(response_service, google, cli):  # noqa: F811
    """A revoked session is exit 5, before the choice check, with nothing
    written or recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    revoke(run.service, run.secret)
    for argv in ((), ("--timezone", "Mars/Olympus")):
        code, body, document = export(run, *argv)
        assert code == 5 and document["error"]["code"] == "session_ended", argv
        assert body == b""
    assert not AuditEvent.objects.filter(event_type=EVENT).exists()


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: not_available, and nothing recorded."""
    code, body, document = stream(
        admin, "export", "limitations", secret=session(admin, scope="full")
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert body == b""
    assert not AuditEvent.objects.filter(event_type=EVENT).exists()
