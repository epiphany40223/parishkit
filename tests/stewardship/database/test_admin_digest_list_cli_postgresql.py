"""``pk-stewardship admin digest list``: the Emailed reports page (ADM-11).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The command
reads the page's own ``listing``: the current campaign's ready daily
reports and, for an Administrator, its weekly reports, by id and date only,
and the page's ``?requested=`` request. As the page's read, it records no
event.
"""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship import admin_digests
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestRecipient,
    WeeklyManualRequest,
)

from .auth_builders import signed_in
from .automation_builders import paired
from .campaign_builders import campaign_clock
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated as daily_allocated
from .test_daily_digest_planning_postgresql import INSTANT as DAILY_INSTANT
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_weekly_capture_postgresql import INSTANT as WEEKLY_INSTANT
from .test_weekly_coverage_postgresql import accepted
from .test_weekly_dispatch_postgresql import allocated as weekly_allocated

pytestmark = pytest.mark.django_db(transaction=True)

# Documents are printed with sorted keys, so members are compared as sets.
FIELDS = {
    "campaign_id",
    "daily",
    "weekly",
    "weekly_request_available",
    "requested",
    "shown",
}


def revoke(service, secret):
    """Revoke the session ``secret`` names, as its owner would."""
    row = AutomationSession.objects.get(secret_digest=automation.secret_digest(secret))
    actor = current_principal(service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")


def test_the_daily_list_matches_the_page_and_records_nothing(
    family_mail,  # noqa: F811
    google,
    cli,  # noqa: F811
):
    """The page's ready daily report, by id; any session; no event."""
    with campaign_clock(DAILY_INSTANT):
        ready = daily_allocated(family_mail)
        browser, _ = signed_in()
        run = cli()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(reverse("admin:emailed_reports"))
        reader = read_only_session(run.service)
        events = AuditEvent.objects.count()
        code, document = run("digest", "list")
        assert code == 0, document
        result = document["result"]
        assert set(result) == FIELDS
        assert result["campaign_id"] == str(family_mail.campaign.pk)
        [daily] = result["daily"]
        assert daily["snapshot_id"] == str(ready.snapshot_id)
        assert set(daily) == {
            "snapshot_id",
            "through_date",
            "covered_dates",
            "observed_at",
        }
        # The page links the same report.
        link = reverse("admin:daily_digest_snapshot", args=[ready.snapshot_id])
        assert link.encode() in page.content
        # An Administrator's weekly list: none captured yet, and no schedule.
        assert result["weekly"] == [] and not result["weekly_request_available"]
        assert result["requested"] is None and result["shown"] == 60
        assert "@" not in str(document)
        # A read-only session reads it too, and neither read is recorded.
        code, again = run("digest", "list", secret=reader)
        assert code == 0 and again["result"] == result
        assert AuditEvent.objects.count() == events
        # A request key must be a canonical UUID: a usage error.
        code, document = run("digest", "list", "--requested", "x")
        assert code == 2 and document["error"]["code"] == "usage"
        # An ended session reads nothing.
        revoke(run.service, run.secret)
        code, document = run("digest", "list")
        assert code == 5 and document["error"]["code"] == "session_ended"


def test_weekly_reports_and_a_requested_report(
    live_response_service,
    monkeypatch,
    google,
    cli,  # noqa: F811
):
    """The weekly list, the schedule flag and ``--requested`` as the page's."""
    harness = live_response_service
    with campaign_clock(WEEKLY_INSTANT):
        retained = weekly_allocated(harness)
        browser, _ = signed_in()
        run = cli()
        code, document = run("digest", "list")
        assert code == 0, document
        [weekly] = document["result"]["weekly"]
        assert weekly["snapshot_id"] == str(retained.pk) and not weekly["manual"]
        assert set(weekly) == {"snapshot_id", "manual", "observed_at"}
        # This campaign has its Weekly Admin digest schedule.
        assert document["result"]["weekly_request_available"]

        # Once the emailed report is resolved, a manual request names its
        # task.
        accepted(WeeklyDigestRecipient.objects.get(snapshot=retained))
        key = uuid4()
        code, document = run(
            "digest", "weekly-request", "--yes", "--request-key", str(key)
        )
        assert code == 0, document
        manual = WeeklyManualRequest.objects.get(pk=key)
        code, document = run("digest", "list", "--requested", str(key))
        assert code == 0, document
        result = document["result"]
        assert result["weekly_request_available"]
        # Not captured yet: its task, and no report.
        assert result["requested"] == {
            "task_id": str(manual.task_id),
            "report_id": None,
        }
        # Another key names nothing, as on the page.
        code, document = run("digest", "list", "--requested", str(uuid4()))
        assert code == 0 and document["result"]["requested"] is None

        # A Staff reader sees the daily list only, as on the page: ``weekly``
        # is null, with no request and no Send a weekly report now, even with
        # this weekly report, schedule and request in the database. Pairing
        # needs an Administrator, so the command's own admission is the
        # only stand-in: it returns the session's principal as Staff.
        real = admin_digests._admit

        def as_staff(*arguments):
            """The real admission, its principal holding only Staff."""
            return replace(real(*arguments), roles=frozenset({"staff"}))

        monkeypatch.setattr(admin_digests, "_admit", as_staff)
        code, document = run("digest", "list", "--requested", str(key))
        assert code == 0, document
        staff = document["result"]
        assert staff["weekly"] is None and staff["requested"] is None
        assert not staff["weekly_request_available"]
        # The same campaign and daily list as the Administrator's.
        assert staff["campaign_id"] == result["campaign_id"]
        assert staff["daily"] == result["daily"]


def test_a_missing_configuration_row_fails_closed(auth_service, google, monkeypatch):
    """No configuration row is ``not_available``, never "no campaign".

    The page's ``.get()`` denies a reader then; the command's must not read
    the missing row as an empty campaign. The guards keep the row from being
    deleted, so its read alone is made to find none.
    """
    commands = runner(auth_service, monkeypatch)
    _, secret, _ = paired(commands.service)
    manager = SystemConfiguration.objects
    real = manager.values_list
    reads = []

    def missing(*fields, **options):
        """The current campaign's read finds no row; any other is real."""
        if fields == ("current_campaign_id",):
            reads.append(fields)
            return manager.none().values_list(*fields, **options)
        return real(*fields, **options)

    monkeypatch.setattr(manager, "values_list", missing)
    code, document, _ = commands("digest", "list", secret=secret)
    assert reads, "the command did not read the configuration row"
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document


def test_no_current_campaign(auth_service, google, monkeypatch):
    """Without a current campaign the lists are empty, as the page's."""
    commands = runner(auth_service, monkeypatch)
    _, secret, _ = paired(commands.service)
    code, document, _ = commands("digest", "list", secret=secret)
    assert code == 0, document
    assert document["result"] == {
        "campaign_id": None,
        "daily": [],
        "weekly": [],
        "weekly_request_available": False,
        "requested": None,
        "shown": 60,
    }
