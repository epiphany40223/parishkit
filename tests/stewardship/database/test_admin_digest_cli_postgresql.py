"""The digest commands of ``pk-stewardship admin`` (ADM-11 PR 8d, #1786).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. Report emails
replaced the digests (#1786): ``digest daily`` and ``digest weekly`` read
only the digests sent before them, which no release makes any more, so an
unknown snapshot is all a new database can ask them for; ``digest
weekly-request`` is Send now of the converted Weekly report, requested
through the Send a weekly report now page's own function, under the same
database rules, recording the same events and task.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
)

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import WEEKLY_INSTANT, add_digest
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def count(event_type):
    """How many ``event_type`` events have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def test_an_unknown_digest_is_not_available(family_mail, google, cli):  # noqa: F811
    """No digest snapshot by that id: not_available, nothing read."""
    signed_in()
    run = cli()
    before = count("daily_digest_viewed") + count("weekly_digest_viewed")
    for kind in ("daily", "weekly"):
        code, document = run("digest", kind, str(uuid4()))
        assert code == 1 and document["error"]["code"] == "not_available", kind
    assert count("daily_digest_viewed") + count("weekly_digest_viewed") == before


def test_a_manual_weekly_report_is_send_now_of_the_weekly_report(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """The manual request as its page: Send now of the converted Weekly."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        signed_in()
        run = cli()
        key = uuid4()
        argv = ("digest", "weekly-request", "--yes", "--request-key", str(key))
        # Without a Weekly report there is nothing to send now.
        tasks = TaskRun.objects.count()
        code, document = run(*argv)
        assert code == 1 and document["error"]["code"] == "not_available", document
        assert TaskRun.objects.count() == tasks
        definition = add_digest(harness.service.store, harness.campaign, weekly=True)
        # A live campaign's sends wait for its activation catch-up.
        complete_empty_catchup(harness.campaign, uuid4())
        run = cli()  # A session on the configuration that has it.

        # Without the acknowledgement, nothing happens.
        code, document = run("digest", "weekly-request")
        assert code == 4 and document["error"]["code"] == "confirmation_required"
        assert TaskRun.objects.count() == tasks

        # The request is the page's: its task, its database-recorded
        # report_email_manual_requested event, and the command's event once.
        code, document = run(*argv)
        assert code == 0, document
        result = document["result"]
        assert result["created"] and result["request_key"] == str(key)
        manual = ReportEmailManualRequest.objects.get(pk=key)
        assert manual.definition_id == definition
        assert result["task"]["id"] == str(manual.task_id)
        assert result["task"]["type"] == "report_email_prepare"
        assert count("report_email_manual_requested") == 1
        assert count("admin_cmd_digest_weekly_request") == 1
        # The same key returns the same request and records nothing new.
        code, document = run(*argv)
        assert code == 0 and document["result"]["created"] is False
        assert document["result"]["task"]["id"] == str(manual.task_id)
        assert count("report_email_manual_requested") == 1
        assert count("admin_cmd_digest_weekly_request") == 1

        # A read-only session cannot request one.
        reader = read_only_session(run.service)
        code, document = run("digest", "weekly-request", "--yes", secret=reader)
        assert code == 1 and document["error"]["code"] == "denied"
