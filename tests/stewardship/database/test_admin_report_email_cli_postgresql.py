"""The ``report-email`` commands of ``pk-stewardship admin`` (#1786, item 5).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. Each command
is the Report emails page's own read or action: the list, one report's
values and sends, a saved copy, Send now, and the row actions' Pause,
Resume and Delete with the editor's Skip this send through ``preview`` and
``confirm``. They replaced the digest commands of PR 8d.
"""

import json
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
)

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import (
    WEEKLY_INSTANT,
    add_digest,
    settle_messages,
)
from .report_builders import weekly_allocated as allocated
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_emails_page_postgresql import apply_requests

pytestmark = pytest.mark.django_db(transaction=True)


def revoke(service, secret):
    """Revoke the session ``secret`` names, as its owner would.

    Shared by the command suites that check a revoked session's refusal.
    """
    row = AutomationSession.objects.get(secret_digest=automation.secret_digest(secret))
    actor = current_principal(service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")


def count(event_type):
    """How many ``event_type`` events have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def report_values(identifier):
    """One report's applied values."""
    store = SystemConfiguration.objects.get()
    document = store.active_configuration.canonical_document
    return next(
        row["values"]
        for row in document["sections"]["schedules"]
        if row["id"] == str(identifier)
    )


def test_no_current_campaign_is_not_available(auth_service, google, cli):  # noqa: F811
    """Before any campaign the list names nothing: not_available."""
    signed_in()
    code, document = cli()("report-email", "list")
    assert code == 1 and document["error"]["code"] == "not_available", document


def test_an_unknown_saved_copy_is_not_available(family_mail, google, cli):  # noqa: F811
    """No send or saved digest by that id: not_available, nothing recorded."""
    signed_in()
    run = cli()
    before = count("report_email_viewed") + count("daily_digest_viewed")
    code, document = run("report-email", "copy", str(uuid4()))
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert count("report_email_viewed") + count("daily_digest_viewed") == before


def test_list_show_sends_and_copy_read_the_page(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """The reads are the page's: one Weekly report, its send and its copy."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        preparation = allocated(harness)
        settle_messages()
        signed_in()
        run = cli()
        code, document = run("report-email", "list")
        assert code == 0, document
        result = document["result"]
        assert result["campaign_id"] == str(harness.campaign.pk)
        assert len(result["version"]) == 64
        (row,) = result["reports"]
        assert row["id"] == str(preparation.definition_id)
        assert row["recipients"] == "All Administrators"
        assert row["last_send"]["send_id"] == str(preparation.pk)
        assert row["last_send"]["result"] == "sent"
        assert row["schedule"].startswith("Every Wednesday at ")
        code, document = run("report-email", "show", row["id"])
        assert code == 0, document
        shown = document["result"]
        assert shown["recipients"] == [{"kind": "group", "group": "administrators"}]
        assert shown["version"] == result["version"]
        assert all(send["due_at"] for send in shown["next_sends"])
        code, document = run("report-email", "sends", row["id"])
        assert code == 0 and document["result"]["sends"][0]["send_id"] == str(
            preparation.pk
        )
        viewed = count("report_email_viewed")
        code, document = run("report-email", "copy", str(preparation.pk))
        assert code == 0, document
        copy = document["result"]
        assert copy["report_id"] == row["id"] and copy["how"] == "scheduled"
        assert [block["block"] for block in copy["blocks"]] == [
            "additional_information",
            "response_funnel",
        ]
        # A Family's text and name never reach the document.
        assert "family_name" not in json.dumps(copy)
        assert count("report_email_viewed") == viewed + 1
        # A Staff reader's list has no recipients and no version.
        reader = read_only_session(run.service)
        code, document = run("report-email", "list", secret=reader)
        assert code == 0 and document["result"]["reports"][0]["id"] == row["id"]


def test_send_now_is_the_rows_send_now(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """Send now as its row: its task, its events once, and the prompt."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        signed_in()
        definition = add_digest(harness.service.store, harness.campaign, weekly=True)
        # A live campaign's sends wait for its activation catch-up.
        complete_empty_catchup(harness.campaign, uuid4())
        run = cli()
        key = uuid4()
        argv = ("report-email", "send-now", str(definition), "--yes")
        argv += ("--request-key", str(key))
        tasks = TaskRun.objects.count()
        # Without the confirmation, nothing happens.
        code, document = run("report-email", "send-now", str(definition))
        assert code == 4 and document["error"]["code"] == "confirmation_required"
        assert TaskRun.objects.count() == tasks
        # An unknown report: nothing to send.
        code, document = run("report-email", "send-now", str(uuid4()), "--yes")
        assert code == 1 and document["error"]["code"] == "not_available"
        code, document = run(*argv)
        assert code == 0, document
        result = document["result"]
        assert result["created"] and result["request_key"] == str(key)
        manual = ReportEmailManualRequest.objects.get(pk=key)
        assert manual.definition_id == definition
        assert result["task"]["id"] == str(manual.task_id)
        assert count("report_email_manual_requested") == 1
        assert count("admin_cmd_report_email_send_now") == 1
        # The same key returns the same request and records nothing new.
        code, document = run(*argv)
        assert code == 0 and document["result"]["created"] is False
        assert count("admin_cmd_report_email_send_now") == 1
        # Another while it is open is refused by the guard: stale_version.
        code, document = run("report-email", "send-now", str(definition), "--yes")
        assert code == 1 and document["error"]["code"] == "stale_version", document
        # A read-only session cannot send one.
        reader = read_only_session(run.service)
        code, document = run(*argv[:4], secret=reader)
        assert code == 1 and document["error"]["code"] == "denied"


def test_pause_resume_skip_and_delete_are_reviewed_and_confirmed(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """Each change is reviewed with a token, then applied by confirm."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        signed_in()
        definition = add_digest(harness.service.store, harness.campaign, weekly=True)
        complete_empty_catchup(harness.campaign, uuid4())
        run = cli()

        def change(operation, **extra):
            """Review and confirm one change; the confirm's document."""
            version = SystemConfiguration.objects.get().active_configuration.digest
            entry = {"operation": operation, "id": str(definition)} | extra
            code, document = run(
                "report-email",
                "preview",
                "--expected-version",
                version,
                "--changes",
                json.dumps({"changes": [entry]}),
            )
            assert code == 0, document
            assert document["result"]["changes"][0]["operation"] == operation
            token = document["result"]["preview"]["token"]
            code, document = run("report-email", "confirm", "--token", token)
            assert code == 0, document
            # The installer applies the recorded request.
            apply_requests(run.service.store)
            return document

        assert change("pause")["result"]["created"]
        assert report_values(definition)["paused_ranges"][-1]["end"] is None
        # Pausing again is refused at review: nothing changes.
        version = SystemConfiguration.objects.get().active_configuration.digest
        code, document = run(
            "report-email",
            "preview",
            "--expected-version",
            version,
            "--changes",
            json.dumps({"changes": [{"operation": "pause", "id": str(definition)}]}),
        )
        assert code == 1 and document["error"]["code"] == "invalid", document
        change("resume")
        assert report_values(definition)["paused_ranges"][-1]["end"] is not None
        code, document = run("report-email", "show", str(definition))
        send = document["result"]["next_sends"][0]["send"]
        change("skip", send=send)
        assert send in report_values(definition)["skip_dates"]
        assert count("admin_cmd_report_email_confirm") == 3
        change("delete")
        code, document = run("report-email", "list")
        assert code == 0 and document["result"]["reports"] == []
        # A stale version is refused.
        code, document = run(
            "report-email",
            "preview",
            "--expected-version",
            version,
            "--changes",
            json.dumps({"changes": [{"operation": "pause", "id": str(definition)}]}),
        )
        assert code == 1 and document["error"]["code"] == "stale_version", document


def _review(run, definition, operation):
    """``report-email preview`` of one entry: its exit code and document."""
    version = SystemConfiguration.objects.get().active_configuration.digest
    return run(
        "report-email",
        "preview",
        "--expected-version",
        version,
        "--changes",
        json.dumps({"changes": [{"operation": operation, "id": str(definition)}]}),
    )


def test_work_that_starts_after_the_review_refuses_its_apply(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """A send planned between Review and Apply changes the work fingerprint,
    so the Pause reviewed before it is refused (stale_version)."""
    from .report_builders import produce

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        signed_in()
        definition = add_digest(harness.service.store, harness.campaign, weekly=True)
        complete_empty_catchup(harness.campaign, uuid4())
        run = cli()
        code, document = _review(run, definition, "pause")
        assert code == 0, document
        token = document["result"]["preview"]["token"]
        assert produce()  # The scheduler plans the report's due send.
        code, document = run("report-email", "confirm", "--token", token)
        assert code == 1 and document["error"]["code"] == "stale_version", document
        assert report_values(definition)["paused_ranges"] == []


def test_a_queued_send_now_refuses_every_change(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """As the row does, the command line refuses a change while a Send now
    is queued, so the queued send is never left on an old revision: Pause,
    Resume, Skip and Delete alike."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        signed_in()
        definition = add_digest(harness.service.store, harness.campaign, weekly=True)
        complete_empty_catchup(harness.campaign, uuid4())
        run = cli()
        code, document = run("report-email", "show", str(definition))
        send = document["result"]["next_sends"][0]["send"]
        code, document = run("report-email", "send-now", str(definition), "--yes")
        assert code == 0, document
        version = SystemConfiguration.objects.get().active_configuration.digest
        for entry in (
            {"operation": "pause"},
            {"operation": "resume"},
            {"operation": "skip", "send": send},
            {"operation": "delete"},
        ):
            code, document = run(
                "report-email",
                "preview",
                "--expected-version",
                version,
                "--changes",
                json.dumps({"changes": [entry | {"id": str(definition)}]}),
            )
            assert code == 1 and document["error"]["code"] == "denied", (
                entry,
                document,
            )


def test_a_resume_whose_first_send_passes_is_refused(
    live_response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """Resume signs the first send it makes: applied after that send's time
    it would send late, so Apply is refused (#1464)."""
    from datetime import timedelta

    from parishkit.stewardship.accounts import admin_editing

    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        signed_in()
        definition = add_digest(harness.service.store, harness.campaign, weekly=True)
        complete_empty_catchup(harness.campaign, uuid4())
        run = cli()
        code, document = _review(run, definition, "pause")
        token = document["result"]["preview"]["token"]
        assert run("report-email", "confirm", "--token", token)[0] == 0
        apply_requests(run.service.store)
        code, document = _review(run, definition, "resume")
        assert code == 0, document
        first = document["result"]["changes"][0]["next_send"]
        token = document["result"]["preview"]["token"]
        from datetime import datetime

        late = datetime.fromisoformat(first) + timedelta(minutes=5)
        monkeypatch.setattr(admin_editing, "database_now", lambda: late)
        code, document = run("report-email", "confirm", "--token", token)
        assert code == 1 and document["error"]["code"] == "stale_version", document
