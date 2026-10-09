"""The digest commands of ``pk-stewardship admin`` (ADM-11 PR 8d).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The retained
daily and weekly reports are read through their pages' own functions under
the same campaign read guard, recording the pages' own view events; the
manual weekly report is requested through its page's own function, under the
same database rules, recording the same events and task.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestRecipient,
    WeeklyManualRequest,
)

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli, parity  # noqa: F401
from .test_daily_digest_dispatch_postgresql import allocated as daily_allocated
from .test_daily_digest_planning_postgresql import INSTANT as DAILY_INSTANT
from .test_daily_digest_views_postgresql import read
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_weekly_capture_postgresql import INSTANT as WEEKLY_INSTANT
from .test_weekly_coverage_postgresql import accepted
from .test_weekly_dispatch_postgresql import allocated as weekly_allocated

pytestmark = pytest.mark.django_db(transaction=True)


def count(event_type):
    """How many ``event_type`` events have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def test_a_retained_daily_report(family_mail, google, cli):  # noqa: F811
    """The stored snapshot's figures, with the page's view events."""
    with campaign_clock(DAILY_INSTANT):
        ready = daily_allocated(family_mail)
        browser, _ = signed_in()
        run = cli()
        snapshot = str(ready.snapshot_id)
        parity(
            "daily_digest_viewed",
            lambda: read(browser, f"/admin/reports/emailed/daily/{snapshot}/"),
            lambda: run("digest", "daily", snapshot),
        )
        code, document = run("digest", "daily", snapshot)
        assert code == 0, document
        result = document["result"]
        assert result["kind"] == "daily" and result["snapshot_id"] == snapshot
        assert result["participation"]["fact_set_id"] == str(ready.fact_set_id)
        assert result["days"] and result["statistics"]["active"]["families"] >= 1
        assert "@" not in str(document)

        # Another kind's id, or an unknown one: not_available, nothing read.
        before = count("daily_digest_viewed") + count("weekly_digest_viewed")
        for argv in (("weekly", snapshot), ("daily", str(uuid4()))):
            code, document = run("digest", *argv)
            assert code == 1 and document["error"]["code"] == "not_available", argv
        assert count("daily_digest_viewed") + count("weekly_digest_viewed") == before

        # A read-only session reads it.
        reader = read_only_session(run.service)
        code, document = run("digest", "daily", snapshot, secret=reader)
        assert code == 0 and document["result"]["snapshot_id"] == snapshot

        # A retained report whose facts cannot be read is the page's 503:
        # unavailable (exit 3, retry), recorded as a failed read.
        from parishkit.stewardship.reports import digest_views
        from parishkit.stewardship.reports.facts import FactUnavailable

        def unreadable(snapshot_id):
            """The retained facts no longer match their inputs."""
            raise FactUnavailable("Daily report facts do not match.")

        failed = AuditEvent.objects.filter(
            event_type="daily_digest_viewed",
            auditcontext__context__outcome="failed",
        )
        before = failed.count()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(digest_views, "retained_document", unreadable)
            code, document = run("digest", "daily", snapshot)
        assert code == 3 and document["error"]["code"] == "unavailable"
        assert failed.count() == before + 1


def test_a_retained_weekly_report_and_a_manual_request(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """Item states without Family text; the manual request as its page."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        retained = weekly_allocated(harness)
        browser, _ = signed_in()
        run = cli()
        snapshot = str(retained.pk)
        parity(
            "weekly_digest_viewed",
            lambda: read(browser, f"/admin/reports/emailed/weekly/{snapshot}/"),
            lambda: run("digest", "weekly", snapshot),
        )
        code, document = run("digest", "weekly", snapshot)
        assert code == 0, document
        result = document["result"]
        assert result["kind"] == "weekly" and not result["manual"]
        assert result["total"] == len(result["items"]) >= 1
        assert str(retained.information[0]) in {
            item["item_id"] for item in result["items"]
        }
        assert all(item["current"] == "current_actionable" for item in result["items"])
        assert "CANARY" not in str(document) and "@" not in str(document)
        code, document = run("digest", "weekly", snapshot, "--page", "2")
        assert code == 1 and document["error"]["code"] == "not_available"
        code, document = run("digest", "weekly", snapshot, "--page", "02")
        assert code == 1 and document["error"]["code"] == "invalid"

        # The manual report: without the acknowledgement, nothing happens.
        tasks = TaskRun.objects.count()
        code, document = run("digest", "weekly-request")
        assert code == 4 and document["error"]["code"] == "confirmation_required"
        # The emailed report is not resolved yet: the database refuses a
        # manual one, as the page's 409 does, and nothing is recorded.
        key = uuid4()
        argv = ("digest", "weekly-request", "--yes", "--request-key", str(key))
        code, document = run(*argv)
        assert code == 1 and document["error"]["code"] == "stale_version", document
        assert TaskRun.objects.count() == tasks
        assert not WeeklyManualRequest.objects.exists()
        assert count("admin_cmd_digest_weekly_request") == 0

        # Once it is resolved, the request is the page's: its task, its
        # weekly_manual_requested event, and the command's event once.
        accepted(WeeklyDigestRecipient.objects.get(snapshot=retained))
        code, document = run(*argv)
        assert code == 0, document
        result = document["result"]
        assert result["created"] and result["request_key"] == str(key)
        manual = WeeklyManualRequest.objects.get(pk=key)
        assert result["task"]["id"] == str(manual.task_id)
        assert count("weekly_manual_requested") == 1
        assert count("admin_cmd_digest_weekly_request") == 1
        event = AuditEvent.objects.get(event_type="admin_cmd_digest_weekly_request")
        assert event.subject_id is not None
        # The same key returns the same request and records nothing new.
        code, document = run(*argv)
        assert code == 0 and document["result"]["created"] is False
        assert document["result"]["task"]["id"] == str(manual.task_id)
        assert count("weekly_manual_requested") == 1
        assert count("admin_cmd_digest_weekly_request") == 1

        # A read-only session reads digests but cannot request one.
        reader = read_only_session(run.service)
        code, document = run("digest", "weekly-request", "--yes", secret=reader)
        assert code == 1 and document["error"]["code"] == "denied"
        code, document = run("digest", "weekly", snapshot, secret=reader)
        assert code == 0
